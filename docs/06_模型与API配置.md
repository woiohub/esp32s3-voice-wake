# 模型与大模型配置手册

> 适用版本：上位机 v0.6.0 · faster-whisper 1.2.1 · piper-tts 1.8.0 · openai 3.37.0
>
> 本文覆盖三件事：**STT 模型**、**TTS 模型**的下载地址/文件名/放置位置，以及 **大模型（GLM / Qwen）** 的填写项。
> 所有配置最终都写在 `host/config.json`，也可以在界面「模型」页可视化修改后点「保存」。

---

## 0. 一分钟速查表

| 组件 | 模型名 | 需要的文件 | 放到哪里 |
|---|---|---|---|
| STT | `Systran/faster-whisper-<size>` | `model.bin`、`config.json`、`tokenizer.json`（tiny/base 另需 `vocabulary.txt`） | `host/models/whisper/faster-whisper-<size>/` → `stt.model_dir`（**默认手动**） |
| TTS | `zh_CN-huayan-medium` | `zh_CN-huayan-medium.onnx` **和** `zh_CN-huayan-medium.onnx.json`（两个都必须有） | `host/models/piper/` |
| LLM | GLM `glm-4-flash` 或 Qwen `qwen-turbo` | 只需 `api_key`，无需下载模型 | `host/config.json → llm` |

---

## 1. STT：faster-whisper 轻量化模型

### 1.1 模型清单与体积

| 模型名（HF repo） | 文件大小 | 中文效果 | 建议 |
|---|---|---|---|
| `Systran/faster-whisper-tiny` | ≈ 75 MB | 能用，短句够 | **默认，先跑通** |
| `Systran/faster-whisper-base` | ≈ 145 MB | 明显更好 | 有独显/CPU 较强时推荐 |
| `Systran/faster-whisper-small` | ≈ 484 MB | 好 | 追求准确率 |
| `Systran/faster-whisper-medium` | ≈ 1.5 GB | 很好 | 一般不必要 |

> 这些都是 **CTranslate2 格式**（`faster-whisper` 专用），不是 OpenAI 原始的 `.pt` 权重。
> 版本要求：`faster-whisper >= 1.0`（本项目实测 1.2.1）。

### 1.2 下载地址

- 官方：`https://huggingface.co/Systran/faster-whisper-tiny/tree/main`
- 国内镜像（推荐）：`https://hf-mirror.com/Systran/faster-whisper-tiny/tree/main`

把 `tiny` 换成 `base` / `small` / `medium` 即为其他规格。

### 1.3 模型目录里应该有什么

```
faster-whisper-tiny/
├── config.json          # 必需
├── model.bin            # 必需（约 75MB，权重本体）
├── tokenizer.json       # 必需
├── vocabulary.txt       # tiny/base 需要
└── preprocessor_config.json   # 部分版本有，建议一并保留
```

> 只要 `model.bin` 缺失，加载就会失败；`config.json` 缺失会报 `Unable to open config.json`。

### 1.4 放置方式（默认手动，可选一键下载）

> **默认策略：不自动下载。** `stt.auto_download` 默认 `false`，找不到模型时会直接报错并给出指引，
> 避免"没网就卡住"或"没打招呼就下 500MB"。需要自动下载时勾选「允许程序自动下载」。

**方式 A：手动放置（默认，推荐）**

1. 浏览器打开镜像地址，下载必需文件（`README.md` 可选）；
2. 新建目录，例如 `host/models/whisper/faster-whisper-small/`，把文件放进去；
3. 配置指向该目录：

```jsonc
"stt": {
  "model_size": "small",
  "model_dir": "models/whisper/faster-whisper-small",  // 相对 host/ 或绝对路径
  "auto_download": false
}
```

目录结构必须是：

```
host/models/whisper/faster-whisper-small/
├── model.bin          ← 必需（权重，约 484MB）
├── config.json        ← 必需
├── tokenizer.json     ← 必需
├── vocabulary.txt     ← tiny/base 需要
└── README.md          ← 可选
```

> 程序启动时会校验这三个必需文件，缺哪个会明确报出来（`模型目录缺少必需文件：…`）。

**方式 B：界面「一键下载模型」**

模型页（Ctrl+3）底部 →「一键下载模型」：

1. 弹窗列出**将要下载的内容与体积**，确认后才开始；
2. 后台线程下载，界面不卡；进度显示在按钮左侧；
3. **只下缺失的部分**：STT 目标目录 = `stt.model_dir`（未设置则 `models/whisper/faster-whisper-<model_size>`），
   TTS 目标目录 = `tts.model_path` 所在目录；
4. 完成后自动重新自检，底部三个模型灯变绿。

**方式 C：命令行批量下载**

```powershell
# 一次装好（只需一次）
pip install "huggingface_hub[cli]"

# 用镜像下载到指定目录
$env:HF_ENDPOINT = "https://hf-mirror.com"
huggingface-cli download Systran/faster-whisper-small `
  --local-dir .\models\whisper\faster-whisper-small
