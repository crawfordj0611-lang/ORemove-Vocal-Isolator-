"""
ORemove - AI Powered Vocal Isolator
===================================

A desktop app that takes a YouTube video or your own audio files and splits
each into an Acapella (vocals) stem and an Instrumental stem using Ultimate
Vocal Remover (UVR5) models, run through the `audio-separator` library.

Pipeline (runs on a background thread so the window never freezes):
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
when the CUDA build of PyTorch is installed.

The worker thread never touches a widget. It puts messages on a queue, and
the GUI drains that queue every 100 ms from the Tk main loop.

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
import tkinter as tk
from tkinter import filedialog

try:
    import customtkinter as ctk
except ImportError:  # Friendly message instead of a bare traceback.
    sys.exit("customtkinter is not installed. Run:  pip install -r requirements.txt")


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

APP_TITLE = "ORemove"
APP_TAGLINE = "AI Powered Vocal Isolator"

# True when running as the packaged Windows .exe (built with PyInstaller).
FROZEN = getattr(sys, "frozen", False)

# The packaged app keeps output/ and models/ next to the .exe; from source,
# next to this script.
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent

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
MODEL_DIR = APP_DIR / "models"
# Remembers the chosen model, format and output folder between runs.
SETTINGS_FILE = APP_DIR / "settings.json"


@dataclass(frozen=True)
class ModelOption:
    name: str      # the model's name
    best_for: str  # what it is best at, shown next to the name in the dropdown
    filename: str  # model file name known to audio-separator
    note: str      # one extra line shown under the dropdown

    @property
    def label(self) -> str:
        return f"{self.name}  —  {self.best_for}"


# Ranked by audio-separator's published benchmark (median SDR over 40 songs).
# List every model audio-separator knows with:  audio-separator --list_models
MODELS = [
    ModelOption("BS-Roformer 1297", "Best for instrumentals",
                "model_bs_roformer_ep_317_sdr_12.9755.ckpt",
                "Highest-rated instrumental quality  ·  large one-time download on first use"),
    ModelOption("BS-Roformer 1296", "Best for acapellas",
                "model_bs_roformer_ep_368_sdr_12.9628.ckpt",
                "Highest-rated acapella quality  ·  large one-time download on first use"),
    ModelOption("MDX-Net Inst HQ 3", "Fastest",
                "UVR-MDX-NET-Inst_HQ_3.onnx",
                "Good quality in a fraction of the time  ·  included"),
]
MODELS_BY_LABEL = {m.label: m for m in MODELS}

OUTPUT_FORMATS = ["WAV", "MP3"]  # WAV is lossless; MP3 is written at 320 kbps

QUALITY_MODES = ["Best", "Faster"]  # "Faster": half the overlapping passes for BS-Roformer

# The self-test switches this on to exercise the NVIDIA GPU code path (the fast
# model converted to PyTorch, in batches) on build machines without a GPU.
force_gpu_settings = False

# Share of the progress bar given to each stage.
DOWNLOAD_SHARE = 0.40   # 0 % -> 40 %  : yt-dlp download
MODEL_LOAD_AT = 0.45    # 45 %         : model loaded, separation starts
SEPARATED_AT = 0.95     # 95 %         : stems written, moving files

# Design tokens: a near-black neutral palette with one green accent.
BG = "#0B0B0E"
SURFACE = "#141418"
FIELD = "#1C1C22"
BORDER = "#2A2A32"
TEXT = "#F4F4F5"
MUTED = "#8E8E9A"
SUBTLE = "#55555F"
ACCENT = "#22C55E"
ACCENT_HOVER = "#1EAE53"
ON_ACCENT = "#04130A"
DANGER = "#F87171"

if sys.platform.startswith("win"):
    UI_FONT, MONO_FONT = "Segoe UI", "Cascadia Mono"
elif sys.platform == "darwin":
    UI_FONT, MONO_FONT = "Helvetica Neue", "Menlo"
else:
    UI_FONT, MONO_FONT = "DejaVu Sans", "DejaVu Sans Mono"


# --------------------------------------------------------------------------- #
# Helpers (no GUI code here, so they are easy to test or reuse)
# --------------------------------------------------------------------------- #

class PipelineError(Exception):
    """An expected failure with a message meant for the user."""


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
AUDIO_FILE_TYPES = ("*.mp3 *.wav *.flac *.m4a *.aac *.ogg *.opus *.wma *.aiff *.aif "
                    "*.alac *.mp4 *.mkv *.mov *.webm")

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
    else:
        open_path(path.parent)


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}  # first run, or unreadable: use defaults


def save_settings(settings: dict) -> None:
    try:
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

    def __init__(self, log, progress, stage=lambda _name, _detail: None):
        self.log = log
        self._progress_cb = progress
        self._stage_cb = stage
        self._index, self._count = 0, 1  # position in the current batch

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
        except DownloadError as exc:
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
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vn", "-ac", "2",
             "-c:a", "pcm_s24le", str(dest)],
            capture_output=True, text=True, creationflags=NO_WINDOW)
        if result.returncode != 0 or not dest.exists():
            reason = (result.stderr.strip().splitlines() or ["not a readable audio file"])[-1]
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

    def run(self, job: Job) -> tuple[list[Path], list[str]]:
        """
        Process every source in `job`. Returns (saved stem paths, failure messages).
        A single source raises on failure; in a batch, a failed item is skipped
        and reported, and the rest still run.
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
                    saved += self._run_one(source, i, job, work_dir, stems_dir)
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

    def _run_one(self, source: str, i: int, job: Job, work_dir: Path, stems_dir: Path) -> list[Path]:
        """Get one source as WAV, separate it, and save its stems."""
        wav = work_dir / f"input{i}.wav"  # unique per item: stems are named after it
        if looks_like_url(source):
            if not is_youtube_url(source):
                raise PipelineError("Only YouTube links are supported (youtube.com or youtu.be).")
            base_name = sanitize_filename(self.fetch_title(source))
            download_dir = work_dir / f"download{i}"
            download_dir.mkdir()
            self.download_wav(source, download_dir).replace(wav)
        else:
            src = Path(source)
            base_name = sanitize_filename(src.stem)
            self.convert_local_file(src, wav)

        stems = self.separate(wav, job, stems_dir)
        saved = self.file_stems(stems, base_name, job.output_dir)
        # Free disk space as we go; a batch of long WAVs adds up.
        for path in [wav, *stems]:
            path.unlink(missing_ok=True)
        return saved


