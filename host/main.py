"""上位机入口：初始化日志 → 加载/生成配置 → 引擎自检 → 启动 TCP 服务 + GUI。

设计要点：
- 首次运行会自动生成 `host/config.json`，用户不必再去代码里找默认值；
- 启动时对 STT / LLM / TTS 各做一次自检，缺什么当场说清楚，
  避免"等设备说话时才报一句处理失败"这种难排查的体验。
"""
import os
import socket
import sys

from voice_assistant.log import init_logging, get_logger
from voice_assistant.config import DEFAULT_CONFIG_PATH, ensure_config_file, load_config
from voice_assistant.network import TcpServer
from voice_assistant.pipeline import Pipeline
from voice_assistant.stt import STTEngine
from voice_assistant.llm import LLMClient
from voice_assistant.tts import TTSEngine


def local_ip() -> str:
    """取本机对外的 IPv4（不实际发包），用于提示下位机该填哪个 IP。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _self_check(log, cfg, stt, llm, tts) -> None:
    """启动自检：把三类问题一次性说清楚，而不是等会话时报错。"""
    # 1) LLM：最常见的是 api_key 没填
    err = llm.preload()
    if err:
        log.warning("【LLM 未就绪】%s", err)
    else:
        log.info("LLM 就绪：%s / %s（key=%s****）",
                 cfg.llm.provider, cfg.llm.model, cfg.llm.api_key[:6])

    # 2) STT：模型可能没下载（国内直连 HF 会超时）
    err = stt.preload()
    if err:
        log.warning("【STT 未就绪】%s", err)
    else:
        log.info("STT 就绪：%s", cfg.stt.model_size)

    # 3) TTS：模型文件是否存在
    err = tts.preload()
    if err:
        log.warning("【TTS 未就绪】%s", err)
    else:
        log.info("TTS 就绪：%s", os.path.basename(cfg.tts.model_path))


def main():
    # 统一日志：控制台 + 文件落盘（项目根 logs/ 目录）
    log_dir = os.path.join(os.path.dirname(__file__), "..", "logs")
    init_logging(level=20, log_dir=log_dir)  # level=20 → INFO
    log = get_logger("main")

    # 首次运行生成配置模板，之后所有参数（含大模型）都在这个文件里
    created = ensure_config_file()
    if created:
        log.warning("已生成配置文件模板：%s（请填写 llm.api_key 等信息）", DEFAULT_CONFIG_PATH)

    cfg = load_config()
    log.info("配置文件：%s", DEFAULT_CONFIG_PATH)
    log.info("上位机启动：监听 %s:%d，STT=%s(%s)，LLM=%s/%s，TTS=%s",
             cfg.network.host, cfg.network.port,
             cfg.stt.model_size, cfg.stt.device,
             cfg.llm.provider, cfg.llm.model,
             os.path.basename(cfg.tts.model_path))
    log.info("本机 IP=%s，下位机 host.ip 应填这个地址（当前配置 host_ip=%s）",
             local_ip(), cfg.device.host_ip)

    # 各引擎（按需懒加载，避免无模型时启动失败）
    stt = STTEngine(cfg.stt)
    llm = LLMClient(cfg.llm)
    tts = TTSEngine(cfg.tts)

    _self_check(log, cfg, stt, llm, tts)

    pipeline = Pipeline(cfg, stt, llm, tts)
    server = TcpServer(cfg.network.host, cfg.network.port)
    server.on_frame = pipeline.on_frame

    from voice_assistant.gui.main_window import run_gui
    run_gui(cfg, server, pipeline, stt, llm, tts)


if __name__ == "__main__":
    sys.exit(main())
