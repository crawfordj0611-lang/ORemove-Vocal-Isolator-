**ORemove - AI Powered Vocal Isolator**: clean acapellas and instrumentals from YouTube links and your own audio files.

### What's new in 1.5.0
- **All-new interface.** A clean, Apple-style design: drop files anywhere on the window, watch a live progress ring, and get results as two cards with waveforms.
- **Listen inside the app.** Play the vocals and instrumental right away, click a waveform to jump, and switch between them at the same point with **A/B**.
- **History** of every song you've separated, ready to play again.
- **Stop** a job between steps.
- **Mac version** for Apple Silicon (M1 or newer), using the Mac's GPU.
- **Faster separation:** 16-bit processing on NVIDIA GPUs, MDX-Net Inst HQ 3 now runs on the GPU too, the model stays loaded between songs, and a new **Quality: Faster** option roughly halves BS-Roformer time.
- A proper app icon.

> **⚠️ On Windows, GPU acceleration works with NVIDIA graphics cards only.** AMD and Intel GPUs are not supported; use the CPU edition on those PCs.

### Windows · NVIDIA GPU edition (NVIDIA graphics cards only)
Download **all** the `ORemove-NVIDIA-GPU-Setup` files (the `.exe` and every `.bin` part) into the same folder, then run `ORemove-NVIDIA-GPU-Setup.exe`. Installs for your user only; no admin needed. The installer warns you if it can't find an NVIDIA card, and installs Microsoft's WebView2 component if needed. Needs a GeForce GTX 900-series or newer card and a current driver.

### Windows · CPU edition (any PC, including AMD and Intel graphics)
Download `ORemove-Windows-CPU.zip`, extract it, and run `ORemove.exe` inside. Works everywhere, but much slower.

### macOS · Apple Silicon
Download `ORemove-macOS-AppleSilicon.dmg`, open it and drag ORemove into Applications. Needs an M1 or newer Mac with macOS 13 or newer. The first time, macOS blocks it because it isn't notarized: see the installation guide for the one-time fix.

FFmpeg and the MDX-Net model are included; the two BS-Roformer models download once on first use.
Step-by-step instructions: [Installation guide](https://github.com/crawfordj0611-lang/ORemove-Vocal-Isolator-/blob/main/INSTALL.md).
Windows SmartScreen may warn because the app isn't code-signed: click **More info**, then **Run anyway**.
