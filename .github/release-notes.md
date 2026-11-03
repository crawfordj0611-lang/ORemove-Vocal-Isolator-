**ORemove - AI Powered Vocal Isolator**: clean acapellas and instrumentals from YouTube links and your own audio files.

### What's new in 1.5.7
A stability and speed release; no new features.
- **Speed test** (Settings → Performance): finds the fastest way to run the Roformer models on your computer's GPU and uses it from then on. Recommended on Macs, where it can make separation noticeably faster.
- **More accurate time remaining**: the estimate no longer starts out far too long while the GPU warms up.
- **Log file for problem reports** (Settings → Activity → Open log folder), including details of any crash.
- Frees GPU memory properly on Macs between songs.
- Every library is now locked to a tested version, so updates can't break ORemove unexpectedly.

- **A cleaner, darker design.** Model, quality and format sit in one row under the drop area; the model menu shows which models are **Installed**; Settings is reorganised to fit without scrolling.
- **One-file NVIDIA install.** Download just `ORemove-NVIDIA-GPU-Setup.exe`: it downloads and checks the rest itself.
- **More reliable YouTube links.** ORemove sets up Deno, the JavaScript runtime YouTube now requires (a one-time 40 MB download on your first link), alongside the automatic yt-dlp updates.
- **Update notices.** ORemove tells you when a new version is out.
- **Drag stems into your DAW** straight from the results cards (Windows).
- **Faster.** WAV stems are written directly, keeping your file's bit depth (24-bit sources give 24-bit stems), and your chosen model loads in the background a few seconds after opening.
- **Welcome guide** on first launch, with the option to install the recommended model straight away.
- **No more console windows** flashing up when separation finishes, and smaller downloads.
- ORemove is licensed under [PolyForm Shield 1.0.0](https://github.com/crawfordj0611-lang/ORemove-Vocal-Isolator-/blob/main/LICENSE) with an added permission to share it for free; never charge for it. **If anyone charged you for ORemove, you've been scammed.**

> **⚠️ On Windows, GPU acceleration works with NVIDIA graphics cards only.** AMD and Intel GPUs are not supported; use the CPU edition on those PCs.

### Windows · NVIDIA GPU edition (NVIDIA graphics cards only)
**Download only `ORemove-NVIDIA-GPU-Setup.exe` and run it.** Setup downloads the rest of ORemove (about 1.9 GB) and checks every part, then removes the downloaded parts once it's installed. The `.bin` files and `ORemove-NVIDIA-GPU-Setup-parts.txt` below are what it downloads: you don't need to download them yourself.

Installs for your user only; no admin needed. The installer warns you if it can't find an NVIDIA card, and installs Microsoft's WebView2 component if needed. Needs a GeForce GTX 900-series or newer card and a current driver.

### Windows · CPU edition (any PC, including AMD and Intel graphics)
Download `ORemove-Windows-CPU.zip`, extract it, and run `ORemove.exe` inside. Works everywhere, but much slower.

### macOS · Apple Silicon
Download `ORemove-macOS-AppleSilicon.dmg`, open it and drag ORemove into Applications. Needs an M1 or newer Mac with macOS 13 or newer. The first time, macOS blocks it because it isn't notarized: see the installation guide for the one-time fix.

FFmpeg and the MDX-Net model are included; the Roformer models download once, on first use.
Step-by-step instructions: [Installation guide](https://github.com/crawfordj0611-lang/ORemove-Vocal-Isolator-/blob/main/INSTALL.md).
Windows SmartScreen may warn because the app isn't code-signed: click **More info**, then **Run anyway**.
