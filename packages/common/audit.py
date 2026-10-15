"""全链路审计日志（T-D2 准出⑦）。

事件语义接口（不用 callers 记字段名、不会漏三要素）：
    audit.record_retrieve(actor_id, query, hits_cnt, trace_id)
    audit.record_mask(actor_id, asset_id, policy_id, trace_id)
    audit.record_unmask(actor_id, asset_id, reason, trace_id)
    audit.record_login(actor_id, method, result, trace_id)
    audit.record_share(actor_id, resource_ref, audience, trace_id)
    # 自定义兜底
    audit.record_raw(action, actor_id, extra, trace_id)

每条记录恒含：timestamp / actor / action / trace_id
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class AuditEvent:
    event_id: str
    timestamp: str  # ISO8601 with ms
    actor_id: str
    action: str
    trace_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AuditLogger:
    """T-D2 核心：人 / 时间 / 动作 三要素恒有，一条不漏。"""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("ekos.audit")
        if not self._logger.handlers:
            h = logging.StreamHandler()
            fmt = logging.Formatter("AUDIT %(asctime)s %(levelname)s %(name)s %(message)s")
            h.setFormatter(fmt)
            self._logger.addHandler(h)
        self._logger.setLevel(logging.INFO)
        self._events: list[AuditEvent] = []  # 内存缓冲，便于测试断言覆盖率

    # --------------------------------------------------------- 基础设施
    @property
    def events(self) -> list[AuditEvent]:
        return list(self._events)

    def clear(self) -> None:
        self._events.clear()

    def record_raw(
        self,
        action: str,
        actor_id: str,
        *,
        trace_id: str | None = None,
        **extra: Any,
    ) -> AuditEvent:
        ev = AuditEvent(
            event_id=uuid.uuid4().hex,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S.")
            + f"{int(time.time() * 1000) % 1000:03d}"
            + time.strftime("%z", time.localtime()),
            actor_id=actor_id,
            action=action,
            trace_id=trace_id,
            extra=extra,
        )
        self._events.append(ev)
        self._logger.info(json.dumps(ev.to_dict(), ensure_ascii=False, sort_keys=True))
        return ev

    # --------------------------------------------------------- 事件语义接口
    def record_retrieve(
        self,
        actor_id: str,
        *,
        query: str,
        hits_count: int,
        refused: bool,
        trace_id: str | None = None,
    ) -> AuditEvent:
        return self.record_raw(
            "retrieval.query",
            actor_id,
            trace_id=trace_id,
            query=query,
            hits_count=hits_count,
            refused=refused,
        )

    def record_mask(
        self,
        actor_id: str,
        *,
        asset_id: str,
        policy_id: str,
        masked_fields: int,
        trace_id: str | None = None,
    ) -> AuditEvent:
        return self.record_raw(
            "vault.mask",
            actor_id,
            trace_id=trace_id,
            asset_id=asset_id,
            policy_id=policy_id,
            masked_fields=masked_fields,
        )

    def record_unmask(
        self,
        actor_id: str,
        *,
        asset_id: str,
        reason: str,
        approved: bool,
        trace_id: str | None = None,
    ) -> AuditEvent:
        return self.record_raw(
            "vault.unmask",
            actor_id,
            trace_id=trace_id,
            asset_id=asset_id,
            reason=reason,
            approved=approved,
        )

    def record_login(
        self,
        actor_id: str,
        *,
        method: str,
        success: bool,
        trace_id: str | None = None,
    ) -> AuditEvent:
        return self.record_raw(
            "identity.login",
            actor_id,
            trace_id=trace_id,
            method=method,
            success=success,
        )

    def record_share(
        self,
        actor_id: str,
        *,
        resource_ref: str,
        audience: str,
        share_mode: str,
        trace_id: str | None = None,
    ) -> AuditEvent:
        return self.record_raw(
            "resource.share",
            actor_id,
            trace_id=trace_id,
            resource_ref=resource_ref,
            audience=audience,
            share_mode=share_mode,
        )

    def record_permission_deny(
        self,
        actor_id: str,
        *,
        action: str,
        resource_ref: str,
        reason: str | None = None,
        trace_id: str | None = None,
    ) -> AuditEvent:
        return self.record_raw(
            "permission.deny",
            actor_id,
            trace_id=trace_id,
            attempted_action=action,
            resource_ref=resource_ref,
            reason=reason or "not allowed",
        )


_GLOBAL_AUDIT: AuditLogger | None = None


def get_audit() -> AuditLogger:
    global _GLOBAL_AUDIT
    if _GLOBAL_AUDIT is None:
        _GLOBAL_AUDIT = AuditLogger()
    return _GLOBAL_AUDIT


__all__ = ["AuditEvent", "AuditLogger", "get_audit"]
