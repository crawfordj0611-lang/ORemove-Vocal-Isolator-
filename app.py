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

import functools
import importlib
import importlib.abc
import importlib.machinery
import json
import logging
import multiprocessing
import os
import queue
import re
import shutil
import socketserver
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
APP_VERSION = "1.5.9"

# True when running as the packaged app (built with PyInstaller).
FROZEN = getattr(sys, "frozen", False)
MACOS = sys.platform == "darwin"

# Folder holding the program; from source, this script's folder.
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
# Read-only files bundled with the app (the interface, FFmpeg, the fast model).
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
UI_DIR = BUNDLE_DIR / "ui"
# The app icon for the window and taskbar (bundled; from source, in packaging/).
ICON_FILE = next((p for p in (BUNDLE_DIR / "icon.ico", BUNDLE_DIR / "packaging" / "icon.ico")
                  if sys.platform.startswith("win") and p.exists()),
                 next((p for p in (BUNDLE_DIR / "icon.png", BUNDLE_DIR / "packaging" / "icon.png") if p.exists()), None))
# Windows groups taskbar buttons by this ID; it must match the installer's shortcuts.
APP_USER_MODEL_ID = "newmoneymadeit.ORemove"
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
    # MelBand Roformer by becruily: audio-separator's recommended presets rate the
    # instrumental model the highest-scoring instrumental model (SDR 17.55).
    ModelOption("MelBand Roformer Inst", "Fullest instrumentals",
                "mel_band_roformer_instrumental_becruily.ckpt", "871 MB"),
    ModelOption("MelBand Roformer Vocals", "Fullest acapellas",
                "mel_band_roformer_vocals_becruily.ckpt", "871 MB"),
    ModelOption("MDX-Net Inst HQ 3", "Fastest · lighter quality",
                "UVR-MDX-NET-Inst_HQ_3.onnx", "Included"),
]
MODELS_BY_FILE = {m.filename: m for m in MODELS}

OUTPUT_FORMATS = ["WAV", "MP3"]  # WAV is lossless; MP3 is written at 320 kbps

QUALITY_MODES = ["Best", "Faster"]  # "Faster": half the overlapping passes for BS-Roformer

# The self-test switches this on to exercise the NVIDIA GPU code path (the fast
# model converted to PyTorch, in batches) on build machines without a GPU.
force_gpu_settings = False

# Share of the progress bar given to each stage, roughly in proportion to the
# time each takes: separation is by far the longest.
DOWNLOAD_SHARE = 0.10   # 0 % -> 10 %  : getting the audio (YouTube download or decoding a file)
MODEL_LOAD_AT = 0.15    # 10 % -> 15 % : loading (or, first time, downloading) the model
SEPARATED_AT = 0.97     # 15 % -> 97 % : separating, reported chunk by chunk; then saving

# --------------------------------------------------------------------------- #
# Helpers (no GUI code here, so they are easy to test or reuse)
# --------------------------------------------------------------------------- #

class PipelineError(Exception):
    """An expected failure with a message meant for the user."""


class YouTubeChanged(PipelineError):
    """yt-dlp failed in a way a newer yt-dlp may fix."""


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
    analyse: bool = True  # detect BPM and key after separating


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

if sys.platform.startswith("win"):
    # Libraries ORemove uses also start programs (pydub runs FFmpeg to save MP3
    # stems, yt-dlp runs FFmpeg and Deno) without hiding their console window,
    # which flashed up on screen. Hide it for every program started, unless the
    # caller chose its own flags.
    _popen_init = subprocess.Popen.__init__

    def _popen_init_no_window(self, *args, **kwargs):
        if not kwargs.get("creationflags"):
            kwargs["creationflags"] = NO_WINDOW
        _popen_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = _popen_init_no_window


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


# --------------------------------------------------------------------------- #
# yt-dlp auto-update
# --------------------------------------------------------------------------- #
# YouTube changes its site often, and yt-dlp releases fixes within days. So
# that YouTube links keep working without a new ORemove version, the app checks
# PyPI (the official Python package index) once a day for a newer yt-dlp. A
# newer one is downloaded (a single ~3 MB file), checked against PyPI's SHA-256
# hash, kept in DATA_DIR/yt-dlp, and used instead of the bundled copy from the
# next YouTube download on. If it ever fails to load, the bundled copy is used.

YTDLP_DIR = DATA_DIR / "yt-dlp"
YTDLP_INDEX = "https://pypi.org/pypi/yt-dlp/json"
YTDLP_CHECK_EVERY = 24 * 3600  # seconds between automatic checks
_YTDLP_WHEEL = re.compile(r"^yt_dlp-(\d+(?:\.\d+)*)-py3-none-any\.whl$")
_ytdlp_lock = threading.RLock()  # held while yt-dlp runs or is swapped
_deno_lock = threading.Lock()
_ytdlp = {"wheel": None, "bundled": None, "auto": True}


def _version_key(version: str) -> tuple:
    """'2026.10.26' -> (2026, 10, 26); yt-dlp versions are dates."""
    return tuple(int(x) for x in re.findall(r"\d+", version or "")) or (0,)


class _YtDlpWheelFinder(importlib.abc.MetaPathFinder):
    """Imports yt_dlp from the downloaded update when one is in use (ahead of
    the copy bundled in the app, which PyInstaller serves from its own importer)."""

    def find_spec(self, fullname, path=None, target=None):
        wheel = _ytdlp["wheel"]
        if wheel is None or not (fullname == "yt_dlp" or fullname.startswith("yt_dlp.")):
            return None
        return importlib.machinery.PathFinder.find_spec(fullname, [str(wheel)] if fullname == "yt_dlp" else path)


def _forget_ytdlp():
    """Unload yt_dlp so the next import loads the chosen copy."""
    for name in [n for n in sys.modules if n == "yt_dlp" or n.startswith("yt_dlp.")]:
        del sys.modules[name]
    importlib.invalidate_caches()


def bundled_ytdlp_version() -> str:
    """The version of the yt-dlp built into this ORemove version."""
    with _ytdlp_lock:
        if _ytdlp["bundled"] is None:
            try:
                from importlib.metadata import version
                _ytdlp["bundled"] = version("yt-dlp")
            except Exception:  # no package metadata: ask the module itself
                wheel, _ytdlp["wheel"] = _ytdlp["wheel"], None
                _forget_ytdlp()
                try:
                    from yt_dlp.version import __version__
                    _ytdlp["bundled"] = __version__
                except Exception:
                    _ytdlp["bundled"] = "0"
                _forget_ytdlp()
                _ytdlp["wheel"] = wheel
        return _ytdlp["bundled"]


def downloaded_ytdlp() -> list[tuple[str, Path]]:
    """Downloaded yt-dlp updates as (version, file), newest first."""
    found = []
    for path in YTDLP_DIR.glob("yt_dlp-*.whl"):
        match = _YTDLP_WHEEL.match(path.name)
        if match:
            found.append((match.group(1), path))
    return sorted(found, key=lambda item: _version_key(item[0]), reverse=True)


def ytdlp_version() -> str:
    """The yt-dlp version the next YouTube download will use."""
    bundled = bundled_ytdlp_version()
    if _ytdlp["auto"]:
        for version, _path in downloaded_ytdlp():
            if _version_key(version) > _version_key(bundled):
                return version
    return bundled


def use_ytdlp(log=lambda _text: None) -> str:
    """
    Before a YouTube download: switch to the newest working yt-dlp (a
    downloaded update, or the bundled copy). Call with _ytdlp_lock held, since
    yt-dlp must not be swapped while it runs. Returns the version in use.
    """
    bundled = bundled_ytdlp_version()
    candidates = [(v, p) for v, p in downloaded_ytdlp() if _version_key(v) > _version_key(bundled)] \
        if _ytdlp["auto"] else []
    for version, path in candidates + [(bundled, None)]:
        if _ytdlp["wheel"] != path:
            _forget_ytdlp()
            _ytdlp["wheel"] = path
        if path is None:
            return bundled
        try:
            import yt_dlp
            import yt_dlp.version
            if not str(getattr(yt_dlp, "__file__", "")).startswith(str(path)):
                raise ImportError(f"loaded from {getattr(yt_dlp, '__file__', '?')}")
            return yt_dlp.version.__version__
        except Exception as exc:
            log(f"yt-dlp {version} update didn't load ({exc}); using the next one.")
            _forget_ytdlp()
            _ytdlp["wheel"] = None
            try:  # set aside, so it isn't used (or downloaded) again
                path.replace(path.with_name(path.name + ".bad"))
            except OSError:
                pass
    return bundled


def _download(url: str, timeout: float = 30, on_progress=None) -> bytes:
    import ssl
    import urllib.request
    cafile = None
    if not os.environ.get("SSL_CERT_FILE"):
        try:
            import certifi  # the packaged app has no system certificates on macOS
            cafile = certifi.where()
        except ImportError:
            pass
    context = ssl.create_default_context(cafile=cafile)
    request = urllib.request.Request(url, headers={"User-Agent": f"ORemove/{APP_VERSION}"})
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        if on_progress is None:
            return response.read()
        total = int(response.headers.get("Content-Length") or 0)
        chunks, done = [], 0
        while chunk := response.read(1 << 18):
            chunks.append(chunk)
            done += len(chunk)
            on_progress(done, total)
        return b"".join(chunks)


def update_ytdlp(force: bool = False, log=lambda _text: None) -> dict:
    """
    Check PyPI for a newer yt-dlp and download it. Unless forced, checks at
    most once a day. Returns {"version": in use next, "updated": bool,
    "checked": bool}. Raises on network or verification errors.
    """
    import hashlib
    import zipfile
    stamp = YTDLP_DIR / "last-check"
    if not force and stamp.exists() and time.time() - stamp.stat().st_mtime < YTDLP_CHECK_EVERY:
        return {"version": ytdlp_version(), "updated": False, "checked": False}
    index = json.loads(_download(YTDLP_INDEX))
    latest = index["info"]["version"]
    YTDLP_DIR.mkdir(parents=True, exist_ok=True)
    stamp.write_text(latest, encoding="utf-8")  # mtime = time of this check
    current = ytdlp_version()
    if _version_key(latest) <= _version_key(current):
        return {"version": current, "updated": False, "checked": True}
    needs = re.match(r"\s*>=\s*(\d+)\.(\d+)", index["info"].get("requires_python") or "")
    if needs and sys.version_info[:2] < (int(needs.group(1)), int(needs.group(2))):
        log(f"yt-dlp {latest} needs a newer Python than this ORemove version; install the latest ORemove.")
        return {"version": current, "updated": False, "checked": True}
    wheel = next((f for f in index["urls"] if _YTDLP_WHEEL.match(f.get("filename", ""))), None)
    if wheel is None:
        raise RuntimeError(f"no installable file for yt-dlp {latest} on PyPI")
    if (YTDLP_DIR / (wheel["filename"] + ".bad")).exists():
        return {"version": current, "updated": False, "checked": True}  # this one failed to load before
    log(f"Updating the YouTube downloader (yt-dlp {current} → {latest})...")
    data = _download(wheel["url"], timeout=120)
    if hashlib.sha256(data).hexdigest() != wheel["digests"]["sha256"]:
        raise RuntimeError("the downloaded yt-dlp file didn't match PyPI's checksum")
    temp = YTDLP_DIR / (wheel["filename"] + ".part")
    temp.write_bytes(data)
    try:
        with zipfile.ZipFile(temp) as archive:
            if not {"yt_dlp/__init__.py", "yt_dlp/version.py"} <= set(archive.namelist()):
                raise RuntimeError("the downloaded yt-dlp file is incomplete")
        os.replace(temp, YTDLP_DIR / wheel["filename"])
    finally:
        temp.unlink(missing_ok=True)
    for _version, old in downloaded_ytdlp()[2:]:  # keep the newest two
        try:
            old.unlink()
        except OSError:
            pass  # in use (Windows); removed next time
    log(f"YouTube downloader updated to yt-dlp {latest}.")
    return {"version": ytdlp_version(), "updated": True, "checked": True}


sys.meta_path.insert(0, _YtDlpWheelFinder())


# --------------------------------------------------------------------------- #
# Deno: the JavaScript runtime yt-dlp needs for YouTube
# --------------------------------------------------------------------------- #
# YouTube now makes downloaders solve JavaScript challenges; without a
# JavaScript runtime yt-dlp misses formats and, increasingly, fails. Deno
# (about 40 MB) is downloaded once, from Deno's official servers, the first
# time a YouTube link is used, and checked against its published SHA-256.

