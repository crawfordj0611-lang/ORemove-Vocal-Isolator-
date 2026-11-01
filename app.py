"""
ORemove - AI Powered Vocal Isolator
===================================

A desktop app that takes a YouTube video or your own audio files and splits
each into an Acapella (vocals) stem and an Instrumental stem using Ultimate
Vocal Remover (UVR5) models, run through the `audio-separator` library.

The interface is a web page (ui/) shown in a native window by pywebview. It
calls the Api class below and polls it for progress events; the engine runs
on a background thread so the window never freezes.

Pipeline:
    1. Check FFmpeg is on PATH.
    2. Get the audio as WAV in a temporary folder: for a YouTube link, yt-dlp
       reads the title and downloads the best audio stream; for a local file
       (MP3, WAV, FLAC, M4A, ... or a video), FFmpeg decodes it.
    3. audio-separator: load the chosen UVR model and split the WAV into
       Vocals and Instrumental.
    4. Copy the stems to <output>/acapellas/ and <output>/instrumentals/,
       named after the video title or file name, and delete the temporary
       files. Several files can be queued; the model is loaded once for all.

The AI engine (PyTorch) takes a while to import, so it starts loading in the
background as soon as the window opens. An NVIDIA GPU is used automatically
when the CUDA build of PyTorch is installed; Apple Silicon GPUs on macOS.

Run:  python app.py
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

APP_TITLE = "ORemove"
APP_TAGLINE = "AI Powered Vocal Isolator"
APP_VERSION = "1.5.0"

# True when running as the packaged app (built with PyInstaller).
FROZEN = getattr(sys, "frozen", False)
MACOS = sys.platform == "darwin"

# Folder holding the program; from source, this script's folder.
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
# Read-only files bundled with the app (the interface, FFmpeg, the fast model).
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
UI_DIR = BUNDLE_DIR / "ui"
# Where models, settings and history are kept. On Windows (and from source)
# that's next to the program; a macOS app bundle must not be written to, so
# there it's ~/Library/Application Support/ORemove.
DATA_DIR = (Path.home() / "Library" / "Application Support" / "ORemove") if (FROZEN and MACOS) else APP_DIR

if FROZEN:
    # A windowed .exe has no console, so sys.stdout/stderr are None and any
    # library that prints (tqdm progress bars, logging) would crash.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    # The build ships ffmpeg.exe/ffprobe.exe next to the app; put them on PATH
    # so yt-dlp, pydub and audio-separator all find them.
    bundle_dirs = [APP_DIR, Path(getattr(sys, "_MEIPASS", APP_DIR))]
    os.environ["PATH"] = os.pathsep.join(str(d) for d in bundle_dirs) + os.pathsep + os.environ.get("PATH", "")

# The installed app saves to the user's Music folder; from source, next to the script.
DEFAULT_OUTPUT_DIR = (Path.home() / "Music" / "ORemove") if FROZEN else APP_DIR / "output"
# UVR models are downloaded here on first use and reused after that.
MODEL_DIR = DATA_DIR / "models"
# Remembers the chosen model, quality, format and output folder between runs.
SETTINGS_FILE = DATA_DIR / "settings.json"
# The songs separated so far (shown under History).
HISTORY_FILE = DATA_DIR / "history.json"


@dataclass(frozen=True)
class ModelOption:
    name: str      # the model's name
    best_for: str  # what it is best at
    filename: str  # model file name known to audio-separator
    size: str      # download size, shown in Settings


# Ranked by audio-separator's published benchmark (median SDR over 40 songs).
# List every model audio-separator knows with:  audio-separator --list_models
MODELS = [
    ModelOption("BS-Roformer 1297", "Best for instrumentals",
                "model_bs_roformer_ep_317_sdr_12.9755.ckpt", "610 MB"),
    ModelOption("BS-Roformer 1296", "Best for acapellas",
                "model_bs_roformer_ep_368_sdr_12.9628.ckpt", "610 MB"),
    ModelOption("MDX-Net Inst HQ 3", "Fastest",
                "UVR-MDX-NET-Inst_HQ_3.onnx", "Included"),
]
MODELS_BY_FILE = {m.filename: m for m in MODELS}

OUTPUT_FORMATS = ["WAV", "MP3"]  # WAV is lossless; MP3 is written at 320 kbps

QUALITY_MODES = ["Best", "Faster"]  # "Faster": half the overlapping passes for BS-Roformer

# The self-test switches this on to exercise the NVIDIA GPU code path (the fast
# model converted to PyTorch, in batches) on build machines without a GPU.
force_gpu_settings = False

# Share of the progress bar given to each stage.
DOWNLOAD_SHARE = 0.40   # 0 % -> 40 %  : yt-dlp download
MODEL_LOAD_AT = 0.45    # 45 %         : model loaded, separation starts
SEPARATED_AT = 0.95     # 95 %         : stems written, moving files

# --------------------------------------------------------------------------- #
# Helpers (no GUI code here, so they are easy to test or reuse)
# --------------------------------------------------------------------------- #

class PipelineError(Exception):
    """An expected failure with a message meant for the user."""


class Cancelled(PipelineError):
    """The user pressed Stop."""

    def __init__(self):
        super().__init__("Stopped")


@dataclass
class Job:
    """Everything the worker needs, captured from the UI when Start is clicked."""
    sources: list[str]  # YouTube links and/or local audio file paths, done in order
    model_file: str
    output_format: str  # "WAV" or "MP3"
    output_dir: Path
    faster: bool = False  # fewer overlapping passes: about 2x faster, slightly lower quality


YOUTUBE_URL_RE = re.compile(
    r"^(https?://)?(www\.|m\.|music\.)?(youtube\.com|youtu\.be|youtube-nocookie\.com)/\S+$",
    re.IGNORECASE,
)


def is_youtube_url(url: str) -> bool:
    return bool(YOUTUBE_URL_RE.match(url.strip()))


def sanitize_filename(title: str, max_length: int = 150) -> str:
    """Turn a video title into a filename that is safe on Windows, macOS and Linux."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", title)  # characters Windows forbids
    name = re.sub(r"\s+", " ", name).strip(" .")         # tidy spaces, no trailing dot
    name = name[:max_length].rstrip(" .")
    return name or "untitled"


