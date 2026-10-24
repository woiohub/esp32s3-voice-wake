#include "net_client.h"
#include "net_proto.h"
#include "wifi_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "lwip/sockets.h"
#include "lwip/netdb.h"
#include "esp_log.h"
#include "esp_timer.h"
#include <string.h>
#include <errno.h>
#include <fcntl.h>

#define TAG "comm:net"

#define CONNECT_TIMEOUT_S 3                             // 单次连接超时
#define HB_PERIOD_S       CONFIG_VW_HEARTBEAT_PERIOD_S  // 心跳周期（menuconfig 可调）
#define HB_TIMEOUT_S      CONFIG_VW_HEARTBEAT_TIMEOUT_S // 无下行数据判定断线

typedef struct {
    char host[16];
    uint16_t port;
    int sock;
    volatile bool connected;
    SemaphoreHandle_t send_lock;
    vw_parser_t parser;
    vw_ctrl_handler_t on_ctrl;
    vw_audio_down_handler_t on_audio;
    vw_conn_cb_t on_conn;
    TaskHandle_t recv_task;
    int64_t last_recv_ms;
    int64_t last_tx_ms;         // 最近一次成功发送（心跳/音频/控制）的时间
    int64_t last_fail_log_ms;   // 连接失败日志限频（避免每 3s 刷屏）
    uint32_t fail_cnt;          // 连续连接失败次数
    uint32_t seq;
} net_ctx_t;

static net_ctx_t s_net = {
    .sock = -1,
    .send_lock = NULL,
    .connected = false,
};

static int64_t now_ms(void)
{
    return esp_timer_get_time() / 1000;
}

/* ---------- 帧解析分发 ---------- */

static void on_frame(uint8_t type, uint32_t seq, uint32_t ts_ms,
                     const uint8_t *payload, uint32_t len, void *arg)
{
    (void)arg;
    s_net.last_recv_ms = now_ms();

    if (type == VW_TYPE_CONTROL) {
        if (s_net.on_ctrl) {
            s_net.on_ctrl((const char *)payload, (int)len);
        }
    } else if (type == VW_TYPE_AUDIO_DOWN) {
        // 音频子帧：session_id(4B BE) + flags(1B) + PCM
        if (len > 5 && s_net.on_audio) {
            uint32_t sid = ((uint32_t)payload[0] << 24) | ((uint32_t)payload[1] << 16) |
                           ((uint32_t)payload[2] << 8) | payload[3];
            s_net.on_audio(sid, payload[4], (const int16_t *)(payload + 5), (int)((len - 5) / 2));
        }
    }
    // HEARTBEAT / ACK 由业务层处理（ACK 也走 CONTROL JSON）
}

/* ---------- 发送 ---------- */

static esp_err_t send_all(const uint8_t *buf, size_t len)
{
    size_t off = 0;
    while (off < len) {
        int n = send(s_net.sock, buf + off, len - off, 0);
        if (n <= 0) {
            return ESP_FAIL;
        }
        off += n;
    }
    return ESP_OK;
}

static esp_err_t send_frame(uint8_t type, const uint8_t *payload, uint32_t payload_len)
{
    if (!s_net.connected) {
        return ESP_ERR_INVALID_STATE;
    }
    uint8_t buf[1200];
    if (VW_HDR_LEN + payload_len + VW_CRC_LEN > sizeof(buf)) {
        return ESP_ERR_NO_MEM;
    }
    size_t total = vw_build_frame(buf, sizeof(buf), type, s_net.seq++,
                                  (uint32_t)now_ms(), payload, payload_len);
    if (total == 0) {
        return ESP_ERR_NO_MEM;
    }

    esp_err_t err;
    xSemaphoreTake(s_net.send_lock, portMAX_DELAY);
    err = send_all(buf, total);
    xSemaphoreGive(s_net.send_lock);
    if (err != ESP_OK) {
        s_net.connected = false;  // 触发主任务重连
    } else {
        s_net.last_tx_ms = now_ms();  // 发送成功即视为链路活跃（用于存活判定）
    }
    return err;
}

