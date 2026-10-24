#pragma once

/**
 * 固件版本：**唯一**定义处。
 *
 * 历史上 boot 日志写 0.4.0、hello/status.get 写 0.2.0，上位机设备页显示的
 * 固件版本与实际固件对不上。凡是要输出固件版本的地方（启动日志、hello、
 * status.get），一律引用这里，不要再写字面量。
 */
#define VW_FW_VERSION "0.5.0"
