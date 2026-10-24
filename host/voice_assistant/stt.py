"""本地 STT：faster-whisper。

国内网络注意：faster-whisper 首次使用会从 HuggingFace 下载模型，
直连大概率超时（WinError 10060）。这里做了三件事：
1. 在导入 faster_whisper 之前设置 `HF_ENDPOINT`（默认 hf-mirror.com），
   否则 huggingface_hub 已经把常量读走了，改配置也没用；
2. 支持 `stt.model_dir` 指向本地已下载的模型目录，完全不联网；
3. 失败时抛出带操作指引的 `STTError`，而不是让 pipeline 只打一句"处理失败"。
"""
from __future__ import annotations

import logging
import os

import numpy as np

log = logging.getLogger(__name__)

_HOST_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


class STTError(RuntimeError):
    """STT 不可用（模型缺失/下载失败），消息可直接展示给用户。"""


class STTEngine:
    def __init__(self, cfg):
        self.cfg = cfg
        self._model = None

    # ---------- 内部 ----------

    # 这三个文件缺任何一个人都无法加载（vocabulary.txt 只有 tiny/base 需要）
    REQUIRED_FILES = ("model.bin", "config.json", "tokenizer.json")

    def _resolve(self):
        """返回 (模型路径或名称, download_root)。

        默认策略是 **只用本地模型**：找不到就报错并给出明确操作指引，
        只有显式开启 `stt.auto_download` 才会联网下载。
        """
        md = (getattr(self.cfg, "model_dir", "") or "").strip()
        if md:
            path = md if os.path.isabs(md) else os.path.join(_HOST_ROOT, md)
            if not os.path.isdir(path):
                raise STTError(f"stt.model_dir 指向的目录不存在：{path}")
            missing = [f for f in self.REQUIRED_FILES
                       if not os.path.exists(os.path.join(path, f))]
            if missing:
                raise STTError(
                    "模型目录缺少必需文件：%s\n目录：%s" % ("、".join(missing), path))
            return path, None

        if not bool(getattr(self.cfg, "auto_download", False)):
            root_hint = (getattr(self.cfg, "download_root", "") or "models/whisper").strip()
            raise STTError(
                "未找到本地 STT 模型：stt.model_dir 为空，且已关闭自动下载。\n"
                "  · 手动下载后把 stt.model_dir 指向模型目录\n"
                "    （约定位置：%s/faster-whisper-%s）\n"
                "  · 或在「模型」页点「一键下载模型」\n"
                "  · 或勾选「允许程序自动下载」后重启"
                % (root_hint, self.cfg.model_size))

        root = (getattr(self.cfg, "download_root", "") or "models/whisper").strip()
        if not os.path.isabs(root):
            root = os.path.join(_HOST_ROOT, root)
        os.makedirs(root, exist_ok=True)
        return self.cfg.model_size, root

    # ---------- 对外 ----------

    @property
    def model(self):
        if self._model is None:
            # 关键：必须早于 faster_whisper / huggingface_hub 的导入
            ep = (getattr(self.cfg, "hf_endpoint", "") or "").strip()
            if ep:
                os.environ["HF_ENDPOINT"] = ep
                os.environ.setdefault("HUGGINGFACE_HUB_ENDPOINT", ep)

            # 关掉 xet 后端：它会在用户目录写日志，Windows 上常见
            # "I/O error: 拒绝访问 (os error 5)"，直接导致模型下载失败。
            os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
            os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

            # 缓存根放到项目内，避开 C:\Users\<user>\.cache 的权限/沙箱问题
            hf_home = (getattr(self.cfg, "hf_home", "") or "models/hf_home").strip()
            if not os.path.isabs(hf_home):
                hf_home = os.path.join(_HOST_ROOT, hf_home)
            os.makedirs(hf_home, exist_ok=True)
            os.environ["HF_HOME"] = hf_home

            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise STTError("未安装 faster-whisper，请先执行 pip install -r requirements.txt") from exc

            name, root = self._resolve()
            log.info("加载 faster-whisper 模型 %s（HF 镜像=%s，缓存=%s）",
                     name, ep or "官方", root)
            try:
                self._model = WhisperModel(name,
                                           device=self.cfg.device,
                                           compute_type=self.cfg.compute_type,
                                           download_root=root)
            except Exception as exc:      # noqa: BLE001 - 需要把底层异常转成可读提示
                raise STTError(
                    "加载 faster-whisper 模型失败：%s\n"
                    "  · 国内网络请确认 config.json 里 stt.hf_endpoint = https://hf-mirror.com\n"
                    "  · 或手动下载模型，把 stt.model_dir 指向本地目录\n"
                    "  · 模型缓存目录：%s" % (exc, root)) from exc
        return self._model

    def transcribe(self, pcm: bytes) -> str:
        """pcm: 16k/16bit 单声道原始 PCM。"""
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _info = self.model.transcribe(
            audio,
            language=self.cfg.language,
            beam_size=getattr(self.cfg, "beam_size", 5),
            vad_filter=True,          # 去掉首尾静音：15s 长录音更准也更快
        )
        text = "".join(s.text for s in segments).strip()
        log.info("stt result: %s", text)
        return text

    def preload(self) -> str | None:
        """启动自检：尝试加载模型，成功返回 None，失败返回错误描述。"""
        try:
            _ = self.model
            return None
        except Exception as exc:      # noqa: BLE001
            return str(exc)
