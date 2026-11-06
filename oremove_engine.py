"""
ORemove's separation engine for the Roformer models.

audio-separator loads the models; this module runs them. Compared with calling
audio-separator's own loop, it:

- takes the song as an array in memory (decoded once, no temporary WAV),
- separates several chunks of audio at once when the GPU has room,
- keeps the running result on the GPU and only waits for the GPU when it has
  to, so the GPU isn't left idle between chunks,
- can do the models' spectral maths (STFT, mask, inverse STFT) with ordinary
  real-number arithmetic. Apple GPUs don't support every complex-number
  operation, and without this path those steps would run on the CPU, with
  the audio copied back and forth on every chunk.

The results match audio-separator's (checked by the build's self-test).
"""

from __future__ import annotations

import math
import struct
import subprocess
import threading
from pathlib import Path

import numpy as np

SAMPLE_RATE = 44100


# --------------------------------------------------------------------------- #
# Decoding: any audio or video file -> float32 stereo array at 44.1 kHz
# --------------------------------------------------------------------------- #

def _read_wav_stream(stream) -> tuple[np.ndarray, int]:
    """Read a 32-bit float WAV written by FFmpeg to a pipe (its sizes are unknown)."""
    header = stream.read(12)
    if len(header) < 12 or header[:4] not in (b"RIFF", b"RF64") or header[8:12] != b"WAVE":
        raise ValueError("FFmpeg didn't produce WAV audio")
    rate = channels = 0
    while True:
        chunk = stream.read(8)
        if len(chunk) < 8:
            raise ValueError("no audio data")
        name, size = chunk[:4], struct.unpack("<I", chunk[4:])[0]
        if name == b"data":
            break
        body = stream.read(size + (size & 1))
        if name == b"fmt ":
            channels, rate = struct.unpack("<HI", body[2:8])
    data = np.frombuffer(stream.read(), dtype="<f4")
    frames = len(data) // channels
    return data[: frames * channels].reshape(frames, channels).T.copy(), rate


def decode(path: Path, cancel=None, creationflags: int = 0) -> np.ndarray:
    """
    Decode `path` with FFmpeg to a float32 array shaped (2, samples) at 44.1 kHz.
    Resampling (when the file isn't 44.1 kHz) uses soxr at high quality, as
    audio-separator does, so the model gets exactly the same input.
    """
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-vn", "-ac", "2", "-c:a", "pcm_f32le", "-f", "wav", "pipe:1"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=creationflags)
    result = {}

    def read():
        try:
            result["audio"] = _read_wav_stream(proc.stdout)
        except Exception as exc:
            result["error"] = exc

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    errors = []
    err_reader = threading.Thread(target=lambda: errors.append(proc.stderr.read()), daemon=True)
    err_reader.start()
    while reader.is_alive():
        reader.join(0.1)
        if cancel and reader.is_alive():
            try:
                cancel()
            except BaseException:
                proc.kill()
                proc.wait()
                raise
    proc.wait()
    err_reader.join(5)
    stderr = (errors[0] if errors else b"").decode("utf-8", "replace").strip()
    if proc.returncode != 0 or "audio" not in result:
        reason = (stderr.splitlines() or [str(result.get("error", "not a readable audio file"))])[-1]
        raise ValueError(reason)
    audio, rate = result["audio"]
    if audio.shape[1] == 0:
        raise ValueError("the file contains no audio")
    if rate != SAMPLE_RATE:
        import soxr
        audio = soxr.resample(audio.T, rate, SAMPLE_RATE, quality="HQ").T.astype(np.float32)
    return np.ascontiguousarray(audio, dtype=np.float32)


# --------------------------------------------------------------------------- #
# STFT and inverse STFT with real-number arithmetic (matrix multiplies)
# --------------------------------------------------------------------------- #

_bases: dict = {}


def _dft_bases(n_fft: int, device, dtype):
    """Forward basis (n_fft, 2F) giving [real | imag] and inverse basis (2F, n_fft)."""
    key = (n_fft, str(device), dtype)
    if key not in _bases:
        import torch
        bins = n_fft // 2 + 1
        n = torch.arange(n_fft, dtype=torch.float64)
        k = torch.arange(bins, dtype=torch.float64)
        angle = 2 * math.pi * torch.outer(n, k) / n_fft          # (n_fft, F)
        forward = torch.cat([torch.cos(angle), -torch.sin(angle)], dim=1)
        weight = torch.full((bins,), 2.0, dtype=torch.float64)
        weight[0] = 1.0
        if n_fft % 2 == 0:
            weight[-1] = 1.0
        inverse = torch.cat([weight[:, None] * torch.cos(angle.T), -weight[:, None] * torch.sin(angle.T)], dim=0) / n_fft
        _bases[key] = (forward.to(device=device, dtype=dtype), inverse.to(device=device, dtype=dtype))
    return _bases[key]


