# Third-party notices

ORemove's own code is MIT-licensed (see [LICENSE](LICENSE)). The Windows
downloads also bundle the open-source components below, each under its own
license. The full license text of every bundled Python package is included in
the download as `THIRD-PARTY-LICENSES.txt`, generated when the app is built.

## Main components

| Component | Used for | License |
|---|---|---|
| [audio-separator](https://github.com/nomadkaraoke/python-audio-separator) | Running the UVR separation models | MIT |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Downloading audio from YouTube | Unlicense (public domain) |
| [PyTorch](https://pytorch.org) | AI engine | BSD-3-Clause |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) | Running ONNX models | MIT |
| [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) | User interface | MIT |
| [librosa](https://github.com/librosa/librosa), NumPy, SciPy, soundfile and others | Audio processing | ISC / BSD |
| [FFmpeg](https://ffmpeg.org) | Decoding and encoding audio | LGPL 2.1 or later (see below) |
| NVIDIA CUDA libraries (GPU version only) | GPU acceleration | NVIDIA CUDA Toolkit EULA (redistributable components) |
| Python | Runtime | PSF License |

## FFmpeg

The downloads include `ffmpeg.exe` and `ffprobe.exe` from the LGPL builds at
<https://github.com/BtbN/FFmpeg-Builds>. They are separate programs that
ORemove runs; they are not modified. FFmpeg is licensed under the
GNU Lesser General Public License version 2.1 or later. Its source code is
available at <https://ffmpeg.org/download.html> and
<https://github.com/FFmpeg/FFmpeg>, and the exact build used is recorded in
`FFMPEG-LICENSE.txt` in each download. You may replace the bundled FFmpeg
with your own build.

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

YouTube is a trademark of Google LLC. ORemove is not affiliated with,
endorsed by or sponsored by YouTube, Google, or the Ultimate Vocal Remover
project.
