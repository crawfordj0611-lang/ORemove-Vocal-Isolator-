<h1 align="center">ORemove</h1>
<h3 align="center">AI Powered Vocal Isolator</h3>

<p align="center">
  <strong>Clean acapellas and instrumentals from any YouTube link or audio file, powered by state-of-the-art UVR AI models.</strong>
</p>

<p align="center">
  <img alt="Windows 10/11" src="https://img.shields.io/badge/Windows-10%20%7C%2011-0078D4">
  <img alt="macOS Apple Silicon" src="https://img.shields.io/badge/macOS-Apple%20Silicon-000000">
  <img alt="GPU: NVIDIA or Apple Silicon" src="https://img.shields.io/badge/GPU-NVIDIA%20%7C%20Apple%20Silicon-76B900">
  <img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-22C55E">
</p>

<p align="center">
  <img src="docs/screenshot.png" alt="ORemove - AI Powered Vocal Isolator" width="720">
</p>

> [!IMPORTANT]
> **On Windows, GPU acceleration works with NVIDIA graphics cards only.** AMD Radeon and Intel graphics are **not supported** for GPU acceleration; use the **CPU edition** on those PCs. It runs on any Windows PC, just more slowly.
> **On a Mac, ORemove needs Apple Silicon** (M1 or newer) and uses its built-in GPU automatically. Intel Macs are not supported.

---

## Features