def unique_path(path: Path) -> Path:
    """Return `path`, or `name (2).ext`, `name (3).ext`, ... if it already exists."""
    if not path.exists():
        return path
    n = 2
    while True:
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


# File types offered by the Browse dialog. FFmpeg reads all of these (video files
# too: only their audio is used).
AUDIO_EXTENSIONS = ("mp3", "wav", "flac", "m4a", "aac", "ogg", "opus", "wma", "aiff", "aif",
                    "alac", "mp4", "mkv", "mov", "webm")

# Keeps FFmpeg from flashing a console window when run from the windowed .exe.
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0


def looks_like_url(text: str) -> bool:
    return bool(re.match(r"^(https?://|www\.)", text.strip(), re.IGNORECASE)) or is_youtube_url(text)


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def open_path(path: Path) -> None:
    """Open a file with its default app, or a folder in the file manager."""
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]  # Windows only
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def reveal_in_file_manager(path: Path) -> None:
    """Show a file selected in Explorer (elsewhere: open its folder)."""
    if sys.platform.startswith("win"):
        subprocess.Popen(["explorer", "/select,", str(path)])
    elif MACOS:
        subprocess.Popen(["open", "-R", str(path)])
    else:
        open_path(path.parent)


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}  # first run, or unreadable: use defaults


def save_settings(settings: dict) -> None:
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    except Exception:
        pass  # settings are a convenience; never fail a run over them


def detect_device() -> str:
    """Import the AI engine and describe what it will run on, e.g. 'RTX 4070 SUPER'."""
    import torch
    import audio_separator.separator  # noqa: F401  # the slow import we want done early
    if torch.cuda.is_available():
        return torch.cuda.get_device_name(0).replace("NVIDIA ", "").replace("GeForce ", "")
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "Apple GPU"
    return "CPU"


def is_nvidia_edition() -> bool:
    """True for the NVIDIA GPU edition (PyTorch built with CUDA)."""
    try:
        import torch
        return torch.version.cuda is not None
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Processing pipeline (runs on the worker thread)
# --------------------------------------------------------------------------- #

class _YtDlpLogger:
    """Sends yt-dlp warnings and errors to the UI log instead of the console."""

    def __init__(self, log):
        self._log = log

    def debug(self, msg):
        pass  # yt-dlp is very chatty at debug level

    def info(self, msg):
        pass

    def warning(self, msg):
        self._log(f"yt-dlp warning: {msg}")

    def error(self, msg):
        self._log(f"yt-dlp: {msg}")


# The loaded model, kept between runs so each extraction doesn't reload it.
# Only one job runs at a time, so no locking is needed. "safe" becomes True
# after a GPU out-of-memory error and stays on for the rest of the session.
_cache = {"key": None, "separator": None, "safe": False}


def _release_separator():
    """Drop the loaded model and free its GPU memory."""
    _cache["key"] = _cache["separator"] = None
    try:
        import gc
        import torch
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


