**ORemove - AI Powered Vocal Isolator**: clean acapellas and instrumentals from YouTube links and your own audio files. Free, and everything runs on your computer.

## What's new in 1.5.9
A speed release: the same features, faster. The stems come out the same (the new engine was checked sample by sample against the old one).
- **Batches finish sooner.** While one song separates, the next one downloads and the previous one is saved and gets its BPM and key, so the GPU never waits. The model also loads while your first song downloads.
- **Each song is read once.** Audio goes straight into memory instead of through a temporary WAV file, and stems are written straight into your output folder.
- **Macs:** on some Macs, PyTorch can't do part of the Roformer models' maths on the Apple GPU, so those steps ran on the CPU, copying audio back and forth for every chunk. ORemove can now do them on the GPU. Run the **speed test** (**Settings → Performance**) once so it picks the fastest option for your Mac; the log file shows which way your Mac uses.
- **Faster on bigger NVIDIA cards:** cards with 10 GB or more separate two chunks at a time (the speed test also tries four on 12 GB cards), and drop back automatically if memory runs short.
- **No more pauses between chunks:** the GPU keeps working while the previous chunk's result is added up, instead of stopping to copy it back to the CPU.
- **Faster CPU edition:** one thread per physical core, and faster handling of very small numbers. The speed test can now tune the CPU edition too.
- **Models load about 40% faster** the first time after opening ORemove.

<details>
<summary><b>Earlier updates (1.5.6 to 1.5.8)</b></summary>

**1.5.8**
- **BPM and key detection** from the instrumental, with Camelot codes, on the results and in History (**Settings → Output** to turn off).
- **Stop works straight away**, and progress shows each chunk.
- **Fixed:** releasing *Drag to DAW* over ORemove's own window started separating the stem again.
- Steadier on long songs and smaller GPUs, GPU memory returned after every song, faster YouTube downloads and instant playback.

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
