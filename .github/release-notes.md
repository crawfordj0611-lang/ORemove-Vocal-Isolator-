**ORemove - AI Powered Vocal Isolator**: clean acapellas and instrumentals from YouTube links and your own audio files.

### What's new in 1.5.5
- **New license.** ORemove is now licensed under [PolyForm Shield 1.0.0](https://github.com/crawfordj0611-lang/ORemove-Vocal-Isolator-/blob/main/LICENSE) with an added permission to share it for free: use it for anything (including paid music work), change it and give it away free (bundling with free software included), but never charge for it. **ORemove is free; if anyone charged you for it, you've been scammed.**
- **YouTube links keep working on their own.** ORemove checks daily for yt-dlp updates (the YouTube downloader) and installs them automatically; see **Settings → YouTube downloader**.

> **⚠️ On Windows, GPU acceleration works with NVIDIA graphics cards only.** AMD and Intel GPUs are not supported; use the CPU edition on those PCs.

### Windows · NVIDIA GPU edition (NVIDIA graphics cards only)
Download **all** the `ORemove-NVIDIA-GPU-Setup` files (the `.exe` and every `.bin` part) into the same folder, then run `ORemove-NVIDIA-GPU-Setup.exe`. Installs for your user only; no admin needed. The installer warns you if it can't find an NVIDIA card, and installs Microsoft's WebView2 component if needed. Needs a GeForce GTX 900-series or newer card and a current driver.

### Windows · CPU edition (any PC, including AMD and Intel graphics)
Download `ORemove-Windows-CPU.zip`, extract it, and run `ORemove.exe` inside. Works everywhere, but much slower.

### macOS · Apple Silicon
Download `ORemove-macOS-AppleSilicon.dmg`, open it and drag ORemove into Applications. Needs an M1 or newer Mac with macOS 13 or newer. The first time, macOS blocks it because it isn't notarized: see the installation guide for the one-time fix.

FFmpeg and the MDX-Net model are included; the Roformer models download once, on first use.
Step-by-step instructions: [Installation guide](https://github.com/crawfordj0611-lang/ORemove-Vocal-Isolator-/blob/main/INSTALL.md).
Windows SmartScreen may warn because the app isn't code-signed: click **More info**, then **Run anyway**.