class ExtractionPipeline:
    """
    Download + separation logic.

    `log(str)`, `progress(float 0..1 or None)` and `stage(name, detail)` are
    callbacks supplied by the GUI; progress(None) means "busy, but the amount is
    unknown". Stages are "download", "separate" and "save".

    For a batch of several sources, progress and stage details are reported
    for the whole batch ("File 2 of 3 · ..."), and the model is loaded once.
    """

    def __init__(self, log, progress, stage=lambda _name, _detail: None,
                 title=lambda _text: None, cancel_event: threading.Event | None = None):
        self.log = log
        self._progress_cb = progress
        self._stage_cb = stage
        self._title_cb = title
        self.cancel_event = cancel_event or threading.Event()
        self._index, self._count = 0, 1  # position in the current batch

    def check_cancel(self):
        """Stop between steps once the user has pressed Stop."""
        if self.cancel_event.is_set():
            raise Cancelled()

    def progress(self, value):
        """Report progress of the current item, scaled to the whole batch."""
        self._progress_cb(None if value is None else (self._index + value) / self._count)

    def stage(self, name, detail):
        prefix = f"File {self._index + 1} of {self._count}  ·  " if self._count > 1 else ""
        self._stage_cb(name, prefix + detail)

    # ---- Step 1: download --------------------------------------------------

    def fetch_title(self, url: str) -> str:
        import yt_dlp
        from yt_dlp.utils import DownloadError

        self.log("Fetching video information...")
        opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,          # a watch?v=..&list=.. URL means just this video
            "skip_download": True,
            "logger": _YtDlpLogger(self.log),
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except DownloadError as exc:
            raise PipelineError(self._explain_download_error(exc)) from exc

        if info is None:
            raise PipelineError("YouTube returned no information for this link.")
        if info.get("_type") == "playlist":
            raise PipelineError("This is a playlist link. Paste the link of a single video.")
        if info.get("is_live"):
            raise PipelineError("Live streams can't be processed. Try again after the stream ends.")

        title = info.get("title") or info.get("id") or "untitled"
        duration = info.get("duration")
        length = f" ({int(duration) // 60}:{int(duration) % 60:02d})" if duration else ""
        self.log(f"Video: {title}{length}")
        return title

    def download_wav(self, url: str, work_dir: Path) -> Path:
        """Download the best audio stream and convert it to WAV. Returns the WAV path."""
        import yt_dlp
        from yt_dlp.utils import DownloadError

        last_reported = {"pct": -10}

        def on_progress(d):
            self.check_cancel()  # aborts the download; reported as Cancelled below
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                if total:
                    fraction = d.get("downloaded_bytes", 0) / total
                    self.progress(fraction * DOWNLOAD_SHARE)
                    pct = int(fraction * 100)
                    if pct >= last_reported["pct"] + 10:  # one log line per 10 %
                        last_reported["pct"] = pct
                        self.log(f"  downloading... {pct}%")
                else:
                    self.progress(None)
            elif d.get("status") == "finished":
                self.progress(DOWNLOAD_SHARE)
                self.log("Download finished. Converting to WAV with FFmpeg...")

        opts = {
            # Best audio-only stream (usually Opus ~160k or AAC ~128k on YouTube);
            # fall back to the best combined stream if there is no audio-only one.
            "format": "bestaudio/best",
            "outtmpl": str(work_dir / "source.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "retries": 5,
            "fragment_retries": 5,
            "logger": _YtDlpLogger(self.log),
            "progress_hooks": [on_progress],
            # Decode straight to uncompressed WAV so no extra lossy step is added
            # before separation.
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}],
        }

        self.log("Downloading audio...")
        self.stage("download", "Downloading audio")
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
        except (DownloadError, Cancelled) as exc:
            self.check_cancel()
            raise PipelineError(self._explain_download_error(exc)) from exc

        wav = work_dir / "source.wav"
        if not wav.exists():
            raise PipelineError("The download finished but no WAV file was produced. Is FFmpeg working?")
        self.log(f"Audio ready ({wav.stat().st_size / 1_048_576:.1f} MB WAV).")
        return wav

    @staticmethod
    def _explain_download_error(exc: Exception) -> str:
        """Map yt-dlp's error text to a short, actionable message."""
        text = str(exc)
        lowered = text.lower()
        if "unsupported url" in lowered or "is not a valid url" in lowered:
            return "That link isn't a supported YouTube video URL."
        if "video unavailable" in lowered or "private video" in lowered:
            return "The video is unavailable or private."
        if "sign in to confirm your age" in lowered:
            return "This video is age-restricted and can't be downloaded without signing in."
        if any(s in lowered for s in ("getaddrinfo", "name resolution", "timed out",
                                      "connection", "network is unreachable", "urlopen error")):
            return "Network error: check your internet connection and try again."
        if "ffmpeg" in lowered or "ffprobe" in lowered:
            return "FFmpeg failed while converting the audio. Check your FFmpeg install."
        return f"Download failed: {text.removeprefix('ERROR: ')}"

    def convert_local_file(self, src: Path, dest: Path) -> Path:
        """Decode a local audio (or video) file to WAV with FFmpeg."""
        self.log(f"Reading {src}")
        self.stage("download", f"Reading {src.name}")
        self.progress(None)
        if not src.is_file():
            raise PipelineError(f"File not found: {src}")
        # 24-bit keeps the full quality of 24-bit WAV/FLAC sources.
        proc = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vn", "-ac", "2",
             "-c:a", "pcm_s24le", str(dest)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, creationflags=NO_WINDOW)
        while proc.poll() is None:
            if self.cancel_event.is_set():
                proc.kill()
                proc.wait()
                raise Cancelled()
            time.sleep(0.1)
        stderr = proc.stderr.read()
        if proc.returncode != 0 or not dest.exists():
            reason = (stderr.strip().splitlines() or ["not a readable audio file"])[-1]
            raise PipelineError(f"Couldn't read {src.name}: {reason}")
        self.progress(DOWNLOAD_SHARE)
        return dest

    # ---- Step 2: separation -----------------------------------------------

    def separate(self, wav: Path, job: Job, stems_dir: Path) -> list[Path]:
        """Run UVR on `wav`, writing stems into `stems_dir`. Returns their paths."""
        separator = self._load_separator(job, stems_dir)

        self.log("Separating stems...")
        self.stage("separate", "Separating stems")
        self.progress(None)
        started = time.monotonic()
        try:
            produced = separator.separate(str(wav))
        except Exception as exc:
            if "out of memory" not in str(exc).lower() or _cache["safe"]:
                raise PipelineError(f"Separation failed: {exc}") from exc
            # The GPU ran out of memory with the fast settings: retry once with
            # the conservative ones (no batching, full precision).
            self.log("GPU ran out of memory; retrying with lower-memory settings...")
            _release_separator()
            separator = self._load_separator(job, stems_dir, safe=True)
            try:
                produced = separator.separate(str(wav))
            except Exception as exc2:
                raise PipelineError(f"Separation failed: {exc2}") from exc2
        self.log(f"Separated in {time.monotonic() - started:.1f}s")

        # audio-separator returns file names relative to output_dir.
        paths = [p if p.is_absolute() else stems_dir / p for p in map(Path, produced)]
        paths = [p for p in paths if p.exists()]
        if not paths:
            raise PipelineError("The model ran but produced no output files.")
        self.progress(SEPARATED_AT)
        return paths

    def _load_separator(self, job: Job, stems_dir: Path, safe: bool = False):
        """
        Return a separator with the job's model loaded. The loaded model is kept
        between runs (and across a batch) and only reloaded when the model,
        format or quality setting changes.
        """
        try:
            from audio_separator.separator import Separator
            import torch
        except ImportError as exc:
            raise PipelineError(
                "audio-separator is not installed correctly. "
                "Run: pip install -r requirements.txt"
            ) from exc

        cuda = torch.cuda.is_available()
        safe = safe or _cache["safe"]
        gpu_settings = (cuda or force_gpu_settings) and not safe
        key = (job.model_file, job.output_format, job.faster, safe, gpu_settings)
        if _cache["key"] == key:
            # Point the loaded model at this run's folder; it is read when writing.
            _cache["separator"].output_dir = str(stems_dir)
            _cache["separator"].model_instance.output_dir = str(stems_dir)
            self.log(f"Model already loaded: {job.model_file}")
            return _cache["separator"]
        _release_separator()

        self.log("Loading the AI engine (PyTorch)...")
        self.stage("separate", "Loading the AI engine")
        self.progress(None)
        try:
            device = detect_device()
            self.log(f"Running on: {device}" + (" (slower; an NVIDIA GPU is much faster)" if device == "CPU" else ""))
        except Exception:
            pass  # device info is a nicety only

        roformer = "roformer" in job.model_file.lower()
        options = dict(
            # Half precision on NVIDIA GPUs: native float16 for BS-Roformer (verified
            # by audio-separator), autocast for the MDX-Net model.
            use_native_fp16=cuda and not safe and roformer,
            use_autocast=cuda and not safe and not roformer,
            # A segment size other than the model's own (256) makes audio-separator
            # run the MDX-Net model through PyTorch, which uses the GPU, instead of
            # ONNX Runtime on the CPU; batching keeps the GPU busy.
            mdx_params={"hop_length": 1024, "segment_size": 512 if gpu_settings else 256,
                        "overlap": 0.25, "batch_size": 4 if gpu_settings else 1,
                        "enable_denoise": False},
            # BS-Roformer runs 4 overlapping passes by default; "Faster" uses 2.
            mdxc_params={"segment_size": 256, "override_model_segment_size": False,
                         "batch_size": None, "overlap": 2 if job.faster else None,
                         "pitch_shift": 0},
        )
        settings = [name for name, on in [
            ("float16", options["use_native_fp16"]),
            ("autocast", options["use_autocast"]),
            ("MDX-Net on PyTorch, batch 4", gpu_settings and not roformer),
            ("2 passes (Faster)", job.faster and roformer),
            ("low-memory mode", safe),
        ] if on]
        self.log("Settings: " + (", ".join(settings) or "standard"))

        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        separator = Separator(
            log_level=logging.WARNING,      # keep the console quiet; we log stages ourselves
            model_file_dir=str(MODEL_DIR),
            output_dir=str(stems_dir),
            output_format=job.output_format,
            output_bitrate="320k" if job.output_format == "MP3" else None,
            **options,
        )

        model_cached = (MODEL_DIR / job.model_file).exists()
        self.log(f"Loading UVR model: {job.model_file}"
                 + ("" if model_cached else "  (first use: downloading model, please wait)"))
        self.stage("separate", "Loading model" if model_cached
                   else "Downloading model (first use only)")
        try:
            separator.load_model(model_filename=job.model_file)
        except Exception as exc:
            raise PipelineError(f"Could not load model '{job.model_file}': {exc}") from exc
        self.progress(MODEL_LOAD_AT)
        _cache.update(key=key, separator=separator, safe=safe)
        return separator

    # ---- Step 3: put the stems where they belong ---------------------------

    def file_stems(self, stems: list[Path], base_name: str, output_dir: Path) -> list[Path]:
        """
        Copy each stem into acapellas/ or instrumentals/, named after the video
        or file. audio-separator names files like 'input0_(Vocals)_<model>.wav'.
        """
        self.stage("save", "Saving stems")
        acapella_dir = output_dir / "acapellas"
        instrumental_dir = output_dir / "instrumentals"
        acapella_dir.mkdir(parents=True, exist_ok=True)
        instrumental_dir.mkdir(parents=True, exist_ok=True)

        saved = []
        for stem in stems:
            label = stem.name.lower()
            # Check instrumental first: "(no vocals)" also contains "vocals)".
            # Roformer models call the instrumental stem "other".
            if any(t in label for t in ("(instrumental)", "(other)", "(no vocals)", "(no_vocals)")):
                target = instrumental_dir / f"{base_name} (Instrumental){stem.suffix}"
            elif "(vocals)" in label:
                target = acapella_dir / f"{base_name} (Acapella){stem.suffix}"
            else:
                # A model with other stem names: keep it, unsorted, with its stem label.
                stem_label = re.search(r"_\(([^)]+)\)_", stem.name)
                suffix = stem_label.group(1) if stem_label else "Stem"
                target = output_dir / f"{base_name} ({suffix}){stem.suffix}"
            target = unique_path(target)
            # Copy, don't move: the temp folder is private to the current user
            # (Python restricts it on Windows), and a moved file keeps that
            # restrictive permission, so apps like Windows Media Player get
            # "permission denied". A copy inherits the output folder's normal
            # permissions. The temp folder is deleted afterwards anyway.
            shutil.copyfile(stem, target)
            saved.append(target)
        return saved

    # ---- Whole job --------------------------------------------------------

    def run(self, job: Job) -> tuple[list[dict], list[str]]:
        """
        Process every source in `job`. Returns (songs, failure messages), where
        each song is {"title": str, "stems": [saved stem paths]}. A single
        source raises on failure; in a batch, a failed item is skipped and
        reported, and the rest still run. Stop raises Cancelled.
        """
        if not ffmpeg_available():
            raise PipelineError(
                "FFmpeg was not found on your PATH. Install FFmpeg (see README) "
                "and restart the app."
            )
        saved, failures = [], []
        self._count = len(job.sources)

        # Everything temporary lives here and is deleted at the end, even on errors.
        with tempfile.TemporaryDirectory(prefix="oremove_", ignore_cleanup_errors=True) as tmp:
            work_dir = Path(tmp)
            stems_dir = work_dir / "stems"
            stems_dir.mkdir()
            for i, source in enumerate(job.sources):
                self._index = i
                try:
                    saved.append(self._run_one(source, i, job, work_dir, stems_dir))
                except Cancelled:
                    raise
                except PipelineError as exc:
                    if self._count == 1:
                        raise
                    name = source if looks_like_url(source) else Path(source).name
                    failures.append(f"{name}: {exc}")
                    self.log(f"Skipped {name}: {exc}")
            self.log("Cleaning up temporary files...")

        if failures and not saved:
            raise PipelineError(f"None of the files could be processed. {failures[0]}")
        return saved, failures

    def _run_one(self, source: str, i: int, job: Job, work_dir: Path, stems_dir: Path) -> dict:
        """Get one source as WAV, separate it, and save its stems."""
        self.check_cancel()
        wav = work_dir / f"input{i}.wav"  # unique per item: stems are named after it
        if looks_like_url(source):
            if not is_youtube_url(source):
                raise PipelineError("Only YouTube links are supported (youtube.com or youtu.be).")
            title = self.fetch_title(source)
            self._title_cb(title)
            self.check_cancel()
            download_dir = work_dir / f"download{i}"
            download_dir.mkdir()
            self.download_wav(source, download_dir).replace(wav)
        else:
            src = Path(source)
            title = src.stem
            self._title_cb(title)
            self.convert_local_file(src, wav)
        base_name = sanitize_filename(title)

        self.check_cancel()
        stems = self.separate(wav, job, stems_dir)
        try:
            self.check_cancel()  # separation can't be interrupted; discard its result instead
            saved = self.file_stems(stems, base_name, job.output_dir)
        finally:
            # Free disk space as we go; a batch of long WAVs adds up.
            for path in [wav, *stems]:
                path.unlink(missing_ok=True)
        return {"title": title, "stems": saved}


