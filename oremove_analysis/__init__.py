"""
BPM and key detection for ORemove.

Both run on the separated instrumental stem: with the vocals removed, the
drums give a clean beat and the harmony gives a clean key, which is more
accurate than analysing the full mix.

BPM: the "Beat This!" beat tracker (Foscarin, Schlüter & Widmer, ISMIR 2024;
MIT licence, see beat_this/LICENSE), a transformer trained on 16 beat-tracking
datasets. It finds every beat; the tempo is then measured from the beats
themselves (the median beat period over the steady part of the song, refined
by a straight-line fit), so it lands on the exact BPM rather than a coarse
tempo bin. Its audio front end is reimplemented here on plain PyTorch, because
the original uses torchaudio, which is no longer published for current PyTorch.

Key: a constant-Q chromagram of the harmonic part of the instrumental (tuning
estimated automatically), weighted by loudness, matched against three
established key profiles (Krumhansl-Kessler, Temperley-Kostka-Payne, and
KeyFinder's Shaath profiles) for all 24 keys. The profiles vote; the margin
between the best and second-best key gives the confidence.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

import numpy as np

# The trained beat tracker: downloaded once (81 MB) and checked against this hash.
CHECKPOINT_URL = "https://cloud.cp.jku.at/public.php/dav/files/7ik4RrBKTS273gp/final0.ckpt"
CHECKPOINT_FILE = "beat_this-final0.ckpt"
CHECKPOINT_SHA256 = "8c328b45f59d8dd3dff219253ff6a8d6482be57d0133a29140e2febbf8eb8331"
CHECKPOINT_MB = 81

SAMPLE_RATE = 22050
FPS = 50  # beat tracker frames per second (hop 441 at 22050 Hz)


# --------------------------------------------------------------------------- #
# Beat tracker front end (identical to torchaudio's MelSpectrogram as used by
# Beat This!: 1024-point STFT, hop 441, Hann window, "frame_length"
# normalisation, magnitude, 128 Slaney mel bands from 30 Hz to 11 kHz;
# checked against torchaudio 2.11 to within float rounding).
# --------------------------------------------------------------------------- #

def _hz_to_mel_slaney(freq):
    f_sp = 200.0 / 3
    min_log_hz, logstep = 1000.0, math.log(6.4) / 27.0
    min_log_mel = min_log_hz / f_sp
    freq = np.asarray(freq, dtype=np.float64)
    mels = freq / f_sp
    log_part = freq >= min_log_hz
    mels = np.where(log_part, min_log_mel + np.log(np.maximum(freq, 1e-10) / min_log_hz) / logstep, mels)
    return mels


def _mel_to_hz_slaney(mels):
    f_sp = 200.0 / 3
    min_log_hz, logstep = 1000.0, math.log(6.4) / 27.0
    min_log_mel = min_log_hz / f_sp
    mels = np.asarray(mels, dtype=np.float64)
    freqs = f_sp * mels
    log_part = mels >= min_log_mel
    return np.where(log_part, min_log_hz * np.exp(logstep * (mels - min_log_mel)), freqs)


def mel_filterbank(n_freqs=513, f_min=30.0, f_max=11000.0, n_mels=128, sample_rate=SAMPLE_RATE):
    """Triangular Slaney mel filters, shape (n_freqs, n_mels), no area normalisation."""
    all_freqs = np.linspace(0, sample_rate // 2, n_freqs)
    m_pts = np.linspace(_hz_to_mel_slaney(f_min), _hz_to_mel_slaney(f_max), n_mels + 2)
    f_pts = _mel_to_hz_slaney(m_pts)
    f_diff = f_pts[1:] - f_pts[:-1]
    slopes = f_pts[None, :] - all_freqs[:, None]
    down = (-slopes[:, :-2]) / f_diff[:-1]
    up = slopes[:, 2:] / f_diff[1:]
    return np.maximum(0.0, np.minimum(down, up)).astype(np.float32)


class LogMelSpect:
    def __init__(self, device="cpu"):
        import torch
        self.torch = torch
        self.device = device
        self.window = torch.hann_window(1024, device=device)
        self.fb = torch.from_numpy(mel_filterbank()).to(device)

    def __call__(self, signal):
        """Mono waveform at 22050 Hz -> log mel spectrogram, shape (frames, 128)."""
        torch = self.torch
        # torchaudio's normalized="frame_length" is torch.stft(normalized=True): divide by sqrt(n_fft).
        spec = torch.stft(signal, n_fft=1024, hop_length=441, win_length=1024, window=self.window,
                          center=True, pad_mode="reflect", normalized=True, onesided=True, return_complex=True).abs()
        mel = torch.matmul(spec.transpose(0, 1), self.fb)
        return torch.log1p(1000 * mel)


# --------------------------------------------------------------------------- #
# Chunked inference (from Beat This!, beat_this/inference.py, MIT licence)
# --------------------------------------------------------------------------- #

def _split_piece(spect, chunk_size, border_size=6):
    import torch.nn.functional as F
    starts = np.arange(-border_size, len(spect) - border_size, chunk_size - 2 * border_size)
    if len(spect) > chunk_size - 2 * border_size:
        starts[-1] = len(spect) - (chunk_size - border_size)
    chunks = []
    for start in starts:
        piece = spect[max(start, 0): min(start + chunk_size, len(spect))]
        left = max(0, -start)
        right = max(0, min(border_size, start + chunk_size - len(spect)))
        chunks.append(F.pad(piece, (0, 0, left, right)) if left or right else piece)
    return chunks, starts


def _predict(model, spect, device, float16, cancel=None):
    import torch
    chunk_size, border = 1500, 6
    chunks, starts = _split_piece(spect, chunk_size, border)
    beat = torch.full((spect.shape[0],), -1000.0, device=device)
    downbeat = torch.full((spect.shape[0],), -1000.0, device=device)
    with torch.inference_mode(), torch.autocast(device_type=torch.device(device).type, enabled=float16):
        # keep_first: later chunks are written first, so earlier ones win in overlaps
        for start, chunk in reversed(list(zip(starts, chunks))):
            if cancel:
                cancel()
            pred = model(chunk.unsqueeze(0))
            b, d = pred["beat"][0], pred["downbeat"][0]
            if border:
                b, d = b[border:-border], d[border:-border]
            beat[start + border: start + chunk_size - border] = b.float()
            downbeat[start + border: start + chunk_size - border] = d.float()
    return beat, downbeat


# --------------------------------------------------------------------------- #
# Beat tracker
# --------------------------------------------------------------------------- #

class BeatTracker:
    """Loads the Beat This! model once and keeps it for later songs."""

    def __init__(self, model_dir: Path, device: str = "cpu"):
        self.model_dir = Path(model_dir)
        self.device = device
        self.model = None
        self.spect = None

    @property
    def checkpoint(self) -> Path:
        return self.model_dir / CHECKPOINT_FILE

    def installed(self) -> bool:
        return self.checkpoint.is_file()

    def install(self, download, on_progress=None):
        """Download and verify the model. `download(url, timeout, on_progress) -> bytes`."""
        data = download(CHECKPOINT_URL, 120, on_progress)
        if hashlib.sha256(data).hexdigest() != CHECKPOINT_SHA256:
            raise RuntimeError("the downloaded beat tracker didn't match its checksum")
        self.model_dir.mkdir(parents=True, exist_ok=True)
        temp = self.checkpoint.with_suffix(".part")
        temp.write_bytes(data)
        temp.replace(self.checkpoint)

    def _load(self):
        if self.model is not None:
            return
        import inspect
        import torch
        from .beat_this.beat_tracker import BeatThis
        from .beat_this.utils import replace_state_dict_key
        ckpt = torch.load(self.checkpoint, map_location="cpu", weights_only=True)
        params = set(inspect.signature(BeatThis).parameters)
        model = BeatThis(**{k: v for k, v in ckpt["hyper_parameters"].items() if k in params})
        model.load_state_dict(replace_state_dict_key(ckpt["state_dict"], "model.", ""))
        self.model = model.to(self.device).eval()
        self.spect = LogMelSpect(self.device)

    def beats(self, mono: np.ndarray, cancel=None) -> tuple[np.ndarray, np.ndarray]:
        """Beat and downbeat times in seconds, for mono audio at 22050 Hz."""
        import torch
        from .beat_this.postprocessor import Postprocessor
        self._load()
        signal = torch.tensor(mono, dtype=torch.float32, device=self.device)
        spect = self.spect(signal)
        beat, downbeat = _predict(self.model, spect, self.device, self.device == "cuda", cancel)
        found_beats, found_downbeats = Postprocessor(type="minimal", fps=FPS)(beat.cpu(), downbeat.cpu())
        return np.asarray(found_beats, dtype=np.float64), np.asarray(found_downbeats, dtype=np.float64)


def tempo_from_beats(beats: np.ndarray) -> dict | None:
    """
    The BPM implied by beat times: the median period over the steady part of
    the song, refined with a straight-line fit through the beats that follow
    it. Returns {"bpm", "confidence", "alternative"} or None.
    """
    if len(beats) < 8:
        return None
    periods = np.diff(beats)
    median = float(np.median(periods))
    if median <= 0:
        return None
    steady = np.abs(periods / median - 1) < 0.06
    confidence = float(steady.mean())
    # Refine over the longest run of steady beats: in a song at constant
    # tempo, beat times lie on a line whose slope is the period.
    best_start, best_len, run_start = 0, 0, None
    for i, ok in enumerate(list(steady) + [False]):
        if ok and run_start is None:
            run_start = i
        elif not ok and run_start is not None:
            if i - run_start > best_len:
                best_start, best_len = run_start, i - run_start
            run_start = None
    period = median
    if best_len >= 8:
        times = beats[best_start: best_start + best_len + 1]
        period = float(np.polyfit(np.arange(len(times)), times, 1)[0])
    bpm = 60.0 / period
    # Most music is made at a whole-number tempo: snap when it's that close.
    bpm = float(round(bpm)) if abs(bpm - round(bpm)) < 0.3 else round(bpm, 1)
    alternative = None
    if bpm < 85:
        alternative = bpm * 2
    elif bpm >= 150:
        alternative = round(bpm / 2, 1)
    if alternative is not None and float(alternative).is_integer():
        alternative = int(alternative)
    return {"bpm": int(bpm) if float(bpm).is_integer() else bpm, "confidence": round(confidence, 2),
            "alternative": alternative}


# --------------------------------------------------------------------------- #
# Key
# --------------------------------------------------------------------------- #

NOTES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]

# Key profiles: how strongly each scale degree (from the tonic up) belongs to
# a major and a minor key.
PROFILES = {
    "krumhansl": ([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88],
                  [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]),
    "temperley": ([0.748, 0.060, 0.488, 0.082, 0.670, 0.460, 0.096, 0.715, 0.104, 0.366, 0.057, 0.400],
                  [0.712, 0.084, 0.474, 0.618, 0.049, 0.460, 0.105, 0.747, 0.404, 0.067, 0.133, 0.330]),
    "shaath": ([6.6, 2.0, 3.5, 2.3, 4.6, 4.0, 2.5, 5.2, 2.4, 3.7, 2.3, 3.4],
               [6.5, 2.7, 3.5, 5.4, 2.6, 3.5, 2.5, 5.2, 4.0, 2.7, 4.3, 3.2]),
}

# Camelot wheel codes (used by DJs for harmonic mixing).
CAMELOT_MAJOR = {"B": "1B", "F#": "2B", "C#": "3B", "Ab": "4B", "Eb": "5B", "Bb": "6B", "F": "7B",
                 "C": "8B", "G": "9B", "D": "10B", "A": "11B", "E": "12B"}
CAMELOT_MINOR = {"Ab": "1A", "Eb": "2A", "Bb": "3A", "F": "4A", "C": "5A", "G": "6A", "D": "7A",
                 "A": "8A", "E": "9A", "B": "10A", "F#": "11A", "C#": "12A"}


def key_name(tonic: int, minor: bool) -> str:
    return f"{NOTES[tonic]} {'minor' if minor else 'major'}"


def camelot(tonic: int, minor: bool) -> str:
    return (CAMELOT_MINOR if minor else CAMELOT_MAJOR)[NOTES[tonic]]


def key_scores(chroma: np.ndarray) -> np.ndarray:
    """Average correlation of a 12-bin chroma vector with every key, shape (24,):
    index tonic for major, 12 + tonic for minor."""
    chroma = np.asarray(chroma, dtype=np.float64)
    chroma = (chroma - chroma.mean()) / (chroma.std() + 1e-12)
    total = np.zeros(24)
    for major, minor in PROFILES.values():
        for mode, profile in enumerate((major, minor)):
            p = np.asarray(profile, dtype=np.float64)
            p = (p - p.mean()) / p.std()
            for tonic in range(12):
                total[mode * 12 + tonic] += float(np.dot(chroma, np.roll(p, tonic)) / 12)
    return total / len(PROFILES)


def song_chroma(mono: np.ndarray, sr: int = SAMPLE_RATE) -> tuple[np.ndarray, np.ndarray]:
    """
    Loudness-weighted average chroma of the harmonic part of the audio, for
    the whole range and for the bass alone (C1-B3, where the bass and 808 sit).
    """
    import librosa
    harmonic = librosa.effects.harmonic(mono, margin=2.0)
    cqt = np.abs(librosa.cqt(harmonic, sr=sr, hop_length=2048, fmin=librosa.note_to_hz("C1"),
                             n_bins=36 * 7, bins_per_octave=36))
    tuning = librosa.estimate_tuning(y=harmonic, sr=sr, bins_per_octave=36)
    to_chroma = lambda c: librosa.feature.chroma_cqt(C=c, sr=sr, bins_per_octave=36, norm=None, tuning=tuning)
    full, bass = to_chroma(cqt), to_chroma(cqt[: 36 * 3])
    energy = full.sum(axis=0)
    if not np.any(energy > 0):
        return np.zeros(12), np.zeros(12)
    weights = energy * (energy > np.percentile(energy, 20))  # skip near-silent frames
    return (full * weights).sum(axis=1), (bass * weights).sum(axis=1)


def detect_key(mono: np.ndarray, sr: int = SAMPLE_RATE) -> dict | None:
    chroma, bass = song_chroma(mono, sr)
    if not np.any(chroma > 0):
        return None
    scores = key_scores(chroma)
    if np.any(bass > 0):
        # The bass usually sits on the tonic: it separates a minor key from its
        # relative major (same notes, different home note), the classic mix-up.
        bass = bass / (bass.sum() + 1e-12)
        for i in range(24):
            scores[i] += 0.6 * (bass[i % 12] - 1 / 12)
    order = np.argsort(scores)[::-1]
    best, second = int(order[0]), int(order[1])
    margin = float(scores[best] - scores[second])
    as_key = lambda i: {"name": key_name(i % 12, i >= 12), "camelot": camelot(i % 12, i >= 12)}
    return {**as_key(best), "confidence": round(min(1.0, margin / 0.12), 2), "alternative": as_key(second)}


# --------------------------------------------------------------------------- #
# Whole song
# --------------------------------------------------------------------------- #

def load_mono(path: Path, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Mono float32 audio at `sr` (22050 Hz), resampled with soxr (high quality)."""
    import soxr
    try:
        import soundfile as sf
        data, rate = sf.read(str(path), dtype="float32", always_2d=True)
        mono = data.mean(axis=1)
    except Exception:  # a format soundfile can't read: let librosa (FFmpeg) decode it
        import librosa
        mono, rate = librosa.load(str(path), sr=None, mono=True)
    return soxr.resample(mono, rate, sr).astype(np.float32) if rate != sr else mono.astype(np.float32)


def analyse(path: Path, tracker: BeatTracker | None, cancel=None, log=lambda _t: None,
            audio: np.ndarray | None = None, audio_rate: int = 44100) -> dict:
    """
    {"bpm": {...} or None, "key": {...} or None} for an audio file, or for
    `audio` (samples, channels) at `audio_rate` when it's already in memory.
    """
    if audio is not None:
        import soxr
        mono = np.asarray(audio, dtype=np.float32)
        mono = mono.mean(axis=1) if mono.ndim == 2 else mono
        mono = soxr.resample(mono, audio_rate, SAMPLE_RATE).astype(np.float32) if audio_rate != SAMPLE_RATE else mono
    else:
        mono = load_mono(path)
    result = {"bpm": None, "key": None}
    if tracker is not None:
        try:
            beats, _downbeats = tracker.beats(mono, cancel)
            result["bpm"] = tempo_from_beats(beats)
        except Exception as exc:
            if cancel and type(exc).__name__ == "Cancelled":
                raise
            log(f"BPM detection didn't work: {exc}")
    if cancel:
        cancel()
    try:
        result["key"] = detect_key(mono)
    except Exception as exc:
        log(f"Key detection didn't work: {exc}")
    return result
