"""LLM 精简回复：GLM / Qwen，走 OpenAI 兼容接口。

两个常见失败点，都在这里提前变成可读的错误：
1. `api_key` 为空 —— 不再等 HTTP 401，直接给出"去界面/配置里填"的提示；
2. 网络不通 / key 无效 / 额度用完 —— 按状态码给出不同说明。
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """LLM 不可用，消息可直接展示给用户。"""


class LLMClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self._client = None

    def reset(self) -> None:
        """配置变更后重建客户端（GUI 保存新 key 时调用）。"""
        self._client = None

    # ---------- 内部 ----------

    def _check_key(self) -> str:
        key = (getattr(self.cfg, "api_key", "") or "").strip()
        if not key:
            raise LLMError(
                "未配置大模型 api_key。请在界面「大模型」面板填写，"
                "或编辑 host/config.json → llm.api_key（GLM: open.bigmodel.cn，Qwen: 阿里云百炼）")
        return key

    @property
    def client(self):
        key = self._check_key()
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(
                base_url=self.cfg.base_url,
                api_key=key,
                timeout=float(getattr(self.cfg, "timeout", 30.0) or 30.0),
                max_retries=1,
            )
        return self._client

    @staticmethod
    def _explain(exc: Exception) -> str:
        status = getattr(exc, "status_code", None)
        if status == 401:
            return "api_key 无效或已过期（401），请重新填写"
        if status == 403:
            return "没有该模型的访问权限（403），请确认 model 名称与服务商一致"
        if status == 429:
            return "请求过于频繁或额度已用完（429）"
        if status and status >= 500:
            return f"服务端错误（{status}），稍后重试"
        text = str(exc)
        low = text.lower()
        if "timeout" in low or "timed out" in low:
            return "连接超时：检查网络/代理，或把 llm.base_url 换成可直连的端点"
        if "connect" in low or "10060" in text or "getaddrinfo" in low:
            return "无法连接大模型服务：检查网络、DNS 或代理设置"
        return text

    # ---------- 对外 ----------

    def reply(self, text: str) -> str:
        try:
            resp = self.client.chat.completions.create(
                model=self.cfg.model,
                messages=[
                    {"role": "system", "content": self.cfg.system_prompt},
                    {"role": "user", "content": text},
                ],
                max_tokens=int(self.cfg.max_tokens),
                temperature=float(self.cfg.temperature),
            )
        except LLMError:
            raise
        except Exception as exc:      # noqa: BLE001 - 统一转成可读错误
            raise LLMError(self._explain(exc)) from exc

        reply = (resp.choices[0].message.content or "").strip()
        log.info("llm reply: %s", reply)
        return reply

    def preload(self) -> str | None:
        """启动自检：只校验 key 是否存在（不发请求，避免启动变慢/产生费用）。"""
        try:
            self._check_key()
            return None
        except Exception as exc:      # noqa: BLE001
            return str(exc)