# --------------------------------------------------------------------------- #
# Interface support: waveforms, history, local file server
# --------------------------------------------------------------------------- #

def waveform(path: Path, points: int = 200) -> tuple[list[float], float]:
    """Peak levels (0..1) across the file for drawing its waveform, and its duration."""
    try:
        import numpy as np
        import soundfile as sf
        with sf.SoundFile(str(path)) as f:
            duration = f.frames / f.samplerate
            block = max(1, f.frames // points)
            peaks = []
            for data in f.blocks(blocksize=block, dtype="float32", always_2d=True):
                peaks.append(float(np.abs(data).max()) if len(data) else 0.0)
        top = max(peaks) or 1.0
        return [round(p / top, 3) for p in peaks[:points]], duration
    except Exception:
        return [], 0.0


def human_size(path: Path) -> str:
    try:
        return f"{path.stat().st_size / 1_048_576:.1f} MB"
    except OSError:
        return ""


STEM_KINDS = {"acapellas": "Acapella", "instrumentals": "Instrumental"}


def load_history() -> list[dict]:
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def add_to_history(entries: list[dict]) -> None:
    try:
        history = (entries + load_history())[:100]
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        HISTORY_FILE.write_text(json.dumps(history, indent=1), encoding="utf-8")
    except Exception:
        pass  # history is a convenience; never fail a run over it


class LocalServer:
    """
    Serves the interface (ui/) and finished stems to the window over
    http://127.0.0.1 on a random port. Stems are only reachable through
    unguessable tokens handed out by the app. Supports byte ranges, so the
    audio player can seek.
    """

    TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript",
             ".wav": "audio/wav", ".mp3": "audio/mpeg", ".svg": "image/svg+xml", ".png": "image/png"}

    def __init__(self):
        self.files: dict[str, Path] = {}
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass  # keep quiet

            def do_GET(self):
                route = unquote(self.path.split("?", 1)[0])
                if route.startswith("/audio/"):
                    token = route[len("/audio/"):].split(".", 1)[0]  # URL ends in .wav/.mp3
                    path = server.files.get(token)
                else:
                    path = (UI_DIR / route.lstrip("/")).resolve()
                    if UI_DIR.resolve() not in path.parents:
                        path = None
                if not path or not path.is_file():
                    self.send_error(404)
                    return
                self.send_file(path)

            def send_file(self, path: Path):
                size = path.stat().st_size
                start, end = 0, size - 1
                match = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
                if match and (match.group(1) or match.group(2)):
                    if match.group(1):
                        start = int(match.group(1))
                        end = int(match.group(2)) if match.group(2) else end
                    else:  # "bytes=-N": the last N bytes
                        start = max(0, size - int(match.group(2)))
                    end = min(end, size - 1)
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                else:
                    self.send_response(200)
                self.send_header("Content-Type", server.TYPES.get(path.suffix.lower(), "application/octet-stream"))
                self.send_header("Content-Length", str(end - start + 1))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    with open(path, "rb") as f:
                        f.seek(start)
                        left = end - start + 1
                        while left > 0:
                            chunk = f.read(min(1 << 16, left))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            left -= len(chunk)
                except (ConnectionError, OSError):
                    pass  # the player stopped reading (seek or pause)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/index.html"

    def audio_url(self, path: Path) -> str:
        token = uuid.uuid4().hex
        self.files[token] = path
        return f"http://127.0.0.1:{self.port}/audio/{token}{path.suffix.lower()}"