esp_err_t net_client_send_control(const char *json)
{
    return send_frame(VW_TYPE_CONTROL, (const uint8_t *)json, (uint32_t)strlen(json));
}

esp_err_t net_client_send_audio(uint32_t session_id, uint8_t flags,
                                const int16_t *pcm, int samples)
{
    uint8_t payload[5 + 640 * 2];
    int pcm_bytes = samples * (int)sizeof(int16_t);
    if (pcm_bytes > (int)sizeof(payload) - 5) {
        return ESP_ERR_INVALID_SIZE;
    }
    payload[0] = (uint8_t)(session_id >> 24);
    payload[1] = (uint8_t)(session_id >> 16);
    payload[2] = (uint8_t)(session_id >> 8);
    payload[3] = (uint8_t)session_id;
    payload[4] = flags;
    if (pcm) {
        memcpy(payload + 5, pcm, pcm_bytes);
    } else {
        pcm_bytes = 0;  // 空载荷帧（如只带 END 标志）
    }
    return send_frame(VW_TYPE_AUDIO_UP, payload, 5 + pcm_bytes);
}

bool net_client_connected(void)
{
    return s_net.connected;
}

/* ---------- 接收 ---------- */

static void recv_task(void *arg)
{
    (void)arg;
    uint8_t buf[512];
    while (s_net.connected) {
        int n = recv(s_net.sock, buf, sizeof(buf), 0);
        if (n > 0) {
            s_net.last_recv_ms = now_ms();
            vw_parser_feed(&s_net.parser, buf, n);
            continue;
        }
        if (n < 0) {
            /* SO_RCVTIMEO=2s：没数据时 recv 会返回 -1/EAGAIN，这是“暂时没收到”，
             * 不是断线。早期实现把 n<=0 一律当断开，导致空闲链路每 2s 断开一次、
             * 无限重连（日志表现为 connected/recv closed (-1) 循环）。
             * 真正的链路死亡由 client_task 的心跳超时判定。 */
            if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) {
                continue;
            }
            ESP_LOGW(TAG, "recv error (%d, %s)", errno, strerror(errno));
            break;
        }
        ESP_LOGW(TAG, "peer closed the connection");
        break;
    }
    s_net.connected = false;
    s_net.recv_task = NULL;
    vTaskDelete(NULL);
}

/* ---------- 连接与监控 ---------- */

static int connect_host(void)
{
    int sock = socket(AF_INET, SOCK_STREAM, 0);
    if (sock < 0) {
        return -1;
    }

    // 非阻塞连接 + select 超时
    int flags = fcntl(sock, F_GETFL, 0);
    fcntl(sock, F_SETFL, flags | O_NONBLOCK);

    struct sockaddr_in addr = {0};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(s_net.port);
    addr.sin_addr.s_addr = inet_addr(s_net.host);

    int rc = connect(sock, (struct sockaddr *)&addr, sizeof(addr));
    if (rc < 0 && errno != EINPROGRESS) {
        close(sock);
        return -1;
    }
    if (rc < 0) {
        fd_set wfds;
        FD_ZERO(&wfds);
        FD_SET(sock, &wfds);
        struct timeval tv = { .tv_sec = CONNECT_TIMEOUT_S, .tv_usec = 0 };
        if (select(sock + 1, NULL, &wfds, NULL, &tv) <= 0) {
            close(sock);
            return -1;
        }
        int err = 0;
        socklen_t len = sizeof(err);
        getsockopt(sock, SOL_SOCKET, SO_ERROR, &err, &len);
        if (err != 0) {
            close(sock);
            return -1;
        }
    }

    // 恢复阻塞模式并设置收发超时
    fcntl(sock, F_SETFL, flags);
    struct timeval tv = { .tv_sec = 2, .tv_usec = 0 };
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
    return sock;
}