DENO_DIR = DATA_DIR / "deno"
DENO_RELEASES = "https://dl.deno.land"


def _deno_target() -> str | None:
    import platform
    machine = platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    if sys.platform.startswith("win"):
        return None if arm else "x86_64-pc-windows-msvc"
    if MACOS:
        return "aarch64-apple-darwin" if arm else "x86_64-apple-darwin"
    if sys.platform.startswith("linux"):
        return "aarch64-unknown-linux-gnu" if arm else "x86_64-unknown-linux-gnu"
    return None


def deno_path() -> Path | None:
    """The downloaded Deno, if any."""
    exe = DENO_DIR / ("deno.exe" if sys.platform.startswith("win") else "deno")
    return exe if exe.is_file() else None


def install_deno(on_progress=None) -> str:
    """Download and verify Deno. Returns its version. Raises on failure."""
    import hashlib
    import zipfile
    target = _deno_target()
    if not target:
        raise RuntimeError("no Deno build for this computer")
    version = _download(f"{DENO_RELEASES}/release-latest.txt").decode().strip()
    if not re.fullmatch(r"v\d+\.\d+\.\d+", version):
        raise RuntimeError(f"unexpected Deno version {version!r}")
    name = f"deno-{target}.zip"
    checksum = _download(f"{DENO_RELEASES}/release/{version}/{name}.sha256sum").decode().split()[0].lower()
    data = _download(f"{DENO_RELEASES}/release/{version}/{name}", timeout=120, on_progress=on_progress)
    if hashlib.sha256(data).hexdigest() != checksum:
        raise RuntimeError("the downloaded Deno didn't match its published checksum")
    exe_name = "deno.exe" if sys.platform.startswith("win") else "deno"
    DENO_DIR.mkdir(parents=True, exist_ok=True)
    temp = DENO_DIR / (exe_name + ".part")
    import io
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        temp.write_bytes(archive.read(exe_name))
    if not sys.platform.startswith("win"):
        temp.chmod(0o755)
    os.replace(temp, DENO_DIR / exe_name)
    (DENO_DIR / "version.txt").write_text(version, encoding="utf-8")
    return version


def ytdlp_js_options() -> dict:
    """yt-dlp options that point it at the downloaded Deno."""
    exe = deno_path()
    if not exe:
        return {}
    # The challenge scripts come from the bundled yt-dlp-ejs package; if an
    # updated yt-dlp needs a newer version, yt-dlp fetches it from yt-dlp's
    # own GitHub releases (and caches it).
    return {"js_runtimes": {"deno": {"path": str(exe)}}, "remote_components": {"ejs:github"}}


# --------------------------------------------------------------------------- #
# New ORemove versions
# --------------------------------------------------------------------------- #

REPO = "crawfordj0611-lang/ORemove-Vocal-Isolator-"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
UPDATE_FILE = DATA_DIR / "update-check.json"


def check_app_update(force: bool = False) -> dict | None:
    """
    Ask GitHub, at most once a day, whether a newer ORemove has been released.
    Returns {"version", "url"} for a newer version, or None.
    """
    try:
        saved = json.loads(UPDATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        saved = {}
    if force or time.time() - saved.get("checked", 0) > 24 * 3600:
        release = json.loads(_download(f"https://api.github.com/repos/{REPO}/releases/latest"))
        saved = {"checked": time.time(), "latest": str(release.get("tag_name", "")).lstrip("v"),
                 "url": release.get("html_url") or RELEASES_PAGE}
        try:
            UPDATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            UPDATE_FILE.write_text(json.dumps(saved), encoding="utf-8")
        except OSError:
            pass
    latest = saved.get("latest") or ""
    if re.fullmatch(r"\d+(\.\d+)*", latest) and _version_key(latest) > _version_key(APP_VERSION):
        url = saved.get("url") or RELEASES_PAGE
        return {"version": latest, "url": url if url.startswith(f"https://github.com/{REPO}/") else RELEASES_PAGE}
    return None


# The activity log is also saved to a file, for sending with a problem report.
LOG_FILE = DATA_DIR / "logs" / "oremove.log"
_log_lock = threading.Lock()


def write_log_file(text: str):
    try:
        with _log_lock:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            if LOG_FILE.exists() and LOG_FILE.stat().st_size > 2_000_000:
                os.replace(LOG_FILE, LOG_FILE.with_suffix(".old.log"))  # keep one previous file
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {text}\n")
    except Exception:
        pass  # logging must never break the app


def _log_uncaught(kind, exc_type, exc, tb):
    write_log_file(f"CRASH ({kind}):\n" + "".join(traceback.format_exception(exc_type, exc, tb)))


sys.excepthook = lambda t, e, tb: (_log_uncaught("main", t, e, tb), sys.__excepthook__(t, e, tb))
threading.excepthook = lambda a: _log_uncaught(f"thread {a.thread.name if a.thread else '?'}", a.exc_type, a.exc_value, a.exc_traceback)


def model_installed(filename: str) -> bool:
    """True once a model is on this computer (downloaded, or bundled with the app)."""
    return (MODEL_DIR / filename).exists() or (BUNDLE_DIR / "models" / filename).exists()


def detect_device() -> str:
    """Import the AI engine and describe what it will run on, e.g. 'RTX 4070 SUPER'."""
    import torch
    import audio_separator.separator  # noqa: F401  # the slow import we want done early
    if torch.cuda.is_available():
        return torch.cuda.get_device_name(0).replace("NVIDIA ", "").replace("GeForce ", "")
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "Apple GPU"
    return "CPU"


def nvidia_gpu_info() -> tuple[str, str] | None:
    """
    (name, driver version) of the first NVIDIA GPU, from NVIDIA's own nvidia-smi
    tool. Fast and light: it doesn't load PyTorch. None if there's no NVIDIA GPU
    (or no driver).
    """
    exe = shutil.which("nvidia-smi")
    if not exe and sys.platform.startswith("win"):
        candidate = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvidia-smi.exe"
        exe = str(candidate) if candidate.exists() else None
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=name,driver_version", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=10, creationflags=NO_WINDOW).stdout
        name, driver = [x.strip() for x in out.strip().splitlines()[0].split(",")[:2]]
        return name.replace("NVIDIA ", "").replace("GeForce ", ""), driver
    except Exception:
        return None


def nvidia_edition_files() -> bool:
    """True for the NVIDIA edition (PyTorch built with CUDA), without importing PyTorch."""
    try:
        import importlib.util
        spec = importlib.util.find_spec("torch")
        lib = Path(spec.origin).parent / "lib" if spec and spec.origin else BUNDLE_DIR / "torch" / "lib"
        return any(lib.glob("torch_cuda*")) or any(lib.glob("libtorch_cuda*"))
    except Exception:
        return False


# CUDA 12.6 (the NVIDIA edition's PyTorch) needs at least this NVIDIA driver.
MIN_DRIVER = {"win": 528.33, "linux": 525.60}


def cuda_diagnosis() -> str:
    """Why PyTorch can't use the NVIDIA GPU, in words, for the log and the banner."""
    lines = []
    try:
        import torch
        lines.append(f"PyTorch {torch.__version__} (CUDA {torch.version.cuda})")
        try:
            torch.cuda.init()
        except Exception as exc:
            lines.append(f"CUDA error: {exc}")
    except Exception as exc:
        lines.append(f"PyTorch failed to load: {exc}")
    info = nvidia_gpu_info()
    if not info:
        lines.append("No NVIDIA GPU or driver found (nvidia-smi didn't report one).")
    else:
        name, driver = info
        lines.append(f"GPU: {name}, driver {driver}")
        minimum = MIN_DRIVER["win" if sys.platform.startswith("win") else "linux"]
        try:
            if float(".".join(driver.split(".")[:2])) < minimum:
                lines.append(f"The NVIDIA driver is too old: version {minimum:g} or newer is needed. "
                             "Update it from nvidia.com/drivers or the NVIDIA app, then restart ORemove.")
        except ValueError:
            pass
    return " | ".join(lines)


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


# --------------------------------------------------------------------------- #
# Progress from inside audio-separator
# --------------------------------------------------------------------------- #
# audio-separator reports its own progress through tqdm progress bars (one step
# per chunk of audio while separating; bytes while downloading a model). We swap
# tqdm in its modules for _ProgressBar, which passes those counts to
# _progress_sink(kind, done, total) so the app can show real progress.

_progress_sink = None
# Raises Cancelled once Stop is pressed; checked after every chunk of audio, so
# Stop takes effect within a second or two instead of after the whole song.
_progress_cancel = None


class _ProgressBar:
    """A minimal stand-in for tqdm that reports progress to _progress_sink."""

    def __init__(self, iterable=None, total=None, kind="separate", **_kwargs):
        self.iterable = iterable
        if total is None and iterable is not None:
            try:
                total = len(iterable)
            except TypeError:
                total = None
        self.total, self.n, self.kind = total, 0, kind

    def __iter__(self):
        for item in self.iterable:
            yield item
            self.update(1)

    def update(self, n=1):
        self.n += n
        check = _progress_cancel
        if check:
            check()  # raises Cancelled: stops the separation between chunks
        sink = _progress_sink
        if sink and self.total:
            try:
                sink(self.kind, self.n, self.total)
            except Exception:
                pass  # progress display must never break separation

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def __getattr__(self, _name):
        # set_description, refresh, set_postfix, ...: nothing to do
        return lambda *_args, **_kwargs: None


# audio-separator modules that use tqdm, and what their progress means.
_PROGRESS_MODULES = {
    "audio_separator.separator.architectures.mdxc_separator": "separate",  # BS-Roformer
    "audio_separator.separator.architectures.mdx_separator": "separate",   # MDX-Net
    "audio_separator.separator.architectures.vr_separator": "separate",
    "audio_separator.separator.separator": "download",                     # model downloads
}


def _install_progress_hooks():
    for name, kind in _PROGRESS_MODULES.items():
        try:
            module = importlib.import_module(name)
        except Exception:
            continue
        if hasattr(module, "tqdm"):
            module.tqdm = functools.partial(_ProgressBar, kind=kind)


def _set_progress_sink(sink):
    global _progress_sink
    _progress_sink = sink


# The loaded model, kept between runs so each extraction doesn't reload it.
# Only one job runs at a time, so no locking is needed. "safe" becomes True
# after a GPU out-of-memory error and stays on for the rest of the session.
_cache = {"key": None, "separator": None, "safe": False, "device": ""}

# How the Roformer models run on this computer's GPU: 16-bit or full precision,
# and whether PyTorch compiles the model's repeated blocks (torch.compile,
# Apple GPUs). Which is fastest depends on the chip, so the speed test in
# Settings measures them and saves the winner; until then the defaults are used.
# "batch": chunks separated at once; "spectral": "real" or "native" spectral
# maths on Apple GPUs; "threads": PyTorch threads on the CPU.
_engine_profile = {"fp16": None, "compile": None, "batch": None, "spectral": None, "threads": None}  # None = default


def _describe_device(separator) -> str:
    """Where a loaded model actually runs, in words: 'RTX 4070 SUPER', 'the Apple GPU', 'the CPU'."""
    try:
        instance = separator.model_instance
        if getattr(instance, "uses_pytorch_inference", True) is False:
            # MDX-Net through ONNX Runtime: CPU, or CoreML on a Mac.
            providers = getattr(instance, "onnx_execution_provider", None) or separator.onnx_execution_provider or []
            if providers and "CUDA" in providers[0]:
                return detect_device()
            return "the Apple GPU" if providers and "CoreML" in providers[0] else "the CPU"
        device = getattr(instance, "torch_device", None) or separator.torch_device
        kind = getattr(device, "type", str(device))
        if kind == "cuda":
            return detect_device()
        if kind == "mps":
            return "the Apple GPU"
        return "the CPU"
    except Exception:
        return ""


# Loading a model is done by one thread at a time (a job or a warm-up).
_engine_lock = threading.RLock()


def _reset_gpu_memory_peak():
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
    except Exception:
        pass


def _gpu_memory_peak_mb() -> float | None:
    """Peak GPU memory used since the last reset (NVIDIA), or current use (Apple); None on the CPU."""
    try:
        import torch
        if torch.cuda.is_available():
            return torch.cuda.max_memory_allocated() / 1_048_576
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return torch.mps.driver_allocated_memory() / 1_048_576
    except Exception:
        pass
    return None


def _run_separator(separator, wav: Path):
    """Run one separation without autograd bookkeeping (no effect on the result)."""
    import torch
    with torch.inference_mode():
        return separator.separate(str(wav))


def _free_gpu_cache():
    """
    Return cached GPU memory to the system after a song, keeping the model
    loaded: between songs the GPU then holds only the model, not the previous
    song's leftover work buffers.
    """
    try:
        import gc
        import torch
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            torch.mps.empty_cache()
    except Exception:
        pass


_tracker = {"instance": None}


def _beat_tracker():
    """The BPM detector, loaded once and kept (it's small: about 80 MB)."""
    if _tracker["instance"] is None:
        import oremove_analysis as oa
        device = "cpu"
        try:
            import torch
            if torch.cuda.is_available():
                device = "cuda"
        except Exception:
            pass
        _tracker["instance"] = oa.BeatTracker(MODEL_DIR, device)
    return _tracker["instance"]


_tracker_lock = threading.Lock()


def write_audio(audio, target: Path, output_format: str, subtype: str):
    """Write a (samples, 2) float32 stem: WAV with soundfile, MP3 (320 kbps) with FFmpeg."""
    import numpy as np
    if output_format == "WAV":
        import soundfile as sf
        sf.write(str(target), audio, 44100, subtype=subtype, format="WAV")
        return
    # As audio-separator did (through pydub): 16-bit samples into FFmpeg's MP3 encoder.
    pcm = (audio * 32767).astype(np.int16).tobytes()
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "s16le", "-ar", "44100", "-ac", "2", "-i", "pipe:0",
         "-c:a", "libmp3lame", "-b:a", "320k", "-f", "mp3", str(target)],
        input=pcm, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
    if proc.returncode != 0:
        reason = (proc.stderr.decode("utf-8", "replace").strip().splitlines() or ["MP3 encoding failed"])[-1]
        raise RuntimeError(reason)