```

### 1.4.1 `auto_download` 行为对照

| 取值 | 行为 |
|---|---|
| `false`（默认） | 只用本地模型；缺失时报错并提示"手动放置 / 一键下载 / 勾选自动下载" |
| `true` | 加载时若找不到模型，自动从 `hf_endpoint` 下载到 `download_root` |

界面上的勾选框「允许程序自动下载（关闭时只用本地模型）」就是这项，保存后写入 `config.json`。

### 1.5 常见错误

| 报错 | 原因 | 处理 |
|---|---|---|
| `未找到本地 STT 模型：stt.model_dir 为空，且已关闭自动下载` | 默认手动模式且没配目录 | 配置 `stt.model_dir`，或点「一键下载模型」，或勾选自动下载 |
| `模型目录缺少必需文件：model.bin` | 只放了部分文件 | 补齐 `model.bin` / `config.json` / `tokenizer.json` |
| `ConnectTimeout [WinError 10060]` | 直连 HuggingFace 超时 | 设 `stt.hf_endpoint = https://hf-mirror.com` |
| `I/O error: 拒绝访问 (os error 5)` | xet 后端往用户目录写日志被拒 | 已自动设 `HF_HUB_DISABLE_XET=1` 并把 `HF_HOME` 放到项目内；若仍报，确认 `host/models/hf_home` 可写 |
| `Unable to open config.json` | 只下了 `model.bin` | 补齐 `config.json` / `tokenizer.json` / `vocabulary.txt` |
| 识别出乱码或空白 | `stt.language` 不对 | 中文设 `zh`；中英混杂设 `auto` |

---

## 2. TTS：Piper 轻量化模型

### 2.1 模型信息

| 项 | 值 |
|---|---|
| 模型名 | `zh_CN-huayan-medium`（中文女声，中等质量） |
| 采样率 | **22050 Hz**（程序会自动重采样到链路的 16 kHz） |
| 体积 | onnx ≈ 63 MB + json ≈ 5 KB |
| 运行库 | `piper-tts 1.8.0`（纯本地，CPU 即可） |

其他可选中文音色：`zh_CN-huayan-x_low`（更快、更小），英文：`en_US-lessac-medium` 等。

### 2.2 下载地址

- 官方：`https://huggingface.co/rhasspy/piper-voices/tree/main/zh/zh_CN/huayan/medium`
- 国内镜像：`https://hf-mirror.com/rhasspy/piper-voices/tree/main/zh/zh_CN/huayan/medium`

!!! 注意：**必须同时下载两个文件**，缺 `.onnx.json` 无法合成。

### 2.3 放置位置

```
host/
└── models/
    └── piper/
        ├── zh_CN-huayan-medium.onnx        ← 必需
        └── zh_CN-huayan-medium.onnx.json   ← 必需（同名 + .json）
```

配置：

```jsonc
"tts": {
  "model_path": "models/piper/zh_CN-huayan-medium.onnx",
  "speaker": 0,
  "length_scale": 1.0          // >1 更慢，<1 更快
}
```

> 界面「模型 → TTS」里可以直接选文件，保存时会自动校验同目录 `.onnx.json` 是否存在。

### 2.4 常见错误

| 报错 | 原因 | 处理 |
|---|---|---|
| `Piper 模型不存在` | 路径写错 / 文件没放 | 确认 `host/models/piper/` 下两个文件都在 |
| `缺少同名 .onnx.json` | 只下了 onnx | 补下 json（里面是音素表与采样率） |
| 声音变调、语速偏快 | 旧版未重采样（22050→16000） | v0.5.1 起已自动重采样，无需处理 |

---

## 3. 大模型（LLM）配置

两个厂商都走 **OpenAI 兼容接口**，所以只需要 4 个字段：`base_url`、`api_key`、`model`，以及可选的采样参数。

### 3.1 智谱 GLM（默认）

| 字段 | 填写内容 |
|---|---|
| `provider` | `glm` |
| `base_url` | `https://open.bigmodel.cn/api/paas/v4` |
| `api_key` | 在 <https://open.bigmodel.cn/> → 控制台 → API Keys 创建，形如 `xxxxxxxx.xxxxxxxx` |
| `model` | `glm-4-flash`（免费/低价、延迟低，推荐）；也可 `glm-4-air`、`glm-4-plus` |

### 3.2 阿里云百炼 Qwen

| 字段 | 填写内容 |
|---|---|
| `provider` | `qwen` |
| `base_url` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `api_key` | 在 <https://bailian.console.aliyun.com/> → API-KEY 管理 创建，形如 `sk-xxxxxxxx` |
| `model` | `qwen-turbo`（快）/ `qwen-plus`（更准）/ `qwen-max` |

### 3.3 全部字段说明

