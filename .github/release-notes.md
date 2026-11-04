**ORemove - AI Powered Vocal Isolator**: clean acapellas and instrumentals from YouTube links and your own audio files. Free, and everything runs on your computer.

## What's new in 1.5.8
- **BPM and key detection.** Each song's BPM and key (with its Camelot code) are read from the instrumental, so the vocals don't throw them off, and shown on the results and in History. Click a tag to copy it. Uncertain readings are marked "?", with the alternative on hover. The beat tracker is a one-time 81 MB download; turn it off under **Settings → Output**.
- **Stop works straight away**, even halfway through a song, and progress shows each chunk as it's separated.
- **Fixed:** releasing *Drag to DAW* over ORemove's own window started separating the stem again and broke the A/B player.
- **Steadier on long songs and smaller GPUs:** if the GPU runs out of memory, ORemove frees it and tries again with lower-memory settings, and it returns GPU memory between songs.
- **Instant playback:** stems are fully loaded, so seeking and A/B switching never wait for the disk.

<details>
<summary><b>Earlier updates (1.5.6 and 1.5.7)</b></summary>

**1.5.7**
- **Speed test** (**Settings → Performance**) finds the fastest way to run the Roformer models on your GPU. Recommended on Macs.
- More accurate time remaining, a log file for problem reports (**Settings → Activity**), better GPU memory handling on Macs, and every library locked to a tested version.

**1.5.6**
- A cleaner, darker design; the model menu shows which models are installed.
- One-file NVIDIA install, update notices, and a welcome guide on first launch.
- More reliable YouTube links (Deno is set up automatically alongside yt-dlp updates).
- Drag stems into your DAW from the results (Windows).
- Faster WAV output that keeps your file's bit depth, and no more console windows flashing up.
</details>

## Download

| Edition | Download |
|---|---|
| **Windows · NVIDIA GPU** (GeForce GTX 900-series or newer) | `ORemove-NVIDIA-GPU-Setup.exe` only |
| **Windows · CPU** (any PC, including AMD and Intel graphics) | `ORemove-Windows-CPU.zip` |
| **macOS · Apple Silicon** (M1 or newer, macOS 13+) | `ORemove-macOS-AppleSilicon.dmg` |

- **NVIDIA:** run the Setup. It downloads the rest of ORemove (about 1.9 GB), checks every part and deletes the downloaded parts afterwards; the `.bin` and `parts.txt` files below are what it fetches, so you don't need them. No admin rights needed. **GPU acceleration on Windows works with NVIDIA cards only**; use the CPU edition on other PCs (it's much slower).
- **CPU:** extract the zip and run `ORemove.exe`.
- **Mac:** open the dmg and drag ORemove into Applications. macOS blocks it the first time because it isn't notarized; the installation guide has the one-time fix.
- **Windows SmartScreen** may warn because the app isn't code-signed: click **More info**, then **Run anyway**.

FFmpeg and the MDX-Net model are included; Roformer models download once, on first use. Step-by-step help: [installation guide](https://github.com/crawfordj0611-lang/ORemove-Vocal-Isolator-/blob/main/INSTALL.md).

---

**ORemove is free.** It's licensed under [PolyForm Shield 1.0.0](https://github.com/crawfordj0611-lang/ORemove-Vocal-Isolator-/blob/main/LICENSE) with permission to share it for free, but never charge for it. If anyone charged you for ORemove, you've been scammed.

**Use ORemove responsibly:** only separate audio you own, have permission to use, or are otherwise legally allowed to use. Releasing or selling stems from someone else's music usually needs the rights holder's permission, and downloading from YouTube may break YouTube's Terms of Service.
