# 上位机（Python / PySide6）

接收下位机上传的语音 → **STT（faster-whisper）→ LLM（GLM/Qwen）→ TTS（Piper）** →
把合成音频按实时节奏回传给设备播放，并提供图形界面配置设备、查看会话与日志。

| 项 | 值 |
|---|---|
| 版本 | **v0.7.2** |
| Python | 3.10+（实测 3.10 / 3.13） |
| GUI | PySide6 6.x |
| 角色 | **TCP 服务端**，监听 `0.0.0.0:8888` |
| 音频链路 | 16kHz / 16bit / 单声道 / 20ms 分帧 |

---

## 1. 安装

```bash
cd host

# 方式 A：venv
python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # Linux/macOS
pip install -r requirements.txt

# 方式 B：conda
conda create -n voice-upper python=3.10 -y
conda activate voice-upper
pip install -r requirements.txt
```

依赖：`pyside6` `openai` `faster-whisper` `piper-tts` `onnxruntime` `numpy` `sounddevice`(可选)。

> **每个新终端都要先激活环境**。用错解释器最常见的表现就是
> `ModuleNotFoundError: No module named 'PySide6'`。

---

## 2. 运行

```bash
python main.py
```

首次运行会：

1. 自动生成 `config.json`（含全部默认值）；
2. 对 STT / LLM / TTS 各做一次自检，缺什么当场打印；
3. 打印本机 IP —— **这就是下位机要填的 `host.ip`**；
4. 启动 TCP 服务 + GUI 窗口。

```
INFO: 配置文件：E:\...\host\config.json
INFO: 上位机启动：监听 0.0.0.0:8888，STT=small(cpu)，LLM=glm/glm-4-flash，TTS=zh_CN-huayan-medium.onnx
INFO: 本机 IP=172.17.253.195，下位机 host.ip 应填这个地址（当前配置 host_ip=）
INFO: LLM 就绪：glm / glm-4-flash（key=xxxxxx****）
INFO: STT 就绪：small
INFO: Piper 输出 22050 Hz，将重采样到 16000 Hz
INFO: TTS 就绪：zh_CN-huayan-medium.onnx
INFO: tcp server listening on 0.0.0.0:8888
INFO: device connected: ('172.17.253.46', 59597)
INFO: 设备上线: {"device": "esp32s3", "fw": "0.5.0", "audio": {...}}
```

---

## 3. 三处必须配的东西

### 3.1 大模型 key

`config.json → llm.api_key`，或在界面「模型」页填写后保存。

| provider | `base_url` | 推荐 `model` |
|---|---|---|
| `glm` | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash`（有免费额度） |
| `qwen` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-turbo` |

验证方式：模型页点 **「测试大模型」**（快捷键 `F5`）。

### 3.2 STT 模型（默认手动放置）

```
host/models/whisper/faster-whisper-small/
├── model.bin          ← 必需
├── config.json        ← 必需
├── tokenizer.json     ← 必需
└── ...
```

```json
"stt": { "model_size": "small", "model_dir": "models/whisper/faster-whisper-small" }
```

> `model_dir` 要指到**含 `model.bin` 的那一层**。默认 `auto_download=false`，
> 找不到模型会直接报错而不是静默联网。
> 也可在模型页点「一键下载模型」，或勾选「允许程序自动下载」。

### 3.3 TTS 模型（两个文件都要）

```
host/models/piper/zh_CN-huayan-medium.onnx
host/models/piper/zh_CN-huayan-medium.onnx.json
```

模型的 22050Hz 输出会被**自动重采样**到 16kHz，不用手工处理。

---

## 4. 与下位机对接

1. 启动上位机，记下日志里的 `本机 IP`；
2. 打开「设备」页，把该 IP 填进「上位机 IP」，端口默认 8888 → 点「下发配置」；
3. 若设备还没配 WiFi：设备会**开启蓝牙广播 `VoiceWake-XXXX`**，在「蓝牙配网」卡片点「扫描」
   选中设备，填好 WiFi 凭证与上位机地址后「写入设备并连接」。设备连上 WiFi 后会自动关闭蓝牙；
4. 设备重启后顶部状态胶囊变绿，日志出现 `设备上线: {...}` 即可说唤醒词。

> 端口两侧必须一致：`config.json → network.port` ↔ 固件 `CONFIG_VW_TCP_PORT`。

---

## 5. 界面速览

