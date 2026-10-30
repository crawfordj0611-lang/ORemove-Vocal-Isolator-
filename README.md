# Money-Extractor-YT2MP3-Tool

**YT Stem Extractor & UVR Studio**: a desktop app that takes a YouTube link and gives you a clean **Acapella** and **Instrumental**, using Ultimate Vocal Remover (UVR5) models through [`audio-separator`](https://github.com/nomadkaraoke/python-audio-separator).

```
output/
├── acapellas/       <Video Title> (Acapella).wav
└── instrumentals/   <Video Title> (Instrumental).wav
```

## Setup

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
