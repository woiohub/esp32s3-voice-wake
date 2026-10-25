"""语音会话管线：上行音频 → STT → LLM → TTS → 下发下行音频。

同时负责：
- 下行控制帧解析（hello / session.* / 对 config.set 的 ACK 应答）
- 主动向设备下发控制命令（供 GUI 调用），并跟踪 ACK
- STT/LLM/TTS 阻塞计算放线程池，避免卡住 asyncio 事件循环
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Callable, Optional

import numpy as np

from . import protocol

log = logging.getLogger(__name__)


class Session:
    """一次语音会话的上行音频缓冲。"""

    def __init__(self, session_id: int):
        self.session_id = session_id
        self.chunks: list[bytes] = []
        self.closed = False

    def add(self, pcm: bytes) -> None:
        self.chunks.append(pcm)

    @property
    def pcm(self) -> bytes:
        return b"".join(self.chunks)


class Pipeline:
    def __init__(self, cfg, stt, llm, tts):
        self.cfg = cfg
        self.stt = stt
        self.llm = llm
        self.tts = tts
        self.sessions: dict[int, Session] = {}
        self.device_info: dict = {}

        # GUI 回调（均可为 None）
        self.on_status: Optional[Callable[[str], None]] = None      # 日志/状态
        self.on_ack: Optional[Callable[[str, int, object], None]] = None  # (cmd, code, result)
        self.on_device: Optional[Callable[[dict], None]] = None     # 设备 hello 信息

        # 结构化事件（供 GUI 阶段条 / 气泡 / 电平表使用，最小侵入，不影响原逻辑）
        self.on_stage: Optional[Callable[[str, str, float], None]] = None   # (stage, state, ms)
        self.on_transcript: Optional[Callable[[str, float], None]] = None   # (text, conf)
        self.on_reply: Optional[Callable[[str], None]] = None              # (text)
        self.on_tts: Optional[Callable[[str], None]] = None                # "start"/"done"
        self.on_interrupt: Optional[Callable[[], None]] = None
        self.on_level: Optional[Callable[[float], None]] = None            # 0..1
        self.on_error: Optional[Callable[[int, str], None]] = None         # (code, msg)

        self._pending: dict[str, str] = {}  # req_id -> cmd
        self._level_tick = 0

    # ---------- 事件辅助 ----------

    def _stage(self, stage: str, state: str, ms: float = 0.0) -> None:
        if self.on_stage:
            self.on_stage(stage, state, ms)

    def _error(self, code: int, msg: str) -> None:
        if self.on_error:
            self.on_error(code, msg)

    # ---------- 回调注册辅助 ----------

    def _notify(self, text: str) -> None:
        log.info(text)
        if self.on_status:
            self.on_status(text)

    # ---------- 网络帧入口 ----------

    def on_frame(self, type_, seq, ts_ms, payload, conn):
        if type_ == protocol.TYPE_AUDIO_UP:
            self._on_audio_up(payload, conn)
        elif type_ in (protocol.TYPE_CONTROL, protocol.TYPE_ACK):
            self._on_control(payload, conn)
        elif type_ == protocol.TYPE_HEARTBEAT:
            # 回一帧心跳：让下位机确认链路双向可用，并刷新其“最近接收”时间。
            # 不回的话，下位机空闲时只有发没有收，会误判链路超时并重连。
            self._reply_heartbeat(conn)

    def _reply_heartbeat(self, conn) -> None:
        try:
            asyncio.get_running_loop().create_task(
                conn.send(protocol.TYPE_HEARTBEAT, b""))
        except (RuntimeError, AttributeError):
            log.debug("heartbeat reply skipped (no running loop)")

    def _on_control(self, payload: bytes, conn) -> None:
        try:
            msg = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            log.warning("bad control json: %r", payload[:64])
            return

        cmd = msg.get("cmd", "")
        if cmd == "hello":
            self.device_info = msg.get("params", {})
            first = not getattr(self, "_hello_seen", False)
            self._hello_seen = True
            if first:
                self._notify(f"设备上线: {json.dumps(self.device_info, ensure_ascii=False)}")
            else:
                # 参数变更后设备会补发一次 hello（见固件 send_hello），
                # 日志里和"上线"区分开，避免每次调音量都误报"设备上线"。
                self._notify("设备信息已同步：volume=%s wake=%s ack=%s" % (
                    self.device_info.get("volume", "?"),
                    self.device_info.get("wake_enable", "?"),
                    self.device_info.get("ack_enable", "?")))
            if self.on_device:
                self.on_device(self.device_info)
        elif cmd in ("session.start", "session.end"):
            params = msg.get("params", {}) or {}
            if cmd == "session.start":
                # 唤醒命中
                self._stage("wake", "done")
                if params.get("need_ack"):
                    # 设备在等"回复词"：立刻合成并下发，播完它才开始采集
                    asyncio.create_task(
                        self._send_ack(conn, int(params.get("session_id", 0))))
                else:
                    self._stage("capture", "active")
            else:
                self._stage("capture", "done")
                if params.get("reason") == "interrupt" and self.on_interrupt:
                    self.on_interrupt()
            self._notify(f"{cmd} {params}")
        elif cmd == "interrupt":
            self._stage("play", "skipped")
            if self.on_interrupt:
                self.on_interrupt()
            self._notify("设备报告打断")
        elif "id" in msg and "code" in msg:
            # 设备对控制命令的应答
            req_id = msg["id"]
            code = msg.get("code", -1)
            result = msg.get("result", msg.get("error"))
            expected = self._pending.pop(req_id, None)
            self._notify(f"ACK[{expected or cmd}] code={code} {result}")
            if self.on_ack:
                self.on_ack(cmd, code, result)
        else:
            log.info("control: %s", msg)

    # ---------- 上行语音 ----------

    def _on_audio_up(self, payload: bytes, conn) -> None:
        session_id, flags, pcm = protocol.unpack_audio(payload)
        if session_id not in self.sessions:
            self.sessions[session_id] = Session(session_id)
        sess = self.sessions[session_id]
        sess.add(pcm)

        # 上行电平（供 UI 电平表）：每 2 帧算一次，约 25fps，开销可忽略
        if self.on_level and pcm:
            self._level_tick ^= 1
            if self._level_tick == 0:
                arr = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2")
                if arr.size:
                    ac = arr.astype(np.float32) - float(arr.mean())
                    rms = float(np.sqrt(np.mean(ac * ac)))
                    self.on_level(min(1.0, (rms / 6000.0) ** 0.5))

        if flags & protocol.AUDIO_FLAG_END:
            sess.closed = True
            asyncio.create_task(self._process(sess, conn))

    async def _process(self, sess: Session, conn) -> None:
        # 记录当前步骤：出错时能直接告诉用户卡在 STT / LLM / TTS 哪一环，
        # 而不是只给一句"处理失败（见日志）"。
        step = "音频校验"
        cur = "stt"
        try:
            loop = asyncio.get_running_loop()
            pcm = sess.pcm
            if len(pcm) < self.cfg.audio.sample_rate:  # <1s 视为无效输入
                self._notify("语音过短，忽略")
                self._stage("stt", "skipped")
                return

            step, cur = "STT（语音识别）", "stt"
            self._stage("stt", "active")
            t0 = time.perf_counter()
            text = await loop.run_in_executor(None, self.stt.transcribe, pcm)
            t_stt = (time.perf_counter() - t0) * 1000.0
            self._stage("stt", "done", t_stt)
            if not text:
                self._notify("STT 未识别到内容")
                self._stage("llm", "skipped")
                self._stage("tts", "skipped")
                self._stage("play", "skipped")
                return
            if self.on_transcript:
                self.on_transcript(text, 0.0)

            step, cur = "LLM（大模型）", "llm"
            self._stage("llm", "active")
            t0 = time.perf_counter()
            reply = await loop.run_in_executor(None, self.llm.reply, text)
            self._stage("llm", "done", (time.perf_counter() - t0) * 1000.0)
            self._notify(f"用户: {text} → 回复: {reply}")
            if self.on_reply:
                self.on_reply(reply)

            step, cur = "TTS（语音合成）", "tts"
            tts_pcm, _ = await self._synth(reply)
            if not tts_pcm:
                self._notify("TTS 返回空音频，跳过下发")
                self._stage("play", "skipped")
                return

            step, cur = "下行音频发送", "play"
            self._stage("play", "active")
            if self.on_tts:
                self.on_tts("start")
            t0 = time.perf_counter()
            await self._send_audio_frames(conn, sess.session_id, tts_pcm)
            self._stage("play", "done", (time.perf_counter() - t0) * 1000.0)
            if self.on_tts:
                self.on_tts("done")
            self._notify(f"已下发回复音频 {len(tts_pcm) / 2 / self.cfg.audio.sample_rate:.1f}s")
        except Exception as exc:
            log.exception("session %d process failed", sess.session_id)
            detail = str(exc).strip().splitlines()
            brief = detail[0] if detail else type(exc).__name__
            self._stage(cur, "failed")
            self._notify(f"{step} 失败：{brief}")
            self._error(0, f"{step} 失败：{brief}")
        finally:
            self.sessions.pop(sess.session_id, None)

    async def _send_ack(self, conn, session_id: int) -> None:
        """唤醒后立即下发"回复词"（如"在的"）。

        设备处于 APP_ACK_WAIT：收到这串音频就播放，**播完才切到采集**，
        这样回复词不会被自己的麦克风录进去（AEC 只能压掉一部分）。
        未启用/未配置回复词时，发一个空的 END 帧让设备立刻进入采集。
        """
        text = (getattr(self.cfg.device, "ack_text", "") or "").strip()
        enabled = bool(getattr(self.cfg.device, "ack_enable", True))

        async def _skip(reason: str) -> None:
            log.info("ack skipped: %s", reason)
            try:
                await conn.send_audio(session_id, protocol.AUDIO_FLAG_END, b"")
            except Exception:       # noqa: BLE001 - 连接可能已断，设备有超时兜底
                pass
            self._stage("capture", "active")

        if not enabled or not text:
            await _skip("未启用或回复词为空")
            return

        try:
            loop = asyncio.get_running_loop()
            t0 = time.perf_counter()
            pcm = await loop.run_in_executor(None, self.tts.synthesize, text)
            ms = (time.perf_counter() - t0) * 1000.0
            if not pcm:
                await _skip("TTS 返回空音频")
                return

            self._notify("唤醒回复词：%s（合成 %.0fms，%.1fs 音频）"
                         % (text, ms, len(pcm) / 2 / self.cfg.audio.sample_rate))
            await self._send_audio_frames(conn, session_id, pcm)
            # 下发按实时节奏完成，此时播放即将结束 → 阶段条切到"录音"
            self._stage("capture", "active")
        except Exception as exc:      # noqa: BLE001
            log.exception("ack phrase failed")
            self._notify(f"唤醒回复词失败：{exc}")
            await _skip("合成/下发异常")

    async def _synth(self, text: str):
        """跑一次 TTS，带阶段与耗时。"""
        loop = asyncio.get_running_loop()
        self._stage("tts", "active")
        t0 = time.perf_counter()
        pcm = await loop.run_in_executor(None, self.tts.synthesize, text)
        ms = (time.perf_counter() - t0) * 1000.0
        self._stage("tts", "done" if pcm else "failed", ms if pcm else 0.0)
        return pcm, ms

    # ---------- 文本入口（GUI 调试输入 / 重新生成 / 重新播报） ----------

    def _next_text_sid(self) -> int:
        self._text_sid = getattr(self, "_text_sid", 1000) + 1
        return self._text_sid

    async def ask_text(self, text: str, conn) -> None:
        """跳过唤醒/录音/STT，直接 LLM → TTS → 下发。"""
        cur, step = "llm", "LLM（大模型）"
        try:
            self._stage("wake", "skipped")
            self._stage("capture", "skipped")
            self._stage("stt", "skipped")

            loop = asyncio.get_running_loop()
            self._stage("llm", "active")
            t0 = time.perf_counter()
            reply = await loop.run_in_executor(None, self.llm.reply, text)
            self._stage("llm", "done", (time.perf_counter() - t0) * 1000.0)
            if self.on_reply:
                self.on_reply(reply)

            cur, step = "tts", "TTS（语音合成）"
            pcm, _ = await self._synth(reply)
            if not pcm:
                self._stage("play", "skipped")
                return

            cur, step = "play", "下行音频发送"
            self._stage("play", "active")
            if self.on_tts:
                self.on_tts("start")
            t0 = time.perf_counter()
            await self._send_audio_frames(conn, self._next_text_sid(), pcm)
            self._stage("play", "done", (time.perf_counter() - t0) * 1000.0)
            if self.on_tts:
                self.on_tts("done")
        except Exception as exc:      # noqa: BLE001
            log.exception("ask_text failed")
            brief = (str(exc).strip().splitlines() or [type(exc).__name__])[0]
            self._stage(cur, "failed")
            self._notify(f"{step} 失败：{brief}")
            self._error(0, f"{step} 失败：{brief}")

    async def speak_text(self, text: str, conn) -> None:
        """只重跑 TTS 并下发（重新播报）。"""
        cur, step = "tts", "TTS（语音合成）"
        try:
            pcm, _ = await self._synth(text)
            if not pcm:
                self._stage("play", "skipped")
                return
            cur, step = "play", "下行音频发送"
            self._stage("play", "active")
            if self.on_tts:
                self.on_tts("start")
            await self._send_audio_frames(conn, self._next_text_sid(), pcm)
            self._stage("play", "done")
            if self.on_tts:
                self.on_tts("done")
        except Exception as exc:      # noqa: BLE001
            log.exception("speak_text failed")
            brief = (str(exc).strip().splitlines() or [type(exc).__name__])[0]
            self._stage(cur, "failed")
            self._notify(f"{step} 失败：{brief}")
            self._error(0, f"{step} 失败：{brief}")

    async def _send_audio_frames(self, conn, session_id: int, pcm: bytes) -> None:
        """按 20ms 分帧、**按实时节奏**下发。

        必须限速：下位机播放队列只有 40 帧（≈800ms），若一次性把整段 TTS
        （200+ 帧）推过去，队列满就会被丢弃——表现就是"10 秒的话 1 秒念完"。
        这里让发送进度保持在播放进度前约 150ms：既不会丢帧，也能容忍网络抖动。
        """
        rate = self.cfg.audio.sample_rate
        frame_ms = self.cfg.audio.frame_ms
        frame_bytes = rate * 2 * frame_ms // 1000

        total = len(pcm)
        offset = 0
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        sent_ms = 0.0
        lead_s = 0.15          # 允许领先播放进度 150ms，用于吸收网络抖动

        while offset < total:
            chunk = pcm[offset:offset + frame_bytes]
            flags = 0
            if offset == 0:
                flags |= protocol.AUDIO_FLAG_START
            offset += len(chunk)
            if offset >= total:
                flags |= protocol.AUDIO_FLAG_END

            await conn.send_audio(session_id, flags, chunk)

            sent_ms += len(chunk) / 2.0 / rate * 1000.0
            delay = (t0 + sent_ms / 1000.0 - lead_s) - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)

    # ---------- 主动控制（GUI 调用） ----------

    async def request_device(self, conn, cmd: str, params: dict) -> str:
        req_id = uuid.uuid4().hex[:8]
        self._pending[req_id] = cmd
        await conn.send_json(cmd, params, req_id)
        return req_id

    def submit_device_request(self, conn, cmd: str, params: dict, loop) -> None:
        """线程安全入口：把控制命令投递到 asyncio 循环（GUI 线程调用）。"""
        if loop is None:
            self._notify("asyncio 循环未就绪，命令未发送")
            return
        asyncio.run_coroutine_threadsafe(self.request_device(conn, cmd, params), loop)