```jsonc
"llm": {
  "provider": "glm",                                  // glm | qwen（仅用于界面预设，不影响请求）
  "base_url": "https://open.bigmodel.cn/api/paas/v4", // 兼容端点，必须以 http(s):// 开头
  "api_key": "在此填入你的 Key",                        // 必填，空值会在发请求前就报错
  "model": "glm-4-flash",                             // 模型名，必须与服务商一致
  "max_tokens": 128,                                  // 语音播报要短，建议 80~160
  "temperature": 0.7,                                 // 0~2，越低越稳定
  "timeout": 30.0,                                    // 单次请求超时（秒）
  "system_prompt": "你是语音助手，用简洁口语回答，控制在1-2句话以内。"
}
```

> `max_tokens` 直接决定播报时长。语音场景建议 ≤160，否则一句话要念十几秒。
> `system_prompt` 里强调"1-2 句、口语"，是控制端到端延迟最有效的一招。

### 3.4 怎么验证

三种方式，任选：

1. **界面**：模型页 → 填好后点「测试大模型」，成功会显示 `成功 1234ms · 你好！...`；
2. **启动自检**：`python main.py` 启动日志出现 `LLM 就绪：glm / glm-4-flash（key=xxxxxx****）`；
3. **命令行**：

```powershell
python -c "from voice_assistant.config import load_config; from voice_assistant.llm import LLMClient; print(LLMClient(load_config().llm).reply('你好'))"
```

### 3.5 常见错误

| 现象 | 原因 | 处理 |
|---|---|---|
| `未配置大模型 api_key` | key 为空 | 界面或 `config.json` 填写 |
| `api_key 无效或已过期（401）` | key 错 / 有空格 / 已删除 | 重新复制，注意不要带引号和空格 |
| `没有该模型的访问权限（403）` | `model` 名称错 / 未开通 | 换成 `glm-4-flash` 或 `qwen-turbo` |
| `请求过于频繁或额度已用完（429）` | 限流/余额 | 稍后重试或充值 |
| `无法连接大模型服务` | 网络/代理/DNS | 换网络；公司网需配置代理 |
| 回复很长、播报很久 | `max_tokens` 太大 | 调到 128 以内 |

---

## 4. 完整配置示例

`host/config.json`（首次启动自动生成，可直接照此修改）：

```json
{
  "audio": { "sample_rate": 16000, "bits": 16, "channels": 1, "frame_ms": 20 },
  "stt": {
    "model_size": "small",
    "model_dir": "models/whisper/faster-whisper-small",
    "auto_download": false,
    "hf_endpoint": "https://hf-mirror.com",
    "download_root": "models/whisper",
    "hf_home": "models/hf_home",
    "language": "zh",
    "device": "cpu",
    "compute_type": "int8",
    "beam_size": 5
  },
  "llm": {
    "provider": "glm",
    "base_url": "https://open.bigmodel.cn/api/paas/v4",
    "api_key": "",
    "model": "glm-4-flash",
    "max_tokens": 128,
    "temperature": 0.7,
    "timeout": 30.0,
    "system_prompt": "你是语音助手，用简洁口语回答，控制在1-2句话以内。"
  },
  "tts": {
    "model_path": "models/piper/zh_CN-huayan-medium.onnx",
    "speaker": 0,
    "length_scale": 1.0
  },
  "network": { "host": "0.0.0.0", "port": 8888 },
  "device": {
    "volume": 60, "wake_enable": true, "host_ip": "", "host_port": 8888,
    "wifi_ssid": "", "wifi_password": ""
  }
}
```

---

## 5. 下载完成后的自检清单

```
[ ] host/models/piper/zh_CN-huayan-medium.onnx         存在
[ ] host/models/piper/zh_CN-huayan-medium.onnx.json    存在
[ ] <stt.model_dir>/model.bin                         存在
[ ] <stt.model_dir>/config.json                       存在
[ ] <stt.model_dir>/tokenizer.json                    存在
[ ] config.json → llm.api_key                          非空
[ ] python main.py 启动日志三行都是 "就绪"，没有 "【xx 未就绪】"
[ ] 界面底部三个模型灯都是绿色；LLM 灯为红色时说明 key 未配置
```

---

## 6. 本机当前配置（已实测校验）

| 项 | 实际值 |
|---|---|
| STT 目录 | `host/models/whisper/faster-whisper-small/`（`model.bin` 483 MB） |
| STT 配置 | `model_size=small`、`model_dir=models/whisper/faster-whisper-small`、`auto_download=false` |
| TTS 文件 | `host/models/piper/zh_CN-huayan-medium.onnx` + `.onnx.json` |
| 实测 STT | 加载 **1.3 s**；1.5 s 静音推理 **0.31 s**；`language=zh` 生效 |
| 实测 TTS | 加载 **3.6 s**；合成 1.45 s → 2.75 s 音频（已重采样 22050→16000） |
| 大模型 | 待填 `llm.api_key`（GLM `glm-4-flash` / Qwen `qwen-turbo`） |

> **性能提示**：`small` 比 `tiny` 慢约 3~5 倍（CPU / int8）。
> 若一轮对话里 STT 超过 2 s，可将 `stt.model_size` 改回 `tiny` 并放入对应目录；
> 汉语短句场景 tiny 通常已够用，长句/嘈杂环境再上 small。