# --------------------------------------------------------------------------- #
# Bridge between the interface (ui/app.js) and the engine
# --------------------------------------------------------------------------- #

def _dialog(kind: str):
    """pywebview's dialog type ("OPEN" or "FOLDER"), across pywebview versions."""
    import webview
    if hasattr(webview, "FileDialog"):
        return getattr(webview.FileDialog, kind)
    return getattr(webview, f"{kind}_DIALOG")


class Api:
    """
    Methods the page calls through window.pywebview.api. Each returns plain
    JSON-able data. Long work runs on a worker thread, which reports through
    an event queue that the page drains by calling poll().
    """

    def __init__(self, server: LocalServer):
        self._server = server
        self._events: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._cancel = threading.Event()
        self._device = ""
        self.engine_error = ""
        self.window = None  # set by main() once the window exists
        self.ready_event = threading.Event()
        settings = load_settings()
        model = settings.get("model")
        self._settings = {
            "model": model if model in MODELS_BY_FILE else MODELS[0].filename,
            "quality": settings.get("quality") if settings.get("quality") in QUALITY_MODES else QUALITY_MODES[0],
            "format": settings.get("format") if settings.get("format") in OUTPUT_FORMATS else OUTPUT_FORMATS[0],
            "output_dir": settings.get("output_dir") or str(DEFAULT_OUTPUT_DIR),
        }

    # Events -----------------------------------------------------------------

    def emit(self, event_type: str, **data):
        self._events.put({"type": event_type, **data})

    def poll(self) -> list[dict]:
        events = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                return events

    def ready(self):
        """Called by the page once it has loaded."""
        self.ready_event.set()

    def preload_engine(self):
        """Import the AI engine in the background and report what it runs on."""
        try:
            device = detect_device()
        except Exception as exc:
            self.engine_error = f"{type(exc).__name__}: {exc}"
            self.emit("device", kind="error", name="Engine error", detail=str(exc))
            self.emit("log", text=f"AI engine failed to load: {self.engine_error}")
            return
        self._device = device
        self.emit("log", text=f"AI engine ready on: {device}")
        if device != "CPU":
            self.emit("device", kind="gpu", name=device)
        elif is_nvidia_edition():
            detail = ("No usable NVIDIA GPU found (none installed, or the driver is out of date). "
                      "This edition only accelerates NVIDIA GPUs, so it's running on the CPU, which is much slower.")
            self.emit("device", kind="nonvidia", name="No NVIDIA GPU", detail=detail)
            self.emit("log", text=detail)
        else:
            self.emit("device", kind="cpu", name="CPU only")

    # Settings -----------------------------------------------------------------

    def get_state(self) -> dict:
        return {
            "version": APP_VERSION,
            "models": [{"name": m.name, "best_for": m.best_for, "filename": m.filename, "size": m.size}
                       for m in MODELS],
            "settings": dict(self._settings),
            "ffmpeg": ffmpeg_available(),
        }

    def save_settings(self, change: dict) -> dict:
        allowed = {"model": MODELS_BY_FILE, "quality": QUALITY_MODES, "format": OUTPUT_FORMATS}
        for key, value in (change or {}).items():
            if key in allowed and value in allowed[key]:
                self._settings[key] = value
            elif key == "output_dir" and value:
                self._settings[key] = str(value)
        save_settings(dict(self._settings))
        return dict(self._settings)

    def choose_folder(self):
        result = self.window.create_file_dialog(_dialog("FOLDER"), directory=self._settings["output_dir"])
        return result[0] if result else None

    def browse_files(self) -> list[str]:
        patterns = ";".join(f"*.{e}" for e in AUDIO_EXTENSIONS)
        start = load_settings().get("input_dir") or str(Path.home() / "Music")
        result = self.window.create_file_dialog(
            _dialog("OPEN"), allow_multiple=True, directory=start,
            file_types=(f"Audio and video files ({patterns})", "All files (*.*)"))
        paths = [str(p) for p in (result or [])]
        if paths:
            settings = load_settings()
            settings["input_dir"] = str(Path(paths[0]).parent)
            save_settings({**settings, **self._settings})
        return paths

    # Files ----------------------------------------------------------------------

    def open_path(self, path: str):
        try:
            open_path(Path(path))
        except Exception as exc:
            self.emit("log", text=f"Could not open {path}: {exc}")

    def reveal(self, path: str):
        try:
            reveal_in_file_manager(Path(path))
        except Exception as exc:
            self.emit("log", text=f"Could not show {path}: {exc}")

    def open_output(self):
        folder = Path(self._settings["output_dir"])
        folder.mkdir(parents=True, exist_ok=True)
        self.open_path(str(folder))

    def on_drop(self, paths: list[str]):
        """Files dropped onto the window (see main())."""
        files = [p for p in paths if p and Path(p).is_file()]
        if files:
            self.emit("dropped", paths=files)

    # Running a job -----------------------------------------------------------------

    def start(self, sources: list[str]) -> dict:
        if self._worker and self._worker.is_alive():
            return {"ok": False, "error": "Already working on something."}
        sources = [s.strip().strip('"').strip("'") for s in (sources or []) if s and s.strip()]
        if not sources:
            return {"ok": False, "error": "Paste a YouTube link, or browse for audio files."}
        if len(sources) == 1 and not looks_like_url(sources[0]) and not Path(sources[0]).is_file():
            return {"ok": False, "error": "That isn't a YouTube link or an audio file that exists."}
        s = self._settings
        job = Job(sources=sources, model_file=s["model"], output_format=s["format"],
                  output_dir=Path(s["output_dir"]), faster=s["quality"] == "Faster")
        self._cancel.clear()
        self.emit("log", text="-" * 50)
        self.emit("log", text=f"Starting {len(sources)} item(s) [{job.model_file}, {job.output_format}, {s['quality']}]")
        self._worker = threading.Thread(target=self._run, args=(job,), daemon=True)
        self._worker.start()
        return {"ok": True}

    def cancel(self):
        self._cancel.set()
        self.emit("log", text="Stopping after the current step...")

    def _run(self, job: Job):
        pipeline = ExtractionPipeline(
            log=lambda text: self.emit("log", text=text),
            progress=lambda value: self.emit("progress", value=value),
            stage=lambda name, detail: self.emit("stage", name=name, detail=detail),
            title=lambda text: self.emit("title", text=text),
            cancel_event=self._cancel,
        )
        started = time.monotonic()
        try:
            songs, failures = pipeline.run(job)
        except Cancelled:
            self.emit("cancelled")
            return
        except PipelineError as exc:
            self.emit("error", message=str(exc))
            return
        except Exception as exc:  # anything unexpected: show it, keep the app alive
            traceback.print_exc()
            self.emit("error", message=f"Unexpected error: {type(exc).__name__}: {exc}")
            return
        seconds = time.monotonic() - started
        model = MODELS_BY_FILE.get(job.model_file)
        now = datetime.now()
        entries = [{"id": uuid.uuid4().hex, "title": song["title"], "date": now.isoformat(timespec="seconds"),
                    "model": model.name if model else job.model_file, "format": job.output_format,
                    "stems": [str(p) for p in song["stems"]]} for song in songs]
        add_to_history(entries)
        for failure in failures:
            self.emit("log", text=f"FAILED: {failure}")
        self.emit("done", songs=[self._song_view(e) for e in entries], failures=failures,
                  seconds=round(seconds, 1), device=self._device if self._device != "CPU" else "")

    def _song_view(self, entry: dict) -> dict | None:
        """A history entry as the results screen shows it (with waveforms and playable URLs)."""
        stems = []
        order = {"Acapella": 0, "Instrumental": 1}
        for p in map(Path, entry["stems"]):
            if not p.exists():
                continue
            peaks, duration = waveform(p)
            stems.append({"kind": STEM_KINDS.get(p.parent.name, "Stem"), "path": str(p), "name": p.name,
                          "size": human_size(p), "url": self._server.audio_url(p),
                          "peaks": peaks, "duration": duration})
        if not stems:
            return None
        stems.sort(key=lambda st: order.get(st["kind"], 2))
        length = max(st["duration"] for st in stems)
        meta = " · ".join(filter(None, [
            f"{int(length) // 60}:{int(length) % 60:02d}" if length else "",
            entry.get("model"), entry.get("format"), "saved to " + _short_folder(Path(stems[0]["path"]).parent.parent)]))
        return {"title": entry["title"], "meta": meta, "stems": stems}

    # History -----------------------------------------------------------------------

    def history(self) -> list[dict]:
        items = []
        for e in load_history():
            try:
                when = datetime.fromisoformat(e["date"]).strftime("%d %b %Y, %H:%M")
            except Exception:
                when = ""
            items.append({"id": e.get("id"), "title": e.get("title", "?"), "when": when, "model": e.get("model", "")})
        return items

    def history_item(self, entry_id: str):
        for e in load_history():
            if e.get("id") == entry_id:
                return self._song_view(e)
        return None


