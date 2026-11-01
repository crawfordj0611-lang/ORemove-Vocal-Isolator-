# Money-Extractor-YT2MP3-Tool

**YT Stem Extractor & UVR Studio**: a desktop app that takes a YouTube link and gives you a clean **Acapella** and **Instrumental**, using Ultimate Vocal Remover (UVR5) models through [`audio-separator`](https://github.com/nomadkaraoke/python-audio-separator).

```
output/
├── acapellas/       <Video Title> (Acapella).wav
└── instrumentals/   <Video Title> (Instrumental).wav
```

## Download for Windows

Get **YT-Stem-Extractor-Windows.zip** from the [latest release](https://github.com/crawfordj0611-lang/Money-Extractor-YT2MP3-Tool/releases/latest) (about 465 MB).

1. Extract the zip.
2. Open the `YT Stem Extractor` folder and run `YT Stem Extractor.exe`.
3. If Windows SmartScreen warns you (the app isn't code-signed), click **More info**, then **Run anyway**.

FFmpeg and the default UVR model are included, so there is nothing else to install.

**This is the CPU version.** It runs on any 64-bit Windows PC but does not use an NVIDIA graphics card, even if you have one. A GPU build would be over 2 GB, too large for a single GitHub release file. For GPU speed, run from source with CUDA (see [Setup](#setup), step 3).

## System requirements

These are estimates; actual times depend heavily on your processor and the song's length.

| | Minimum (runs, slowly) | Recommended |
|---|---|---|
| OS | Windows 10/11 64-bit (or macOS/Linux from source) | Windows 10/11 64-bit |
| Processor | 4 cores, from the last ~8 years | 6–8 cores (Ryzen 5/7, Core i5/i7, 2018 or newer) |
| RAM | 8 GB | 16 GB |
| Disk | ~2 GB free (app is ~1.5 GB extracted) | SSD, 5 GB+ free if you keep lots of WAV stems |
| Internet | Needed to download from YouTube | |

**What to expect with the CPU version:**

- **First start:** 10–30 seconds while the AI engine (PyTorch) loads.
- **Separation:** roughly 2–6 minutes for a 4-minute song on a recommended PC, 10+ minutes on a minimum-spec PC. Time grows with song length.
- **Your PC slows down while it works:** separation uses most of the processor.
- **Memory:** usually peaks at a few GB; very long tracks (30+ minutes) use more.
- **File sizes:** WAV is about 10 MB per minute of audio, so a 4-minute song gives two ~40 MB stems. MP3 (320 kbps) is about 2.4 MB per minute.

**Models:** Inst HQ 3 and Kim_Vocal_2 take about the same time. UVR HP-Vocal is usually somewhat faster on CPU.

**GPU option (from source):** an NVIDIA card with 4 GB+ video memory (GTX 1650 or better, ideally RTX) and a current driver cuts a song down to seconds. Apple Silicon Macs also use their GPU automatically when run from source.

## Setup

These steps are for running from source (any OS, or to use an NVIDIA GPU). On Windows you can skip them and use the [download](#download-for-windows) instead.

You need **Python 3.10 or newer** and **FFmpeg**.

### 1. Install FFmpeg

| OS | Command |
|----|---------|
| Windows | `winget install Gyan.FFmpeg` (then open a new terminal) |
| macOS | `brew install ffmpeg` |
| Ubuntu/Debian | `sudo apt install ffmpeg` |

Check it works: `ffmpeg -version`. The app also checks on startup and warns you in the log if it's missing.

On Linux you may also need Tk for the GUI: `sudo apt install python3-tk`.

### 2. Create a virtual environment and install

```bash
python -m venv .venv
```

Activate it (Windows: `.venv\Scripts\activate`, macOS/Linux: `source .venv/bin/activate`), then:

```bash
pip install -r requirements.txt
```

### 3. (Optional) NVIDIA GPU acceleration

CPU works everywhere but a song takes a few minutes; a CUDA GPU makes it take seconds. Install a CUDA build of PyTorch **before** the requirements, and use the GPU build of audio-separator:

```bash
pip install torch --index-url https://download.pytorch.org/whl/cu124
```

```bash
pip install customtkinter yt-dlp "audio-separator[gpu]"
```

Pick the CUDA version that matches your driver at <https://pytorch.org/get-started/locally/>. The log shows "GPU detected" when it's in use. Apple Silicon Macs use the GPU (MPS) automatically.

### 4. Run

```bash
python app.py
```

## Using it

1. Paste a YouTube link (or click **Paste**).
2. Choose a model:
   - **UVR-MDX-NET Inst HQ 3**: best all-round, especially clean instrumentals (default).
   - **Kim_Vocal_2**: tuned for clean acapellas.
   - **UVR HP-Vocal (VR architecture)**: the classic UVR "HP" vocal model (`4_HP-Vocal-UVR.pth`).
3. Choose **WAV** (lossless) or **MP3** (320 kbps), and an output folder.
4. Click **Start Extraction**. Follow progress in the log, then click **Open Output Folder**.

**First run:** each model is downloaded once (roughly 50–200 MB) into the `models/` folder next to `app.py`, and reused after that. The first start of the AI engine is also slower while PyTorch loads.

Temporary downloads go to your system temp folder and are deleted automatically, even if a job fails. Existing files are never overwritten: a second run of the same video saves `… (Acapella) (2).wav`.

## Troubleshooting

- **"FFmpeg was not found"**: install FFmpeg (step 1) and restart the app from a new terminal.
- **Download errors on videos that play fine**: YouTube changes often; update yt-dlp with `pip install -U yt-dlp`.
- **Age-restricted / private videos** can't be downloaded without signing in, so they're refused with a message.
- To use another UVR model, add it to `UVR_MODELS` at the top of `app.py`. List all available models with `audio-separator --list_models`.

Only download content you have the rights to use.
