<h1 align="center">ORemove</h1>
<h3 align="center">AI Powered Vocal Isolator</h3>

<p align="center">
  <strong>Clean acapellas and instrumentals from any YouTube link or audio file, powered by state-of-the-art UVR AI models.</strong>
</p>

<p align="center">
  <img alt="Windows 10/11" src="https://img.shields.io/badge/Windows-10%20%7C%2011-0078D4">
  <img alt="GPU: NVIDIA only" src="https://img.shields.io/badge/GPU-NVIDIA%20only-76B900">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/Python-3.10%2B-3776AB">
  <img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-22C55E">
</p>

<p align="center">
  <img src="docs/screenshot.png" alt="ORemove - AI Powered Vocal Isolator" width="640">
</p>

> [!IMPORTANT]
> **GPU acceleration works with NVIDIA graphics cards only.** AMD Radeon and Intel graphics are **not supported** for GPU acceleration.
> If your PC doesn't have an NVIDIA card, use the **CPU edition**. It runs on any Windows PC, just more slowly.

---

## Features

- **YouTube or your own files.** Paste a YouTube link, or browse for MP3, WAV, FLAC, M4A, AAC, OGG, OPUS, WMA or AIFF files. Video files work too; only their audio is used.
- **Batch processing.** Select several files at once. They're separated one after another with the model loaded once, and a file that can't be read is skipped without stopping the rest.
- **Top-rated separation models.** BS-Roformer models that lead the benchmark published with [audio-separator](https://github.com/nomadkaraoke/python-audio-separator), plus a fast MDX-Net option.
- **NVIDIA GPU acceleration.** The NVIDIA GPU edition runs the BS-Roformer models with CUDA, turning minutes into seconds. **NVIDIA cards only**; for AMD, Intel or no graphics card, the CPU edition runs on any PC.
- **Lossless quality.** Audio is decoded straight to uncompressed WAV before separation (24-bit for your own files). Export to WAV, or MP3 at 320 kbps.
- **Organised output.** Stems land in `acapellas/` and `instrumentals/`, named after the video or file. Existing files are never overwritten.
- **Play straight away.** Each finished song gets one-click **▶ Acapella** and **▶ Instrumental** buttons, and **Show** opens it in Explorer.
- **Modern interface.** Dark, minimal design with a live step indicator, elapsed time, and a device badge showing whether your GPU is in use.
- **Fast start.** The window opens instantly while the AI engine loads in the background.
- **Remembers your choices.** Model, format and folders carry over between sessions.
- **Self-contained.** FFmpeg and the fast model are bundled; no Python or setup needed.

## Download

Get the latest version from the **[Releases](https://github.com/crawfordj0611-lang/ORemove-Vocal-Isolator-/releases/latest)** page.

> 📘 **New to ORemove? Follow the step-by-step [Installation guide](INSTALL.md).** It covers choosing an edition, installing, first launch, updating, uninstalling and fixing installation problems.

| Edition | Works on | Download |
|---|---|---|
| **NVIDIA GPU edition** (fastest) | **Only** PCs with an **NVIDIA** GeForce graphics card (GTX 900 series or newer) | All `ORemove-NVIDIA-GPU-Setup` files: the `.exe` and every `.bin` part |
| **CPU edition** | **Any** 64-bit Windows PC, including PCs with **AMD** or **Intel** graphics | `ORemove-Windows-CPU.zip` |

**Not sure which graphics card you have?** Open **Task Manager → Performance** and look at the **GPU** entries. If none of them says **NVIDIA**, download the CPU edition.

**NVIDIA GPU edition:** save the `.exe` and all `.bin` parts into the same folder, then run `ORemove-NVIDIA-GPU-Setup.exe`. If it can't find an NVIDIA graphics card, the installer warns you before installing anything. The installer is split into parts because GitHub limits each file to 2 GB. It installs for your user only (no admin rights needed), adds a Start Menu shortcut, and can be removed from **Settings → Apps**. Installing a new version upgrades the old one and keeps your downloaded models and settings.

**CPU edition:** extract the zip (right-click → **Extract All…**) and run `ORemove.exe` inside the folder. Nothing is installed.

> Windows SmartScreen may warn that the app is unrecognised because it isn't code-signed. Click **More info**, then **Run anyway**.

## Quick start

1. **Choose a source.** Paste a YouTube link, or click **Browse** to pick one or more audio files. You can also paste a file's path.
2. **Choose a model** from the dropdown (see [Models](#models)), a format (**WAV** or **MP3**), and where to save.
3. **Click Extract stems.** Progress moves through **Get audio → Separate → Save**. When it's done, play the stems right from the window.

Open **Show details** at any time for a technical log.

## Models

Every model outputs both an acapella and an instrumental. The dropdown shows what each one does best.

| Model | Best for | Notes |
|---|---|---|
| **BS-Roformer 1297** (default) | Instrumentals | Highest-rated instrumental quality in audio-separator's benchmark. Downloaded once on first use. |
| **BS-Roformer 1296** | Acapellas | Highest-rated acapella quality in the same benchmark. Downloaded once on first use. |
| **MDX-Net Inst HQ 3** | Speed | Good quality in a fraction of the time. Bundled with the app. |

The BS-Roformer models are a large one-time download each, stored in the app's `models` folder and reused afterwards.

## Output

```
Music\ORemove\
├── acapellas\       <Song or video title> (Acapella).wav
└── instrumentals\   <Song or video title> (Instrumental).wav
```

The default folder is `Music\ORemove`. Click **Change** to pick another; the app remembers it. Running the same song again saves `… (Acapella) (2).wav` instead of overwriting.

## System requirements

| | NVIDIA GPU edition | CPU edition |
|---|---|---|
| **OS** | Windows 10 or 11, 64-bit | Windows 10 or 11, 64-bit |
| **Graphics** | **NVIDIA only:** GeForce GTX 900 series or newer, 4 GB+ VRAM (8 GB+ recommended for BS-Roformer), current driver. **AMD and Intel GPUs are not supported.** | Any, or none (not used) |
| **Processor** | Any modern 4+ core CPU | 6–8 cores recommended, 4 minimum |
| **Memory** | 16 GB recommended | 8 GB minimum, 16 GB recommended |
| **Disk** | ~6 GB, plus ~1.5 GB for the BS-Roformer models | ~2 GB, plus ~1.5 GB for the BS-Roformer models |
| **Internet** | For YouTube downloads and the first use of each BS-Roformer model | Same |

**Typical processing time for a 4-minute song** (estimates; they vary with hardware):

| | BS-Roformer | MDX-Net Inst HQ 3 |
|---|---|---|
| **NVIDIA GPU edition** (RTX card) | Seconds to about a minute | About a minute (runs on the CPU) |
| **CPU edition** | 15–30+ minutes | 2–6 minutes |

WAV stems take about 10 MB per minute of audio; MP3 at 320 kbps takes about 2.4 MB per minute.

## Troubleshooting

| Problem | Fix |
|---|---|
| I have an **AMD** or **Intel** graphics card | GPU acceleration is NVIDIA-only. Use the **CPU edition**. |
| The NVIDIA GPU edition shows **No NVIDIA GPU** | Either the PC has no NVIDIA card (use the CPU edition), or the NVIDIA driver is out of date (update it from nvidia.com, then restart the app). |
| The installer says no NVIDIA graphics card was found | Your PC has no NVIDIA card that Windows can see. Use the CPU edition. |
| YouTube downloads fail for videos that play fine | YouTube changes often. Install the latest release, which includes an updated downloader. |
| "Age-restricted" or "private" video | These can't be downloaded without signing in, so they're refused. |
| A file in a batch was skipped | **Show details** lists each skipped file and why (e.g. not a readable audio file). |

## Running from source

For development, or on macOS and Linux. Requires **Python 3.10+** and **FFmpeg**.

**1. Install FFmpeg.** On Windows: `winget install Gyan.FFmpeg`. On macOS: `brew install ffmpeg`. On Ubuntu/Debian: `sudo apt install ffmpeg python3-tk`.

**2. Create a virtual environment.**

```bash
python -m venv .venv
```

Activate it (Windows: `.venv\Scripts\activate`; macOS/Linux: `source .venv/bin/activate`).

**3. NVIDIA GPUs only:** install the CUDA build of PyTorch first. Skip this step for AMD, Intel or no GPU.

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
```

**4. Install the requirements and run.**

```bash
pip install -r requirements.txt
```

```bash
python app.py
```

From source, stems are saved to `output/` and models to `models/` next to `app.py`. To add another model, extend `MODELS` at the top of `app.py`; `audio-separator --list_models` lists every available model.

## How it works

1. **Get audio.** For a YouTube link, [yt-dlp](https://github.com/yt-dlp/yt-dlp) downloads the best available audio stream. For a local file, FFmpeg decodes it. Either way, the audio becomes uncompressed WAV in a private temporary folder.
2. **Separate.** [audio-separator](https://github.com/nomadkaraoke/python-audio-separator) runs the chosen Ultimate Vocal Remover model with PyTorch or ONNX Runtime, on an NVIDIA GPU when available, otherwise on the CPU.
3. **Save.** The stems are named after the song, sorted into `acapellas/` and `instrumentals/`, and the temporary files are deleted, even if something fails.

All processing happens on your computer. Nothing is uploaded.

### Building the Windows app

The [Windows build workflow](.github/workflows/windows-build.yml) builds both editions on GitHub Actions. It packages the app with PyInstaller, bundles an LGPL build of FFmpeg, and runs a self-test of every model, both from source and with the packaged `.exe`. It then creates the CPU zip and the GPU installer with Inno Setup ([`packaging/installer.iss`](packaging/installer.iss)). To publish a release, run the workflow from the **Actions** tab with a new version number such as `v1.4.0`.

## Responsible use

Only separate audio you own or have permission to use, such as your own productions, licensed material, or content whose rights holder allows it. Downloading from YouTube may be restricted by YouTube's Terms of Service. You are responsible for how you use this software and anything you create with it.

ORemove is not affiliated with, endorsed by or sponsored by YouTube, Google, or the Ultimate Vocal Remover project.

## License

ORemove is released under the [MIT License](LICENSE).

It builds on outstanding open-source work, including [Ultimate Vocal Remover](https://github.com/Anjok07/ultimatevocalremovergui) and its model authors, [audio-separator](https://github.com/nomadkaraoke/python-audio-separator), [yt-dlp](https://github.com/yt-dlp/yt-dlp), [PyTorch](https://pytorch.org), [FFmpeg](https://ffmpeg.org) and [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter). See [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) for their licenses and for notes on the AI models. Each download also includes the full license texts.