def _short_folder(folder: Path) -> str:
    """The last two parts of a folder path, e.g. Music\\ORemove."""
    parts = folder.parts[-2:]
    return os.sep.join(parts) if parts else str(folder)


def copy_bundled_models():
    """On macOS the app bundle is read-only: copy the bundled model to the data folder."""
    bundled = BUNDLE_DIR / "models"
    if bundled.resolve() == MODEL_DIR.resolve() or not bundled.is_dir():
        return
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for f in bundled.iterdir():
        target = MODEL_DIR / f.name
        if f.is_file() and not target.exists():
            try:
                shutil.copyfile(f, target)
            except OSError:
                pass


def show_fatal_error(message: str):
    """Show an error before (or instead of) the main window."""
    if sys.platform.startswith("win"):
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, APP_TITLE, 0x10)
    elif MACOS:
        subprocess.run(["osascript", "-e", f'display alert "{APP_TITLE}" message {json.dumps(message)} as critical'])
    else:
        print(message, file=sys.stderr)


def run_window(smoke_test_report: Path | None = None) -> int:
    """
    Open the main window. With smoke_test_report, close it again as soon as the
    page has loaded and talked to Python, and write a report (used by the build
    to check the packaged interface works).
    """
    try:
        import webview
        from webview.dom import DOMEventHandler
    except Exception as exc:
        show_fatal_error(f"The interface could not start: {exc}")
        return 1

    copy_bundled_models()
    server = LocalServer()
    api = Api(server)
    window = webview.create_window(f"{APP_TITLE} - {APP_TAGLINE}", server.url, js_api=api,
                                   width=1000, height=720, min_size=(760, 620), background_color="#111114")
    api.window = window

    def on_drop(event):
        files = (event.get("dataTransfer") or {}).get("files") or []
        api.on_drop([f.get("pywebviewFullPath") for f in files])

    def on_loaded():
        try:
            window.dom.document.events.dragover += DOMEventHandler(lambda _e: None, True, True)
            window.dom.document.events.drop += DOMEventHandler(on_drop, True, True)
        except Exception as exc:
            api.emit("log", text=f"Drag and drop unavailable: {exc}")
        threading.Thread(target=api.preload_engine, daemon=True).start()

    window.events.loaded += on_loaded

    def smoke_test():
        started = time.monotonic()
        ok = api.ready_event.wait(90)
        lines = [f"page loaded and called Python: {ok}", f"version: {APP_VERSION}", f"url: {server.url}"]
        if ok:
            try:
                lines.append("title: " + str(window.evaluate_js("document.querySelector('.hero-title').textContent")))
            except Exception as exc:
                lines.append(f"evaluate_js failed: {exc}")
                ok = False
            # The AI engine loads in the background once the page is up.
            for _ in range(240):
                if api._device or api.engine_error:
                    break
                time.sleep(0.5)
            lines.append(f"engine: {api._device or api.engine_error or 'did not load in time'}")
            ok = ok and bool(api._device)
            # Stay open long enough for the build to take a screenshot (at ~40 s).
            time.sleep(max(0.0, 55 - (time.monotonic() - started)))
        smoke_test_report.write_text("\n".join(lines), encoding="utf-8")
        result["ok"] = ok
        window.destroy()

    result = {"ok": True}
    try:
        # On Windows, insist on Edge WebView2 (a missing runtime then gives the
        # clear message below instead of a broken fallback window).
        gui = "edgechromium" if sys.platform.startswith("win") else None
        webview.start(smoke_test if smoke_test_report else None, gui=gui, debug=False)
    except Exception as exc:
        if sys.platform.startswith("win"):
            show_fatal_error(
                f"The interface could not start ({exc}).\n\nORemove needs the Microsoft Edge WebView2 Runtime. "
                "Install it from https://go.microsoft.com/fwlink/p/?LinkId=2124703 and start ORemove again.")
        else:
            show_fatal_error(f"The interface could not start: {exc}")
        return 1
    return 0 if result["ok"] else 1