def _default_batch() -> int:
    """Chunks separated at once when the speed test hasn't measured it: 2 on NVIDIA cards with 10 GB or more."""
    try:
        import torch
        if torch.cuda.is_available():
            total = torch.cuda.get_device_properties(0).total_memory
            return 2 if total >= 10 * 1024 ** 3 else 1
    except Exception:
        pass
    return 1


def _chunk_batch() -> int:
    batch = _engine_profile.get("batch") or _default_batch()
    limit = _cache.get("batch_limit")
    return max(1, min(batch, limit) if limit else batch)


def _complex_ops_on_gpu(device) -> bool:
    """Whether PyTorch can do the models' complex-number maths on this (Apple) GPU."""
    try:
        from audio_separator.separator.uvr_lib_v5.device_utils import should_fallback_to_cpu_for_complex_ops
        return not should_fallback_to_cpu_for_complex_ops(device)
    except Exception:
        return False


def _real_spectral(instance) -> bool:
    """
    Apple GPUs: do the spectral maths with real numbers when PyTorch can't do
    the complex-number versions on the GPU (they would otherwise run on the
    CPU, copying audio back and forth for every chunk). The speed test can
    also choose it when it measures faster.
    """
    try:
        device = next(instance.model_run.parameters()).device
    except Exception:
        return False
    if device.type != "mps":
        return False
    choice = _engine_profile.get("spectral")
    if choice in ("real", "native"):
        return choice == "real"
    return not _complex_ops_on_gpu(device)


def physical_cores() -> int | None:
    """The number of physical CPU cores (not hyper-threads), or None if unknown."""
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "hw.physicalcpu"], capture_output=True, text=True, timeout=5)
            return int(out.stdout.strip()) or None
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            size = wintypes.DWORD(0)
            # RelationProcessorCore = 0: one record per physical core.
            kernel32.GetLogicalProcessorInformationEx(0, None, ctypes.byref(size))
            buffer = ctypes.create_string_buffer(size.value)
            if not kernel32.GetLogicalProcessorInformationEx(0, buffer, ctypes.byref(size)):
                return None
            count, offset = 0, 0
            while offset < size.value:
                record_size = int.from_bytes(buffer.raw[offset + 4:offset + 8], "little")
                if record_size <= 0:
                    break
                count += 1
                offset += record_size
            return count or None
        cores = set()
        with open("/proc/cpuinfo") as f:
            physical = core = None
            for line in f:
                if line.startswith("physical id"):
                    physical = line.split(":")[1].strip()
                elif line.startswith("core id"):
                    core = line.split(":")[1].strip()
                    cores.add((physical, core))
        return len(cores) or None
    except Exception:
        return None


_cpu_tuned = {"done": False}


def tune_cpu(log=lambda _t: None):
    """
    CPU edition: one PyTorch thread per physical core (hyper-threads share a
    core's maths units, so extra threads mostly add overhead), unless the speed
    test measured otherwise; and treat tiny "denormal" numbers as zero, which
    the CPU otherwise handles very slowly (no audible effect: they're below
    -700 dB).
    """
    if _cpu_tuned["done"]:
        return
    _cpu_tuned["done"] = True
    try:
        import torch
        torch.set_flush_denormal(True)
        if torch.cuda.is_available() or (getattr(torch.backends, "mps", None) and torch.backends.mps.is_available()):
            return
        threads = _engine_profile.get("threads") or physical_cores()
        if threads and threads != torch.get_num_threads():
            torch.set_num_threads(int(threads))
        log(f"CPU: {torch.get_num_threads()} threads ({os.cpu_count()} logical cores, "
            f"{physical_cores() or '?'} physical)")
    except Exception as exc:
        log(f"CPU tuning skipped: {exc}")


class _FastModelLoading:
    """
    Load Roformer models faster: don't fill the new model with random starting
    weights (every weight is replaced from the model file straight after, and
    loading is strict, so none can be missed), and memory-map the file instead
    of reading it into memory and then copying it.
    """

    _NO_INIT = ("uniform_", "normal_", "kaiming_uniform_", "kaiming_normal_", "xavier_uniform_",
                "xavier_normal_", "trunc_normal_", "orthogonal_", "constant_", "zeros_", "ones_")

    def __enter__(self):
        self._saved = []
        try:
            import torch
            for name in self._NO_INIT:
                original = getattr(torch.nn.init, name, None)
                if original is not None:
                    self._saved.append((torch.nn.init, name, original))
                    setattr(torch.nn.init, name, lambda tensor, *args, **kwargs: tensor)
        except Exception:
            pass  # a speed-up only: load normally
        try:
            from audio_separator.separator.roformer import roformer_loader
            original_load = roformer_loader.torch.load

            def load(path, *args, map_location=None, **kwargs):
                if isinstance(path, (str, os.PathLike)) and "mmap" not in kwargs:
                    try:
                        # The model is moved to the GPU afterwards anyway.
                        return original_load(path, *args, map_location="cpu", mmap=True, **kwargs)
                    except Exception:
                        pass
                return original_load(path, *args, map_location=map_location, **kwargs)

            self._loader_module, self._original_load = roformer_loader.torch, original_load
            roformer_loader.torch = _TorchProxy(roformer_loader.torch, load)
            self._roformer_loader = roformer_loader
        except Exception:
            self._roformer_loader = None
        return self

    def __exit__(self, *_exc):
        for module, name, original in self._saved:
            setattr(module, name, original)
        if self._roformer_loader is not None:
            self._roformer_loader.torch = self._loader_module
        return False


class _TorchProxy:
    """`torch` with a different `load`, for one module only."""

    def __init__(self, torch_module, load):
        self._torch, self.load = torch_module, load

    def __getattr__(self, name):
        return getattr(self._torch, name)


