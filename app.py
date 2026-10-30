"""
YT Stem Extractor & UVR Studio
==============================

A desktop app that downloads the audio of a YouTube video and splits it into
an Acapella (vocals) stem and an Instrumental stem using Ultimate Vocal
Remover (UVR5) models, run through the `audio-separator` library.

Pipeline (runs on a background thread so the window never freezes):
    1. Check FFmpeg is on PATH.
    2. yt-dlp: read the video's metadata (title), then download the best audio
       stream and convert it to WAV in a temporary folder.
    3. audio-separator: load the chosen UVR model and split the WAV into
       Vocals and Instrumental.
    4. Move the stems to <output>/acapellas/ and <output>/instrumentals/,
       named after the video title, and delete the temporary files.

The worker thread never touches a widget. It puts messages on a queue, and
the GUI drains that queue every 100 ms from the Tk main loop.

Run:  python app.py
"""

from __future__ import annotations

import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog

try:
    import customtkinter as ctk
except ImportError:  # Friendly message instead of a bare traceback.
    sys.exit("customtkinter is not installed. Run:  pip install -r requirements.txt")


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

APP_TITLE = "YT Stem Extractor & UVR Studio"
APP_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = APP_DIR / "output"
# UVR models are downloaded here on first use and reused after that.
MODEL_DIR = APP_DIR / "models"

# Display name -> model file name known to audio-separator.
# (List every model audio-separator knows with:  audio-separator --list_models)
UVR_MODELS = {
    "UVR-MDX-NET Inst HQ 3 (best instrumentals)": "UVR-MDX-NET-Inst_HQ_3.onnx",
    "Kim_Vocal_2 (best acapellas)": "Kim_Vocal_2.onnx",
    "UVR HP-Vocal (VR architecture)": "4_HP-Vocal-UVR.pth",
}

OUTPUT_FORMATS = {
    "WAV (lossless, maximum quality)": "WAV",
    "MP3 (320 kbps)": "MP3",
}

# Share of the progress bar given to each stage.
DOWNLOAD_SHARE = 0.40   # 0 % -> 40 %  : yt-dlp download
MODEL_LOAD_AT = 0.45    # 45 %         : model loaded, separation starts
SEPARATED_AT = 0.95     # 95 %         : stems written, moving files

ACCENT = "#1f9d55"
ACCENT_HOVER = "#17804a"


# --------------------------------------------------------------------------- #
# Helpers (no GUI code here, so they are easy to test or reuse)
# --------------------------------------------------------------------------- #

class PipelineError(Exception):
    """An expected failure with a message meant for the user."""


@dataclass
class Job:
    """Everything the worker needs, captured from the UI when Start is clicked."""
    url: str
    model_file: str
    output_format: str  # "WAV" or "MP3"
    output_dir: Path


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


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def open_in_file_explorer(path: Path) -> None:
    """Open a folder in Explorer / Finder / the Linux file manager."""
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]  # Windows only
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


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


