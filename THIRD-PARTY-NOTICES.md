# Third-party notices

ORemove's own code is licensed under the PolyForm Shield License 1.0.0 (see
[LICENSE](LICENSE)); that license doesn't apply to these components. The Windows
downloads also bundle the open-source components below, each under its own
license. The full license text of every bundled Python package is included in
the download as `THIRD-PARTY-LICENSES.txt`, generated when the app is built.

## Main components

| Component | Used for | License |
|---|---|---|
| [audio-separator](https://github.com/nomadkaraoke/python-audio-separator) | Running the UVR separation models | MIT |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Downloading audio from YouTube | Unlicense (public domain) |
| [Beat This!](https://github.com/CPJKU/beat_this) (Institute of Computational Perception, JKU Linz) | BPM detection: its model code is included in `oremove_analysis/beat_this/`, and its trained model downloads on first use | MIT |
| [PyTorch](https://pytorch.org) | AI engine | BSD-3-Clause |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) | Running ONNX models | MIT |
| [pywebview](https://github.com/r0x0r/pywebview) | Native window for the interface | BSD-3-Clause |
| [librosa](https://github.com/librosa/librosa), NumPy, SciPy, soundfile and others | Audio processing | ISC / BSD |
| [FFmpeg](https://ffmpeg.org) | Decoding and encoding audio | Windows: LGPL 2.1 or later; macOS: GPL v3 (see below) |
| NVIDIA CUDA libraries (GPU version only) | GPU acceleration | NVIDIA CUDA Toolkit EULA (redistributable components) |
| Microsoft Edge WebView2 Runtime installer (Windows NVIDIA installer only) | Installs the component the interface runs in, if missing | Microsoft redistributable terms |
| Python | Runtime | PSF License |

## FFmpeg

The downloads include FFmpeg's `ffmpeg` and `ffprobe` programs, unmodified.
ORemove runs them as separate programs. The exact build used is recorded in
`FFMPEG-LICENSE.txt` in each download, and you may replace the bundled FFmpeg
with your own build.

- **Windows:** LGPL builds from <https://github.com/BtbN/FFmpeg-Builds>,
  licensed under the GNU Lesser General Public License version 2.1 or later.
- **macOS:** release builds for Apple Silicon from <https://ffmpeg.martin-riedl.de>,
  licensed under the GNU General Public License version 3 (build scripts:
  <https://git.martin-riedl.de/ffmpeg/build-script>).

FFmpeg's source code is available at <https://ffmpeg.org/download.html> and
<https://github.com/FFmpeg/FFmpeg>.

## AI models

The separation models are created and published by their authors, not by
this project:

| Model | Author | Delivered |
|---|---|---|
| MDX-Net Inst HQ 3 | [Ultimate Vocal Remover](https://github.com/Anjok07/ultimatevocalremovergui) (Anjok07, aufr33) | Bundled |
| BS-Roformer 1297 / 1296 | viperx (via [audio-separator](https://github.com/nomadkaraoke/python-audio-separator)) | Downloaded from the model hosts on first use |

Several separation models were trained on research datasets with
non-commercial terms, and model weights often carry their own licenses or
none. ORemove is distributed free of charge; if you intend any
commercial use of the models or their output, check the terms of each model
first.

## Trademarks

YouTube is a trademark of Google LLC. Apple and macOS are trademarks of Apple Inc. ORemove is not
affiliated with, endorsed by or sponsored by YouTube, Google, Apple, or the
Ultimate Vocal Remover project.