def _release_separator():
    """Drop the loaded model and free its GPU memory."""
    _cache["key"] = _cache["separator"] = None
    try:
        import gc
        import torch
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        mps = getattr(torch, "mps", None)
        if mps and getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            mps.empty_cache()
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
                 title=lambda _text: None, cancel_event: threading.Event | None = None,
                 engine=lambda _device: None):
        self.log = log
        self._engine_cb = engine  # told what the model runs on, once it's loaded
        self._progress_cb = progress
        self._stage_cb = stage
        self._title_cb = title
        self.cancel_event = cancel_event or threading.Event()
        self._count = 1                   # songs in the current batch
        self._shown = 0.0                 # highest progress shown for the current item
        self.run_device = ""              # what the loaded model runs on, e.g. "RTX 4070 SUPER"
        self.on_model_download = None     # optional (percent, total bytes) callback
        # Seconds spent in each step, for the breakdown on the results screen.
        self.timings = {"analyse": 0.0, "audio": 0.0, "engine": 0.0, "model_download": 0.0, "model_load": 0.0,
                        "separate": 0.0, "save": 0.0}
        self._timing_lock = threading.Lock()
        self.gpu_memory_mb = 0.0          # peak GPU memory used while separating (proof the GPU worked)
        # In a batch, songs overlap: the next one downloads while this one
        # separates, and the previous one is saved. Each thread knows which
        # song it is working on; only the song on screen updates the progress
        # bar and status line (the others still write to the activity log).
        self._local = threading.local()
        self._ui_item = 0                 # the song the screen shows; None = whichever reports
        self.overlapped = False           # True when steps of different songs ran at the same time
        self._warming = False             # the model is loading before the first song

    def _item(self):
        return getattr(self._local, "item", self._ui_item)

    def _on_screen(self) -> bool:
        return self._ui_item is None or self._item() == self._ui_item

    def add_time(self, key: str, seconds: float):
        with self._timing_lock:
            self.timings[key] += seconds

    def _show_item(self, i):
        """Put song `i` on screen (its progress restarts from where it is)."""
        if i != self._ui_item:
            self._ui_item = i
            self._shown = 0.0

    def check_cancel(self):
        """Stop between steps once the user has pressed Stop."""
        if self.cancel_event.is_set():
            raise Cancelled()

    def progress(self, value):
        """Report progress of the current item, scaled to the whole batch. Never goes backwards."""
        if not self._on_screen():
            return
        if value is not None:
            value = max(value, self._shown)
            self._shown = value
        self._progress_cb(None if value is None else (self._item() + value) / self._count)

    def stage(self, name, detail):
        if not self._on_screen():
            return
        prefix = f"File {self._item() + 1} of {self._count}  ·  " if self._count > 1 else ""
        self._stage_cb(name, prefix + detail)

    def show_title(self, title: str):
        if self._on_screen():
            self._title_cb(title)

    # ---- Step 1: download --------------------------------------------------

    def download_youtube(self, url: str, download_dir: Path) -> tuple[str, Path]:
        """Download a YouTube video's audio with the newest yt-dlp. Returns (title, audio file)."""
        self._ensure_deno()
        with _ytdlp_lock:  # yt-dlp mustn't be updated while it runs
            for attempt in (1, 2):
                version = use_ytdlp(self.log)
                self.log(f"Using yt-dlp {version}.")
                try:
                    title = self.fetch_title(url)
                    self.show_title(title)
                    self.check_cancel()
                    return title, self.download_audio(url, download_dir)
                except YouTubeChanged:
                    # Maybe YouTube changed and yt-dlp has a fix already: check
                    # now (not just once a day) and retry once with the update.
                    if attempt == 2 or not _ytdlp["auto"]:
                        raise
                    self.stage("download", "Updating the YouTube downloader")
                    try:
                        updated = update_ytdlp(force=True, log=self.log)["updated"]
                    except Exception as exc:
                        self.log(f"Couldn't check for a yt-dlp update: {exc}")
                        updated = False
                    if not updated:
                        raise
                    self.check_cancel()
                    shutil.rmtree(download_dir, ignore_errors=True)
                    download_dir.mkdir(exist_ok=True)
                    self.log("Trying again with the updated yt-dlp...")

    def _ensure_deno(self):
        """The first YouTube link: download Deno, which yt-dlp needs for YouTube."""
        if deno_path() or not _deno_target():
            return
        with _deno_lock:
            if deno_path():
                return
            self.log("Setting up the YouTube downloader (one-time download of Deno, about 40 MB)...")
            self.stage("download", "Setting up the YouTube downloader (one time only)")
            shown = {"pct": -1}

            def on_progress(done, total):
                self.check_cancel()
                if total:
                    pct = int(done * 100 / total)
                    if pct != shown["pct"]:
                        shown["pct"] = pct
                        self.progress(DOWNLOAD_SHARE * done / total)
                        self.stage("download", f"Setting up the YouTube downloader, one time only ({pct}%)")
                else:
                    self.progress(None)

            try:
                self.log(f"Deno {install_deno(on_progress)} installed.")
            except Cancelled:
                raise
            except Exception as exc:
                # YouTube may still work without it, just less reliably.
                self.log(f"Couldn't set up Deno ({exc}); trying YouTube without it.")
            self.progress(0)

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
            **ytdlp_js_options(),
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except DownloadError as exc:
            raise self._download_error(exc) from exc

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

    def download_audio(self, url: str, work_dir: Path) -> Path:
        """Download the best audio stream as it is (it's decoded straight into memory later)."""
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
                self.log("Download finished.")

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
            "concurrent_fragment_downloads": 4,  # streams split into fragments download 4 at a time
            "logger": _YtDlpLogger(self.log),
            "progress_hooks": [on_progress],
            **ytdlp_js_options(),
        }

        self.log("Downloading audio...")
        self.stage("download", "Downloading audio")
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
        except (DownloadError, Cancelled) as exc:
            self.check_cancel()
            raise self._download_error(exc) from exc

        files = [f for f in work_dir.glob("source.*") if f.suffix not in (".part", ".ytdl") and f.is_file()]
        if not files:
            raise PipelineError("The download finished but no audio file was produced.")
        audio = max(files, key=lambda f: f.stat().st_size)
        self.log(f"Audio downloaded ({audio.stat().st_size / 1_048_576:.1f} MB).")
        return audio

    @staticmethod
    def _download_error(exc: Exception) -> PipelineError:
        """Map yt-dlp's error text to a short, actionable message."""
        text = str(exc)
        lowered = text.lower()
        if "unsupported url" in lowered or "is not a valid url" in lowered:
            return PipelineError("That link isn't a supported YouTube video URL.")
        if "video unavailable" in lowered or "private video" in lowered:
            return PipelineError("The video is unavailable or private.")
        if "sign in to confirm your age" in lowered:
            return PipelineError("This video is age-restricted and can't be downloaded without signing in.")
        if any(s in lowered for s in ("getaddrinfo", "name resolution", "timed out",
                                      "connection", "network is unreachable", "urlopen error")):
            return PipelineError("Network error: check your internet connection and try again.")
        if "ffmpeg" in lowered or "ffprobe" in lowered:
            return PipelineError("FFmpeg failed while converting the audio. Check your FFmpeg install.")
        return YouTubeChanged(
            f"YouTube download failed: {text.removeprefix('ERROR: ')}\n\nYouTube changes its site often, "
            "which can break downloads until yt-dlp (the YouTube downloader) releases a fix. ORemove "
            "installs yt-dlp updates automatically, so try again later, or use an audio file instead "
            "(your own files always work).")

    def decode(self, path: Path, name: str):
        """Decode an audio (or video) file straight into memory: float32 stereo at 44.1 kHz."""
        import oremove_engine as eng
        try:
            return eng.decode(path, cancel=self.check_cancel, creationflags=NO_WINDOW)
        except Cancelled:
            raise
        except FileNotFoundError as exc:
            raise PipelineError("FFmpeg was not found. Reinstall ORemove.") from exc
        except Exception as exc:
            raise PipelineError(f"Couldn't read {name}: {exc}") from exc

    # ---- Step 2: separation -----------------------------------------------

    def separate(self, song: dict, job: Job, stems_dir: Path) -> list[tuple[str, object]]:
        """
        Separate one song. Returns [(stem name, audio)] in saving order, where
        audio is a (samples, 2) array (Roformer models, run by oremove_engine)
        or a file in `stems_dir` (MDX-Net, run by audio-separator from a WAV).
        """
        separator = self._load_separator(job, stems_dir)
        roformer = getattr(separator.model_instance, "is_roformer", False)

        self.log("Separating stems...")
        self.stage("separate", f"Separating stems on {self.run_device}" if self.run_device else "Separating stems")
        self.progress(None)  # until the first chunk is done
        started = time.monotonic()
        reported = {"fraction": 0.0, "logged": -10}

        device_text = f" on {self.run_device}" if self.run_device else ""

        def on_progress(kind, done, total):
            if kind != "separate":
                return
            self.stage("separate", f"Separating stems{device_text} · chunk {min(done, total)} of {total}")
            fraction = min(1.0, done / total)
            if fraction < reported["fraction"] + 0.002 and fraction < 1.0:
                return  # only report visible changes
            reported["fraction"] = max(fraction, reported["fraction"])
            self.progress(MODEL_LOAD_AT + reported["fraction"] * (SEPARATED_AT - MODEL_LOAD_AT))
            pct = int(reported["fraction"] * 100)
            if pct >= reported["logged"] + 10:
                reported["logged"] = pct
                self.log(f"  separating... {pct}% ({time.monotonic() - started:.0f}s)")

        def attempt(separator):
            if roformer:
                return self._separate_roformer(separator, song, on_progress)
            return self._separate_file(separator, song, stems_dir, on_progress)

        _reset_gpu_memory_peak()
        try:
            stems = attempt(separator)
        except Cancelled:
            raise
        except Exception as exc:
            if "out of memory" not in str(exc).lower() or _cache["safe"]:
                raise PipelineError(f"Separation failed: {exc}") from exc
            # The GPU ran out of memory (another app may be using it, or the
            # song is very long): free everything and retry once with the
            # lower-memory settings, which keep 16-bit (half the memory of
            # full precision) but separate one chunk at a time.
            self.log("GPU ran out of memory; freeing memory and retrying with lower-memory settings...")
            _release_separator()
            separator = self._load_separator(job, stems_dir, safe=True)
            reported.update(fraction=0.0, logged=-10)
            try:
                stems = attempt(separator)
            except Cancelled:
                raise
            except Exception as exc2:
                if "out of memory" in str(exc2).lower():
                    raise PipelineError(
                        "Your GPU ran out of memory, even with lower-memory settings. Close other apps that "
                        "use the GPU (games, video editors, browsers with many tabs) and try again, or use "
                        "MDX-Net Inst HQ 3, which needs much less memory.") from exc2
                raise PipelineError(f"Separation failed: {exc2}") from exc2
        finally:
            _free_gpu_cache()
        elapsed = time.monotonic() - started
        self.add_time("separate", elapsed)
        self.log(f"Separated in {elapsed:.1f}s")
        peak = _gpu_memory_peak_mb()
        if peak is not None:
            self.gpu_memory_mb = max(self.gpu_memory_mb, peak)
            self.log(f"GPU memory used while separating: {peak:.0f} MB")
            if peak < 100:
                self.log("Warning: the GPU was barely used during separation; it may have run on the CPU.")
        if not stems:
            raise PipelineError("The model ran but produced no output files.")
        self.progress(SEPARATED_AT)
        return stems

    def _separate_roformer(self, separator, song: dict, on_progress) -> list[tuple[str, object]]:
        import oremove_engine as eng
        instance = separator.model_instance
        batch = 1 if _cache["safe"] else _chunk_batch()
        run = eng.Separation(instance, batch=batch, real_spectral=_real_spectral(instance),
                             progress=lambda done, total: on_progress("separate", done, total),
                             cancel=self.check_cancel, log=self.log)
        if _cache.get("logged_engine") != _cache["key"]:
            _cache["logged_engine"] = _cache["key"]
            details = [f"{batch} chunk{'s' if batch > 1 else ''} at a time"]
            if run.real_spectral:
                details.append("spectral maths on the GPU without complex numbers")
            self.log("Engine: " + ", ".join(details))
        stems = run.run(song["audio"])
        if run.real_failed:
            _engine_profile["spectral"] = "native"  # for this session; the speed test can measure again
        if run.batch < batch:
            _cache["batch_limit"] = run.batch  # remember: this GPU has less room than expected
        return stems

    def _separate_file(self, separator, song: dict, stems_dir: Path, on_progress) -> list[tuple[str, object]]:
        """MDX-Net: audio-separator reads a WAV and writes the stems itself."""
        import soundfile as sf
        wav = stems_dir.parent / f"input{song['index']}.wav"
        if not wav.exists():
            sf.write(str(wav), song["audio"].T, 44100, subtype=song["subtype"])
        global _progress_cancel
        _install_progress_hooks()
        _set_progress_sink(on_progress)
        _progress_cancel = self.check_cancel
        try:
            produced = _run_separator(separator, wav)
        finally:
            _set_progress_sink(None)
            _progress_cancel = None
        # audio-separator returns file names relative to output_dir.
        paths = [p if p.is_absolute() else stems_dir / p for p in map(Path, produced)]
        stems = []
        for path in paths:
            if path.exists():
                label = re.search(r"_\(([^)]+)\)_", path.name)
                stems.append((label.group(1) if label else "Stem", path))
        return stems

    def _load_separator(self, job: Job, stems_dir: Path, safe: bool = False):
        """
        Return a separator with the job's model loaded. The loaded model is kept
        between runs (and across a batch) and only reloaded when the model,
        format or quality setting changes.
        """
        with _engine_lock:  # a warm-up may be loading the same model right now
            return self._load_separator_locked(job, stems_dir, safe)

    def _load_separator_locked(self, job: Job, stems_dir: Path, safe: bool):
        engine_started = time.monotonic()
        try:
            from audio_separator.separator import Separator
            import torch
        except ImportError as exc:
            raise PipelineError(
                "audio-separator is not installed correctly. "
                "Run: pip install -r requirements.txt"
            ) from exc
        self.add_time("engine", time.monotonic() - engine_started)

        cuda = torch.cuda.is_available()
        mps = bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())
        safe = safe or _cache["safe"]
        gpu_settings = (cuda or force_gpu_settings) and not safe
        if cuda:
            # Pick the fastest convolution algorithms for these (fixed) chunk
            # shapes; same results, chosen once per shape.
            torch.backends.cudnn.benchmark = True
        roformer = "roformer" in job.model_file.lower()
        # 16-bit stays on in low-memory mode: it halves the model's memory use.
        fp16 = (cuda or mps) and roformer and _engine_profile["fp16"] is not False
        compile_blocks = mps and not safe and roformer and bool(_engine_profile["compile"])
        # Roformer stems are written by ORemove, so the format doesn't need a reload.
        key = (job.model_file, None if roformer else job.output_format, job.faster, safe, gpu_settings, fp16,
               compile_blocks)
        if _cache["key"] == key:
            self.run_device = _cache.get("device", "")
            self._engine_cb(self.run_device)
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

        options = dict(
            # Half precision on GPUs: native float16 for BS-Roformer (verified by
            # audio-separator on NVIDIA and Apple GPUs), autocast for MDX-Net on NVIDIA.
            # The speed test may turn either of these choices around (see _engine_profile).
            use_native_fp16=fp16,
            use_torch_compile=compile_blocks,
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
            ("compiled (torch.compile)", compile_blocks),
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
            # WAV stems are written directly (keeping the source's bit depth),
            # without starting FFmpeg for each one; MP3 needs FFmpeg's encoder.
            use_soundfile=job.output_format == "WAV",
            **options,
        )

        model_cached = (MODEL_DIR / job.model_file).exists()
        self.log(f"Loading UVR model: {job.model_file}"
                 + ("" if model_cached else "  (first use: downloading model, please wait)"))
        self.stage("separate", "Loading model" if model_cached
                   else "Downloading model (first use only)")
        shown = {"pct": -1}

        def on_download(kind, done, total):
            # The first use of a model downloads it (610 MB for BS-Roformer).
            if kind != "download":
                return
            pct = min(100, int(done * 100 / total))
            if pct == shown["pct"]:
                return
            shown["pct"] = pct
            if self.on_model_download:
                self.on_model_download(pct, total)
            self.progress(DOWNLOAD_SHARE + (MODEL_LOAD_AT - DOWNLOAD_SHARE) * pct / 100)
            self.stage("separate", f"Downloading model, first use only ({pct}% of {total / 1_048_576:.0f} MB)")
            if pct % 10 == 0:
                self.log(f"  downloading model... {pct}%")

        _install_progress_hooks()
        _set_progress_sink(on_download)
        tune_cpu(self.log)
        load_started = time.monotonic()
        try:
            with _FastModelLoading():
                separator.load_model(model_filename=job.model_file)
        except Exception as exc:
            raise PipelineError(f"Could not load model '{job.model_file}': {exc}") from exc
        finally:
            _set_progress_sink(None)
            self.add_time("model_load" if model_cached else "model_download", time.monotonic() - load_started)
        self.progress(MODEL_LOAD_AT)
        _cache.update(key=key, separator=separator, safe=safe, device=_describe_device(separator))
        self.run_device = _cache["device"]
        self.log(f"Model running on: {self.run_device}" + (f" (loaded in {time.monotonic() - load_started:.1f}s)"
                                                           if model_cached else ""))
        if mps and roformer:
            device = getattr(separator.model_instance, "torch_device", None)
            on_gpu = _complex_ops_on_gpu(device) if device is not None else False
            self.log("Apple GPU: complex-number maths " + ("supported" if on_gpu else "not supported, using real-number maths")
                     + f"; spectral maths: {'real' if _real_spectral(separator.model_instance) else 'native'}")
        self._engine_cb(self.run_device)
        if self.run_device == "the CPU":
            self.log("Running on the CPU is much slower than a GPU: a full song can take 15 minutes or more with BS-Roformer.")
        return separator

    # ---- Step 3: save the stems where they belong --------------------------

    @staticmethod
    def stem_target(stem_name: str, base_name: str, suffix: str, job: Job) -> Path:
        """acapellas/<song> (Acapella).wav, instrumentals/<song> (Instrumental).wav, or <song> (<stem>).wav."""
        label = stem_name.lower().replace("_", " ")
        # Check instrumental first: "no vocals" also contains "vocals".
        # Roformer models call the instrumental stem "other".
        if label in ("instrumental", "other", "no vocals") or "no vocals" in label:
            folder = job.output_dir / "instrumentals"
            target = folder / f"{base_name} (Instrumental){suffix}"
        elif label == "vocals":
            folder = job.output_dir / "acapellas"
            target = folder / f"{base_name} (Acapella){suffix}"
        else:
            folder = job.output_dir
            target = folder / f"{base_name} ({stem_name}){suffix}"
        folder.mkdir(parents=True, exist_ok=True)
        return unique_path(target)

    def save_stems(self, stems: list[tuple[str, object]], song: dict, job: Job) -> list[Path]:
        """
        Write each stem straight into the output folder (both at once). MDX-Net
        stems, already written by audio-separator, are copied there instead.
        """
        self.stage("save", "Saving stems")
        suffix = "." + job.output_format.lower()
        try:
            targets = [self.stem_target(name, song["base_name"], suffix, job) for name, _ in stems]
        except OSError as exc:
            raise PipelineError(f"Couldn't create the folder to save the stems in: {exc}") from exc
        errors = []

        def write(audio, target):
            try:
                if isinstance(audio, Path):
                    # Copy, don't move: the temp folder is private to the
                    # current user, and a moved file keeps that restrictive
                    # permission (apps like Windows Media Player then get
                    # "permission denied"). A copy gets the folder's normal ones.
                    shutil.copyfile(audio, target)
                else:
                    write_audio(audio, target, job.output_format, song["subtype"])
            except Exception as exc:
                target.unlink(missing_ok=True)
                errors.append(exc)

        threads = [threading.Thread(target=write, args=(audio, target), daemon=True)
                   for (_name, audio), target in zip(stems, targets)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if errors:
            for target in targets:
                target.unlink(missing_ok=True)
            raise PipelineError(f"Couldn't save the stems: {errors[0]}")
        return targets

    # ---- Whole job --------------------------------------------------------

    def run(self, job: Job) -> tuple[list[dict], list[str]]:
        """
        Process every source in `job`. Returns (songs, failure messages), where
        each song is {"title", "stems": [saved stem paths], "analysis"}. A
        single source raises on failure; in a batch, a failed item is skipped
        and reported, and the rest still run. Stop raises Cancelled.

        Three steps run side by side, each in its own thread, so in a batch
        the GPU never waits: getting song 2's audio (download and decode)
        happens while song 1 separates, and song 1 is saved (and its BPM and
        key detected) while song 2 separates. The model also loads while the
        first song downloads.
        """
        if not ffmpeg_available():
            raise PipelineError(
                "FFmpeg was not found on your PATH. Install FFmpeg (see README) "
                "and restart the app."
            )
        self._count = len(job.sources)
        results: list[dict | None] = [None] * self._count
        failures: list[tuple[int, str]] = []
        roformer = "roformer" in job.model_file.lower()

        def fail(i: int, exc: Exception):
            source = job.sources[i]
            name = source if looks_like_url(source) else Path(source).name
            failures.append((i, f"{name}: {exc}"))
            self.log(f"Skipped {name}: {exc}")

        # Everything temporary lives here and is deleted at the end, even on errors.
        with tempfile.TemporaryDirectory(prefix="oremove_", ignore_cleanup_errors=True) as tmp:
            work_dir = Path(tmp)
            stems_dir = work_dir / "stems"
            stems_dir.mkdir()
            prepared: queue.Queue = queue.Queue(maxsize=1)   # songs whose audio is ready
            to_save: queue.Queue = queue.Queue(maxsize=1)    # separated songs waiting to be saved
            stop = threading.Event()                          # an error ends the whole run

            def put(q, item):
                while not stop.is_set():
                    try:
                        q.put(item, timeout=0.2)
                        return
                    except queue.Full:
                        pass

            def get(q):
                while True:
                    try:
                        return q.get(timeout=0.2)
                    except queue.Empty:
                        if stop.is_set():
                            return None

            def preparer():
                for i, source in enumerate(job.sources):
                    if stop.is_set():
                        return
                    self._local.item = i
                    try:
                        item = self._prepare(source, i, job, work_dir)
                        if self._warming:
                            self.stage("separate", "Loading the AI model")
                            self.progress(None)
                    except BaseException as exc:  # handed to the GPU thread, which decides
                        item = exc
                    put(prepared, (i, item))
                put(prepared, None)

            def saver():
                while True:
                    entry = get(to_save)
                    if entry is None:
                        return
                    i, song, stems = entry
                    self._local.item = i
                    try:
                        results[i] = self._finish(song, stems, job)
                    except Cancelled:
                        stop.set()
                        return
                    except PipelineError as exc:
                        if self._count == 1:
                            failures.append((i, str(exc)))
                            stop.set()
                            return
                        fail(i, exc)
                    except Exception as exc:
                        traceback.print_exc()
                        fail(i, PipelineError(f"Couldn't save the stems: {exc}"))
                    finally:
                        for _name, audio in stems:
                            if isinstance(audio, Path):
                                audio.unlink(missing_ok=True)

            threads = [threading.Thread(target=preparer, daemon=True, name="oremove-prepare"),
                       threading.Thread(target=saver, daemon=True, name="oremove-save")]
            for t in threads:
                t.start()
            error: BaseException | None = None
            try:
                self._warm_engine(job, stems_dir)
                expected = 0
                while True:
                    self._show_item(expected)
                    entry = get(prepared)
                    if entry is None:
                        break
                    i, song = entry
                    self._local.item = i
                    expected = i + 1
                    if isinstance(song, BaseException):
                        if isinstance(song, PipelineError) and self._count > 1:
                            fail(i, song)
                            continue
                        raise song
                    self.show_title(song["title"])
                    if i > 0:
                        self.overlapped = True
                    self.check_cancel()
                    try:
                        stems = self.separate(song, job, stems_dir)
                    except PipelineError as exc:
                        if self._count == 1:
                            raise
                        fail(i, exc)
                        continue
                    finally:
                        if not roformer:
                            (work_dir / f"input{i}.wav").unlink(missing_ok=True)
                    put(to_save, (i, song, stems))
                    if stop.is_set():
                        break
                # The GPU is done: whatever is left (saving the last song) may report.
                self._show_item(None)
                put(to_save, None)
                threads[1].join()
            except BaseException as exc:
                error = exc
            finally:
                stop.set()
                for t in threads:
                    t.join(timeout=30)
            if error is not None:
                raise error
            self.check_cancel()
            if self._count == 1 and failures:
                raise PipelineError(failures[0][1])
            self.log("Cleaning up temporary files...")

        saved = [r for r in results if r is not None]
        messages = [m for _i, m in sorted(failures)]
        if messages and not saved:
            raise PipelineError(f"None of the files could be processed. {messages[0]}")
        return saved, messages

    def _warm_engine(self, job: Job, stems_dir: Path):
        """Load the model now, while the first song is still downloading."""
        # Loading a cached model is quick and the download is what's worth
        # watching; a first-use model download is shown instead.
        self._local.item = 0 if not (MODEL_DIR / job.model_file).exists() else -1
        self._warming = True
        try:
            self._load_separator(job, stems_dir)
        except PipelineError:
            pass  # reported again (with the song) when separating
        finally:
            self._warming = False

    def _prepare(self, source: str, i: int, job: Job, work_dir: Path) -> dict:
        """Get one source's audio into memory (download it first if it's a link)."""
        self.check_cancel()
        started = time.monotonic()
        if looks_like_url(source):
            if not is_youtube_url(source):
                raise PipelineError("Only YouTube links are supported (youtube.com or youtu.be).")
            download_dir = work_dir / f"download{i}"
            download_dir.mkdir()
            title, path = self.download_youtube(source, download_dir)
            subtype = "PCM_16"  # YouTube audio is lossy: 16-bit WAVs lose nothing
            self.stage("download", "Decoding audio")
            self.progress(None)
            try:
                audio = self.decode(path, "the downloaded audio")
            finally:
                shutil.rmtree(download_dir, ignore_errors=True)
        else:
            src = Path(source)
            title = src.stem
            self.show_title(title)
            self.log(f"Reading {src}")
            self.stage("download", f"Reading {src.name}")
            self.progress(None)
            if not src.is_file():
                raise PipelineError(f"File not found: {src}")
            subtype = "PCM_24"  # keeps the full quality of 24-bit WAV and FLAC sources
            audio = self.decode(src, src.name)
        self.progress(DOWNLOAD_SHARE)
        self.add_time("audio", time.monotonic() - started)
        self.log(f"Audio ready: {audio.shape[1] / 44100 / 60:.0f}:{audio.shape[1] / 44100 % 60:02.0f}")
        return {"index": i, "title": title, "base_name": sanitize_filename(title), "audio": audio, "subtype": subtype}

    def _finish(self, song: dict, stems: list[tuple[str, object]], job: Job) -> dict:
        """Save one song's stems and detect its BPM and key."""
        self.check_cancel()
        save_started = time.monotonic()
        saved = self.save_stems(stems, song, job)
        self.add_time("save", time.monotonic() - save_started)
        instrumental = None
        for (name, audio), path in zip(stems, saved):
            if stem_kind(path) == "Instrumental" and not isinstance(audio, Path):
                instrumental = audio
        song.pop("audio", None)  # free the song's audio
        analysis = self.analyse(saved, instrumental) if job.analyse else None
        return {"title": song["title"], "stems": saved, "analysis": analysis}

    # ---- Step 4: BPM and key ------------------------------------------------

    def analyse(self, saved: list[Path], instrumental=None) -> dict | None:
        """Detect BPM and key from the instrumental (in memory, or the saved file). Never fails the song."""
        source = next((p for p in saved if stem_kind(p) == "Instrumental"), saved[0] if saved else None)
        if source is None:
            return None
        started = time.monotonic()
        try:
            import oremove_analysis as oa
            with _tracker_lock:
                tracker = _beat_tracker()
                if not tracker.installed():
                    self.log(f"Downloading the beat tracker (one time only, {oa.CHECKPOINT_MB} MB)...")
                    shown = {"pct": -1}

                    def on_progress(done, total):
                        self.check_cancel()
                        pct = int(done * 100 / total) if total else 0
                        if pct != shown["pct"]:
                            shown["pct"] = pct
                            self.stage("save", f"Downloading the BPM detector, one time only ({pct}%)")

                    try:
                        tracker.install(lambda url, timeout, cb: _download(url, timeout=timeout, on_progress=cb),
                                        on_progress)
                    except Cancelled:
                        raise
                    except Exception as exc:
                        self.log(f"Couldn't download the beat tracker ({exc}); detecting the key only.")
                        tracker = None
            self.stage("save", "Detecting BPM and key")
            self.progress(None)
            result = oa.analyse(source, tracker, cancel=self.check_cancel, log=self.log,
                                audio=instrumental, audio_rate=44100)
        except Cancelled:
            raise
        except Exception as exc:
            self.log(f"BPM and key detection didn't work: {exc}")
            return None
        bpm, key = result.get("bpm"), result.get("key")
        parts = []
        if bpm:
            parts.append(f"{bpm['bpm']} BPM" + (f" (or {bpm['alternative']})" if bpm.get("alternative") else ""))
        if key:
            parts.append(f"{key['name']} ({key['camelot']})" + (f", maybe {key['alternative']['name']}"
                                                                if key["confidence"] < 0.35 else ""))
        self.log(("Detected: " + ", ".join(parts) if parts else "BPM and key: not detected")
                 + f" in {time.monotonic() - started:.1f}s")
        self.add_time("analyse", time.monotonic() - started)
        return result


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


def stem_kind(path: Path) -> str:
    """ "Acapella", "Instrumental" or "Stem", from the name ORemove gave the file (or its folder, for old files)."""
    stem = path.stem
    for kind in ("Acapella", "Instrumental"):
        if re.search(rf"\({kind}\)(?: \(\d+\))?$", stem):
            return kind
    return STEM_KINDS.get(path.parent.name, "Stem")


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
        self.token = uuid.uuid4().hex   # required for /events
        self.events_source = None       # Api.wait_events, set by the Api
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass  # keep quiet

            def do_GET(self):
                route, _, query = unquote(self.path).partition("?")
                if route == "/events":
                    # Long poll: the page waits here for the next progress events,
                    # which costs nothing while idle (unlike calling Python often).
                    if f"t={server.token}" not in query or not server.events_source:
                        self.send_error(403)
                        return
                    body = json.dumps(server.events_source()).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    try:
                        self.wfile.write(body)
                    except (ConnectionError, OSError):
                        pass
                    return
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

        class Server(ThreadingHTTPServer):
            def server_bind(self):
                # The standard version also looks up the computer's network
                # name, which on macOS triggers a "find devices on local
                # networks" permission prompt. Loopback needs no name.
                socketserver.TCPServer.server_bind(self)
                self.server_name, self.server_port = "127.0.0.1", self.server_address[1]

        self.httpd = Server(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/index.html?t={self.token}"

    def audio_url(self, path: Path) -> str:
        token = uuid.uuid4().hex
        self.files[token] = path
        return f"http://127.0.0.1:{self.port}/audio/{token}{path.suffix.lower()}"


# --------------------------------------------------------------------------- #
# Bridge between the interface (ui/app.js) and the engine
# --------------------------------------------------------------------------- #

def _badge_device(device: str) -> str:
    """'RTX 4070 SUPER' for the results badge; '' for the CPU."""
    return {"the Apple GPU": "the Apple GPU", "the CPU": "", "": ""}.get(device, device)


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
        server.events_source = self.wait_events
        self._events: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._cancel = threading.Event()
        self._device = ""
        self._idle_timer: threading.Timer | None = None
        self._warming = False
        # Underscored, so pywebview doesn't expose them to the page: it walks
        # every public attribute, and the native window's properties nest
        # endlessly, which can stall the window on Windows.
        self._window = None  # set by run_window() once the window exists
        self._ready = threading.Event()
        settings = load_settings()
        model = settings.get("model")
        self._settings = {
            "model": model if model in MODELS_BY_FILE else MODELS[0].filename,
            "quality": settings.get("quality") if settings.get("quality") in QUALITY_MODES else QUALITY_MODES[0],
            "format": settings.get("format") if settings.get("format") in OUTPUT_FORMATS else OUTPUT_FORMATS[0],
            "output_dir": settings.get("output_dir") or str(DEFAULT_OUTPUT_DIR),
            "ytdlp_auto": settings.get("ytdlp_auto") is not False,
            "analyse": settings.get("analyse") is not False,
        }
        _ytdlp["auto"] = self._settings["ytdlp_auto"]
        self._installing: str | None = None  # model being installed from the welcome card
        self._speed_testing = False
        self._dragging_out: set[str] = set()  # a stem being dragged out of the window
        self._drag_active = False
        self._drag_ignore_until = 0.0
        saved = settings.get("engine_profile") or {}
        _engine_profile.update(fp16=saved.get("fp16"), compile=saved.get("compile"), batch=saved.get("batch"),
                               spectral=saved.get("spectral"), threads=saved.get("threads"))

    # Events -----------------------------------------------------------------

    def emit(self, event_type: str, **data):
        self._events.put({"type": event_type, **data})
        if event_type == "log":
            write_log_file(str(data.get("text", "")))
        elif event_type == "error":
            write_log_file("ERROR: " + str(data.get("message", "")))

    def wait_events(self, timeout: float = 20.0) -> list[dict]:
        """Wait until there's at least one event (or the timeout), then return them all."""
        try:
            first = self._events.get(timeout=timeout)
        except queue.Empty:
            return []
        return [first] + self.poll()

    def poll(self) -> list[dict]:
        events = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                return events

    def ready(self):
        """Called by the page once it has loaded."""
        self._ready.set()

    def detect_device_quickly(self):
        """
        Show what ORemove will run on, without loading the AI engine (which
        takes time and memory; it loads when a job starts).
        """
        if MACOS:
            import platform
            if platform.machine() == "arm64":
                self._device = "Apple GPU"
                self.emit("device", kind="gpu", name="Apple GPU")
            else:
                self._device = "CPU"
                self.emit("device", kind="cpu", name="CPU only")
        elif nvidia_edition_files():
            info = nvidia_gpu_info()
            if info:
                self._device = info[0]
                self.emit("device", kind="gpu", name=info[0], detail=f"NVIDIA driver {info[1]}")
                self.emit("log", text=f"NVIDIA GPU: {info[0]} (driver {info[1]})")
            else:
                self._device = "CPU"
                detail = ("No NVIDIA GPU found (none installed, or no NVIDIA driver). This edition only "
                          "accelerates NVIDIA GPUs, so it will run on the CPU, which is much slower.")
                self.emit("device", kind="nonvidia", name="No NVIDIA GPU", detail=detail)
                self.emit("log", text=detail)
        else:
            self._device = "CPU"
            self.emit("device", kind="cpu", name="CPU only")

    def on_engine(self, device: str):
        """The AI engine has loaded a model: confirm (or correct) the device badge."""
        if not device:
            return
        if device == "the CPU" and nvidia_edition_files():
            diagnosis = cuda_diagnosis()
            self.emit("log", text=f"The NVIDIA GPU can't be used: {diagnosis}")
            self.emit("device", kind="nonvidia", name="GPU not used",
                      detail=f"ORemove couldn't use your NVIDIA GPU, so it's running on the CPU (much slower). {diagnosis}")
            self._device = "CPU"
        elif device == "the Apple GPU":
            self.emit("device", kind="gpu", name="Apple GPU")
        elif device != "the CPU":
            self._device = device
            self.emit("device", kind="gpu", name=device)

    def warm_up(self):
        """
        The user has started choosing a song: load the AI engine and the chosen
        model in the background, so separation can begin straight away. Only
        for models already downloaded (never starts a large download by itself).
        """
        if self._warming or self._installing or (self._worker and self._worker.is_alive()):
            return
        s = self._settings
        job = Job(sources=[], model_file=s["model"], output_format=s["format"],
                  output_dir=Path(s["output_dir"]), faster=s["quality"] == "Faster")
        if not (MODEL_DIR / job.model_file).exists():
            return
        self._warming = True

        def load():
            try:
                pipeline = ExtractionPipeline(log=lambda text: self.emit("log", text=text),
                                              progress=lambda _v: None, engine=self.on_engine)
                warm_dir = Path(tempfile.gettempdir()) / "oremove_warmup"
                warm_dir.mkdir(exist_ok=True)
                pipeline._load_separator(job, warm_dir)
                self._schedule_idle_release()
            except Exception as exc:
                self.emit("log", text=f"Warm-up skipped: {exc}")
            finally:
                self._warming = False

        threading.Thread(target=load, daemon=True).start()

    def _schedule_idle_release(self):
        """Free the loaded model (RAM and GPU memory) after 10 idle minutes."""
        if self._idle_timer:
            self._idle_timer.cancel()

        def release():
            if not (self._worker and self._worker.is_alive()) and _cache["key"] is not None:
                _release_separator()
                self.emit("log", text="Freed the AI model from memory after 10 minutes idle (it reloads on the next song).")

        self._idle_timer = threading.Timer(600, release)
        self._idle_timer.daemon = True
        self._idle_timer.start()

    # Settings -----------------------------------------------------------------

    def models(self) -> list[dict]:
        return [{"name": m.name, "best_for": m.best_for, "filename": m.filename, "size": m.size,
                 "installed": model_installed(m.filename), "bundled": m.size == "Included"}
                for m in MODELS]

    def get_state(self) -> dict:
        settings = load_settings()
        return {
            "version": APP_VERSION,
            "models": self.models(),
            "settings": dict(self._settings),
            "ffmpeg": ffmpeg_available(),
            "ytdlp": ytdlp_version(),
            "platform": "win" if sys.platform.startswith("win") else "mac" if MACOS else "other",
            "welcome": not settings.get("welcomed"),
            "installing": self._installing,
            "engine_profile": settings.get("engine_profile"),
            "log_file": str(LOG_FILE),
        }

    # First run, models and updates ---------------------------------------------

    def welcome_done(self):
        save_settings({**load_settings(), **self._settings, "welcomed": True})

    def install_model(self, filename: str) -> dict:
        """Download a model now (from the welcome card), and keep it loaded for the first song."""
        if filename not in MODELS_BY_FILE:
            return {"ok": False, "error": "Unknown model."}
        if self._installing or (self._worker and self._worker.is_alive()):
            return {"ok": False, "error": "Busy, try again in a moment."}
        self._installing = filename
        s = self._settings
        job = Job(sources=[], model_file=filename, output_format=s["format"],
                  output_dir=Path(s["output_dir"]), faster=s["quality"] == "Faster")

        def run():
            try:
                pipeline = ExtractionPipeline(log=lambda text: self.emit("log", text=text),
                                              progress=lambda _v: None, engine=self.on_engine)
                pipeline.on_model_download = lambda pct, total: self.emit(
                    "install", model=filename, percent=pct, mb=round(total / 1_048_576))
                work_dir = Path(tempfile.gettempdir()) / "oremove_warmup"
                work_dir.mkdir(exist_ok=True)
                pipeline._load_separator(job, work_dir)
                self.emit("installed", model=filename, models=self.models())
                self._schedule_idle_release()
            except Exception as exc:
                self.emit("install_error", model=filename, message=str(exc))
            finally:
                self._installing = None

        threading.Thread(target=run, daemon=True).start()
        return {"ok": True}

    def check_app_update(self):
        """At most once a day: tell the page when a newer ORemove is available."""
        def check():
            try:
                update = check_app_update()
            except Exception as exc:
                self.emit("log", text=f"Couldn't check for a new ORemove version: {exc}")
                return
            if update and load_settings().get("skip_update") != update["version"]:
                self.emit("update", **update)
                self.emit("log", text=f"ORemove {update['version']} is available.")

        threading.Thread(target=check, daemon=True).start()

    def speed_test(self) -> dict:
        """
        Find the fastest way to run the Roformer models on this computer's GPU:
        separate a short test clip with each candidate setting (twice, timing
        the second run so one-off start-up costs don't count) and keep the
        fastest. Takes a few minutes.
        """
        if self._speed_testing or self._installing or (self._worker and self._worker.is_alive()):
            return {"ok": False, "error": "Busy, try again when the current job has finished."}
        model = next((m.filename for m in MODELS if m.filename == self._settings["model"] and "roformer" in m.filename.lower()
                      and model_installed(m.filename)), None) \
            or next((m.filename for m in MODELS if "roformer" in m.filename.lower() and model_installed(m.filename)), None)
        if not model:
            return {"ok": False, "error": "Install a Roformer model first (separate a song with one, or use Install now)."}
        self._speed_testing = True

        def run():
            log = lambda text: self.emit("log", text=text)
            saved_profile = dict(_engine_profile)
            results = []
            try:
                import torch
                import oremove_engine as eng
                cuda = torch.cuda.is_available()
                mps = bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())
                logical, physical = os.cpu_count() or 1, physical_cores()
                if not (cuda or mps) and not (physical and physical != logical):
                    self.emit("speedtest", done=True,
                              summary="This computer runs ORemove on the CPU, already set up for its cores: there's nothing to tune.")
                    return
                # A setting: fp16, compile, batch, spectral, threads. Each round tries
                # variations of the best so far, so the number of runs stays small.
                base = {"fp16": True, "compile": False, "batch": 1, "spectral": None, "threads": None}
                if mps:
                    rounds = [[{"fp16": True}, {"fp16": False}, {"fp16": True, "compile": True}, {"fp16": False, "compile": True}],
                              ["spectral"], [{"batch": 2}]]
                elif cuda:
                    big = torch.cuda.get_device_properties(0).total_memory >= 12 * 1024 ** 3
                    rounds = [[{"fp16": True}, {"fp16": False}], [{"batch": 2}] + ([{"batch": 4}] if big else [])]
                else:
                    base.update(fp16=False)
                    rounds = [[{"threads": physical}, {"threads": logical}]]
                seconds_long = 11 if not (cuda or mps) else 20  # 10 s or more: the normal chunk size

                def describe(c):
                    parts = [("16-bit" if c["fp16"] else "full precision")] if (cuda or mps) else []
                    if c["compile"]:
                        parts.append("compiled")
                    if c["spectral"]:
                        parts.append("real-number spectral maths" if c["spectral"] == "real" else "complex spectral maths")
                    if c["batch"] > 1:
                        parts.append(f"{c['batch']} chunks at a time")
                    if c["threads"]:
                        parts.append(f"{c['threads']} threads")
                    return ", ".join(parts)

                with tempfile.TemporaryDirectory(prefix="oremove_speed_") as tmp:
                    clip = Path(tmp) / "clip.wav"
                    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"anoisesrc=d={seconds_long}:c=pink:a=0.3",
                                    "-f", "lavfi", "-i", f"sine=f=220:d={seconds_long}", "-filter_complex", "amix=inputs=2",
                                    "-ac", "2", "-ar", "44100", str(clip)], check=True, creationflags=NO_WINDOW)
                    song = {"index": 0, "title": "clip", "base_name": "clip", "audio": eng.decode(clip, creationflags=NO_WINDOW),
                            "subtype": "PCM_16"}
                    job = Job(sources=[], model_file=model, output_format="WAV", output_dir=Path(tmp), faster=True)
                    total = sum(len(r) for r in rounds)
                    n = 0
                    best = None
                    for round_ in rounds:
                        if round_ == ["spectral"]:
                            # Try the spectral maths the default didn't use.
                            current = (best or base)["spectral"]
                            if current is None:
                                _engine_profile.update({k: v for k, v in (best or base).items() if k in _engine_profile})
                                _release_separator()
                                probe = ExtractionPipeline(log=lambda _t: None, progress=lambda _v: None)
                                instance = probe._load_separator(job, Path(tmp)).model_instance
                                current = "real" if _real_spectral(instance) else "native"
                                if best:
                                    best["spectral"] = current
                            round_ = [{"spectral": "native" if current == "real" else "real"}]
                        for change in round_:
                            n += 1
                            candidate = {**(best or base), **change}
                            name = describe(candidate)
                            self.emit("speedtest", step=n, total=total, status=f"Testing {name} ({n} of {total})…")
                            log(f"Speed test {n}/{total}: {name}")
                            _engine_profile.update({k: v for k, v in candidate.items() if k in _engine_profile})
                            _cache.pop("batch_limit", None)
                            _release_separator()
                            try:
                                if candidate["threads"]:
                                    torch.set_num_threads(int(candidate["threads"]))
                                pipeline = ExtractionPipeline(log=lambda _t: None, progress=lambda _v: None)
                                seconds = None
                                for attempt in ((1, 2) if (cuda or mps) else (1,)):
                                    out = Path(tmp) / f"out{n}_{attempt}"
                                    out.mkdir()
                                    started = time.monotonic()
                                    pipeline.separate(song, job, out)
                                    seconds = time.monotonic() - started
                                if _cache.get("batch_limit") and _cache["batch_limit"] < candidate["batch"]:
                                    raise RuntimeError("not enough GPU memory")
                                result = {**candidate, "setting": name, "seconds": round(seconds, 1)}
                                results.append(result)
                                log(f"  {name}: {seconds:.1f}s for a {seconds_long}-second clip")
                                if best is None or result["seconds"] < best["seconds"]:
                                    best = result
                            except Exception as exc:
                                log(f"  {name}: didn't work ({exc})")
                    _release_separator()
                if not results:
                    raise RuntimeError("no setting worked")
                default = results[0]
                profile = {k: best[k] for k in ("fp16", "compile", "batch", "spectral", "threads")}
                _engine_profile.update(profile)
                _cache.pop("batch_limit", None)
                if profile["threads"]:
                    torch.set_num_threads(int(profile["threads"]))
                save_settings({**load_settings(), **self._settings,
                               "engine_profile": {**profile, "setting": best["setting"], "model": model, "results": results,
                                                  "when": datetime.now().isoformat(timespec="minutes")}})
                gain = f", {default['seconds'] / best['seconds']:.1f}× faster than the default" \
                    if best is not default and best["seconds"] > 0 else ""
                summary = f"Fastest here: {best['setting']}{gain}. ORemove now uses it."
                log("Speed test done. " + summary)
                self.emit("speedtest", done=True, summary=summary, results=results)
            except Exception as exc:
                _engine_profile.update(saved_profile)
                log(f"Speed test failed: {exc}")
                self.emit("speedtest", done=True, summary=f"The speed test didn't finish: {exc}")
            finally:
                self._speed_testing = False

        threading.Thread(target=run, daemon=True).start()
        return {"ok": True}

    def open_logs(self):
        try:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            open_path(LOG_FILE.parent)
        except Exception as exc:
            self.emit("log", text=f"Could not open the log folder: {exc}")

    def dismiss_update(self, version: str):
        save_settings({**load_settings(), **self._settings, "skip_update": str(version)})

    def open_release(self, url: str = RELEASES_PAGE):
        import webbrowser
        webbrowser.open(url if str(url).startswith(f"https://github.com/{REPO}/") else RELEASES_PAGE)

    def drag_out(self, path: str) -> bool:
        """
        Start dragging a stem file out of the window, e.g. into a DAW
        (Windows; called while the mouse button is held down).
        """
        file = Path(path)
        if not (sys.platform.startswith("win") and self._window and file.is_file()):
            return False
        try:
            from System import Action, Array, String
            from System.Windows.Forms import DataFormats, DataObject, DragDropEffects
            form = self._window.native

            def start_drag():
                form.DoDragDrop(DataObject(DataFormats.FileDrop, Array[String]([str(file)])), DragDropEffects.Copy)

            self._dragging_out = {os.path.normcase(os.path.abspath(str(file)))}
            self._drag_active = True
            try:
                form.Invoke(Action(start_drag))  # returns when the mouse button is released
            finally:
                self._drag_active = False
                self._drag_ignore_until = time.monotonic() + 3  # the drop event can arrive just after
            return True
        except Exception as exc:
            self.emit("log", text=f"Dragging the file out didn't work: {exc}")
            return False

    def save_settings(self, change: dict) -> dict:
        allowed = {"model": MODELS_BY_FILE, "quality": QUALITY_MODES, "format": OUTPUT_FORMATS}
        for key, value in (change or {}).items():
            if key in allowed and value in allowed[key]:
                self._settings[key] = value
            elif key == "output_dir" and value:
                self._settings[key] = str(value)
            elif key == "analyse" and isinstance(value, bool):
                self._settings[key] = value
            elif key == "ytdlp_auto" and isinstance(value, bool):
                self._settings[key] = _ytdlp["auto"] = value
                if value:
                    self.check_ytdlp(force=False)
                else:
                    self.emit("ytdlp", version=bundled_ytdlp_version(), status="Automatic updates off")
        save_settings({**load_settings(), **self._settings})  # keep other saved values (welcome, input folder)
        return dict(self._settings)

    def check_ytdlp(self, force: bool = True):
        """Check for a yt-dlp update in the background (Settings > Check now, and at startup)."""
        def check():
            self.emit("ytdlp", version=ytdlp_version(), status="Checking for updates…")
            try:
                result = update_ytdlp(force=force, log=lambda text: self.emit("log", text=text))
                status = (f"Updated to {result['version']}" if result["updated"]
                          else "Up to date" if result["checked"] else "Checks for updates daily")
            except Exception as exc:
                self.emit("log", text=f"Couldn't check for a yt-dlp update: {exc}")
                status = "Couldn't check for updates (offline?)"
            self.emit("ytdlp", version=ytdlp_version(), status=status)

        threading.Thread(target=check, daemon=True).start()

    def choose_folder(self):
        result = self._window.create_file_dialog(_dialog("FOLDER"), directory=self._settings["output_dir"])
        return result[0] if result else None

    def browse_files(self) -> list[str]:
        patterns = ";".join(f"*.{e}" for e in AUDIO_EXTENSIONS)
        start = load_settings().get("input_dir") or str(Path.home() / "Music")
        result = self._window.create_file_dialog(
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
        # A stem dragged out of a results card and let go over ORemove itself
        # comes back as a drop: ignore it (it used to start separating the stem).
        if self._dragging_out and (time.monotonic() < self._drag_ignore_until or self._drag_active):
            files = [p for p in files if os.path.normcase(os.path.abspath(p)) not in self._dragging_out]
        if files:
            self.emit("dropped", paths=files)

    # Running a job -----------------------------------------------------------------

    def start(self, sources: list[str]) -> dict:
        if self._worker and self._worker.is_alive():
            return {"ok": False, "error": "Already working on something."}
        if self._speed_testing:
            return {"ok": False, "error": "The speed test is running; it takes a few minutes."}
        sources = [s.strip().strip('"').strip("'") for s in (sources or []) if s and s.strip()]
        if not sources:
            return {"ok": False, "error": "Paste a YouTube link, or browse for audio files."}
        if len(sources) == 1 and not looks_like_url(sources[0]) and not Path(sources[0]).is_file():
            return {"ok": False, "error": "That isn't a YouTube link or an audio file that exists."}
        s = self._settings
        job = Job(sources=sources, model_file=s["model"], output_format=s["format"],
                  output_dir=Path(s["output_dir"]), faster=s["quality"] == "Faster", analyse=s["analyse"])
        self._cancel.clear()
        if self._idle_timer:
            self._idle_timer.cancel()
        self.emit("log", text="-" * 50)
        self.emit("log", text=f"Starting {len(sources)} item(s) [{job.model_file}, {job.output_format}, {s['quality']}]")
        self._worker = threading.Thread(target=self._run, args=(job,), daemon=True)
        self._worker.start()
        return {"ok": True}

    def cancel(self):
        self._cancel.set()
        self.emit("log", text="Stopping...")

    def _run(self, job: Job):
        try:
            self._run_job(job)
        finally:
            self._schedule_idle_release()

    def _run_job(self, job: Job):
        pipeline = ExtractionPipeline(
            log=lambda text: self.emit("log", text=text),
            progress=lambda value: self.emit("progress", value=value),
            stage=lambda name, detail: self.emit("stage", name=name, detail=detail),
            title=lambda text: self.emit("title", text=text),
            cancel_event=self._cancel,
            engine=self.on_engine,
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
                    "stems": [str(p) for p in song["stems"]], "analysis": song.get("analysis")}
                   for song in songs]
        add_to_history(entries)
        for failure in failures:
            self.emit("log", text=f"FAILED: {failure}")
        self.emit("done", songs=[self._song_view(e) for e in entries], failures=failures,
                  seconds=round(pipeline.timings["separate"], 1), total=round(seconds, 1),
                  timings={k: round(v, 1) for k, v in pipeline.timings.items()},
                  gpu_memory_mb=round(pipeline.gpu_memory_mb), device=_badge_device(pipeline.run_device),
                  overlapped=pipeline.overlapped)
        self.emit("models", models=self.models())  # a first run may have installed a model

    def _song_view(self, entry: dict) -> dict | None:
        """A history entry as the results screen shows it (with waveforms and playable URLs)."""
        stems = []
        order = {"Acapella": 0, "Instrumental": 1}
        for p in map(Path, entry["stems"]):
            if not p.exists():
                continue
            peaks, duration = waveform(p)
            stems.append({"kind": stem_kind(p), "path": str(p), "name": p.name,
                          "size": human_size(p), "url": self._server.audio_url(p),
                          "peaks": peaks, "duration": duration})
        if not stems:
            return None
        stems.sort(key=lambda st: order.get(st["kind"], 2))
        length = max(st["duration"] for st in stems)
        meta = " · ".join(filter(None, [
            f"{int(length) // 60}:{int(length) % 60:02d}" if length else "",
            entry.get("model"), entry.get("format"), "saved to " + _saved_to([Path(st["path"]) for st in stems])]))
        return {"title": entry["title"], "meta": meta, "stems": stems, "analysis": entry.get("analysis")}

    # History -----------------------------------------------------------------------

    def history(self) -> list[dict]:
        items = []
        for e in load_history():
            try:
                when = datetime.fromisoformat(e["date"]).strftime("%d %b %Y, %H:%M")
            except Exception:
                when = ""
            a = e.get("analysis") or {}
            tags = " · ".join(filter(None, [f"{a['bpm']['bpm']} BPM" if a.get("bpm") else "",
                                            a["key"]["name"] if a.get("key") else ""]))
            items.append({"id": e.get("id"), "title": e.get("title", "?"), "when": when, "model": e.get("model", ""),
                          "tags": tags})
        return items

    def history_item(self, entry_id: str):
        for e in load_history():
            if e.get("id") == entry_id:
                return self._song_view(e)
        return None