| 页面 | 内容 |
|---|---|
| **会话** | 6 段阶段条（唤醒/录音/识别/大模型/合成/播报）+ 对话流 + 文本调试输入 + 就绪清单 |
| **设备** | 连接卡（IP/MAC/信号/固件/唤醒词/音量）、**蓝牙配网**（扫描→写入凭证与上位机地址）、下位机参数（音量、唤醒开关、**唤醒回复词**、上位机 IP/端口） |
| **模型** | STT / LLM / TTS 三卡：字段校验、异步自检、测试大模型、试听、一键下载 |
| **日志** | 级别/模块/关键字过滤、暂停、清空、导出、ERROR 染色、错误码说明（上限 5000 行） |

快捷键：

| 键 | 作用 |
|---|---|
| `Ctrl+1..4` | 切换页面 |
| `Ctrl+,` | 打开模型/设置页 |
| `Ctrl+R` | 折叠/展开右侧运行状态栏 |
| `Ctrl+L` | 清空对话 |
| `Ctrl+F` | 聚焦日志搜索 |
| `F5` | 测试大模型 |
| `Ctrl+Shift+C` | 复制最后一条回复 |
| `Esc` | 清空搜索/输入框 |

气泡上的三个悬浮按钮：**复制 / 重新播报（只走 TTS）/ 重新生成（重走 LLM）**。

---

## 6. 目录结构

```
host/
├── main.py                     # 日志 → 配置 → 自检 → TCP 服务 + GUI
├── requirements.txt
├── config.json                 # 业务配置（首次启动生成；含 api_key，勿提交）
├── ui_state.json               # GUI 偏好（窗口尺寸/页面/折叠/主题，自动生成）
├── models/                     # 模型目录（不入库）
└── voice_assistant/
    ├── log.py  config.py  protocol.py  network.py  pipeline.py
    ├── stt.py  llm.py  tts.py  model_download.py  provision.py
    └── gui/
        ├── theme.py  icons.py  bridge.py  tasks.py  main_window.py
        ├── pages/    # session / device / model / log
        └── widgets/  # chat_bubble / stage_timeline / level_meter
                      # status_pill / model_status_card / toast / setup_checklist
```

约定：

- **业务层（pipeline/stt/llm/tts）里没有任何 Qt import**，需要通知界面时只调可选回调；
- 所有耗时操作走 `gui/tasks.run_task`（QThreadPool）或 asyncio 线程，UI 线程不阻塞；
- 颜色/字号/间距只来自 `theme.py` 的 token，页面里不写字面量。

---

## 7. 常见问题速查

| 现象 | 原因 | 处理 |
|---|---|---|
| `No module named 'PySide6'` | 解释器不对 | 激活 venv/conda 后重装依赖 |
| `TCP 监听 8888 失败` | 端口被上次的进程占用 | `taskkill /PID <pid> /F` 或换端口（两侧都改） |
| `【STT 未就绪】` | `model_dir` 层级不对 / 缺文件 | 指到含 `model.bin` 的那一层 |
| 下载 `WinError 10060` | 直连 HF 不通 | 用 `hf_endpoint=https://hf-mirror.com` |
| 下载 `os error 5` | xet 后端写用户缓存被拦 | 已内置 `HF_HUB_DISABLE_XET=1` + 项目内 `HF_HOME` |
| `鉴权失败（401）` | key 错/过期/无该模型权限 | 模型页「测试大模型」看原始错误 |
| 播报飞快 | 下发节奏问题（设备队列被灌满丢帧） | 已修：实时节奏 + 队列 40 帧 |
| 声音变调 | 22050 → 16000 未重采样 | 已修：`tts.resample_int16()` |
| 没有"在的" | `ack_enable` 关 / TTS 未就绪 | 设备页开关 + 看日志 `ack skipped` |
| 界面白色方块 | 原生控件漏 QSS | 见 `docs/03_故障排查.md §15` |

完整排查见 [docs/03_故障排查.md](docs/03_故障排查.md)。

---

## 8. 文档

| 文档 | 内容 |
|---|---|
| [docs/01_接口设计.md](docs/01_接口设计.md) | 帧协议、控制命令、下发节奏、对 GUI 的回调事件表 |
| [docs/02_架构设计.md](docs/02_架构设计.md) | 分层、线程模型、管线、GUI 结构、性能基线 |
| [docs/03_故障排查.md](docs/03_故障排查.md) | 18 类问题的现象→根因→处理 |
| [docs/04_配置说明.md](docs/04_配置说明.md) | `config.json` 全部键、模型放置、环境变量 |

下位机侧见 `../firmware/README.md`；全项目总览见 `../README.md`。
