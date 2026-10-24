"""按需下载轻量化模型（默认关闭自动下载，由用户显式触发）。

约定：
- 所有文件都落在 `host/models/` 下，路径可预测；
- 统一走镜像（默认 hf-mirror.com）并关掉 xet 后端——Windows 上 xet 会因
  写用户目录日志被拒而直接失败（`I/O error: 拒绝访问 os error 5`）；
- 只依赖 `huggingface_hub`（faster-whisper 的传递依赖），不引入新的第三方包。
"""
from __future__ import annotations

import os
import shutil

HOST_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# size -> (HF repo, 约 MB)
STT_REPOS: dict[str, tuple[str, int]] = {
    "tiny": ("Systran/faster-whisper-tiny", 75),
    "base": ("Systran/faster-whisper-base", 145),
    "small": ("Systran/faster-whisper-small", 484),
    "medium": ("Systran/faster-whisper-medium", 1500),
}
STT_REQUIRED = ("model.bin", "config.json", "tokenizer.json")

PIPER_REPO = "rhasspy/piper-voices"
PIPER_FILES = (
    "zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx",
    "zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx.json",
)
TTS_REQUIRED_SUFFIX = ("", ".json")


def _abs(path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(HOST_ROOT, path)


def _stt_cfg(cfg):
    """允许传 AppConfig 或 STTConfig，统一取到 STTConfig。"""
    return getattr(cfg, "stt", cfg)


def _tts_cfg(cfg):
    return getattr(cfg, "tts", cfg)


def prepare_env(cfg) -> str:
    cfg = _stt_cfg(cfg)
    """设置镜像与缓存目录，返回实际使用的镜像地址。"""
    ep = (getattr(cfg, "hf_endpoint", "") or "https://hf-mirror.com").strip()
    os.environ["HF_ENDPOINT"] = ep
    os.environ["HUGGINGFACE_HUB_ENDPOINT"] = ep
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
    home = _abs(getattr(cfg, "hf_home", "") or "models/hf_home")
    os.makedirs(home, exist_ok=True)
    os.environ["HF_HOME"] = home
    return ep


def stt_target(cfg, size: str | None = None) -> str:
    """模型落地目录：优先用配置里的 model_dir，否则 models/whisper/faster-whisper-<size>。"""
    cfg = _stt_cfg(cfg)
    md = (getattr(cfg, "model_dir", "") or "").strip()
    if md:
        return _abs(md)
    size = size or getattr(cfg, "model_size", "tiny")
    root = _abs(getattr(cfg, "download_root", "") or "models/whisper")
    return os.path.join(root, f"faster-whisper-{size}")


def tts_target(cfg) -> str:
    cfg = _tts_cfg(cfg)
    return os.path.dirname(_abs(getattr(cfg, "model_path",
                                         "models/piper/zh_CN-huayan-medium.onnx")))


def stt_missing(path: str) -> list[str]:
    return [f for f in STT_REQUIRED if not os.path.exists(os.path.join(path, f))]


def tts_missing(path: str) -> list[str]:
    base = os.path.join(path, "zh_CN-huayan-medium.onnx")
    return [s for s in TTS_REQUIRED_SUFFIX if not os.path.exists(base + s)]


def download_stt(cfg, size: str, report) -> str:
    ep = prepare_env(cfg)
    repo, mb = STT_REPOS.get(size, STT_REPOS["tiny"])
    target = stt_target(cfg, size)
    os.makedirs(target, exist_ok=True)
    report(f"从 {ep} 下载 {repo}（约 {mb} MB）→ {target}")
    from huggingface_hub import snapshot_download
    snapshot_download(repo_id=repo, local_dir=target)
    miss = stt_missing(target)
    if miss:
        raise RuntimeError("下载结束但缺少文件：" + "、".join(miss))
    report(f"STT 就绪：{target}")
    return target


def download_tts(cfg, report) -> str:
    ep = prepare_env(cfg)
    target = tts_target(cfg)
    os.makedirs(target, exist_ok=True)
    report(f"从 {ep} 下载 Piper 中文音色（约 63 MB）→ {target}")

    from huggingface_hub import hf_hub_download
    tmp = os.path.join(_abs("models"), "_piper_dl")
    try:
        for name in PIPER_FILES:
            report("下载 %s …" % os.path.basename(name))
            got = hf_hub_download(repo_id=PIPER_REPO, filename=name, local_dir=tmp)
            shutil.copy2(got, os.path.join(target, os.path.basename(name)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    miss = tts_missing(target)
    if miss:
        raise RuntimeError("下载结束但缺少文件：" + "".join(miss))
    report(f"TTS 就绪：{target}")
    return target


def download(cfg, kinds=("stt", "tts"), size: str | None = None, report=None) -> str:
    """按需下载。`report(str)` 用于把进度写到界面/日志。"""
    report = report or (lambda _m: None)
    done: list[str] = []
    if "stt" in kinds:
        done.append(download_stt(cfg, size or getattr(cfg, "model_size", "tiny"), report))
    if "tts" in kinds:
        done.append(download_tts(cfg, report))
    return "；".join(done)