def _saved_to(paths: list[Path]) -> str:
    """Music\\ORemove for the standard layout; otherwise each stem's folder."""
    parents = [p.parent for p in paths]
    if all(p.name in STEM_KINDS for p in parents) and len({p.parent for p in parents}) == 1:
        return _short_folder(parents[0].parent)
    return " and ".join(dict.fromkeys(_short_folder(p) for p in parents))


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
    if sys.platform.startswith("win"):
        # Our own taskbar identity, so Windows shows ORemove's icon (and never
        # groups the window under python.exe).
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
        except Exception:
            pass
    server = LocalServer()
    api = Api(server)
    window = webview.create_window(f"{APP_TITLE} - {APP_TAGLINE}", server.url, js_api=api,
                                   width=1000, height=720, min_size=(760, 620), background_color="#111114")
    api._window = window

    def on_drop(event):
        files = (event.get("dataTransfer") or {}).get("files") or []
        api.on_drop([f.get("pywebviewFullPath") for f in files])

    def on_loaded():
        try:
            window.dom.document.events.dragover += DOMEventHandler(lambda _e: None, True, True)
            window.dom.document.events.drop += DOMEventHandler(on_drop, True, True)
        except Exception as exc:
            api.emit("log", text=f"Drag and drop unavailable: {exc}")
        threading.Thread(target=api.detect_device_quickly, daemon=True).start()
        api.check_app_update()
        # Load the chosen model a few seconds after opening (if it's installed),
        # so the first song starts straight away. Freed again after 10 idle minutes.
        preload = threading.Timer(4, api.warm_up)
        preload.daemon = True
        preload.start()
        if api._settings["ytdlp_auto"]:
            api.check_ytdlp(force=False)  # at most once a day
        else:
            api.emit("ytdlp", version=bundled_ytdlp_version(), status="Automatic updates off")

    window.events.loaded += on_loaded

    def smoke_test():
        started = time.monotonic()
        ok = api._ready.wait(90)
        lines = [f"page loaded and called Python: {ok}", f"version: {APP_VERSION}", f"url: {server.url}"]
        if ok:
            try:
                lines.append("title: " + str(window.evaluate_js("document.querySelector('.hero-title').textContent")))
            except Exception as exc:
                lines.append(f"evaluate_js failed: {exc}")
                ok = False
            # The device badge is filled in without loading the AI engine.
            for _ in range(60):
                if api._device:
                    break
                time.sleep(0.5)
            lines.append(f"device: {api._device or 'not detected in time'}")
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
        webview.start(smoke_test if smoke_test_report else None, gui=gui, debug=False,
                      icon=str(ICON_FILE) if ICON_FILE else None)
    except Exception as exc:
        if sys.platform.startswith("win"):
            show_fatal_error(
                f"The interface could not start ({exc}).\n\nORemove needs the Microsoft Edge WebView2 Runtime. "
                "Install it from https://go.microsoft.com/fwlink/p/?LinkId=2124703 and start ORemove again.")
        else:
            show_fatal_error(f"The interface could not start: {exc}")
        return 1
    return 0 if result["ok"] else 1


