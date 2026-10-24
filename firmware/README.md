# 下位机固件（ESP32-S3-CAM + INMP441 + MAX98357A）

本地**离线**唤醒词检测 + 语音采集上传 + TTS 音频播放。

- 唤醒在片内完成（esp-sr WakeNet），**不联网、不上传音频**
- 唤醒后按 20ms 分帧把 PCM 上传给上位机
- 接收上位机下发的 TTS PCM 并驱动功放播放，支持播报中打断（AEC）
- 参数（音量/唤醒开关/回复词开关/WiFi/上位机地址）可由上位机下发并持久化到 NVS

| 项 | 值 |
|---|---|
| 固件版本 | **0.5.0**（`components/vw_utils/include/vw_version.h`） |
| 芯片 / Flash | ESP32-S3 / 16MB |
| ESP-IDF | v5.3.5（要求 ≥ 5.0） |
| esp-sr | 1.9.x（**AFE 接口**，非 `esp_sr.h`） |
| 音频链路 | 16kHz / 16bit / 单声道 / 20ms 分帧（320 样本 = 640 字节） |

---

## 1. 硬件接线

| 信号 | INMP441（I2S0 RX） | MAX98357A（I2S1 TX） |
|---|---|---|
| BCLK / SCK | GPIO5 | GPIO16 |
| WS / LRC | GPIO4 | GPIO15 |
| DIN / SD | GPIO6（输入） | GPIO7（输出） |
| 电源 | 3.3V + GND | VIN 5V（3.3V 也可，声小）+ GND |
| 其他 | `L/R` 接 GND 或 VDD（不能悬空） | `SD_MODE` 接 3.3V；`GAIN` 按需 |

> 引脚是**推荐默认值**，在 `idf.py menuconfig → Voice Wake System` 里可改，
> 务必按你的板子原理图核对。本项目**不启用摄像头**，建议把摄像头排线拔掉（会占 GPIO）。

---

## 2. 快速开始

### 2.1 环境

```bash
# 安装 ESP-IDF v5.3.x 并进入其导出环境
# Windows: 运行 esp-idf 目录下的 export.bat；Linux/macOS: source export.sh
idf.py --version        # 确认 v5.3.x
```

### 2.2 首次构建与烧录

```bash
cd firmware

idf.py set-target esp32s3
rm -f sdkconfig                 # 关键：sdkconfig 已存在时不会套用 sdkconfig.defaults 的新增项
idf.py reconfigure
idf.py menuconfig               # 核对下面 4 项
idf.py build
ls -l build/srmodels/srmodels.bin    # 必须 < 4MB（model 分区大小）
idf.py -p COMx erase-flash           # 分区表偏移已变时必须整片擦
idf.py -p COMx flash monitor         # 必须 flash，不能用 app-flash
```

`menuconfig` 必查 4 项：

| 位置 | 检查 |
|---|---|
| Voice Wake System → 麦克风引脚 | 与原理图一致 |
| Voice Wake System → 唤醒词模型名 | 与启动日志里的 `可用模型:` 之一匹配 |
| Voice Wake System → 上位机 TCP 监听端口 | 与上位机 `network.port` 一致 |
| Component config → ESP PSRAM | 开启；Octal 板选 OCT，Quad 板（N8R2）改 QUAD |

### 2.3 后续只改代码时

```bash
idf.py build
idf.py -p COMx flash monitor        # 仍然建议整片 flash（会顺带刷 model 分区）
```

---

## 3. 目录结构

