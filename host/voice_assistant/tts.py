"""本地 TTS：Piper。

采样率对齐（原实现的坑）：
- 链路固定 16kHz（见 host/docs/01_接口设计.md §4 与 host/docs/04_配置说明.md §5）；
- Piper 模型输出采样率由 `.onnx.json` 决定（zh_CN-huayan-medium = 22050Hz）；
- 旧代码直接 `wav[44:]` 切头返回，既没解析真实采样率（WAV 头可能不止 44 字节），
  也没重采样 → 下位机按 16k 播放 22.05k 的音频，音调偏高、语速偏快。
- 现在用 piper 1.8 的 `synthesize()` 直接拿 PCM（无 WAV 头），
  并按 `voice.config.sample_rate` 重采样到 16k。
"""
from __future__ import annotations

import logging
import os

import numpy as np

log = logging.getLogger(__name__)

_HOST_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
TARGET_RATE = 16000


class TTSError(RuntimeError):
    """TTS 不可用（模型缺失/依赖缺失），消息可直接展示给用户。"""


def resample_int16(data: bytes, src_rate: int, dst_rate: int) -> bytes:
    """int16 单声道重采样（降采样时先做抗混叠低通，再做线性插值）。

    没有 scipy/soxr 依赖，纯 numpy 实现；语音场景足够，且比直接抽取少很多混叠。
    """
    if not data or src_rate == dst_rate:
        return data

    x = np.frombuffer(data, dtype=np.int16).astype(np.float32)
    n_out = int(round(len(x) * dst_rate / src_rate))
    if n_out <= 1:
        return b""

    if dst_rate < src_rate:
        # 截止频率取目标采样率的 45%，归一化到源采样率
        fc = 0.45 * dst_rate / src_rate
        taps = 32
        k = np.arange(-taps, taps + 1, dtype=np.float32)
        h = 2.0 * fc * np.sinc(2.0 * fc * k) * np.hamming(2 * taps + 1).astype(np.float32)
        s = h.sum()
        if s != 0:
            h /= s
        x = np.convolve(x, h, mode="same")

    pos = np.arange(n_out, dtype=np.float64) * (src_rate / dst_rate)
    y = np.interp(pos, np.arange(len(x), dtype=np.float64), x)
    y = np.clip(y, -32768, 32767).astype(np.int16)
    return y.tobytes()


class TTSEngine:
    def __init__(self, cfg):
        self.cfg = cfg
        self._voice = None
        self._rate = 0

    # ---------- 内部 ----------

    def _model_path(self) -> str:
        p = (self.cfg.model_path or "").strip()
        return p if os.path.isabs(p) else os.path.join(_HOST_ROOT, p)

    # ---------- 对外 ----------

    @property
    def voice(self):
        if self._voice is None:
            path = self._model_path()
            if not os.path.exists(path):
                raise TTSError(
                    "Piper 模型不存在：%s\n"
                    "  · 下载 zh_CN-huayan-medium.onnx 与同名 .onnx.json 放到该目录\n"
                    "  · 或在 config.json 里修改 tts.model_path" % path)

            try:
                from piper import PiperVoice
            except ImportError as exc:
                raise TTSError("未安装 piper-tts，请先执行 pip install -r requirements.txt") from exc

            log.info("加载 Piper 模型 %s", path)
            try:
                self._voice = PiperVoice.load(path)
            except Exception as exc:      # noqa: BLE001
                raise TTSError("加载 Piper 模型失败：%s" % exc) from exc

            self._rate = int(getattr(self._voice.config, "sample_rate", 0) or 0)
            if self._rate and self._rate != TARGET_RATE:
                log.info("Piper 输出 %d Hz，将重采样到 %d Hz", self._rate, TARGET_RATE)
        return self._voice

    def synthesize(self, text: str) -> bytes:
        """返回 16k/16bit 单声道原始 PCM。"""
        from piper.config import SynthesisConfig

        syn = SynthesisConfig(
            speaker_id=self.cfg.speaker,
            length_scale=float(getattr(self.cfg, "length_scale", 1.0) or 1.0),
        )
        self.voice                    # 触发加载校验
        try:
            parts = [c.audio_int16_bytes for c in self.voice.synthesize(text, syn)]
        except Exception as exc:      # noqa: BLE001
            raise TTSError("Piper 合成失败：%s" % exc) from exc

        pcm = b"".join(parts)
        if not pcm:
            return b""
        if self._rate and self._rate != TARGET_RATE:
            pcm = resample_int16(pcm, self._rate, TARGET_RATE)
        return pcm

    def preload(self) -> str | None:
        """启动自检：尝试加载模型，成功返回 None，失败返回错误描述。"""
        try:
            _ = self.voice
            return None
        except Exception as exc:      # noqa: BLE001
            return str(exc)
