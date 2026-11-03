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
APP_VERSION = "1.5.6"

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
                 title=lambda _text: None, cancel_event: threading.Event | None = None,
                 engine=lambda _device: None):
        self.log = log
        self._engine_cb = engine  # told what the model runs on, once it's loaded
        self._progress_cb = progress
        self._stage_cb = stage
        self._title_cb = title
        self.cancel_event = cancel_event or threading.Event()
        self._index, self._count = 0, 1  # position in the current batch
        self._shown = 0.0                 # highest progress shown for the current item
        self.run_device = ""              # what the loaded model runs on, e.g. "RTX 4070 SUPER"
        self.on_model_download = None     # optional (percent, total bytes) callback
        # Seconds spent in each step, for the breakdown on the results screen.
        self.timings = {"audio": 0.0, "engine": 0.0, "model_download": 0.0, "model_load": 0.0,
                        "separate": 0.0, "save": 0.0}
        self.gpu_memory_mb = 0.0          # peak GPU memory used while separating (proof the GPU worked)

    def check_cancel(self):
        """Stop between steps once the user has pressed Stop."""
        if self.cancel_event.is_set():
            raise Cancelled()

    def progress(self, value):
        """Report progress of the current item, scaled to the whole batch. Never goes backwards."""
        if value is not None:
            value = max(value, self._shown)
            self._shown = value
        self._progress_cb(None if value is None else (self._index + value) / self._count)

    def stage(self, name, detail):
        prefix = f"File {self._index + 1} of {self._count}  ·  " if self._count > 1 else ""
        self._stage_cb(name, prefix + detail)

    # ---- Step 1: download --------------------------------------------------

    def download_youtube(self, url: str, download_dir: Path, wav: Path) -> str:
        """Download a YouTube video's audio to `wav`, with the newest yt-dlp. Returns its title."""
        self._ensure_deno()
        with _ytdlp_lock:  # yt-dlp mustn't be updated while it runs
            for attempt in (1, 2):
                version = use_ytdlp(self.log)
                self.log(f"Using yt-dlp {version}.")
                try:
                    title = self.fetch_title(url)
                    self._title_cb(title)
                    self.check_cancel()
                    self.download_wav(url, download_dir).replace(wav)
                    return title
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
            **ytdlp_js_options(),
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
            raise self._download_error(exc) from exc

        wav = work_dir / "source.wav"
        if not wav.exists():
            raise PipelineError("The download finished but no WAV file was produced. Is FFmpeg working?")
        self.log(f"Audio ready ({wav.stat().st_size / 1_048_576:.1f} MB WAV).")
        return wav

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
        self.stage("separate", f"Separating stems on {self.run_device}" if self.run_device else "Separating stems")
        self.progress(None)  # until the first chunk is done
        started = time.monotonic()
        reported = {"fraction": 0.0, "logged": -10}

        def on_progress(kind, done, total):
            if kind != "separate":
                return
            fraction = min(1.0, done / total)
            if fraction < reported["fraction"] + 0.002 and fraction < 1.0:
                return  # only report visible changes
            reported["fraction"] = max(fraction, reported["fraction"])
            self.progress(MODEL_LOAD_AT + reported["fraction"] * (SEPARATED_AT - MODEL_LOAD_AT))
            pct = int(reported["fraction"] * 100)
            if pct >= reported["logged"] + 10:
                reported["logged"] = pct
                self.log(f"  separating... {pct}% ({time.monotonic() - started:.0f}s)")

        _set_progress_sink(on_progress)
        _reset_gpu_memory_peak()
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
            reported.update(fraction=0.0, logged=-10)
            _set_progress_sink(on_progress)
            try:
                produced = separator.separate(str(wav))
            except Exception as exc2:
                raise PipelineError(f"Separation failed: {exc2}") from exc2
        finally:
            _set_progress_sink(None)
        elapsed = time.monotonic() - started
        self.timings["separate"] += elapsed
        self.log(f"Separated in {elapsed:.1f}s")
        peak = _gpu_memory_peak_mb()
        if peak is not None:
            self.gpu_memory_mb = max(self.gpu_memory_mb, peak)
            self.log(f"GPU memory used while separating: {peak:.0f} MB")
            if peak < 100:
                self.log("Warning: the GPU was barely used during separation; it may have run on the CPU.")

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
        self.timings["engine"] += time.monotonic() - engine_started

        cuda = torch.cuda.is_available()
        mps = bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())
        safe = safe or _cache["safe"]
        gpu_settings = (cuda or force_gpu_settings) and not safe
        key = (job.model_file, job.output_format, job.faster, safe, gpu_settings)
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

        roformer = "roformer" in job.model_file.lower()
        options = dict(
            # Half precision on GPUs: native float16 for BS-Roformer (verified by
            # audio-separator on NVIDIA and Apple GPUs), autocast for MDX-Net on NVIDIA.
            use_native_fp16=(cuda or mps) and not safe and roformer,
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
        load_started = time.monotonic()
        try:
            separator.load_model(model_filename=job.model_file)
        except Exception as exc:
            raise PipelineError(f"Could not load model '{job.model_file}': {exc}") from exc
        finally:
            _set_progress_sink(None)
            self.timings["model_load" if model_cached else "model_download"] += time.monotonic() - load_started
        self.progress(MODEL_LOAD_AT)
        _cache.update(key=key, separator=separator, safe=safe, device=_describe_device(separator))
        self.run_device = _cache["device"]
        self.log(f"Model running on: {self.run_device}")
        self._engine_cb(self.run_device)
        if self.run_device == "the CPU":
            self.log("Running on the CPU is much slower than a GPU: a full song can take 15 minutes or more with BS-Roformer.")
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
        self._shown = 0.0
        self.check_cancel()
        wav = work_dir / f"input{i}.wav"  # unique per item: stems are named after it
        audio_started = time.monotonic()
        if looks_like_url(source):
            if not is_youtube_url(source):
                raise PipelineError("Only YouTube links are supported (youtube.com or youtu.be).")
            download_dir = work_dir / f"download{i}"
            download_dir.mkdir()
            title = self.download_youtube(source, download_dir, wav)
        else:
            src = Path(source)
            title = src.stem
            self._title_cb(title)
            self.convert_local_file(src, wav)
        self.timings["audio"] += time.monotonic() - audio_started
        base_name = sanitize_filename(title)

        self.check_cancel()
        stems = self.separate(wav, job, stems_dir)
        try:
            self.check_cancel()  # separation can't be interrupted; discard its result instead
            save_started = time.monotonic()
            saved = self.file_stems(stems, base_name, job.output_dir)
            self.timings["save"] += time.monotonic() - save_started
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
        }
        _ytdlp["auto"] = self._settings["ytdlp_auto"]
        self._installing: str | None = None  # model being installed from the welcome card

    # Events -----------------------------------------------------------------

    def emit(self, event_type: str, **data):
        self._events.put({"type": event_type, **data})

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

            form.Invoke(Action(start_drag))
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
        if self._idle_timer:
            self._idle_timer.cancel()
        self.emit("log", text="-" * 50)
        self.emit("log", text=f"Starting {len(sources)} item(s) [{job.model_file}, {job.output_format}, {s['quality']}]")
        self._worker = threading.Thread(target=self._run, args=(job,), daemon=True)
        self._worker.start()
        return {"ok": True}

    def cancel(self):
        self._cancel.set()
        self.emit("log", text="Stopping after the current step...")

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
                    "stems": [str(p) for p in song["stems"]]} for song in songs]
        add_to_history(entries)
        for failure in failures:
            self.emit("log", text=f"FAILED: {failure}")
        self.emit("done", songs=[self._song_view(e) for e in entries], failures=failures,
                  seconds=round(pipeline.timings["separate"], 1), total=round(seconds, 1),
                  timings={k: round(v, 1) for k, v in pipeline.timings.items()},
                  gpu_memory_mb=round(pipeline.gpu_memory_mb), device=_badge_device(pipeline.run_device))
        self.emit("models", models=self.models())  # a first run may have installed a model

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
