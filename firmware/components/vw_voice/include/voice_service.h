#pragma once
#include "esp_err.h"

/**
 * 业务层：语音会话服务。
 *
 * 职责：
 *  - 会话状态机（APP_IDLE 待唤醒 / APP_ACK_WAIT 等回复词 / APP_CAPTURE 采集上行 /
 *    APP_PLAYBACK 播放下行），见 voice_service.c 的 app_state_t
 *  - VAD 端点检测（AFE VAD + 帧能量兜底；语音后静音判定 + 最大时长 + 唤醒后无人说话超时）
 *  - 音量软件缩放、播报中打断（barge-in，依赖 AEC）
 *  - 控制命令处理（config / volume / wifi / host / wakeword / status / reboot）
 *
 * 依赖：driver/audio_i2s、service/wakeword、service/config_store、comm/net_client。
 * 由 app_main 在完成驱动/配置初始化后调用。
 */
esp_err_t voice_service_init(void);
