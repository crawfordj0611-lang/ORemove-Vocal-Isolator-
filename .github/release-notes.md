Windows builds of **ORemove - AI Powered Vocal Isolator**: AI stem separation for YouTube links and your own audio files.

### What's new in 1.4.0: faster separation
- **NVIDIA GPUs:** BS-Roformer runs in 16-bit precision, and MDX-Net Inst HQ 3 now runs on the GPU too.
- **The model stays loaded** between songs, so only the first extraction waits for it.
- **New Quality option, "Faster":** roughly twice as fast with BS-Roformer, with a small quality trade-off.
- **Automatic low-memory retry** if the GPU runs out of memory.
- The log (**Show details**) now shows how long each separation took.

> **⚠️ GPU acceleration works with NVIDIA graphics cards only.** AMD and Intel GPUs are not supported. If your PC doesn't have an NVIDIA card, download the CPU edition.

### NVIDIA GPU edition: NVIDIA graphics cards only
For PCs with an **NVIDIA** GeForce GTX 900-series or newer card and a current driver.
Download **all** the `ORemove-NVIDIA-GPU-Setup` files (the `.exe` and every `.bin` part) into the same folder, then run `ORemove-NVIDIA-GPU-Setup.exe`. Installs for your user only; no admin needed. The installer warns you if it can't find an NVIDIA card.

### CPU edition: any PC, including AMD and Intel graphics
Download `ORemove-Windows-CPU.zip`, extract it, and run `ORemove.exe` inside. Works everywhere, but much slower.

FFmpeg and the MDX-Net model are included; the two BS-Roformer models download once on first use.
Step-by-step instructions: [Installation guide](https://github.com/crawfordj0611-lang/ORemove-Vocal-Isolator-/blob/main/INSTALL.md).
Windows SmartScreen may warn because the app isn't code-signed: click **More info**, then **Run anyway**.
