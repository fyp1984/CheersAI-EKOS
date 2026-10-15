"""撤回 webhook 下游通知。

设计原则：
- 通知是「尽力而为」的异步语义，不阻塞主 API 响应；
- 目标地址从 configs/consent.yaml 读（不硬编码 host:port，遵守 TR-7）；
- 传输层默认 httpx（已在 pyproject 依赖中），测试时注入 FakeNotifier。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import httpx

from packages.common.log import get_logger

from .types import RevocationNotice

logger = get_logger("ekos.consent.webhook")


class RevocationNotifier(Protocol):
    """下游撤回通知的契约（测试用 fake、生产用 httpx 都能注入）。"""

    def send(self, notice: RevocationNotice) -> tuple[bool, str]: ...


@dataclass
class HttpNotifier:
    """通过 HTTP POST 发出撤回通知的默认实现。

    注意 TR-7（零硬编码端口）：``targets`` 全部由 config 注入，
    不在代码中出现任何 ``host:port`` 字面量。
    """

    targets: list[str] = field(default_factory=list)
    timeout_seconds: float = 3.0

    def send(self, notice: RevocationNotice) -> tuple[bool, str]:
        payload = {
            "notice_id": notice.notice_id,
            "consent_id": notice.consent_id,
            "actor_id": notice.actor_id,
            "revoked_at": notice.revoked_at,
            "scope_ids": notice.scope_ids,
            "trace_id": notice.trace_id,
        }
        ok_count = 0
        last_msg = "no_targets"
        for url in self.targets:
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    resp = client.post(url, json=payload)
                    if 200 <= resp.status_code < 300:
                        ok_count += 1
                        last_msg = f"ok:{url}"
                        logger.info(
                            "revocation_webhook_ok target=%s notice_id=%s",
                            url,
                            notice.notice_id,
                        )
                    else:
                        last_msg = f"fail:{url}:http_{resp.status_code}"
                        logger.warning(
                            "revocation_webhook_bad_status target=%s status=%s notice_id=%s",
                            url,
                            resp.status_code,
                            notice.notice_id,
                        )
            except httpx.HTTPError as exc:
                last_msg = f"fail:{url}:{type(exc).__name__}"
                logger.warning(
                    "revocation_webhook_error target=%s error=%s notice_id=%s",
                    url,
                    type(exc).__name__,
                    notice.notice_id,
                )
        success = bool(self.targets) and ok_count == len(self.targets)
        return success, last_msg


@dataclass
class LogOnlyNotifier:
    """纯日志实现，便于 E0/本地开发不配下游时也能验证调用路径。"""

    def send(self, notice: RevocationNotice) -> tuple[bool, str]:
        logger.info(
            "revocation_log_notice notice_id=%s consent_id=%s actor_id=%s scopes=%s",
            notice.notice_id,
            notice.consent_id,
            notice.actor_id,
            ",".join(notice.scope_ids),
        )
        return True, "logged"


__all__ = ["HttpNotifier", "LogOnlyNotifier", "RevocationNotifier"]