def self_test(report_path: Path, model_files: list[str] | None) -> int:
    """
    Headless check used by the Windows build: FFmpeg is found, and a short
    generated test tone goes through each UVR model and comes out as two
    stems. With no models given it also covers the speed paths: the fast model
    on the GPU code path, the "Faster" quality mode, and reusing a loaded model.
    Writes a report to `report_path`; returns the exit code.
    """
    global force_gpu_settings
    lines = []
    log = lines.append
    fast_model = MODELS[-1].filename
    best_model = MODELS[0].filename
    # (model, faster, GPU code path, must reuse the loaded model)
    cases = [(m, False, False, False) for m in (model_files or [m.filename for m in MODELS])]
    if not model_files:
        cases += [(fast_model, False, True, False),
                  (best_model, True, False, False),
                  (best_model, True, False, True)]
    try:
        if not ffmpeg_available():
            raise PipelineError("FFmpeg not found")
        log(f"ffmpeg: {shutil.which('ffmpeg')}")
        log(f"device: {detect_device()}")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp = Path(tmp)
            tone = tmp / "tone.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                            "sine=frequency=440:duration=8", "-ac", "2", "-ar", "44100", str(tone)],
                           check=True, creationflags=NO_WINDOW)
            for n, (model_file, faster, gpu_path, reuse) in enumerate(cases):
                log(f"--- {model_file}" + (" [Faster]" if faster else "")
                    + (" [GPU code path]" if gpu_path else "") + (" [reuse]" if reuse else ""))
                force_gpu_settings = gpu_path
                stems_dir = tmp / f"stems{n}"
                stems_dir.mkdir()
                job = Job(sources=[], model_file=model_file, output_format="WAV",
                          output_dir=tmp / f"out{n}", faster=faster)
                pipeline = ExtractionPipeline(log=log, progress=lambda _v: None)
                started = time.monotonic()
                first_line = len(lines)
                saved = pipeline.file_stems(pipeline.separate(tone, job, stems_dir), "Self Test",
                                            job.output_dir)
                for path in saved:
                    log(f"saved: {path.relative_to(job.output_dir)}")
                kinds = sorted(p.parent.name for p in saved)
                if kinds != ["acapellas", "instrumentals"]:
                    raise PipelineError(f"{model_file}: expected an acapella and an instrumental, got {kinds}")
                if reuse and not any("Model already loaded" in l for l in lines[first_line:]):
                    raise PipelineError(f"{model_file}: the loaded model was not reused")
                log(f"ok in {time.monotonic() - started:.1f}s")
        log("SELF-TEST PASSED")
        code = 0
    except Exception as exc:
        log(traceback.format_exc())
        log(f"SELF-TEST FAILED: {exc}")
        code = 1
    finally:
        force_gpu_settings = False
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return code


def main():
    # python app.py --self-test report.txt [model_file ...]   engine check, no window
    # python app.py --ui-smoke-test report.txt                 open and close the window
    if len(sys.argv) >= 3 and sys.argv[1] == "--self-test":
        sys.exit(self_test(Path(sys.argv[2]), sys.argv[3:] or None))
    if len(sys.argv) >= 3 and sys.argv[1] == "--ui-smoke-test":
        sys.exit(run_window(Path(sys.argv[2])))
    sys.exit(run_window())


if __name__ == "__main__":
    main()