static void client_task(void *arg)
{
    (void)arg;
    for (;;) {
        if (!wifi_client_sta_connected()) {
            vTaskDelay(pdMS_TO_TICKS(1000));
            continue;
        }

        int sock = connect_host();
        if (sock < 0) {
            /* 失败日志限频：首次 + 之后每 30s 一条，否则每 3s 重试会刷屏。
             * errno 可区分：ECONNREFUSED=上位机未启动/端口没监听；
             * EHOSTUNREACH/ETIMEDOUT=IP 不可达或防火墙拦截。 */
            int e = errno;
            s_net.fail_cnt++;
            if (s_net.fail_cnt == 1 || (now_ms() - s_net.last_fail_log_ms) > 30000) {
                s_net.last_fail_log_ms = now_ms();
                ESP_LOGW(TAG, "连接 %s:%d 失败(%s, errno=%d)，每 3s 重试，已失败 %u 次",
                         s_net.host, s_net.port, strerror(e), e, (unsigned)s_net.fail_cnt);
            }
            vTaskDelay(pdMS_TO_TICKS(3000));
            continue;
        }

        s_net.fail_cnt = 0;
        s_net.sock = sock;
        s_net.last_recv_ms = now_ms();
        s_net.last_tx_ms = now_ms();
        s_net.connected = true;
        ESP_LOGI(TAG, "connected to %s:%d", s_net.host, s_net.port);
        if (s_net.on_conn) {
            s_net.on_conn(true);
        }

        xTaskCreate(recv_task, "net_recv", 4096, NULL, 12, &s_net.recv_task);

        // 监控循环：周期心跳 + 存活判定
        int idle = 0;
        while (s_net.connected) {
            vTaskDelay(pdMS_TO_TICKS(1000));
            if (++idle >= HB_PERIOD_S) {
                idle = 0;
                if (send_frame(VW_TYPE_HEARTBEAT, NULL, 0) != ESP_OK) {
                    break;
                }
            }
            /* 存活判定看“最近一次收发”，而不是只看收：
             * 上位机在空闲时不一定发数据，仅靠 recv 会误判为超时。 */
            int64_t last_active = (s_net.last_recv_ms > s_net.last_tx_ms)
                                  ? s_net.last_recv_ms : s_net.last_tx_ms;
            if (now_ms() - last_active > (int64_t)HB_TIMEOUT_S * 1000) {
                ESP_LOGW(TAG, "链路超时（%d 秒无收发），断开重连", HB_TIMEOUT_S);
                break;
            }
        }

        s_net.connected = false;
        shutdown(s_net.sock, SHUT_RDWR);  // 唤醒阻塞的 recv
        if (s_net.recv_task) {
            vTaskDelay(pdMS_TO_TICKS(200));
        }
        close(s_net.sock);
        s_net.sock = -1;
        if (s_net.on_conn) {
            s_net.on_conn(false);
        }
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}

void net_client_set_handlers(vw_ctrl_handler_t ctrl, vw_audio_down_handler_t audio,
                             vw_conn_cb_t conn)
{
    s_net.on_ctrl = ctrl;
    s_net.on_audio = audio;
    s_net.on_conn = conn;
}

esp_err_t net_client_start(const char *host, uint16_t port)
{
    strlcpy(s_net.host, host, sizeof(s_net.host));
    s_net.port = port;
    s_net.send_lock = xSemaphoreCreateMutex();
    vw_parser_init(&s_net.parser, on_frame, NULL);
    ESP_LOGI(TAG, "TCP 目标 %s:%d（WiFi 就绪后自动连接，失败每 3s 重试）",
             s_net.host, s_net.port);

    if (xTaskCreate(client_task, "net_client", 4096, NULL, 10, NULL) != pdPASS) {
        return ESP_FAIL;
    }
    return ESP_OK;
}
