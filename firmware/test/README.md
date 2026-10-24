# firmware/test —— 硬件最小验证工程

这里放两个**独立**的 ESP-IDF 工程（各自有 `CMakeLists.txt` 和 `sdkconfig.defaults`），
用来把「软件配置」和「硬件接线」彻底分开。它们不依赖主项目的 `components/`、
不含 WiFi / esp-sr，初始化参数与主项目逐行对齐，因此结论可直接套回主项目。

| 工程 | 用途 | 判断标准 |
|---|---|---|
| `mic_test/` | INMP441 能不能采到数据 | 打印 `cls=正常` 且说话时 `rms/max` 变化 |
| `amp_test/` | MAX98357A 能不能出声 | 听到 1/2/3 声，对应三种 I2S 帧格式 |

---

## 1. mic_test（麦克风）

```bash
cd firmware/test/mic_test
idf.py set-target esp32s3
idf.py -p COM6 build flash monitor
```

启动后先做**配置扫描**（32/16bit × 左/右 共 4 种，各采 1 秒）：

```
I MIC_TEST: 引脚: SCK=GPIO5 WS=GPIO4 SD=GPIO6
I MIC_TEST: [SCAN] 32bit/left   BCLK=1024kHz cls=正常 rms=520   min=-3200 max=4100 flat=0.12 alt=0.00 raw=0012 0ff3 ...
I MIC_TEST: [SCAN] 32bit/right  BCLK=1024kHz cls=正常 rms=18    min=-40   max=35   flat=0.31 alt=0.00 raw=...
I MIC_TEST: ----- 扫描结论 -----
I MIC_TEST: 最佳配置: 32bit/left（cls=正常 rms=520）
```

然后每秒打印一行：

```
I MIC_TEST:  12.3s cls=正常 rms=640    dc=-2   min=-4100 max=5200 flat=0.08 alt=0.00 | raw=0a1f ff30 ...
```

**判读**

| 打印 | 含义 | 该做什么 |
|---|---|---|
| `cls=正常` 且说话时 `rms` 变化 | 麦克风正常 | 回到主项目测唤醒词 |
| `cls=静止`（`raw=ffff ffff…`） | SD 线上没有数据 | 量模块焊盘：VDD / SCK / WS / SD，见下 |
| `cls=串扰`（`raw=5555 5555…`） | 读到 BCLK 串扰 | 检查 L/R 接法与 SD 接线 |
| **`dup≈1.0`** | **每个样本重复两次** | **BCLK 只有一半（16bit 槽宽），麦克风实际 8kHz，音频被降速，不能用** |
| 四种配置全是静止 | 与固件无关 | 硬件问题，按下面的顺序查 |

> `dup` 是关键指标：MP441 每个 WS 帧必须 64 个 SCK。给 16bit 槽宽时 BCLK=512kHz
> （只有 32 个 SCK/帧），麦克风只以 8kHz 出新样本，而 ESP32 仍按 16kHz 采样，
> 于是每帧样本出现两次 —— 日志里能看到 `raw=f833 f833 f827 f827` 这种成对重复。
> **正确配置是 32bit 槽宽（BCLK=1.024MHz），此时 dup 应 < 0.3。**
>
> 实测参考（同一块模块）：
> ```
> [SCAN] 32bit/left   BCLK=1024kHz cls=正常 rms=91  dup≈0  raw=f989 f955 f975 f97e   ← 正确
> [SCAN] 16bit/left   BCLK=512kHz  cls=正常 rms=184 dup≈1  raw=f833 f833 f827 f827   ← 降速，不能用
> [SCAN] 32bit/right  BCLK=1024kHz cls=静止                                                    ← L/R 接 GND，右槽无数据（正常）
> ```

**这块圆形模块的坑：丝印印在背面。** 你提供的照片里文字是镜像的，说明拍到的是反面，
**左右顺序与正面正好相反**——按位置接线几乎必错，必须按文字接。

判断时用「邻接关系」（镜像不会改变谁在中间）：

| 位置 | 引脚 |
|---|---|
| 一排三个焊盘的**中间** | `VDD` |
| 同一排的**两个端头** | `GND` 与 `SD` |
| 另一排三个焊盘的**中间** | `WS` |
| 同一排的两个端头 | `L/R` 与 `SCK` |

也就是说：**`VDD` 一定挨着 `GND`**，`SCK` 在另一排。剩下 `GND` 与 `SD` 谁在左谁在右，
请以**能正常读出文字的那一面**为准确认——接反 `VDD`/`GND` 会烧模块。

> 首次上电建议在 3.3V 上**串一个 100Ω 电阻**：接反时限流，不会立刻烧掉；
> 正常工作电流只有 1.4mA，100Ω 上的压降约 0.14V，不影响工作。

测量一律在**模块自己的焊盘**上做（板子排针上量不出断线）：

| 测点 | 正常值 |
|---|---|
| VDD → GND | 3.30 V |
| SCK → GND | ≈1.6 V（1.024 MHz 方波均值） |
| WS → GND | ≈1.6 V（16 kHz 方波均值） |
| SD → GND | ≈1.5~1.7 V（数据在翻转） |

---

## 2. amp_test（功放）

```bash
cd firmware/test/amp_test
idf.py set-target esp32s3
idf.py -p COM6 build flash monitor
```

自动循环三种帧格式，用**响几声**区分：

| 模式 | 帧格式 | 响声数 |
|---|---|---|
| 1 | 16bit 立体声（与主项目一致） | 响 **1** 声 |
| 2 | 16bit 单声道 | 响 **2** 声 |
| 3 | 32bit 立体声 | 响 **3** 声 |

```
I AMP_TEST: ----- [模式 1/3] 16bit/立体声（与主项目一致）：即将响 1 声（BCLK≈512kHz, LRC=16kHz）-----
I AMP_TEST:   ♪ 第 1 声
```

**判读**

| 现象 | 结论 |
|---|---|
| 响 1 声 | 主项目的 16bit 立体声配置可用，问题不在功放 |
| 响 2 或 3 声 | 改主项目 `audio_i2s.c` 的 TX 帧格式即可 |
| 完全没声 | 依次量：`VIN`（建议 5V）、`SD_MODE`（3.3V 才使能）、`BCLK`/`LRC` 是否有时钟、喇叭是否接好 |

> MAX98357A 的 `SD_MODE` 接 GND 会进入 shutdown，芯片完全不工作。
> `GAIN` 接 GND = 12dB；接 VDD = 15dB。
> 若 `BCLK`/`LRC` 在模块焊盘上量不到电平（应≈1.6V），说明线没通，不是芯片问题。

---

## 3. 与主项目的关系

- 引脚常量在各自 `main/*.c` 顶部，与主项目 `main/Kconfig.projbuild` 的默认值一致；
  改了接线只需改这几个宏（主项目则在 `menuconfig` 里改）。
- 这两个工程是**独立的 IDF 工程**，必须在自己的目录里执行 `idf.py`，
  构建产物不会污染主项目的 `firmware/build/`。
- 结论确认后回到主项目：

```bash
cd firmware
idf.py build && idf.py -p COM6 flash monitor
```
