"""双路音频采集：系统回放（WASAPI loopback）+ 麦克风 -> 16kHz 单声道 PCM。

设计要点
--------
1. WASAPI 原生支持 16000Hz 输出，重采样交给操作系统，代码里不再自己做重采样。
2. 录音/麦克风各自一个采集线程，互不阻塞；线程内只做「读取 -> 混单声道 -> 入队」。
3. 队列满时丢最旧的一块（而不是阻塞），保证实时性优先于完整性。
4. 混音前对每路做 AGC（自动增益），否则麦克风音量远低于系统回放，
   对方声音会把你自己的发言完全盖住。

对外接口
--------
    cap = DualChannelCapture(mic_gain=4.0)
    cap.start()
    pcm = cap.read_pcm()          # bytes, 100ms 的 s16le PCM
    cap.stats()                   # 采集统计（用于判断有没有丢音）
    cap.stop()
"""
from __future__ import annotations

import queue
import sys
import threading
import time
import warnings

import numpy as np
import soundcard as sc
from pathlib import Path

warnings.filterwarnings("ignore", category=sc.SoundcardRuntimeWarning)

SAMPLE_RATE = 16000
CHUNK_SAMPLES = 1600          # 100ms @ 16kHz
BYTES_PER_SAMPLE = 2


# --------------------------------------------------------------------------
# 设备枚举
# --------------------------------------------------------------------------
def list_devices() -> dict:
    """列出可用的回放设备和麦克风，供配置时选择。"""
    speakers = [s.name for s in sc.all_speakers()]
    mics = [m.name for m in sc.all_microphones(include_loopback=False)]
    default_spk = sc.default_speaker()
    default_mic = sc.default_microphone()
    return {
        "speakers": speakers,
        "microphones": mics,
        "default_speaker": default_spk.name if default_spk else None,
        "default_microphone": default_mic.name if default_mic else None,
    }


def _resolve_loopback(name: str | None):
    """按名称取回放设备对应的 loopback 采集口；name 为空则用默认扬声器。"""
    if name:
        for s in sc.all_speakers():
            if s.name == name:
                return sc.get_microphone(str(s.name), include_loopback=True)
        raise ValueError(f"找不到扬声器: {name}")
    spk = sc.default_speaker()
    if spk is None:
        return None
    return sc.get_microphone(str(spk.name), include_loopback=True)


def _resolve_mic(name: str | None):
    if name:
        for m in sc.all_microphones(include_loopback=False):
            if m.name == name:
                return m
        raise ValueError(f"找不到麦克风: {name}")
    return sc.default_microphone()


# --------------------------------------------------------------------------
# 轻量 AGC
# --------------------------------------------------------------------------
class Agc:
    """把每路音量往目标电平拉，避免「一方声音大一方声音小」。

    只做慢速调整（时间常数约 2 秒），不会因为一句话的停顿而抽气；
    增益上下限防止把静音段放大成噪声，也防止削顶。
    """

    def __init__(self, target_rms: float = 0.06, min_gain: float = 0.5,
                 max_gain: float = 20.0, enabled: bool = True):
        self.target_rms = target_rms
        self.min_gain = min_gain
        self.max_gain = max_gain
        self.enabled = enabled
        self.gain = 1.0

    def process(self, x: np.ndarray) -> np.ndarray:
        if not self.enabled or x.size == 0:
            return x
        rms = float(np.sqrt(np.mean(np.square(x.astype(np.float64)))))
        # 只在有明显信号时调整，静音段保持当前增益
        if rms > 1e-4:
            desired = self.target_rms / rms
            desired = float(np.clip(desired, self.min_gain, self.max_gain))
            # 一阶平滑：越接近目标越慢
            self.gain = 0.9 * self.gain + 0.1 * desired
        y = x * self.gain
        return _soft_limit(y).astype(np.float32)


