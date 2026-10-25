# 本地离线唤醒语音交互系统

基于 **ESP32-S3-CAM + INMP441 麦克风 + MAX98357 功放** 的本地离线唤醒语音交互系统。

- **下位机**（ESP-IDF）：本地离线唤醒词检测，唤醒后采集语音经 WiFi 传上位机，接收 TTS 音频驱动扬声器播报，可被上位机配置参数。
- **上位机**（Python）：STT → LLM（GLM/Qwen）→ TTS，PySide6 图形界面，可配置下位机参数。

## 已确认方案

| 决策点 | 结论 |
|--------|------|
| 传输 | WiFi STA + TCP 长连接（设备作客户端），**蓝牙（BLE）配网兜底** |
| 音频 | PCM 16kHz / 16bit / 单声道，20ms 分帧 |
| 唤醒词 | ESP-SR WakeNet（默认 `wn9_hilexin`，可换 `wn9_xiaomeitongxue_tts` 等） |
| 打断 | 已集成 AEC，支持播报中打断（barge-in） |
| STT | faster-whisper（本项目实测用 `small`，低配可换 `tiny`） |
| LLM | GLM / Qwen（OpenAI 兼容接口） |
| TTS | Piper（本地轻量，22050Hz 输出、自动重采样到 16k） |
| GUI | PySide6 桌面 |
| 摄像头 | 不启用，释放 GPIO 给两路 I²S |

## 文档地图

**每侧都有独立的 4 份文档**，改代码时只看自己那一侧就够了。

| 内容 | 总览 | 下位机 | 上位机 |
|---|---|---|---|
| 接口设计 | `docs/01_接口设计.md` | `firmware/docs/01_接口设计.md` | `host/docs/01_接口设计.md` |
| 架构设计 | `docs/03_架构设计.md` | `firmware/docs/02_架构设计.md` | `host/docs/02_架构设计.md` |
| 故障排查 | `docs/05_故障排查.md` | `firmware/docs/03_故障排查.md` | `host/docs/03_故障排查.md` |
| 配置说明 | `docs/06_模型与API配置.md` | `firmware/docs/04_配置说明.md` | `host/docs/04_配置说明.md` |
| 快速上手 | `README.md` | `firmware/README.md` | `host/README.md` |

另外：`docs/02_项目计划.md`（里程碑）、`docs/04_变更记录.md`（逐版本变更）。

## 目录结构

```
audio/
├── .gitignore                     # 排除构建产物/模型/含密钥的本地配置
├── README.md                      # 本文件：总览 + 快速开始
├── docs/                          # 跨两端的文档
│   ├── 01_接口设计.md             # 帧协议总览、消息、引脚、错误码、NVS 键
│   ├── 02_项目计划.md             # 里程碑 M0–M7 与任务分解
│   ├── 03_架构设计.md             # 两端分层与扩展点
│   ├── 04_变更记录.md             # 逐版本变更（含根因与验证数据）
│   ├── 05_故障排查.md             # 排障总册（按轮次记录真实案例）
│   └── 06_模型与API配置.md        # 模型下载地址、大模型四字段
├── firmware/                      # 下位机（ESP-IDF 多组件）
│   ├── README.md                  # 构建/烧录/接线
│   ├── docs/                      # 01 接口 / 02 架构 / 03 排障 / 04 配置
│   ├── CMakeLists.txt
│   ├── sdkconfig.defaults         # PSRAM / cache / flash / esp-sr 默认值
│   ├── partitions.csv             # 16MB：model 分区 4MB
│   ├── main/                      # 入口组件（app_main.c + Kconfig.projbuild）
│   ├── components/                # vw_utils / vw_audio / vw_config
│   │                              # vw_wakeword / vw_comm / vw_voice
│   └── test/                      # mic_test / amp_test 独立验证工程
├── host/                          # 上位机（Python / PySide6）
│   ├── README.md                  # 安装/运行/界面/对接
│   ├── docs/                      # 01 接口 / 02 架构 / 03 排障 / 04 配置
│   ├── main.py
│   ├── requirements.txt
│   ├── config.json                # 首次启动生成（含 api_key，已被 .gitignore 排除）
│   ├── models/                    # STT / TTS 模型（不入库）
│   └── voice_assistant/           # protocol / network / pipeline / stt / llm / tts
│                                  # config / log / model_download / gui
├── logs/                          # 运行日志（不入库）
└── SensorMaterial/                # 芯片手册
```

## 快速开始

### 下位机（固件）