class ExtractionPipeline:
    """
    Download + separation logic.

    `log(str)` and `progress(float 0..1 or None)` are callbacks supplied by the
    GUI; progress(None) means "busy, but the amount is unknown".
    """

    def __init__(self, log, progress):
        self.log = log
        self.progress = progress

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

    # ---- Step 2: separation -----------------------------------------------

    def separate(self, wav: Path, job: Job, stems_dir: Path) -> list[Path]:
        """Run UVR on `wav`, writing stems into `stems_dir`. Returns their paths."""
        self.log("Loading the AI engine (PyTorch)... the first start can take a while.")
        self.progress(None)
        try:
            from audio_separator.separator import Separator
        except ImportError as exc:
            raise PipelineError(
                "audio-separator is not installed correctly. "
                "Run: pip install -r requirements.txt"
            ) from exc

        try:
            import torch
            if torch.cuda.is_available():
                self.log(f"GPU detected: {torch.cuda.get_device_name(0)} (CUDA).")
            elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                self.log("Apple Silicon GPU detected (MPS).")
            else:
                self.log("No GPU detected: running on CPU (slower, but works).")
        except Exception:
            pass  # device info is a nicety only

        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        separator = Separator(
            log_level=logging.WARNING,      # keep the console quiet; we log stages ourselves
            model_file_dir=str(MODEL_DIR),
            output_dir=str(stems_dir),
            output_format=job.output_format,
            output_bitrate="320k" if job.output_format == "MP3" else None,
        )

        model_cached = (MODEL_DIR / job.model_file).exists()
        self.log(f"Loading UVR model: {job.model_file}"
                 + ("" if model_cached else "  (first use: downloading model, please wait)"))
        try:
            separator.load_model(model_filename=job.model_file)
        except Exception as exc:
            raise PipelineError(f"Could not load model '{job.model_file}': {exc}") from exc
        self.progress(MODEL_LOAD_AT)

        self.log("Separating stems... (this is the slow part; a few minutes on CPU)")
        self.progress(None)
        try:
            produced = separator.separate(str(wav))
        except Exception as exc:
            raise PipelineError(f"Separation failed: {exc}") from exc

        # audio-separator returns file names relative to output_dir.
        paths = [p if p.is_absolute() else stems_dir / p for p in map(Path, produced)]
        paths = [p for p in paths if p.exists()]
        if not paths:
            raise PipelineError("The model ran but produced no output files.")
        self.progress(SEPARATED_AT)
        return paths

    # ---- Step 3: put the stems where they belong ---------------------------

    def file_stems(self, stems: list[Path], base_name: str, output_dir: Path) -> list[Path]:
        """
        Move each stem into acapellas/ or instrumentals/ and name it after the video.
        audio-separator names files like 'source_(Vocals)_<model>.wav'.
        """
        acapella_dir = output_dir / "acapellas"
        instrumental_dir = output_dir / "instrumentals"
        acapella_dir.mkdir(parents=True, exist_ok=True)
        instrumental_dir.mkdir(parents=True, exist_ok=True)

        saved = []
        for stem in stems:
            label = stem.name.lower()
            if "(vocals)" in label:
                target = acapella_dir / f"{base_name} (Acapella){stem.suffix}"
            elif "(instrumental)" in label:
                target = instrumental_dir / f"{base_name} (Instrumental){stem.suffix}"
            else:
                # A model with other stem names: keep it, unsorted, with its stem label.
                stem_label = re.search(r"_\(([^)]+)\)_", stem.name)
                suffix = stem_label.group(1) if stem_label else "Stem"
                target = output_dir / f"{base_name} ({suffix}){stem.suffix}"
            target = unique_path(target)
            shutil.move(str(stem), target)
            saved.append(target)
        return saved

    # ---- Whole job --------------------------------------------------------

    def run(self, job: Job) -> list[Path]:
        if not ffmpeg_available():
            raise PipelineError(
                "FFmpeg was not found on your PATH. Install FFmpeg (see README) "
                "and restart the app."
            )
        if not is_youtube_url(job.url):
            raise PipelineError("Please paste a valid YouTube link (youtube.com or youtu.be).")

        title = self.fetch_title(job.url)
        base_name = sanitize_filename(title)

        # Everything temporary lives here and is deleted at the end, even on errors.
        with tempfile.TemporaryDirectory(prefix="yt_stem_", ignore_cleanup_errors=True) as tmp:
            work_dir = Path(tmp)
            wav = self.download_wav(job.url, work_dir)
            stems_dir = work_dir / "stems"
            stems_dir.mkdir()
            stems = self.separate(wav, job, stems_dir)
            saved = self.file_stems(stems, base_name, job.output_dir)
            self.log("Cleaning up temporary files...")
        return saved


# --------------------------------------------------------------------------- #
# GUI
# --------------------------------------------------------------------------- #

class StemExtractorApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("green")

        self.title(APP_TITLE)
        self.geometry("820x700")
        self.minsize(680, 600)

        self.output_dir = DEFAULT_OUTPUT_DIR
        self.events: queue.Queue = queue.Queue()  # worker -> GUI messages
        self.worker: threading.Thread | None = None
        self.progress_indeterminate = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)  # the log box takes the spare height

        self._build_header()
        self._build_url_input()
        self._build_settings()
        self._build_actions()
        self._build_feedback()

        self.log(f"Ready. Stems will be saved to: {self.output_dir}")
        if not ffmpeg_available():
            self.log("WARNING: FFmpeg not found on PATH. Install it before extracting (see README).")

        self.after(100, self._drain_events)

    # ---- Layout -----------------------------------------------------------

    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 4))
        ctk.CTkLabel(header, text=APP_TITLE,
                     font=ctk.CTkFont(size=24, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(header, text="YouTube link in, Acapella + Instrumental out. Powered by UVR5.",
                     text_color=("gray40", "gray65")).pack(anchor="w")

    def _build_url_input(self):
        frame = ctk.CTkFrame(self)
        frame.grid(row=1, column=0, sticky="ew", padx=24, pady=(12, 6))
        frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(frame, text="YouTube link", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(12, 4))
        self.url_entry = ctk.CTkEntry(
            frame, height=42, font=ctk.CTkFont(size=14),
            placeholder_text="https://www.youtube.com/watch?v=...")
        self.url_entry.grid(row=1, column=0, sticky="ew", padx=(14, 6), pady=(0, 14))
        self.url_entry.bind("<Return>", lambda _e: self.start_extraction())

        self.paste_button = ctk.CTkButton(frame, text="Paste", width=80, height=42,
                                          fg_color="gray30", hover_color="gray25",
                                          command=self._paste_url)
        self.paste_button.grid(row=1, column=1, padx=(0, 14), pady=(0, 14))

    def _build_settings(self):
        frame = ctk.CTkFrame(self)
        frame.grid(row=2, column=0, sticky="ew", padx=24, pady=6)
        frame.grid_columnconfigure((1, 3), weight=1)

        ctk.CTkLabel(frame, text="Settings", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, columnspan=4, sticky="w", padx=14, pady=(12, 6))

        ctk.CTkLabel(frame, text="UVR model").grid(row=1, column=0, sticky="w", padx=(14, 8))
        self.model_var = ctk.StringVar(value=next(iter(UVR_MODELS)))
        self.model_menu = ctk.CTkOptionMenu(frame, variable=self.model_var,
                                            values=list(UVR_MODELS), dynamic_resizing=False)
        self.model_menu.grid(row=1, column=1, sticky="ew", padx=(0, 16), pady=4)

        ctk.CTkLabel(frame, text="Format").grid(row=1, column=2, sticky="w", padx=(0, 8))
        self.format_var = ctk.StringVar(value=next(iter(OUTPUT_FORMATS)))
        self.format_menu = ctk.CTkOptionMenu(frame, variable=self.format_var,
                                             values=list(OUTPUT_FORMATS), dynamic_resizing=False)
        self.format_menu.grid(row=1, column=3, sticky="ew", padx=(0, 14), pady=4)

        ctk.CTkLabel(frame, text="Save to").grid(row=2, column=0, sticky="w", padx=(14, 8),
                                                 pady=(8, 14))
        self.output_label = ctk.CTkLabel(frame, text=str(self.output_dir), anchor="w",
                                         text_color=("gray30", "gray75"))
        self.output_label.grid(row=2, column=1, columnspan=2, sticky="ew", pady=(8, 14))
        self.browse_button = ctk.CTkButton(frame, text="Choose folder...", width=130,
                                           fg_color="gray30", hover_color="gray25",
                                           command=self._choose_output_dir)
        self.browse_button.grid(row=2, column=3, sticky="e", padx=(0, 14), pady=(8, 14))

    def _build_actions(self):
        self.start_button = ctk.CTkButton(
            self, text="Start Extraction", height=50,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            command=self.start_extraction)
        self.start_button.grid(row=3, column=0, sticky="ew", padx=24, pady=(10, 6))

    def _build_feedback(self):
        frame = ctk.CTkFrame(self)
        frame.grid(row=4, column=0, sticky="nsew", padx=24, pady=(6, 20))
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(2, weight=1)

        status_row = ctk.CTkFrame(frame, fg_color="transparent")
        status_row.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))
        status_row.grid_columnconfigure(0, weight=1)
        self.status_label = ctk.CTkLabel(status_row, text="Idle", anchor="w",
                                         font=ctk.CTkFont(weight="bold"))
        self.status_label.grid(row=0, column=0, sticky="w")
        self.percent_label = ctk.CTkLabel(status_row, text="0%", anchor="e")
        self.percent_label.grid(row=0, column=1, sticky="e")

        self.progress_bar = ctk.CTkProgressBar(frame, height=12, progress_color=ACCENT)
        self.progress_bar.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 10))
        self.progress_bar.set(0)

        # Terminal-style log: monospace, read-only, scrolls automatically.
        self.log_box = ctk.CTkTextbox(frame, font=ctk.CTkFont(family="Consolas", size=12),
                                      fg_color=("gray92", "#0d1117"),
                                      text_color=("gray10", "#c9d1d9"), wrap="word")
        self.log_box.grid(row=2, column=0, sticky="nsew", padx=14)
        self.log_box.configure(state="disabled")

        self.open_button = ctk.CTkButton(frame, text="Open Output Folder", height=36,
                                         command=lambda: self._safe_open(self.output_dir),
                                         state="disabled")
        self.open_button.grid(row=3, column=0, sticky="e", padx=14, pady=12)

    # ---- UI actions -------------------------------------------------------

    def _paste_url(self):
        try:
            text = self.clipboard_get().strip()
        except Exception:
            return  # empty or non-text clipboard
        self.url_entry.delete(0, "end")
        self.url_entry.insert(0, text)

    def _choose_output_dir(self):
        chosen = filedialog.askdirectory(initialdir=str(self.output_dir),
                                         title="Choose where to save the stems")
        if chosen:
            self.output_dir = Path(chosen)
            self.output_label.configure(text=str(self.output_dir))
            self.log(f"Output folder set to: {self.output_dir}")

    def _safe_open(self, path: Path):
        try:
            open_in_file_explorer(path)
        except Exception as exc:
            self.log(f"Could not open the folder: {exc}")

    def _set_running(self, running: bool):
        state = "disabled" if running else "normal"
        for widget in (self.url_entry, self.paste_button, self.model_menu,
                       self.format_menu, self.browse_button):
            widget.configure(state=state)
        self.start_button.configure(state=state,
                                    text="Working..." if running else "Start Extraction")

    def start_extraction(self):
        if self.worker and self.worker.is_alive():
            return  # already running (e.g. Enter pressed twice)

        url = self.url_entry.get().strip()
        if not url:
            self.log("Paste a YouTube link first.")
            self.url_entry.focus_set()
            return

        job = Job(
            url=url,
            model_file=UVR_MODELS[self.model_var.get()],
            output_format=OUTPUT_FORMATS[self.format_var.get()],
            output_dir=self.output_dir,
        )

        self._set_running(True)
        self.open_button.configure(state="disabled")
        self._set_progress(0)
        self.status_label.configure(text="Working...")
        self.log("-" * 60)
        self.log(f"Starting: {url}")

        self.worker = threading.Thread(target=self._worker_main, args=(job,), daemon=True)
        self.worker.start()

    # ---- Worker thread (no widget access in here!) ------------------------

    def _worker_main(self, job: Job):
        pipeline = ExtractionPipeline(
            log=lambda msg: self.events.put(("log", msg)),
            progress=lambda value: self.events.put(("progress", value)),
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
                elif kind == "error":
                    self._finish(success=False)
                    self.log(f"ERROR: {payload}")
                    self.status_label.configure(text="Failed")
                elif kind == "done":
                    self._finish(success=True)
                    for path in payload:
                        self.log(f"Saved: {path}")
                    self.log("Done!")
                    self.status_label.configure(text="Done!")
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _finish(self, success: bool):
        self._set_progress(1.0 if success else 0)
        self._set_running(False)
        self.open_button.configure(state="normal")

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
        self.progress_bar.set(value)
        self.percent_label.configure(text=f"{int(value * 100)}%")

    def log(self, message: str):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{stamp}] {message}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")


def main():
    app = StemExtractorApp()
    app.mainloop()


if __name__ == "__main__":
    main()
