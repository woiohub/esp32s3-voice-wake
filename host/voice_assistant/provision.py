"""蓝牙（BLE）配网：扫描 → 连接 → 写入凭证 → 等待设备回执。

替代原 AP 热点配网。原方案的问题是：设备开热点时上位机连不到它的 TCP，
而设备又要去连另一个网段的上位机，导致「下发 WiFi 配置」这条命令永远发不出去。
BLE 是点对点链路，不依赖 IP，PC 只要蓝牙开着就能配。

GATT 契约（与固件 components/vw_prov/ble_prov.c 一致）：
    Service : a0e0f000-1e4d-4f8a-9b6c-1d2e3f4a5b60
    CFG (W) : a0e0f001-...   {"ssid","password","host_ip","host_port"}
    STATE R/N: a0e0f002-...  {"state":"idle|connecting|ok|fail","error","ip","fw","mac"}

线程模型：本模块全部是**同步阻塞**接口，供 `gui.tasks.run_task` 在线程池里调用；
BLE 的 asyncio 循环在这些函数内部用 `asyncio.run()` 创建，不与 GUI 的事件循环冲突。
"""
from __future__ import annotations

import asyncio
import json
import logging
import socket

log = logging.getLogger(__name__)

SERVICE_UUID = "a0e0f000-1e4d-4f8a-9b6c-1d2e3f4a5b60"
CFG_UUID = "a0e0f001-1e4d-4f8a-9b6c-1d2e3f4a5b60"
STATE_UUID = "a0e0f002-1e4d-4f8a-9b6c-1d2e3f4a5b60"

NAME_PREFIX = "VoiceWake"


class ProvError(RuntimeError):
    """配网失败，消息可直接展示给用户。"""


def local_ip() -> str:
    """本机局域网 IP（不实际发包），用于给"上位机 IP"做默认值。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def available() -> tuple[bool, str]:
    """检查本机能否用蓝牙（依赖 bleak + 系统蓝牙适配器）。"""
    try:
        import bleak  # noqa: F401
    except ImportError:
        return False, "未安装 bleak：pip install bleak"
    if not hasattr(asyncio, "run"):
        return False, "Python 版本过低"
    return True, ""


def scan(timeout: float = 6.0) -> list[dict]:
    """扫描附近的配网设备（名称以 VoiceWake 开头）。返回 [{name,address,rssi}]。"""
    from bleak import BleakScanner

    async def _run() -> list[dict]:
        found = await BleakScanner.discover(timeout=timeout)
        out = []
        for d in found:
            name = d.name or ""
            if name.startswith(NAME_PREFIX):
                out.append({"name": name, "address": d.address,
                            "rssi": getattr(d, "rssi", 0) or 0})
        out.sort(key=lambda x: -x["rssi"])
        return out

    try:
        return asyncio.run(_run())
    except Exception as exc:                     # noqa: BLE001
        raise ProvError(f"蓝牙扫描失败：{exc}") from exc


def provision(address: str, ssid: str, password: str,
              host_ip: str, host_port: int,
              timeout: float = 35.0,
              report=None) -> dict:
    """把凭证写入设备并等待结果。

    report: 进度回调（在**调用线程**执行，只能做线程安全的事，例如往 list 里 append）。
    返回 {"state","error","ip"}；state=="ok" 表示设备已连上 WiFi。
    """
    def emit(msg: str) -> None:
        log.info("prov: %s", msg)
        if report:
            report(msg)

    from bleak import BleakClient

    payload = json.dumps({
        "ssid": ssid,
        "password": password,
        "host_ip": host_ip,
        "host_port": int(host_port),
    }, ensure_ascii=False).encode("utf-8")

    async def _run() -> dict:
        queue: asyncio.Queue = asyncio.Queue()
        state = {"raw": {}}

        def on_state(_char, data: bytearray) -> None:
            try:
                info = json.loads(bytes(data).decode("utf-8", "ignore"))
            except json.JSONDecodeError:
                return
            state["raw"] = info
            queue.put_nowait(info)

        emit(f"正在连接 {address} …")
        async with BleakClient(address, timeout=20.0) as client:
            if not client.is_connected:
                raise ProvError("连接失败（设备是否已离开配网模式？）")

            emit("已连接，读取设备状态…")
            try:
                raw = await client.read_gatt_char(STATE_UUID)
                info = json.loads(bytes(raw).decode("utf-8", "ignore"))
                emit(f"设备 MAC {info.get('mac','?')}  固件 {info.get('fw','?')}")
            except Exception:                    # noqa: BLE001 - 读不到不影响写入
                pass

            await client.start_notify(STATE_UUID, on_state)

            emit("写入 WiFi 凭证与上位机地址…")
            await client.write_gatt_char(CFG_UUID, payload, response=True)

            emit("等待设备连接 WiFi（最长 25s）…")
            deadline = asyncio.get_running_loop().time() + min(timeout, 25.0)
            while True:
                left = deadline - asyncio.get_running_loop().time()
                if left <= 0:
                    return {"state": "fail", "error": "等待设备回执超时", "ip": ""}
                try:
                    info = await asyncio.wait_for(queue.get(), timeout=left)
                except asyncio.TimeoutError:
                    return {"state": "fail", "error": "等待设备回执超时", "ip": ""}

                st = info.get("state", "")
                if st == "connecting":
                    emit("设备正在连接 WiFi…")
                elif st == "ok":
                    emit(f"设备已连上 WiFi，IP={info.get('ip','?')}")
                    return {"state": "ok", "error": "", "ip": info.get("ip", "")}
                elif st == "fail":
                    err = info.get("error", "未知原因")
                    emit(f"设备返回失败：{err}")
                    return {"state": "fail", "error": err, "ip": ""}

    try:
        return asyncio.run(_run())
    except ProvError:
        raise
    except Exception as exc:                     # noqa: BLE001
        msg = str(exc).strip() or exc.__class__.__name__
        raise ProvError(f"蓝牙配网失败：{msg}") from exc