```
firmware/
├── CMakeLists.txt                  # project(voice_wake)
├── sdkconfig.defaults              # PSRAM / cache / flash / esp-sr 默认值
├── partitions.csv                  # 16MB：nvs / factory / model(4MB) / ota_0 / ota_1
├── main/
│   ├── app_main.c                  # 只做编排：NVS → I2S → 业务服务
│   ├── CMakeLists.txt
│   └── Kconfig.projbuild           # 全部 VW_* 可调项（必须在 main/ 下才生效）
├── components/
│   ├── vw_utils/                   # 日志 VW_LOG_E + 错误码 + VW_FW_VERSION
│   ├── vw_audio/                   # I2S RX/TX + 线路自检（静止线/串扰）
│   ├── vw_config/                  # NVS 持久化（namespace "vw"）
│   ├── vw_wakeword/                # esp-sr AFE：WakeNet + AEC + VAD
│   ├── vw_comm/                    # 帧协议 / TCP 客户端 / WiFi+AP 配网
│   └── vw_voice/                   # 会话状态机、控制命令、端点检测、打断
├── test/
│   ├── mic_test/                   # 独立工程：麦克风验证（扫位宽×槽位）
│   └── amp_test/                   # 独立工程：功放验证（3 种帧格式轮播）
└── docs/
    ├── 01_接口设计.md
    ├── 02_架构设计.md
    ├── 03_故障排查.md
    └── 04_配置说明.md
```

依赖方向：`main → vw_voice → {vw_audio, vw_config, vw_comm, vw_wakeword} → vw_utils`，
底层不反向依赖上层。

---

## 4. 运行日志（正常启动）

```
main      : voice wake system boot (fw 0.5.0), reset=POWERON(1), free_heap=xxxxx
app:voice : config: volume=60 host=192.168.1.10:8888
srv:wake  : model partition 'model', 可用模型: wn9_hilexin ...
main      : system ready, wakenet=wn9_hilexin
comm:net  : host connected
app:voice : control: config.get
```

打通后说唤醒词应看到：

```
app:voice : control: session.start
app:voice : control: session.end
```

---

## 5. 三个必须知道的坑

### 5.1 必须 `idf.py flash`，不能 `app-flash`

esp-sr 在 S3 上把唤醒模型打包成 `srmodels.bin` 烧到独立的 `model` 分区
（**不编译进固件**）。`app-flash` 只更新 app 分区 → model 分区为空 →
`esp_srmodel_init()` 拿不到模型 → 启动即 `LoadProhibited` 重启循环。

### 5.2 `model` 分区必须 ≥ `srmodels.bin`

本工程给到 **4MB**（`partitions.csv`）。分区小于 bin 体积时只能 mmap 到部分数据，
模型句柄解析到越界指针，同样表现为启动崩溃。

### 5.3 PSRAM 模式要对

AFE 高性能模式强依赖 PSRAM。ESP32-S3-CAM 常见两种：

| 板型 | PSRAM 模式 |
|---|---|
| N8R8（8MB Octal） | `CONFIG_SPIRAM_MODE_OCT` |
| N8R2（2MB Quad） | `CONFIG_SPIRAM_MODE_QUAD` |

选错 → PSRAM 初始化失败 → `abort` → 重启。

---

## 6. 单独验证硬件（强烈建议）

出问题时不要在业务固件里猜，直接用独立工程定位：

```bash
# 麦克风：自动扫 32/16bit × 左/右槽，输出 rms/peak/dup/cls
cd test/mic_test && idf.py set-target esp32s3 && idf.py -p COMx flash monitor

# 功放：3 种帧格式轮播，"响几声"就是第几种格式可用
cd test/amp_test && idf.py set-target esp32s3 && idf.py -p COMx flash monitor
```

判读方法见 `test/README.md`。

---

## 7. 文档

| 文档 | 内容 |
|---|---|
| [docs/01_接口设计.md](docs/01_接口设计.md) | 帧格式、控制命令、音频子帧、NVS 键、错误码 |
| [docs/02_架构设计.md](docs/02_架构设计.md) | 分层与依赖、任务模型、状态机、数据流 |
| [docs/03_故障排查.md](docs/03_故障排查.md) | 18 类现场问题的现象→根因→处理 |
| [docs/04_配置说明.md](docs/04_配置说明.md) | 全部 menuconfig 项、分区表、NVS 生效顺序 |

上位机侧见 `../host/README.md`；全项目总览见 `../README.md`。