def _self_test_analysis(tmp: Path, log):
    """BPM and key detection on a generated 120 BPM groove in A minor."""
    import numpy as np
    import soundfile as sf
    import oremove_analysis as oa
    sr, bpm, secs = 22050, 120, 32
    t = np.arange(int(0.25 * sr)) / sr
    kick = 0.9 * np.sin(2 * np.pi * (50 + 120 * np.exp(-t * 30)) * t) * np.exp(-t * 12)
    y = np.zeros(int((secs + 3) * sr))
    chords = [(57, 60, 64), (53, 57, 60), (52, 56, 59), (57, 60, 64)]  # Am, F, E, Am
    for b in range(int(secs * bpm / 60)):
        start = int(b * 60 / bpm * sr)
        y[start:start + len(kick)] += kick if b % 2 == 0 else 0.4 * np.random.default_rng(b).standard_normal(len(kick)) * np.exp(-t * 25)
        if b % 4 == 0:
            n = int(4 * 60 / bpm * sr)
            tt = np.arange(n) / sr
            for note in chords[(b // 4) % 4]:
                f = 440 * 2 ** ((note - 69) / 12)
                y[start:start + n] += 0.08 * np.exp(-tt * 0.8) * sum(np.sin(2 * np.pi * f * k * tt) / k for k in (1, 2, 3))
            f = 440 * 2 ** ((chords[(b // 4) % 4][0] - 81) / 12)
            y[start:start + n] += 0.25 * np.exp(-tt * 0.8) * np.sin(2 * np.pi * f * tt)
    clip = tmp / "groove.wav"
    sf.write(str(clip), np.stack([y, y], 1).astype(np.float32), sr)
    tracker = _beat_tracker()
    if not tracker.installed():
        try:
            tracker.install(lambda url, timeout, cb: _download(url, timeout=timeout, on_progress=cb))
        except Exception as exc:
            log(f"analysis: beat tracker download failed ({exc}); BPM check skipped")
            tracker = None
    started = time.monotonic()
    result = oa.analyse(clip, tracker, log=log)
    log(f"analysis: {result} in {time.monotonic() - started:.1f}s")
    if tracker is not None and not (result["bpm"] and abs(float(result["bpm"]["bpm"]) - bpm) <= 1):
        raise PipelineError(f"BPM detection gave {result['bpm']}, expected {bpm}")
    if not result["key"]:
        raise PipelineError("key detection returned nothing")


def _self_test_engine(tmp: Path, model_files: list[str], log):
    """
    ORemove's own Roformer loop must give the same stems as audio-separator's:
    one chunk at a time, two at a time, and with the real-number spectral maths
    used on Apple GPUs.
    """
    import numpy as np
    import soundfile as sf
    import oremove_engine as eng
    clip = tmp / "engine.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anoisesrc=d=11:c=pink:a=0.2",
                    "-f", "lavfi", "-i", "sine=f=330:d=11", "-filter_complex", "amix=inputs=2",
                    "-ac", "2", "-ar", "48000", "-c:a", "pcm_s24le", str(clip)], check=True, creationflags=NO_WINDOW)
    wav = tmp / "engine44.wav"
    audio = eng.decode(clip, creationflags=NO_WINDOW)
    if audio.shape != (2, 11 * 44100):
        raise PipelineError(f"decode: expected (2, {11 * 44100}) samples, got {audio.shape}")
    sf.write(str(wav), audio.T, 44100, subtype="FLOAT")
    for model_file in model_files:
        job = Job(sources=[], model_file=model_file, output_format="WAV", output_dir=tmp / "engine_out", faster=True)
        stems_dir = tmp / f"engine_{model_file[:12]}"
        stems_dir.mkdir()
        pipeline = ExtractionPipeline(log=lambda _t: None, progress=lambda _v: None)
        separator = pipeline._load_separator(job, stems_dir)
        started = time.monotonic()
        produced = [stems_dir / p for p in _run_separator(separator, wav)]
        library = [sf.read(str(p), dtype="float32")[0] for p in produced]
        log(f"engine {model_file}: audio-separator {time.monotonic() - started:.1f}s")
        instance = separator.model_instance
        for batch, real in ((1, False), (2, False), (2, True)):
            started = time.monotonic()
            run = eng.Separation(instance, batch=batch, real_spectral=real)
            if run.real_spectral != real:
                raise PipelineError(f"{model_file}: the real-number spectral path isn't available for this model")
            ours = run.run(audio)
            if run.real_failed:
                raise PipelineError(f"{model_file}: the real-number spectral path failed")
            worst = max(float(np.abs(a[:len(b)] - b).max()) for (_n, a), b in zip(ours, library))
            log(f"engine {model_file}: batch {batch}{', real spectral' if real else ''}: "
                f"{time.monotonic() - started:.1f}s, largest difference {worst:.2e}")
            if len(ours) != len(library) or worst > 2e-3:
                raise PipelineError(f"{model_file}: ORemove's engine differs from audio-separator ({worst:.2e})")
        _release_separator()


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
        log(f"yt-dlp: {bundled_ytdlp_version()}")
        import yt_dlp_ejs  # YouTube challenge scripts, run by Deno: must be bundled
        log(f"yt-dlp-ejs: {yt_dlp_ejs.version}")
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
                job = Job(sources=[str(tone)], model_file=model_file, output_format="WAV",
                          output_dir=tmp / f"out{n}", faster=faster, analyse=False)
                pipeline = ExtractionPipeline(log=log, progress=lambda _v: None)
                started = time.monotonic()
                first_line = len(lines)
                songs, failures = pipeline.run(job)
                saved = songs[0]["stems"]
                for path in saved:
                    log(f"saved: {path.relative_to(job.output_dir)}")
                kinds = sorted(stem_kind(p) for p in saved)
                if kinds != ["Acapella", "Instrumental"]:
                    raise PipelineError(f"{model_file}: expected an acapella and an instrumental, got {kinds}")
                if reuse and any("Loading UVR model" in l for l in lines[first_line:]):
                    raise PipelineError(f"{model_file}: the loaded model was not reused")
                log(f"ok in {time.monotonic() - started:.1f}s")
            if not model_files:
                force_gpu_settings = False
                # A batch: songs overlap (download/decode, separate, save), a bad
                # file is skipped, MP3 output, and BPM/key from the stem in memory.
                log("--- batch of 3 (one unreadable), MP3")
                bad = tmp / "not audio.wav"
                bad.write_text("not audio")
                tone2 = tmp / "tone 2.flac"
                subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=330:duration=6",
                                "-ac", "2", "-ar", "48000", str(tone2)], check=True, creationflags=NO_WINDOW)
                job = Job(sources=[str(tone), str(bad), str(tone2)], model_file=best_model, output_format="MP3",
                          output_dir=tmp / "batch", faster=True)
                started = time.monotonic()
                songs, failures = ExtractionPipeline(log=log, progress=lambda _v: None).run(job)
                names = sorted(p.name for song in songs for p in song["stems"])
                log(f"batch: {names}; failures: {failures}")
                if len(songs) != 2 or len(failures) != 1 or len(names) != 4 or not all(n.endswith(".mp3") for n in names):
                    raise PipelineError("the batch didn't give 2 songs (4 MP3 stems) and 1 failure")
                if not all(p.stat().st_size > 50_000 for song in songs for p in song["stems"]):
                    raise PipelineError("an MP3 stem is too small")
                log(f"ok in {time.monotonic() - started:.1f}s")
                _self_test_engine(tmp, [m.filename for m in MODELS if "roformer" in m.filename.lower()][::3], log)
                _self_test_analysis(tmp, log)
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
    # Helper processes started by the AI engine re-run this program; this makes
    # them do their job instead of opening another window (packaged app).
    multiprocessing.freeze_support()
    # python app.py --self-test report.txt [model_file ...]   engine check, no window
    # python app.py --ui-smoke-test report.txt                 open and close the window
    if len(sys.argv) >= 3 and sys.argv[1] == "--self-test":
        sys.exit(self_test(Path(sys.argv[2]), sys.argv[3:] or None))
    if len(sys.argv) >= 3 and sys.argv[1] == "--ui-smoke-test":
        sys.exit(run_window(Path(sys.argv[2])))
    sys.exit(run_window())


if __name__ == "__main__":
    main()