# --------------------------------------------------------------------------- #
# GUI
# --------------------------------------------------------------------------- #

STAGES = [("download", "Get audio"), ("separate", "Separate"), ("save", "Save")]


def short_path(path: Path, limit: int = 34) -> str:
    """Shorten a long path from the left so the end (the folder name) stays visible."""
    text = str(path)
    return text if len(text) <= limit else "…" + text[-(limit - 1):]


class ORemoveApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")

        self.title(f"{APP_TITLE} - {APP_TAGLINE}")
        self.configure(fg_color=BG)
        self.geometry("780x840")
        self.minsize(660, 760)

        # Restore last session's choices.
        self.settings = load_settings()
        self.output_dir = Path(self.settings.get("output_dir") or DEFAULT_OUTPUT_DIR)
        model_label = self.settings.get("model")
        self.model_label = model_label if model_label in MODELS_BY_LABEL else MODELS[0].label
        fmt = self.settings.get("format")
        self.output_format = fmt if fmt in OUTPUT_FORMATS else OUTPUT_FORMATS[0]
        quality = self.settings.get("quality")
        self.quality = quality if quality in QUALITY_MODES else QUALITY_MODES[0]

        self.events: queue.Queue = queue.Queue()  # worker threads -> GUI messages
        self.worker: threading.Thread | None = None
        self.job_started_at: float | None = None
        self.progress_indeterminate = False
        self.details_visible = False

        self.fonts = {
            "eyebrow": ctk.CTkFont(UI_FONT, 11, "bold"),
            "title": ctk.CTkFont(UI_FONT, 26, "bold"),
            "label": ctk.CTkFont(UI_FONT, 12, "bold"),
            "body": ctk.CTkFont(UI_FONT, 13),
            "small": ctk.CTkFont(UI_FONT, 12),
            "button": ctk.CTkFont(UI_FONT, 15, "bold"),
            "mono": ctk.CTkFont(MONO_FONT, 11),
        }

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=0, column=0, sticky="nsew", padx=32, pady=(22, 16))
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(7, weight=1)  # the details log takes spare height

        self._build_header()
        self._build_source()
        self._build_options()
        self._build_extract_button()
        self._build_progress()
        self._build_results()
        self._build_footer()

        self.log(f"Stems will be saved to: {self.output_dir}")
        if not ffmpeg_available():
            self._set_status("FFmpeg is missing. Install it before extracting (see README).", DANGER)
            self.log("WARNING: FFmpeg not found on PATH.")

        # Import the AI engine now, while the user is still pasting a link.
        threading.Thread(target=self._preload_engine, daemon=True).start()
        self.after(100, self._drain_events)

    # ---- Building blocks ---------------------------------------------------

    def _card(self, row: int, pady=(0, 12)) -> ctk.CTkFrame:
        card = ctk.CTkFrame(self.body, fg_color=SURFACE, corner_radius=14,
                            border_width=1, border_color=BORDER)
        card.grid(row=row, column=0, sticky="ew", pady=pady)
        card.grid_columnconfigure(0, weight=1)
        return card

    def _label(self, parent, text, font="label", color=MUTED, **kw) -> ctk.CTkLabel:
        return ctk.CTkLabel(parent, text=text, font=self.fonts[font], text_color=color,
                            anchor="w", **kw)

    def _ghost_button(self, parent, text, command, width=76, height=34, color=TEXT):
        return ctk.CTkButton(parent, text=text, command=command, width=width, height=height,
                             corner_radius=10, fg_color="transparent", hover_color=FIELD,
                             border_width=1, border_color=BORDER, text_color=color,
                             font=self.fonts["small"])

    def _segmented(self, parent, values, value, command):
        seg = ctk.CTkSegmentedButton(
            parent, values=values, command=command, height=38, corner_radius=10,
            font=self.fonts["body"], fg_color=FIELD, unselected_color=FIELD,
            unselected_hover_color="#25252D", selected_color="#34343F",
            selected_hover_color="#3B3B47", text_color=TEXT, text_color_disabled=SUBTLE)
        seg.set(value)
        return seg

    # ---- Layout ------------------------------------------------------------

    def _build_header(self):
        header = ctk.CTkFrame(self.body, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 18))
        header.grid_columnconfigure(0, weight=1)

        self._label(header, APP_TAGLINE.upper(), font="eyebrow", color=ACCENT).grid(row=0, column=0, sticky="w")
        self._label(header, APP_TITLE, font="title", color=TEXT).grid(row=1, column=0, sticky="w")

        # Device chip: shows what the AI engine will run on once it has loaded.
        chip = ctk.CTkFrame(header, fg_color=SURFACE, corner_radius=14,
                            border_width=1, border_color=BORDER)
        chip.grid(row=0, column=1, rowspan=2, sticky="e")
        self.device_dot = ctk.CTkLabel(chip, text="●", text_color=SUBTLE, font=self.fonts["small"])
        self.device_dot.pack(side="left", padx=(12, 6), pady=5)
        self.device_label = self._label(chip, "Loading engine…", font="small", color=MUTED)
        self.device_label.pack(side="left", padx=(0, 14), pady=5)

    def _build_source(self):
        card = self._card(row=1)
        self._label(card, "YouTube link or audio file").grid(row=0, column=0, columnspan=3,
                                                             sticky="w", padx=20, pady=(16, 8))
        self.url_entry = ctk.CTkEntry(
            card, height=46, corner_radius=10, font=self.fonts["body"],
            fg_color=FIELD, border_color=BORDER, border_width=1, text_color=TEXT,
            placeholder_text="Paste a YouTube link, or browse for audio files",
            placeholder_text_color=SUBTLE)
        self.url_entry.grid(row=1, column=0, sticky="ew", padx=(20, 8), pady=(0, 20))
        self.url_entry.bind("<Return>", lambda _e: self.start_extraction())

        self.paste_button = self._ghost_button(card, "Paste", self._paste_url, height=46)
        self.paste_button.grid(row=1, column=1, padx=(0, 8), pady=(0, 20))
        self.browse_button = self._ghost_button(card, "Browse", self._browse_files, height=46,
                                                width=84)
        self.browse_button.grid(row=1, column=2, padx=(0, 20), pady=(0, 20))

        # Files picked with Browse. When several are picked the entry shows a
        # summary instead of a path; editing the entry drops the selection.
        self.selected_files: list[Path] = []
        self.files_summary = ""

    def _build_options(self):
        card = self._card(row=2)
        card.grid_columnconfigure(0, weight=0)
        card.grid_columnconfigure(2, weight=1)

        self._label(card, "Model").grid(row=0, column=0, columnspan=3, sticky="w",
                                        padx=20, pady=(16, 8))
        self.model_menu = ctk.CTkOptionMenu(
            card, values=[m.label for m in MODELS], command=self._on_model_change,
            height=40, corner_radius=10, dynamic_resizing=False, anchor="w",
            font=self.fonts["body"], dropdown_font=self.fonts["body"],
            fg_color=FIELD, button_color=FIELD, button_hover_color="#25252D",
            text_color=TEXT, text_color_disabled=SUBTLE,
            dropdown_fg_color=SURFACE, dropdown_hover_color=FIELD, dropdown_text_color=TEXT)
        self.model_menu.set(self.model_label)
        self.model_menu.grid(row=1, column=0, columnspan=3, sticky="ew", padx=20)
        self.model_desc = self._label(card, MODELS_BY_LABEL[self.model_label].note,
                                      font="small", color=MUTED)
        self.model_desc.grid(row=2, column=0, columnspan=3, sticky="w", padx=20, pady=(8, 14))

        tk.Frame(card, height=1, bg=BORDER, highlightthickness=0).grid(row=3, column=0, columnspan=3,
                                                           sticky="ew", padx=20)

        # Format, quality and output folder share one row.
        self._label(card, "Format").grid(row=4, column=0, sticky="w", padx=20, pady=(16, 8))
        self._label(card, "Quality").grid(row=4, column=1, sticky="w", padx=(20, 0), pady=(16, 8))
        self._label(card, "Save to").grid(row=4, column=2, sticky="w", padx=(20, 20), pady=(16, 8))

        self.format_seg = self._segmented(card, OUTPUT_FORMATS, self.output_format,
                                          self._on_format_change)
        self.format_seg.configure(width=130)
        self.format_seg.grid(row=5, column=0, sticky="w", padx=20, pady=(0, 20))

        # "Faster" halves BS-Roformer's overlapping passes: about 2x faster,
        # with a small loss of quality.
        self.quality_seg = self._segmented(card, QUALITY_MODES, self.quality, self._on_quality_change)
        self.quality_seg.configure(width=150)
        self.quality_seg.grid(row=5, column=1, sticky="w", padx=(20, 0), pady=(0, 20))

        folder = ctk.CTkFrame(card, fg_color=FIELD, corner_radius=10, height=38,
                              border_width=1, border_color=BORDER)
        folder.grid(row=5, column=2, sticky="ew", padx=(20, 20), pady=(0, 20))
        folder.grid_columnconfigure(0, weight=1)
        self.output_label = self._label(folder, short_path(self.output_dir), font="small", color=TEXT)
        self.output_label.grid(row=0, column=0, sticky="ew", padx=(12, 4), pady=6)
        self.change_button = ctk.CTkButton(
            folder, text="Change", width=64, height=28, corner_radius=8,
            fg_color="transparent", hover_color="#25252D", text_color=ACCENT,
            font=self.fonts["label"], command=self._choose_output_dir)
        self.change_button.grid(row=0, column=1, padx=(0, 5), pady=4)

    def _build_extract_button(self):
        self.start_button = ctk.CTkButton(
            self.body, text="Extract stems", height=52, corner_radius=12,
            font=self.fonts["button"], fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color=ON_ACCENT, text_color_disabled=MUTED, command=self.start_extraction)
        self.start_button.grid(row=3, column=0, sticky="ew", pady=(2, 14))

    def _build_progress(self):
        card = self._card(row=4)
        card.grid_columnconfigure(0, weight=1)

        steps = ctk.CTkFrame(card, fg_color="transparent")
        steps.grid(row=0, column=0, columnspan=2, sticky="w", padx=20, pady=(16, 10))
        self.step_widgets = {}
        for i, (key, name) in enumerate(STAGES):
            if i:
                tk.Frame(steps, width=28, height=1, bg=BORDER, highlightthickness=0).pack(side="left", padx=10)
            dot = ctk.CTkLabel(steps, text="●", text_color=SUBTLE, font=self.fonts["small"])
            dot.pack(side="left", padx=(0, 6))
            label = self._label(steps, name, font="small", color=SUBTLE)
            label.pack(side="left")
            self.step_widgets[key] = (dot, label)

        self.status_label = self._label(card, "Ready", font="body", color=TEXT)
        self.status_label.grid(row=1, column=0, sticky="w", padx=20)
        self.elapsed_label = self._label(card, "", font="small", color=MUTED, justify="right")
        self.elapsed_label.grid(row=1, column=1, sticky="e", padx=20)

        self.progress_bar = ctk.CTkProgressBar(card, height=6, corner_radius=3,
                                               fg_color=FIELD, progress_color=ACCENT)
        self.progress_bar.grid(row=2, column=0, columnspan=2, sticky="ew", padx=20, pady=(10, 20))
        self._set_progress(0)

    def _build_results(self):
        # Filled in when a job finishes; hidden until then.
        self.results_card = self._card(row=5)
        self.results_card.grid_remove()

    def _build_footer(self):
        footer = ctk.CTkFrame(self.body, fg_color="transparent")
        footer.grid(row=6, column=0, sticky="ew")
        footer.grid_columnconfigure(1, weight=1)
        link = dict(fg_color="transparent", hover_color=SURFACE, text_color=MUTED,
                    font=self.fonts["small"], height=30, corner_radius=8)
        self.details_button = ctk.CTkButton(footer, text="Show details", width=110,
                                            command=self._toggle_details, **link)
        self.details_button.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(footer, text="Open output folder", width=140,
                      command=lambda: self._safe_open(self.output_dir, folder=True),
                      **link).grid(row=0, column=2, sticky="e")

        # Technical log, hidden by default to keep the window clean.
        self.log_box = ctk.CTkTextbox(self.body, font=self.fonts["mono"], fg_color=SURFACE,
                                      border_width=1, border_color=BORDER, corner_radius=12,
                                      text_color=MUTED, wrap="word")
        self.log_box.configure(state="disabled")

    # ---- UI actions --------------------------------------------------------

    def _set_source_text(self, text: str):
        self.url_entry.delete(0, "end")
        self.url_entry.insert(0, text)

    def _paste_url(self):
        try:
            text = self.clipboard_get().strip()
        except Exception:
            return  # empty or non-text clipboard
        self.selected_files = []
        self._set_source_text(text)

    def _browse_files(self):
        start = self.settings.get("input_dir") or str(Path.home() / "Music")
        chosen = filedialog.askopenfilenames(
            title="Choose audio files to separate", initialdir=start,
            filetypes=[("Audio and video files", AUDIO_FILE_TYPES), ("All files", "*.*")])
        if not chosen:
            return
        self.selected_files = [Path(p) for p in chosen]
        self.settings["input_dir"] = str(self.selected_files[0].parent)
        save_settings(self.settings)
        if len(self.selected_files) == 1:
            self.files_summary = str(self.selected_files[0])
        else:
            names = ", ".join(p.name for p in self.selected_files[:2])
            more = "…" if len(self.selected_files) > 2 else ""
            self.files_summary = f"{len(self.selected_files)} files: {names}{more}"
        self._set_source_text(self.files_summary)
        self._set_status(f"{len(self.selected_files)} file{'s' if len(self.selected_files) > 1 else ''} "
                         "ready. Press Extract stems.")

    def _current_sources(self) -> list[str]:
        """What to process: the browsed files, or whatever is typed or pasted."""
        text = self.url_entry.get().strip()
        if self.selected_files and text == self.files_summary:
            return [str(p) for p in self.selected_files]
        self.selected_files = []
        # Windows "Copy as path" wraps paths in quotes.
        text = text.strip('"').strip("'")
        return [text] if text else []

    def _on_model_change(self, label):
        self.model_label = label
        self.model_desc.configure(text=MODELS_BY_LABEL[label].note)
        self._remember()

    def _on_quality_change(self, quality):
        self.quality = quality
        self._remember()

    def _on_format_change(self, fmt):
        self.output_format = fmt
        self._remember()

    def _choose_output_dir(self):
        chosen = filedialog.askdirectory(initialdir=str(self.output_dir),
                                         title="Choose where to save the stems")
        if chosen:
            self.output_dir = Path(chosen)
            self.output_label.configure(text=short_path(self.output_dir))
            self.log(f"Output folder set to: {self.output_dir}")
            self._remember()

    def _remember(self):
        self.settings.update(model=self.model_label, format=self.output_format,
                             quality=self.quality, output_dir=str(self.output_dir))
        save_settings(self.settings)

    def _toggle_details(self):
        self.details_visible = not self.details_visible
        if self.details_visible:
            self.log_box.grid(row=7, column=0, sticky="nsew", pady=(8, 0))
            self.details_button.configure(text="Hide details")
            self._fit_height()
        else:
            self.log_box.grid_remove()
            self.details_button.configure(text="Show details")

    def _safe_open(self, path: Path, folder=False, reveal=False):
        try:
            if folder:
                path.mkdir(parents=True, exist_ok=True)
            (reveal_in_file_manager if reveal else open_path)(path)
        except Exception as exc:
            self.log(f"Could not open {path}: {exc}")

    def _set_running(self, running: bool):
        state = "disabled" if running else "normal"
        for widget in (self.url_entry, self.paste_button, self.browse_button, self.model_menu,
                       self.format_seg, self.quality_seg, self.change_button):
            widget.configure(state=state)
        self.start_button.configure(
            state=state, text="Extracting…" if running else "Extract stems",
            fg_color=FIELD if running else ACCENT)

    def start_extraction(self):
        if self.worker and self.worker.is_alive():
            return  # already running (e.g. Enter pressed twice)

        sources = self._current_sources()
        if not sources:
            self._set_status("Paste a YouTube link or browse for audio files first.", DANGER)
            self.url_entry.focus_set()
            return
        single = sources[0]
        if len(sources) == 1 and not looks_like_url(single) and not Path(single).is_file():
            self._set_status("That isn't a YouTube link or an audio file that exists.", DANGER)
            return

        job = Job(sources=sources, model_file=MODELS_BY_LABEL[self.model_label].filename,
                  output_format=self.output_format, output_dir=self.output_dir,
                  faster=self.quality == "Faster")

        self._set_running(True)
        self.results_card.grid_remove()
        self._set_progress(0)
        self._set_stage(None)
        self._set_status("Fetching video info…" if looks_like_url(single) else "Starting…")
        self.job_started_at = time.monotonic()
        self._tick()
        self.log("-" * 60)
        self.log(f"Starting {len(sources)} item(s)  [{job.model_file}, {job.output_format}]")

        self.worker = threading.Thread(target=self._worker_main, args=(job,), daemon=True)
        self.worker.start()

    # ---- Worker threads (no widget access in here!) -----------------------

    def _preload_engine(self):
        try:
            self.events.put(("engine", (True, detect_device())))
        except Exception as exc:
            self.events.put(("engine", (False, f"{type(exc).__name__}: {exc}")))

    def _worker_main(self, job: Job):
        pipeline = ExtractionPipeline(
            log=lambda msg: self.events.put(("log", msg)),
            progress=lambda value: self.events.put(("progress", value)),
            stage=lambda name, detail: self.events.put(("stage", (name, detail))),
        )
        try:
            saved = pipeline.run(job)
        except PipelineError as exc:
            self.events.put(("error", str(exc)))
        except Exception as exc:  # anything unexpected: show it, keep the app alive
            traceback.print_exc()
            self.events.put(("error", f"Unexpected error: {type(exc).__name__}: {exc}"))
        else:
            self.events.put(("done", saved))

    # ---- Main-thread event pump -------------------------------------------

    def _drain_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self.log(payload)
                elif kind == "progress":
                    self._set_progress(payload)
                elif kind == "stage":
                    name, detail = payload
                    self._set_stage(name)
                    self._set_status(f"{detail}…")
                elif kind == "engine":
                    self._on_engine_ready(*payload)
                elif kind == "error":
                    self._finish()
                    self._set_progress(0)
                    self._set_status(payload, DANGER)
                    self.log(f"ERROR: {payload}")
                elif kind == "done":
                    saved, failures = payload
                    self._finish()
                    self._set_progress(1.0)
                    self._set_stage("done")
                    if failures:
                        self._set_status(f"Done, but {len(failures)} file(s) failed. "
                                         "See Show details.", "#F5A524")
                        for failure in failures:
                            self.log(f"FAILED: {failure}")
                    else:
                        self._set_status("Done. Your stems are ready.", ACCENT)
                    for path in saved:
                        self.log(f"Saved: {path}")
                    self._show_results(saved)
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _on_engine_ready(self, ok: bool, info: str):
        if not ok:
            self.device_dot.configure(text_color=DANGER)
            self.device_label.configure(text="Engine error")
            self.log(f"AI engine failed to load: {info}")
            return
        on_gpu = info != "CPU"
        self.device_dot.configure(text_color=ACCENT if on_gpu else "#F5A524")
        self.log(f"AI engine ready on: {info}")
        if on_gpu:
            self.device_label.configure(text=info, text_color=TEXT)
        elif is_nvidia_edition():
            # GPU acceleration in this edition works only with NVIDIA cards.
            self.device_label.configure(text="No NVIDIA GPU", text_color=TEXT)
            message = ("No usable NVIDIA GPU found (none installed, or the driver is out of date). "
                       "This edition only accelerates NVIDIA GPUs; running on the CPU, which is much slower.")
            self.log(message)
            if not (self.worker and self.worker.is_alive()):
                self._set_status(message, "#F5A524")
                self.status_label.configure(wraplength=640, justify="left")
        else:
            self.device_label.configure(text="CPU only", text_color=TEXT)

    def _finish(self):
        self._set_running(False)
        self.job_started_at = None

    def _tick(self):
        """Update the elapsed-time label once a second while a job runs."""
        if self.job_started_at is None:
            return
        secs = int(time.monotonic() - self.job_started_at)
        self.elapsed_label.configure(text=f"{secs // 60}:{secs % 60:02d}")
        self.after(1000, self._tick)

    def _set_status(self, text: str, color: str = TEXT):
        self.status_label.configure(text=text, text_color=color)

    def _set_stage(self, current: str | None):
        """Light up the step indicator: finished steps green, current white."""
        keys = [k for k, _ in STAGES]
        reached = len(keys) if current == "done" else (keys.index(current) if current in keys else -1)
        for i, key in enumerate(keys):
            dot, label = self.step_widgets[key]
            if i < reached:
                dot.configure(text_color=ACCENT)
                label.configure(text_color=TEXT)
            elif i == reached:
                dot.configure(text_color=TEXT)
                label.configure(text_color=TEXT)
            else:
                dot.configure(text_color=SUBTLE)
                label.configure(text_color=SUBTLE)

    def _set_progress(self, value):
        """value in 0..1, or None for 'busy, unknown amount' (animated bar)."""
        if value is None:
            if not self.progress_indeterminate:
                self.progress_bar.configure(mode="indeterminate")
                self.progress_bar.start()
                self.progress_indeterminate = True
            return
        if self.progress_indeterminate:
            self.progress_bar.stop()
            self.progress_bar.configure(mode="determinate")
            self.progress_indeterminate = False
        # At 0 the bar would still draw a small green dot; hide it.
        self.progress_bar.configure(progress_color=ACCENT if value > 0 else FIELD)
        self.progress_bar.set(value)

    def _fit_height(self):
        """Grow the window (up to the screen size) when results or details need room."""
        self.update_idletasks()
        needed = self.winfo_reqheight()
        limit = self.winfo_screenheight() - 80
        if self.winfo_height() < needed:
            self.geometry(f"{self.winfo_width()}x{min(needed, limit)}")

    def _show_results(self, paths: list[Path]):
        """One row per song: its name, a play button per stem, and Show in folder."""
        card = self.results_card
        for child in card.winfo_children():
            child.destroy()
        card.grid_columnconfigure(0, weight=1)
        self._label(card, "Your stems").grid(row=0, column=0, columnspan=4, sticky="w",
                                             padx=20, pady=(16, 6))

        kinds = {"acapellas": "Acapella", "instrumentals": "Instrumental"}
        songs: dict[str, dict[str, Path]] = {}
        for path in paths:
            song = path.stem.rsplit(" (", 1)[0]  # "Title (Acapella)" -> "Title"
            songs.setdefault(song, {})[kinds.get(path.parent.name, "Stem")] = path

        limit = 3  # a long batch would push the window off the screen
        shown = list(songs.items())[:limit]
        hidden = len(songs) - len(shown)
        for row, (song, stems) in enumerate(shown, start=1):
            pady = (4, 18 if row == len(shown) and hidden <= 0 else 4)
            self._label(card, song, font="label", color=TEXT).grid(
                row=row, column=0, sticky="ew", padx=(20, 8), pady=pady)
            col = 1
            for kind in ("Acapella", "Instrumental", "Stem"):
                if kind in stems:
                    self._ghost_button(card, f"▶  {kind}", lambda p=stems[kind]: self._safe_open(p),
                                       width=118 if kind == "Instrumental" else 100,
                                       color=ACCENT).grid(row=row, column=col, padx=(0, 6), pady=pady)
                    col += 1
            first = next(iter(stems.values()))
            self._ghost_button(card, "Show", lambda p=first: self._safe_open(p, reveal=True),
                               width=64).grid(row=row, column=col, padx=(0, 20), pady=pady)
        if hidden > 0:
            self._label(card, f"+ {hidden} more song{'s' if hidden > 1 else ''} in the output folder",
                        font="small").grid(row=len(shown) + 1, column=0, columnspan=5,
                                           sticky="w", padx=20, pady=(6, 18))
        card.grid()
        self._fit_height()

    def log(self, message: str):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{stamp}] {message}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")


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
    # python app.py --self-test report.txt [model_file ...]
    if len(sys.argv) >= 3 and sys.argv[1] == "--self-test":
        sys.exit(self_test(Path(sys.argv[2]), sys.argv[3:] or None))
    app = ORemoveApp()
    app.mainloop()


if __name__ == "__main__":
    main()
