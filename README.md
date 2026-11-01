# Money-Extractor-YT2MP3-Tool

**YT Stem Extractor & UVR Studio**: a desktop app that takes a YouTube link and gives you a clean **Acapella** and **Instrumental**, using Ultimate Vocal Remover (UVR5) models through [`audio-separator`](https://github.com/nomadkaraoke/python-audio-separator).

```
output/
├── acapellas/       <Video Title> (Acapella).wav
└── instrumentals/   <Video Title> (Instrumental).wav
```

## Download for Windows

Both versions are on the [latest release](https://github.com/crawfordj0611-lang/Money-Extractor-YT2MP3-Tool/releases/latest).

### NVIDIA GPU version (recommended)

1. Download **all** the `YT-Stem-Extractor-GPU-Setup` files (the `.exe` and every `.bin` part) into the same folder. The installer is split into parts because GitHub limits each file to 2 GB.
2. Run `YT-Stem-Extractor-GPU-Setup.exe`. It installs for your user only (no admin needed) and adds a Start Menu shortcut.

Needs an NVIDIA GTX 900-series or newer card with a current driver. The top models run on the GPU, so a song takes seconds to a minute instead of many minutes.

### CPU version (any PC)

Download `YT-Stem-Extractor-Windows-CPU.zip`, extract it, and run `YT Stem Extractor.exe` inside the folder. It works on any 64-bit Windows PC but is much slower, especially with the Best models.

Both versions include FFmpeg and the Fast model. If Windows SmartScreen warns you (the app isn't code-signed), click **More info**, then **Run anyway**.

## Using it

1. Paste a YouTube link (or click **Paste**).
2. Choose a **Model**:

   | Model | What it is | Best for |
   |---|---|---|
   | **Best instrumental** (default) | BS-Roformer 1297 | The cleanest instrumentals of any model in audio-separator's benchmark |
   | **Best acapella** | BS-Roformer 1296 | The cleanest acapellas in the same benchmark |
   | **Fast** | MDX-Net Inst HQ 3 | Good results in a fraction of the time |

   Every model produces both an acapella and an instrumental; the name says which stem it does best. The two Best models are a large one-time download the first time you use each.
3. Choose **WAV** (lossless) or **MP3** (320 kbps), and where to save. The installed app saves to `Music\YT Stem Extractor` by default.
4. Click **Extract stems**. When it's done, **Play** opens a stem in your default player and **Show** opens it in Explorer.

The device badge in the top-right corner shows what the AI engine runs on: your GPU's name, or "CPU only". Your model, format and folder choices are remembered.

Existing files are never overwritten: a second run of the same video saves `… (Acapella) (2).wav`. Temporary files are deleted automatically, even if a job fails.

## System requirements

Times are estimates; they depend on your hardware and the song's length.

| | GPU version | CPU version |
|---|---|---|
| OS | Windows 10/11 64-bit | Windows 10/11 64-bit |
| Graphics | NVIDIA GTX 900-series or newer, 4 GB+ video memory (8 GB+ ideal for the Best models), current driver | Not used |
| Processor | Any recent 4+ core | 6–8 cores recommended (4 minimum) |
| RAM | 16 GB recommended | 8 GB minimum, 16 GB recommended |
| Disk | ~6 GB for the app, plus ~1.5 GB for the Best models | ~2 GB, plus ~1.5 GB for the Best models |
| Internet | Needed to download from YouTube and, once, the Best models | Same |

**What to expect:**

- **GPU, Best models:** roughly seconds to a minute for a 4-minute song on an RTX card.
- **CPU, Fast model:** roughly 2–6 minutes for a 4-minute song. **CPU, Best models:** several times longer, possibly 15–30+ minutes.
- **Fast model on the GPU version** runs on the CPU (it uses ONNX Runtime, not PyTorch). It's still quick.
- **Startup:** the window opens right away; the AI engine loads in the background (the device badge says "Loading engine…" until it's ready).
- **File sizes:** WAV is about 10 MB per minute of audio, so a 4-minute song gives two ~40 MB stems. MP3 (320 kbps) is about 2.4 MB per minute.

## Running from source

For macOS/Linux, or to develop the app. You need **Python 3.10 or newer** and **FFmpeg**.

### 1. Install FFmpeg

| OS | Command |
|----|---------|
| Windows | `winget install Gyan.FFmpeg` (then open a new terminal) |
| macOS | `brew install ffmpeg` |
| Ubuntu/Debian | `sudo apt install ffmpeg` |

On Linux you may also need Tk for the GUI: `sudo apt install python3-tk`.

### 2. Create a virtual environment and install

```bash
python -m venv .venv
```

Activate it (Windows: `.venv\Scripts\activate`, macOS/Linux: `source .venv/bin/activate`). For an NVIDIA GPU, install the CUDA build of PyTorch first:

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
```

Then:

```bash
pip install -r requirements.txt
```

Apple Silicon Macs use the GPU automatically.

### 3. Run

```bash
python app.py
```

From source, stems are saved to `output/` and models to `models/` next to `app.py`.

## Troubleshooting

- **"FFmpeg was not found"** (from source): install FFmpeg and restart the app from a new terminal.
- **Download errors on videos that play fine**: YouTube changes often; update yt-dlp (`pip install -U yt-dlp` from source; for the Windows app, a new release).
- **Age-restricted / private videos** can't be downloaded without signing in, so they're refused with a message.
- **Device badge says "CPU only" on the GPU version:** update your NVIDIA driver. Open **Show details** in the app for the reason.
- To use another UVR model, add it to `MODELS` at the top of `app.py`. List all available models with `audio-separator --list_models`.

Only download content you have the rights to use.