```bash
cd firmware
idf.py set-target esp32s3
idf.py fullclean
# 重要：sdkconfig 已存在时不会套用 sdkconfig.defaults 的新增项，首次升级需删除
rm -f sdkconfig            # Windows: del sdkconfig
idf.py reconfigure
idf.py menuconfig          # 核对 I2S 引脚、PSRAM(Octal/80M)、cache 64KB、flash 16MB、唤醒模型
idf.py build
ls -l build/srmodels/srmodels.bin   # 确认体积 < model 分区(4MB)
idf.py -p COMx erase-flash          # 分区表偏移已变，必须整片擦除
idf.py -p COMx flash monitor        # 必须是 flash，app-flash 不会烧 model 分区
```

### 上位机

```bash
cd host
python -m venv .venv && source .venv/Scripts/activate   # Windows
pip install -r requirements.txt
python main.py        # 首次运行会自动生成 host/config.json
```

首次运行后按提示补齐三处（也可在界面「大模型」面板直接填写并保存）：

| 项 | 位置 | 说明 |
|---|---|---|
| 大模型 key | `config.json → llm.api_key` | GLM：`https://open.bigmodel.cn/api/paas/v4`；Qwen：`https://dashscope.aliyuncs.com/compatible-mode/v1` |
| STT 模型 | `config.json → stt.*` | **默认手动**：放到 `models/whisper/faster-whisper-<size>/`，把 `stt.model_dir` 指向它；也可在模型页点「一键下载模型」，或勾选「允许程序自动下载」 |
| TTS 模型 | `config.json → tts.model_path` | 需 `zh_CN-huayan-medium.onnx` 与 `.onnx.json` 两个文件 |

界面上的 **「测试大模型」** 按钮可直接验证 key 是否可用；启动日志会对 STT/LLM/TTS 各做一次自检。

### 界面速览

| 页面 | 内容 |
|---|---|
| 会话 | 6 段阶段条 + 对话流（悬浮可复制/重新播报/重新生成）+ 文本调试输入 |
| 设备 | 连接卡片（IP/信号/固件/唤醒词/音量）、**蓝牙配网**、**唤醒回复词**、参数读取与下发 |
| 模型 | STT / LLM / TTS 三卡：字段校验、异步自检、测试大模型、试听 |
| 日志 | 级别/模块/关键字过滤、暂停、清空、导出、ERROR 行染色、错误码说明（上限 5000 行） |

快捷键：`Ctrl+1..4` 切页 · `Ctrl+,` 设置 · `Ctrl+R` 折叠右侧栏 · `Ctrl+L` 清屏 · `Ctrl+F` 搜日志 · `F5` 测试大模型 · `Ctrl+Shift+C` 复制最后一条回复 · `Esc` 清空输入。

> 模型下载地址、文件名与放置位置，以及大模型四字段的填写，见 **`docs/06_模型与API配置.md`**。

## 关键提醒

- **引脚**：`main/Kconfig.projbuild` 里的 I2S 引脚是推荐默认值，**必须按你的 ESP32-S3-CAM 实际原理图核对**。
- **唤醒模型**：esp-sr 在 S3 上把模型打包为 `srmodels.bin` 烧到 `model` 分区，**运行期必须 `esp_srmodel_init()` 才能使用**（本工程已在 `wakeword_init()` 内完成）。因此请始终用 `idf.py flash` 整片烧录，`app-flash` 会导致 model 分区为空 → 启动崩溃。
- **PSRAM**：AFE 高性能模式强依赖 PSRAM，默认按 Octal/80MHz 配置；若板子是 Quad PSRAM（N8R2 类），改 `CONFIG_SPIRAM_MODE_QUAD`，否则启动即重启。
- **采样率**：Piper 模型输出通常为 22050Hz，`tts.py` 已自动重采样到 16kHz（无需手工处理）。
- **STT 下载**：默认**不自动下载**。模型放好后把 `stt.model_dir` 指到含 `model.bin` 的那一层；
  也可在模型页点「一键下载模型」。国内网络依赖 `stt.hf_endpoint`（默认 hf-mirror.com）。
- **下发节奏**：上位机必须按 ≈实时节奏发音频（每帧 20ms、领先播放 150ms），
  否则设备播放队列会被灌满丢帧，听感是"1 秒念完 10 秒"。

详细文档见上表「文档地图」；下位机看 `firmware/README.md` 与 `firmware/docs/`，
上位机看 `host/README.md` 与 `host/docs/`。
