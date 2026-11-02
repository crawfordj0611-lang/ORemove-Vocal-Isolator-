**ORemove - AI Powered Vocal Isolator**: clean acapellas and instrumentals from YouTube links and your own audio files.

### What's new in 1.5.4
- **YouTube links keep working on their own.** When YouTube changes its site, the fix arrives as a yt-dlp update; ORemove now checks for one daily and installs it automatically (a ~3 MB download, verified before use). If a YouTube download fails, it checks right away and retries with the update. See the version, check now, or turn it off under **Settings → YouTube downloader**.
- **Please update if you use YouTube links.** 1.5.4 is the first version that updates yt-dlp; 1.5.3 and older never do, so their YouTube downloads will eventually stop working.

### What's new in 1.5.3
- **New models: MelBand Roformer** by becruily, for the fullest instrumentals and acapellas (871 MB each, downloaded on first use).
- **See where the time goes.** "Separated in" now means the separation itself, and the results screen breaks down the rest (getting the audio, first-time model download, starting the AI engine, saving) and shows the GPU memory used, so you can confirm your GPU is doing the work.
- **Faster start:** the AI engine and your chosen model start loading as soon as you begin choosing a song.

### What's new in 1.5.2
- **Much lighter while idle.** The AI engine no longer loads when the app opens: it loads when you start a song, and the model is freed after 10 idle minutes. The window no longer checks for updates 10 times a second.
- **Instant GPU badge.** Your NVIDIA card and driver are read straight from NVIDIA's own tool. If the engine then can't use the GPU, the app says exactly why (for example, a driver that's too old).
- **ORemove icon in the taskbar** on Windows, also after upgrading from an older version.

### What's new in 1.5.1
- **Accurate progress.** The percentage now follows the separation chunk by chunk, with time remaining, instead of sitting at 45% until the end. The first-use model download shows its own progress too.
- **Shows where it's running.** The progress screen and activity log say whether the model runs on your GPU or the CPU (which is much slower).
- **Faster on Mac:** BS-Roformer now uses 16-bit processing on Apple GPUs.
- macOS: no more "find devices on local networks" prompt, and no second app icon.

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