def real_stft(x, n_fft: int, hop: int, window, normalized: bool = False):
    """
    torch.stft(x, n_fft, hop, window=window, center=True, pad_mode="reflect",
    onesided=True, return_complex=True) as a real tensor (..., F, frames, 2).
    """
    import torch
    import torch.nn.functional as F
    lead = x.shape[:-1]
    x = x.reshape(-1, 1, x.shape[-1])
    x = F.pad(x, (n_fft // 2, n_fft // 2), mode="reflect")[:, 0]
    frames = x.unfold(-1, n_fft, hop) * window                    # (B, T, n_fft)
    forward, _ = _dft_bases(n_fft, x.device, x.dtype)
    spec = frames @ forward                                        # (B, T, 2F)
    if normalized:
        spec = spec * (n_fft ** -0.5)
    bins = n_fft // 2 + 1
    spec = torch.stack([spec[..., :bins], spec[..., bins:]], dim=-1)  # (B, T, F, 2)
    return spec.transpose(1, 2).reshape(*lead, bins, spec.shape[1], 2)


_overlap_index: dict = {}


def real_istft(spec, n_fft: int, hop: int, window, normalized: bool = False, length: int | None = None):
    """
    torch.istft for a real tensor (..., F, frames, 2) holding a one-sided
    spectrum, with center=True. Returns (..., samples).
    """
    import torch
    lead, bins, count = spec.shape[:-3], spec.shape[-3], spec.shape[-2]
    spec = spec.reshape(-1, bins, count, 2).transpose(1, 2)            # (B, T, F, 2)
    _, inverse = _dft_bases(n_fft, spec.device, spec.dtype)
    frames = torch.cat([spec[..., 0], spec[..., 1]], dim=-1) @ inverse  # (B, T, n_fft)
    if normalized:
        frames = frames * (n_fft ** 0.5)
    frames = frames * window
    # Overlap-add: frame t covers samples t*hop .. t*hop + n_fft.
    total = n_fft + hop * (count - 1)
    key = (n_fft, hop, count, str(frames.device))
    if key not in _overlap_index:
        index = (torch.arange(count, device=frames.device)[:, None] * hop
                 + torch.arange(n_fft, device=frames.device)[None, :]).reshape(-1)
        envelope = torch.zeros(total, dtype=frames.dtype, device=frames.device)
        envelope.index_add_(0, index, (window * window).repeat(count))
        if len(_overlap_index) > 8:
            _overlap_index.clear()
        _overlap_index[key] = (index, envelope)
    index, envelope = _overlap_index[key]
    batch = frames.shape[0]
    out = frames.new_zeros(batch, total).index_add_(1, index, frames.reshape(batch, -1))
    start = n_fft // 2
    end = start + length if length is not None else total - n_fft // 2
    out = out[:, start:end] / envelope[start:end].clamp(min=1e-11)
    if length is not None and out.shape[-1] < length:
        out = torch.nn.functional.pad(out, (0, length - out.shape[-1]))
    return out.reshape(*lead, out.shape[-1])


# --------------------------------------------------------------------------- #
# Roformer forward passes without complex numbers (inference only)
# --------------------------------------------------------------------------- #
# These follow audio-separator's BSRoformer.forward and MelBandRoformer.forward
# step by step; only the STFT, the complex mask multiply and the inverse STFT
# are written with real arithmetic, so every step can run on an Apple GPU.

def _window(model, device):
    cache = model.__dict__.setdefault("_oremove_windows", {})
    if str(device) not in cache:
        cache[str(device)] = model.stft_window_fn().to(device)
    return cache[str(device)]


def supports_real_spectral(model) -> bool:
    kw = getattr(model, "stft_kwargs", None) or {}
    name = type(model).__name__
    n_fft, hop, win = kw.get("n_fft"), kw.get("hop_length"), kw.get("win_length")
    return bool(name in ("BSRoformer", "MelBandRoformer") and n_fft and hop and win == n_fft)


def _stft_frames(model, raw_audio):
    """raw_audio (b, s, t) -> stft (b, (f s), frames, 2), as the models lay it out."""
    kw = model.stft_kwargs
    b, s, length = raw_audio.shape
    window = _window(model, raw_audio.device)
    spec = real_stft(raw_audio.reshape(b * s, length), kw["n_fft"], kw["hop_length"], window, kw.get("normalized", False))
    bins, frames = spec.shape[1], spec.shape[2]
    return spec.reshape(b, s, bins, frames, 2).permute(0, 2, 1, 3, 4).reshape(b, bins * s, frames, 2), window


def _complex_multiply(a, b):
    ar, ai = a[..., 0], a[..., 1]
    br, bi = b[..., 0], b[..., 1]
    import torch
    return torch.stack([ar * br - ai * bi, ar * bi + ai * br], dim=-1)


def _istft_stems(model, spec, window, length=None):
    """spec (b, n, (f s), frames, 2) -> audio (b, n, s, t), or (b, s, t) for one stem."""
    kw = model.stft_kwargs
    s = model.audio_channels
    b, n, fs, frames, _ = spec.shape
    spec = spec.reshape(b, n, fs // s, s, frames, 2).permute(0, 1, 3, 2, 4, 5).reshape(b * n * s, fs // s, frames, 2)
    audio = real_istft(spec, kw["n_fft"], kw["hop_length"], window, kw.get("normalized", False), length)
    audio = audio.reshape(b, n, s, audio.shape[-1])
    return audio[:, 0] if model.num_stems == 1 else audio


def _bs_roformer_forward(model, raw_audio):
    if raw_audio.ndim == 2:
        raw_audio = raw_audio[:, None]
    stft_repr, window = _stft_frames(model, raw_audio)
    b, fs, frames, _ = stft_repr.shape
    x = stft_repr.permute(0, 2, 1, 3).reshape(b, frames, fs * 2)
    dtype = next(model.band_split.parameters()).dtype
    x = model.band_split(x.to(dtype) if x.dtype != dtype else x)
    for block in model.layers:
        if len(block) == 3:
            linear_transformer, time_transformer, freq_transformer = block
            shape = x.shape
            x = linear_transformer(x.reshape(shape[0], -1, shape[-1])).reshape(shape)
        else:
            time_transformer, freq_transformer = block
        bb, t, f, d = x.shape
        x = time_transformer(x.permute(0, 2, 1, 3).reshape(bb * f, t, d)).reshape(bb, f, t, d)
        x = freq_transformer(x.permute(0, 2, 1, 3).reshape(bb * t, f, d)).reshape(bb, t, f, d)
    x = model.final_norm(x)
    import torch
    mask = torch.stack([fn(x) for fn in model.mask_estimators], dim=1)       # (b, n, t, (f c))
    n = mask.shape[1]
    mask = mask.reshape(b, n, frames, fs, 2).permute(0, 1, 3, 2, 4).to(stft_repr.dtype)
    spec = _complex_multiply(stft_repr[:, None], mask)
    return _istft_stems(model, spec, window)


def _mel_band_roformer_forward(model, raw_audio):
    import torch
    if raw_audio.ndim == 2:
        raw_audio = raw_audio[:, None]
    channels, length = raw_audio.shape[1], raw_audio.shape[2]
    stft_repr, window = _stft_frames(model, raw_audio)
    b, fs, frames, _ = stft_repr.shape
    indices = model.freq_indices
    x = stft_repr[:, indices]                                                 # (b, f', t, 2)
    x = x.permute(0, 2, 1, 3).reshape(b, frames, -1)
    dtype = next(model.band_split.parameters()).dtype
    x = model.band_split(x.to(dtype) if x.dtype != dtype else x)
    for time_transformer, freq_transformer in model.layers:
        bb, t, f, d = x.shape
        x = time_transformer(x.permute(0, 2, 1, 3).reshape(bb * f, t, d)).reshape(bb, f, t, d)
        x = freq_transformer(x.permute(0, 2, 1, 3).reshape(bb * t, f, d)).reshape(bb, t, f, d)
    masks = torch.stack([fn(x) for fn in model.mask_estimators], dim=1)       # (b, n, t, (f' c))
    n = masks.shape[1]
    masks = masks.reshape(b, n, frames, len(indices), 2).permute(0, 1, 3, 2, 4).to(stft_repr.dtype)
    summed = stft_repr.new_zeros(b, n, fs, frames, 2).index_add_(2, indices, masks)
    denom = model.num_bands_per_freq.repeat_interleave(channels).to(summed.dtype).clamp(min=1e-8)
    averaged = summed / denom[:, None, None]
    spec = _complex_multiply(stft_repr[:, None], averaged)
    return _istft_stems(model, spec, window, length if model.match_input_audio_length else None)


def real_forward(model, raw_audio):
    """The model's forward pass (inference) with real-number spectral maths."""
    if type(model).__name__ == "MelBandRoformer":
        return _mel_band_roformer_forward(model, raw_audio)
    return _bs_roformer_forward(model, raw_audio)


# --------------------------------------------------------------------------- #
# The chunk loop
# --------------------------------------------------------------------------- #

def _is_out_of_memory(exc: BaseException) -> bool:
    return "out of memory" in str(exc).lower()


def _make_event(device):
    """A GPU event to wait for one batch without stopping the queue behind it (None: not available)."""
    if device.type not in ("cuda", "mps"):
        return None
    import torch
    try:
        return torch.Event(device=device.type)
    except Exception:
        pass
    try:
        if device.type == "cuda":
            return torch.cuda.Event()
        return torch.mps.event.Event()
    except Exception:
        return None


def _synchronize(device):
    import torch
    if device.type == "cuda":
        torch.cuda.synchronize(device)
    elif device.type == "mps":
        torch.mps.synchronize()


def _accumulate_on_device(device, estimated_bytes: int) -> bool:
    import torch
    if device.type == "cpu":
        return True
    if device.type == "cuda":
        try:
            free, _total = torch.cuda.mem_get_info(device)
            return estimated_bytes < 0.25 * free
        except Exception:
            return False
    if device.type == "mps":
        try:
            from audio_separator.separator.uvr_lib_v5.device_utils import should_accumulate_on_device
            return should_accumulate_on_device(device, estimated_bytes)
        except Exception:
            return False
    return False


class Separation:
    """
    Separate one song with an audio-separator MDXC (Roformer) model instance.

    `batch` chunks go through the model together; on running out of GPU
    memory with more than one, the batch is halved and the same chunks are
    tried again. `real_spectral` uses real_forward. `progress(done, total)` is
    called as chunks finish, and `cancel()` (which raises to stop) before each batch.
    """

    def __init__(self, instance, batch: int = 1, real_spectral: bool = False, progress=None, cancel=None,
                 log=lambda _t: None):
        self.instance = instance
        self.batch = max(1, int(batch))
        self.real_spectral = real_spectral and supports_real_spectral(instance.model_run)
        self.progress = progress or (lambda _d, _t: None)
        self.cancel = cancel or (lambda: None)
        self.log = log
        self.accumulated_on_gpu = False
        self.real_failed = False

    def _forward(self, parts):
        instance, model = self.instance, self.instance.model_run
        run = (lambda: real_forward(model, parts)) if self.real_spectral else (lambda: model(parts))
        try:
            return run()
        except Exception as exc:
            if _is_out_of_memory(exc):
                raise
            if self.real_spectral and not getattr(instance, "is_torch_compiled", False):
                # Safety net: if this GPU can't run the real-number path after
                # all, use the model's own forward pass for the rest of the song.
                self.real_spectral, self.real_failed = False, True
                self.log(f"Real-number spectral maths failed ({exc}); using the model's own.")
                return model(parts)
            if not getattr(instance, "is_torch_compiled", False):
                raise
            instance._disable_model_compilation()
            self.log(f"Compiled model failed ({exc}); continuing without compilation.")
            return run()

    def run(self, mix: np.ndarray) -> list[tuple[str, np.ndarray]]:
        """mix: float32 (2, samples) at 44.1 kHz. Returns [(stem name, (samples, 2) array)] in save order."""
        import torch
        from scipy import signal
        from audio_separator.separator.uvr_lib_v5 import spec_utils

        inst = self.instance
        cfg = inst.model_data_cfgdict
        if getattr(inst, "pitch_shift", 0):
            raise ValueError("pitch shift is not supported here")
        mix = spec_utils.normalize(wave=np.array(mix, dtype=np.float32), max_peak=inst.normalization_threshold,
                                   min_peak=inst.amplification_threshold)
        samples = mix.shape[1]
        override = inst._use_model_segment_override(samples / inst.sample_rate)
        dim_t = inst.segment_size if override else cfg.inference.dim_t
        hop = getattr(cfg.model, "stft_hop_length", None) or cfg.audio.hop_length
        chunk = int(hop) * (int(dim_t) - 1)
        step = chunk // inst.overlap
        if step <= 0:
            raise ValueError(f"overlap ({inst.overlap}) must not exceed the chunk size ({chunk})")
        instruments = list(cfg.training.instruments)
        target = cfg.training.target_instrument
        model = inst.model_run
        device = next(model.parameters()).device
        starts = inst._roformer_chunk_starts(samples, chunk, step)

        estimated = (len(instruments) * 2 * samples + samples + chunk) * 4
        on_device = _accumulate_on_device(device, estimated)
        acc = device if on_device else torch.device("cpu")
        self.accumulated_on_gpu = on_device and device.type != "cpu"
        events = on_device and device.type != "cpu"

        with torch.inference_mode():
            source = torch.from_numpy(mix).to(device if on_device else "cpu")
            window = torch.tensor(signal.windows.hamming(chunk), dtype=torch.float32, device=acc)
            result = torch.zeros((len(instruments), 2, samples), dtype=torch.float32, device=acc)
            counter = torch.zeros(samples, dtype=torch.float32, device=acc)
            pending = []   # (event, chunks in that batch): batches queued on the GPU, oldest first
            done = 0
            i = 0
            while i < len(starts):
                self.cancel()
                group = starts[i:i + self.batch]
                parts = torch.stack([source[:, s:s + chunk] for s in group]).to(device)
                try:
                    out = self._forward(parts)
                except Exception as exc:
                    if not _is_out_of_memory(exc) or self.batch == 1:
                        raise
                    del parts
                    self._free(device, pending)
                    self.batch = max(1, self.batch // 2)
                    self.log(f"GPU memory is short: separating {self.batch} chunk(s) at a time instead.")
                    continue
                length = parts.shape[-1]
                for j, start in enumerate(group):
                    x = out[j]
                    if x.device != acc:
                        x = x.to(acc)
                    safe = min(length, x.shape[-1], window.shape[0])
                    if safe > 0:
                        result[..., start:start + safe] += x[..., :safe].float() * window[:safe]
                        counter[start:start + safe] += window[:safe]
                del out, parts
                i += len(group)
                event = _make_event(device) if events else None
                if event is not None:
                    event.record()
                    pending.append((event, len(group)))
                    # Keep at most one batch queued behind the one running, so
                    # progress stays truthful and Stop stays quick.
                    while len(pending) > 1:
                        old, count = pending.pop(0)
                        old.synchronize()
                        done += count
                        self.progress(done, len(starts))
                else:
                    if events:
                        _synchronize(device)
                    done += len(group)
                    self.progress(done, len(starts))
            for old, count in pending:
                old.synchronize()
                done += count
            if pending:
                self.progress(done, len(starts))
            result /= counter.clamp(min=1e-10)
            inferenced = result.cpu().numpy()
            del result, counter, source

        # From here on this mirrors audio-separator's MDXCSeparator.demix and .separate.
        if target:
            primary = inferenced[0]
            if inst.is_primary_stem_main_target:
                if primary.shape[1] != mix.shape[1]:
                    primary = spec_utils.match_array_shapes(primary, mix)
                sources = {inst.primary_stem_name: primary, inst.secondary_stem_name: mix - primary}
            else:
                sources = {inst.primary_stem_name: primary}
        else:
            sources = dict(zip(instruments, inferenced))

        stem_list = [target] if target else instruments
        out = []
        normalize = lambda w: spec_utils.normalize(wave=w, max_peak=inst.normalization_threshold,
                                                   min_peak=inst.amplification_threshold).T
        if inst.process_all_stems and len(stem_list) > 2:
            for name in stem_list:
                out.append((name, normalize(sources[name])))
        else:
            single = (inst.output_single_stem or "").lower()
            if inst.secondary_stem_name in sources and (not single or single == inst.secondary_stem_name.lower()):
                out.append((inst.secondary_stem_name, normalize(sources[inst.secondary_stem_name])))
            if not single or single == inst.primary_stem_name.lower():
                out.append((inst.primary_stem_name, normalize(sources[inst.primary_stem_name])))
        return [(name, np.ascontiguousarray(audio, dtype=np.float32)) for name, audio in out]

    @staticmethod
    def _free(device, pending):
        import gc
        import torch
        for event, _count in pending:
            event.synchronize()
        gc.collect()
        if device.type == "cuda":
            torch.cuda.empty_cache()
        elif device.type == "mps":
            torch.mps.empty_cache()