- **Drop it in.** Drag audio files onto the window, browse for them, or paste a YouTube link. MP3, WAV, FLAC, M4A, AAC, OGG, OPUS, WMA, AIFF and video files all work (only the audio is used).
- **Top-rated separation models.** BS-Roformer models that lead the benchmark published with [audio-separator](https://github.com/nomadkaraoke/python-audio-separator), plus a fast MDX-Net option.
- **Listen right away.** Each result has its own waveform and player. Click a waveform to jump anywhere, and use **A/B** to switch between vocals and instrumental at the same point while it plays.
- **GPU accelerated.** NVIDIA GPUs on Windows (16-bit processing for extra speed) and Apple Silicon GPUs on Mac, turning minutes into seconds.
- **Batch processing.** Drop or select several songs at once. They run one after another with the model loaded once, and a file that can't be read is skipped without stopping the rest.
- **History.** Every song you separate is listed under History, ready to play again.
- **Lossless quality.** Audio is decoded straight to uncompressed WAV before separation (24-bit for your own files). Export to WAV, or MP3 at 320 kbps.
- **Organised output.** Stems land in `acapellas/` and `instrumentals/`, named after the song. Existing files are never overwritten.
- **Built for speed, light when idle.** The window opens instantly and uses little memory while idle: the AI engine loads when you start a song, stays loaded for the next ones, and is freed after 10 idle minutes. A **Faster** quality mode roughly halves BS-Roformer processing time.
- **Private.** Everything runs on your computer; nothing is uploaded.
- **Self-contained.** FFmpeg and the fast model are included; no Python or setup needed.

## Download

Get the latest version from the **[Releases](https://github.com/crawfordj0611-lang/ORemove-Vocal-Isolator-/releases/latest)** page.

> 📘 **New to ORemove? Follow the step-by-step [Installation guide](INSTALL.md).** It covers choosing an edition, installing, first launch, updating, uninstalling and fixing installation problems.

| Edition | Works on | Download |
|---|---|---|
| **Windows · NVIDIA GPU** (fastest) | **Only** Windows PCs with an **NVIDIA** GeForce graphics card (GTX 900 series or newer) | All `ORemove-NVIDIA-GPU-Setup` files: the `.exe` and every `.bin` part |
| **Windows · CPU** | **Any** 64-bit Windows PC, including PCs with **AMD** or **Intel** graphics | `ORemove-Windows-CPU.zip` |
| **macOS · Apple Silicon** | Macs with an **M1 or newer** chip, macOS 13 Ventura or newer | `ORemove-macOS-AppleSilicon.dmg` |

**Not sure which graphics card your PC has?** Open **Task Manager → Performance** and look at the **GPU** entries. If none of them says **NVIDIA**, download the CPU edition.

**Windows · NVIDIA GPU:** save the `.exe` and all `.bin` parts into the same folder, then run `ORemove-NVIDIA-GPU-Setup.exe`. If it can't find an NVIDIA graphics card, the installer warns you before installing anything. It installs for your user only (no admin rights needed), adds a Start Menu shortcut, and installs Microsoft's WebView2 component if your PC doesn't have it yet.

**Windows · CPU:** extract the zip (right-click → **Extract All…**) and run `ORemove.exe` inside the folder. Nothing is installed.

**macOS:** open the `.dmg` and drag **ORemove** into **Applications**. The first time, macOS blocks it because it isn't notarized by Apple; see the [installation guide](INSTALL.md#4-install-on-a-mac) for the one-time fix.

> Windows SmartScreen may warn that the app is unrecognised because it isn't code-signed. Click **More info**, then **Run anyway**.

## Quick start

1. **Add a song.** Drag audio files onto the window, click **Browse files**, or paste a YouTube link (or a file path) and press **Separate**.
2. **Watch it work.** The progress ring moves through **Get audio → Separate → Save**. **Stop** ends the job after the current step.
3. **Listen.** Play the **Vocals** and **Instrumental** cards, click a waveform to jump, and switch with **A/B**. **Show in folder** opens the file's location; **Open in player** opens it in your usual player.

Change the model, quality, format or save folder with the **⚙ Settings** button (or click the chips under the drop zone). **History** (the clock button) lists every song you've separated. **Settings → Activity log** shows the technical details of each run.

## Models

Every model outputs both an acapella and an instrumental. Settings shows what each one does best.

| Model | Best for | Notes |
|---|---|---|
| **BS-Roformer 1297** (default) | Instrumentals | Highest-rated instrumental quality in audio-separator's benchmark. 610 MB, downloaded once on first use. |
| **BS-Roformer 1296** | Acapellas | Highest-rated acapella quality in the same benchmark. 610 MB, downloaded once on first use. |
| **MDX-Net Inst HQ 3** | Speed | Good quality in a fraction of the time. 64 MB, included. |

## Speed

**Quality: Best** (default) gives the highest quality. **Quality: Faster** runs BS-Roformer with 2 overlapping passes instead of 4: roughly twice as fast, with a small loss of quality that's mostly audible as slightly less smooth transitions. It doesn't affect MDX-Net Inst HQ 3.

On an NVIDIA GPU, ORemove also speeds things up automatically:

- BS-Roformer runs in **16-bit (half) precision**, which RTX cards process much faster.
- MDX-Net Inst HQ 3 runs **on the GPU** in batches, also in 16-bit precision.
- If the GPU runs out of memory, ORemove **retries automatically** with lower-memory settings and keeps using them for the rest of the session.

On every edition, the model **stays loaded** between songs, so only the first song waits for it to load. The results screen shows how long separation took, and the activity log records the settings used.

## Output

```
Music/ORemove/
├── acapellas/       <Song or video title> (Acapella).wav
└── instrumentals/   <Song or video title> (Instrumental).wav
```

The default folder is **Music/ORemove** in your user folder. Change it in Settings; the app remembers it. Running the same song again saves `… (Acapella) (2).wav` instead of overwriting.

## System requirements

| | Windows · NVIDIA GPU | Windows · CPU | macOS · Apple Silicon |
|---|---|---|---|
| **System** | Windows 10 or 11, 64-bit | Windows 10 or 11, 64-bit | macOS 13 Ventura or newer |
| **Graphics / chip** | **NVIDIA only:** GeForce GTX 900 series or newer, 4 GB+ VRAM (8 GB+ recommended for BS-Roformer), current driver. **AMD and Intel GPUs are not supported.** | Any, or none (not used) | Apple M1 or newer (Intel Macs are not supported) |
| **Memory** | 16 GB recommended | 8 GB minimum, 16 GB recommended | 8 GB minimum, 16 GB recommended |
| **Disk** | ~6 GB, plus 1.2 GB for both BS-Roformer models (10 GB free recommended) | ~2 GB, plus 1.2 GB for both BS-Roformer models (5 GB free recommended) | ~2 GB, plus 1.2 GB for both BS-Roformer models (5 GB free recommended) |
| **Internet** | For YouTube downloads and the first use of each BS-Roformer model | Same | Same |

**Typical processing time for a 4-minute song** (estimates; they vary with hardware):

| | BS-Roformer | MDX-Net Inst HQ 3 |
|---|---|---|
| **Windows · NVIDIA GPU** (RTX card) | Seconds to about a minute | Seconds |
| **macOS · Apple Silicon** | About 1–5 minutes, depending on the chip | Under a minute |
| **Windows · CPU** | 15–30+ minutes (about half with **Faster**) | 2–6 minutes |

WAV stems take about 10 MB per minute of audio; MP3 at 320 kbps takes about 2.4 MB per minute.

## Troubleshooting

| Problem | Fix |
|---|---|
| I have an **AMD** or **Intel** graphics card (Windows) | GPU acceleration on Windows is NVIDIA-only. Use the **CPU edition**. |
| The NVIDIA edition's badge shows **No NVIDIA GPU** | Either the PC has no NVIDIA card (use the CPU edition), or the NVIDIA driver is out of date (update it from nvidia.com, then restart the app). |
| The installer says no NVIDIA graphics card was found | Your PC has no NVIDIA card that Windows can see. Use the CPU edition. |
| Windows: "The interface could not start… WebView2" | Install the free [Microsoft Edge WebView2 Runtime](https://go.microsoft.com/fwlink/p/?LinkId=2124703), then start ORemove again. (The NVIDIA installer does this automatically.) |
| Mac: "ORemove can't be opened" or "Apple could not verify…" | See [Install on a Mac](INSTALL.md#4-install-on-a-mac) for the one-time steps to allow it. |
| YouTube downloads fail for videos that play fine | YouTube changes often. Install the latest release, which includes an updated downloader. |
| "Age-restricted" or "private" video | These can't be downloaded without signing in, so they're refused. |
| A file in a batch was skipped | The results screen lists each skipped file and why (e.g. not a readable audio file). |

## Running from source

For development. Requires **Python 3.10+** and **FFmpeg**.

**1. Install FFmpeg.** On Windows: `winget install Gyan.FFmpeg`. On macOS: `brew install ffmpeg`. On Linux: install `ffmpeg` with your package manager.

**2. Create a virtual environment.**

```bash
python -m venv .venv
```

Activate it (Windows: `.venv\Scripts\activate`; macOS/Linux: `source .venv/bin/activate`).

**3. NVIDIA GPUs only:** install the CUDA build of PyTorch first. Skip this step otherwise.

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

On Linux, the window also needs Qt: `pip install "pywebview[qt]"`.

From source, stems are saved to `output/` and models to `models/` next to `app.py`. To add another model, extend `MODELS` at the top of `app.py`; `audio-separator --list_models` lists every available model.

## How it works

1. **Get audio.** For a YouTube link, [yt-dlp](https://github.com/yt-dlp/yt-dlp) downloads the best available audio stream. For a local file, FFmpeg decodes it. Either way, the audio becomes uncompressed WAV in a private temporary folder.
2. **Separate.** [audio-separator](https://github.com/nomadkaraoke/python-audio-separator) runs the chosen Ultimate Vocal Remover model with PyTorch or ONNX Runtime, on the GPU when available.
3. **Save.** The stems are named after the song, sorted into `acapellas/` and `instrumentals/`, and the temporary files are deleted, even if something fails.

The interface ([`ui/`](ui)) is a web page shown in a native window by [pywebview](https://pywebview.flowrl.com) (Edge WebView2 on Windows, WebKit on macOS). It talks to the Python engine in [`app.py`](app.py) through a small API, and plays your stems from a local server that only your own computer can reach. All processing happens on your computer. Nothing is uploaded.

### Building the apps

The [build workflow](.github/workflows/build.yml) builds all three editions on GitHub Actions. It packages the app with PyInstaller, bundles FFmpeg, and tests every model both from source and with the packaged app. It also opens the real window once on each system and saves a screenshot. It then creates the Windows CPU zip, the NVIDIA installer (Inno Setup, [`packaging/installer.iss`](packaging/installer.iss)) and the macOS disk image. To publish a release, run the workflow from the **Actions** tab with a new version number such as `v1.6.0`.

## Responsible use

Only separate audio you own or have permission to use, such as your own productions, licensed material, or content whose rights holder allows it. Downloading from YouTube may be restricted by YouTube's Terms of Service. You are responsible for how you use this software and anything you create with it.

ORemove is not affiliated with, endorsed by or sponsored by YouTube, Google, Apple, or the Ultimate Vocal Remover project.

## License

ORemove is released under the [MIT License](LICENSE).

It builds on outstanding open-source work, including [Ultimate Vocal Remover](https://github.com/Anjok07/ultimatevocalremovergui) and its model authors, [audio-separator](https://github.com/nomadkaraoke/python-audio-separator), [yt-dlp](https://github.com/yt-dlp/yt-dlp), [PyTorch](https://pytorch.org), [FFmpeg](https://ffmpeg.org) and [pywebview](https://pywebview.flowrl.com). See [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) for their licenses and for notes on the AI models. Each download also includes the full license texts.
