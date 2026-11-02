<h1 align="center">ORemove</h1>
<h3 align="center">AI Powered Vocal Isolator</h3>

<p align="center">
  Clean acapellas and instrumentals from any YouTube link or audio file, powered by state-of-the-art Roformer models.
</p>

<p align="center">
  <a href="https://github.com/crawfordj0611-lang/ORemove-Vocal-Isolator-/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/crawfordj0611-lang/ORemove-Vocal-Isolator-?color=22C55E"></a>
  <img alt="Windows 10/11" src="https://img.shields.io/badge/Windows-10%20%7C%2011-0078D4">
  <img alt="macOS Apple Silicon" src="https://img.shields.io/badge/macOS-Apple%20Silicon-000000">
  <img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-22C55E">
</p>

<p align="center">
  <img src="docs/screenshot.png" alt="ORemove - AI Powered Vocal Isolator" width="720">
</p>

## Features

- **Drag, drop, done.** Audio files (MP3, WAV, FLAC, M4A and more), video files, or a YouTube link; one song or a whole batch.
- **Top-rated models.** BS-Roformer and MelBand Roformer for the cleanest results, plus a fast MDX-Net option.
- **GPU accelerated.** NVIDIA GPUs on Windows and Apple Silicon on Mac, with 16-bit processing for extra speed.
- **Listen instantly.** Waveforms, in-app playback, and A/B switching between vocals and instrumental.
- **Lossless and private.** WAV or 320 kbps MP3 output; everything runs on your computer.

## Download

**[Get the latest release](https://github.com/crawfordj0611-lang/ORemove-Vocal-Isolator-/releases/latest)**, then follow the **[installation guide](INSTALL.md)**.

| Edition | For | File |
|---|---|---|
| **Windows · NVIDIA GPU** | PCs with an NVIDIA GeForce GTX 900-series or newer | `ORemove-NVIDIA-GPU-Setup` (`.exe` + every `.bin` part) |
| **Windows · CPU** | Any 64-bit Windows PC, including AMD and Intel graphics | `ORemove-Windows-CPU.zip` |
| **macOS** | Apple Silicon (M1 or newer), macOS 13+ | `ORemove-macOS-AppleSilicon.dmg` |

> [!IMPORTANT]
> On Windows, GPU acceleration works with **NVIDIA graphics cards only**. On other PCs, use the CPU edition, which works everywhere but is much slower.

## Models

| Model | Best for | Size |
|---|---|---|
| **BS-Roformer 1297** (default) | Instrumentals: highest-rated in audio-separator's benchmark | 610 MB |
| **BS-Roformer 1296** | Acapellas: highest-rated in the same benchmark | 610 MB |
| **MelBand Roformer Inst** (becruily) | The fullest instrumentals | 871 MB |
| **MelBand Roformer Vocals** (becruily) | The fullest acapellas | 871 MB |
| **MDX-Net Inst HQ 3** | Speed | Included |

Models download once, on first use. **Quality: Faster** roughly halves Roformer processing time for a small quality trade-off.

## Requirements

| | Windows · NVIDIA GPU | Windows · CPU | macOS |
|---|---|---|---|
| **Hardware** | NVIDIA GPU, 8 GB VRAM recommended (4 GB minimum) | 6–8 core CPU recommended | Apple M1 or newer |
| **Memory** | 16 GB | 8 GB minimum, 16 GB recommended | 8 GB minimum, 16 GB recommended |
| **Disk** | ~6 GB + up to 3 GB of models | ~2 GB + up to 3 GB of models | ~2 GB + up to 3 GB of models |
| **4-minute song** | Seconds to about a minute | 15–30+ minutes (best models) | About 1–5 minutes |

Times are estimates. Stems are saved to **Music/ORemove**. For help, see [installation problems](INSTALL.md#9-installation-problems).

## Running from source

Requires Python 3.10+ and FFmpeg. For an NVIDIA GPU, first install the CUDA build of PyTorch (`pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126`).

```bash
pip install -r requirements.txt
```

```bash
python app.py
```

ORemove is a Python engine ([`app.py`](app.py)) built on [audio-separator](https://github.com/nomadkaraoke/python-audio-separator) and [yt-dlp](https://github.com/yt-dlp/yt-dlp), with a web interface ([`ui/`](ui)) in a native window via [pywebview](https://pywebview.flowrl.com). The [build workflow](.github/workflows/build.yml) packages and tests all three editions on GitHub Actions.

## License

[MIT](LICENSE). Built on the work of [Ultimate Vocal Remover](https://github.com/Anjok07/ultimatevocalremovergui) and its model authors, audio-separator, yt-dlp, PyTorch, FFmpeg and pywebview; see [third-party notices](THIRD-PARTY-NOTICES.md).

Only separate audio you own or have permission to use; downloading from YouTube may be restricted by its Terms of Service. ORemove is not affiliated with YouTube, Google, Apple or the Ultimate Vocal Remover project.