def _soft_limit(x: np.ndarray, threshold: float = 0.8) -> np.ndarray:
    """超过 threshold 的部分做软压缩，而不是硬截断，减少破音。"""
    ax = np.abs(x)
    if not np.any(ax > threshold):
        return x
    over = ax > threshold
    compressed = threshold + (1.0 - threshold) * np.tanh((ax[over] - threshold) / (1.0 - threshold))
    out = x.copy()
    out[over] = np.sign(x[over]) * compressed
    return out


# --------------------------------------------------------------------------
# 单路采集线程
# --------------------------------------------------------------------------
class ChannelCapture(threading.Thread):
    def __init__(self, label: str, device, channels: int,
                 blocksize: int = CHUNK_SAMPLES, queue_blocks: int = 100,
                 agc: Agc | None = None, fixed_gain: float = 1.0):
        super().__init__(name=f"cap-{label}", daemon=True)
        self.label = label
        self.device = device
        self.channels = channels
        self.blocksize = blocksize
        self.agc = agc
        self.fixed_gain = fixed_gain
        self.queue: queue.Queue = queue.Queue(maxsize=queue_blocks)
        self._stop = threading.Event()
        self.frames = 0
        self.blocks = 0
        self.dropped_blocks = 0
        self.error: str | None = None
        self.first_at: float | None = None
        self.last_at: float | None = None

    def run(self) -> None:
        try:
            with self.device.recorder(samplerate=SAMPLE_RATE, channels=self.channels,
                                      blocksize=self.blocksize) as rec:
                while not self._stop.is_set():
                    data = rec.record(numframes=self.blocksize)
                    if data is None or len(data) == 0:
                        continue
                    mono = data.mean(axis=1) if data.ndim > 1 else data
                    mono = np.asarray(mono, dtype=np.float32)
                    if self.fixed_gain != 1.0:
                        mono = mono * self.fixed_gain
                    if self.agc is not None:
                        mono = self.agc.process(mono)
                    now = time.time()
                    if self.first_at is None:
                        self.first_at = now
                    self.last_at = now
                    self.frames += mono.size
                    self.blocks += 1
                    try:
                        self.queue.put_nowait(mono)
                    except queue.Full:
                        # 丢最旧的一块，保住实时性
                        try:
                            self.queue.get_nowait()
                        except queue.Empty:
                            pass
                        self.dropped_blocks += 1
                        try:
                            self.queue.put_nowait(mono)
                        except queue.Full:
                            pass
        except Exception as e:  # 设备被拔出 / 被独占等
            self.error = f"{type(e).__name__}: {e}"

    def stop(self) -> None:
        self._stop.set()


