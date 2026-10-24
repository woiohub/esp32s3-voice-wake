"""配置模型与读写。

约定：
- 配置文件默认在 `host/config.json`；
- 首次启动如果文件不存在，`ensure_config_file()` 会自动生成一份带注释说明的模板，
  用户（或 GUI）填好后即可生效，不必再翻代码找默认值。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict

DEFAULT_CONFIG_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "config.json"))


@dataclass
class AudioConfig:
    sample_rate: int = 16000
    bits: int = 16
    channels: int = 1
    frame_ms: int = 20


@dataclass
class STTConfig:
    """本地语音识别（faster-whisper）。

    默认 **不自动下载**：首次使用需用户把模型放到 `stt.model_dir`，
    或在界面点「一键下载模型」显式触发（见 model_download.py）。
    这样启动过程完全可预期，也不会在没网的情况下卡住。
    """
    model_size: str = "tiny"        # tiny | base | small | medium，或本地目录名
    model_dir: str = ""             # 本地模型目录（优先使用，填了就不联网）
    auto_download: bool = False     # True = 找不到模型时允许自动下载
    hf_endpoint: str = "https://hf-mirror.com"   # HuggingFace 镜像（国内直连不通时必须）
    download_root: str = "models/whisper"        # 自动下载的落地目录（相对 host/）
    hf_home: str = "models/hf_home"              # HuggingFace 缓存根（放项目内，避开用户目录权限问题）
    language: str = "zh"
    device: str = "cpu"
    compute_type: str = "int8"
    beam_size: int = 5


@dataclass
class LLMConfig:
    """大模型（OpenAI 兼容接口，支持 GLM 与 Qwen）。"""
    provider: str = "glm"          # glm | qwen
    base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    api_key: str = ""              # ← 必须填，否则会话阶段会直接报“未配置”
    model: str = "glm-4-flash"
    max_tokens: int = 128
    temperature: float = 0.7
    timeout: float = 30.0
    system_prompt: str = "你是语音助手，用简洁口语回答，控制在1-2句话以内。"


@dataclass
class TTSConfig:
    """本地语音合成（Piper）。"""
    model_path: str = "models/piper/zh_CN-huayan-medium.onnx"
    speaker: int = 0
    length_scale: float = 1.0      # >1 语速更慢
    # 模型输出采样率由 .onnx.json 决定（huayan 为 22050Hz），
    # 与链路 16kHz 不一致时由 tts.py 自动重采样。


@dataclass
class NetworkConfig:
    host: str = "0.0.0.0"
    port: int = 8888


@dataclass
class DeviceConfig:              # 下发给下位机的可配项
    volume: int = 60
    wake_enable: bool = True
    ack_enable: bool = True      # 唤醒后先播报回复词，播完再采集（下发给设备）
    ack_text: str = "在的"        # 回复词文本（只在上位机合成 TTS，不发给设备）
    host_ip: str = ""
    host_port: int = 8888
    wifi_ssid: str = ""
    wifi_password: str = ""


@dataclass
class AppConfig:
    audio: AudioConfig = field(default_factory=AudioConfig)
    stt: STTConfig = field(default_factory=STTConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    device: DeviceConfig = field(default_factory=DeviceConfig)


_SECTIONS = (AudioConfig, STTConfig, LLMConfig, TTSConfig, NetworkConfig, DeviceConfig)

# 两个厂商的官方兼容端点，GUI 切换 provider 时自动带出
PROVIDER_PRESETS = {
    "glm":  {"base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-flash"},
    "qwen": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
             "model": "qwen-turbo"},
}


def ensure_config_file(path: str = DEFAULT_CONFIG_PATH) -> bool:
    """配置文件不存在时生成模板。返回 True 表示本次新建了文件。"""
    if os.path.exists(path):
        return False
    cfg = AppConfig()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    save_config(cfg, path)
    return True


def load_config(path: str = DEFAULT_CONFIG_PATH) -> AppConfig:
    cfg = AppConfig()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for name, value in data.items():
            obj = getattr(cfg, name, None)
            if isinstance(obj, _SECTIONS) and isinstance(value, dict):
                for k, v in value.items():
                    if hasattr(obj, k):
                        setattr(obj, k, v)
    return cfg


def save_config(cfg: AppConfig, path: str = DEFAULT_CONFIG_PATH) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(cfg), f, ensure_ascii=False, indent=2)