# --------------------------------------------------------------------------
# 双路混音
# --------------------------------------------------------------------------
class DualChannelCapture:
    """把 loopback 与 mic 混成一路 16kHz 单声道，按 100ms 块吐出 PCM。"""

    def __init__(self, speaker_name: str | None = None, microphone_name: str | None = None,
                 use_loopback: bool = True, use_mic: bool = True,
                 loopback_gain: float = 1.0, mic_gain: float = 4.0,
                 agc: bool = True, mix_ratio: float = 0.5):
        self.use_loopback = use_loopback
        self.use_mic = use_mic
        self.mix_ratio = mix_ratio
        self.channels: list[ChannelCapture] = []
        self._buffers: dict[str, list[np.ndarray]] = {}
        self._buffered: dict[str, int] = {}
        self.started_at: float | None = None

        if use_loopback:
            dev = _resolve_loopback(speaker_name)
            if dev is None:
                self.use_loopback = False
            else:
                self.channels.append(ChannelCapture(
                    "loopback", dev, channels=2,
                    agc=Agc(enabled=agc) if agc else None,
                    fixed_gain=loopback_gain))
        if use_mic:
            dev = _resolve_mic(microphone_name)
            if dev is None:
                self.use_mic = False
            else:
                self.channels.append(ChannelCapture(
                    "mic", dev, channels=1,
                    agc=Agc(enabled=agc) if agc else None,
                    fixed_gain=mic_gain))
        if not self.channels:
            raise RuntimeError("没有任何可用音频输入（loopback 与 mic 都不可用）")

    def start(self) -> None:
        self.started_at = time.time()
        for ch in self.channels:
            self._buffers[ch.label] = []
            self._buffered[ch.label] = 0
            ch.start()

    def _take(self, ch: ChannelCapture, need: int, timeout: float) -> np.ndarray | None:
        """凑够 need 个样本；超时返回 None。"""
        deadline = time.time() + timeout
        buf = self._buffers[ch.label]
        have = self._buffered[ch.label]
        while have < need:
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            try:
                blk = ch.queue.get(timeout=remaining)
            except queue.Empty:
                return None
            buf.append(blk)
            have += blk.size
        self._buffered[ch.label] = have
        flat = buf[0] if len(buf) == 1 else np.concatenate(buf)
        out = flat[:need]
        rest = flat[need:]
        self._buffers[ch.label] = [rest] if rest.size else []
        self._buffered[ch.label] = rest.size
        return out

    def read_pcm(self, n_samples: int = CHUNK_SAMPLES, timeout: float = 2.0) -> bytes | None:
        """取一块 n_samples 的混音 PCM（s16le）。未就绪返回 None。"""
        parts = []
        for ch in self.channels:
            got = self._take(ch, n_samples, timeout)
            if got is not None:
                parts.append(got)
        if not parts:
            return None

        if len(parts) == 1:
            mixed = parts[0]
        else:
            # 两路等权混合；若某路明显偏小，AGC 已在采集线程里拉平
            mixed = parts[0] * self.mix_ratio + parts[1] * (1.0 - self.mix_ratio)
            # 能量守恒，避免混音后整体变小
            mixed = mixed * 1.4

        mixed = _soft_limit(np.clip(mixed, -1.0, 1.0))
        return (mixed * 32767.0).astype("<i2").tobytes()

    def stop(self) -> None:
        for ch in self.channels:
            ch.stop()
        for ch in self.channels:
            if ch.is_alive():
                ch.join(timeout=1.5)

    # ---------------- 统计 ----------------
    def stats(self) -> dict:
        now = time.time()
        elapsed = (now - self.started_at) if self.started_at else 0.0
        out = {"elapsed": round(elapsed, 2), "channels": {}}
        for ch in self.channels:
            span = (ch.last_at - ch.first_at) if (ch.first_at and ch.last_at) else 0.0
            audio_sec = ch.frames / SAMPLE_RATE
            out["channels"][ch.label] = {
                "frames": ch.frames,
                "audio_sec": round(audio_sec, 2),
                "span_sec": round(span, 2),
                # span 是「从第一块到最后一块」的墙钟时间，比较它才算真实丢音
                "drift_pct": round((audio_sec - span) / span * 100, 3) if span > 0 else None,
                "queue_depth": ch.queue.qsize(),
                "dropped_blocks": ch.dropped_blocks,
                "agc_gain": round(ch.agc.gain, 2) if ch.agc else None,
                "error": ch.error,
            }
        return out


# --------------------------------------------------------------------------
# 自检：录一段并写成 WAV，人工听一遍确认两路都在
# --------------------------------------------------------------------------
if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    import wave

    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0
    out = sys.argv[2] if len(sys.argv) > 2 else str(
        Path(__file__).resolve().with_name("_selftest.wav"))

    info = list_devices()
    print("可用扬声器:", info["speakers"])
    print("可用麦克风:", info["microphones"])
    print()

    cap = DualChannelCapture()
    cap.start()
    print(f"开始采集 {secs:.0f} 秒，请对着麦克风说话，同时让电脑放点声音 ...")

    frames = []
    t0 = time.time()
    while time.time() - t0 < secs:
        pcm = cap.read_pcm()
        if pcm:
            frames.append(pcm)
    cap.stop()

    data = b"".join(frames)
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(data)

    print()
    print("=" * 64)
    print(f"写出 {out}")
    print(f"PCM {len(data)} 字节 = {len(data)/2/SAMPLE_RATE:.2f} 秒")
    import json
    print(json.dumps(cap.stats(), ensure_ascii=False, indent=2))
    print("=" * 64)
    print("把 WAV 播一遍：应当同时听到对方（系统声）和你自己的声音。")
