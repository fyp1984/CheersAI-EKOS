"""知情同意门类型定义。

所有类型使用通用语义（零行业词）：
- actor_id / scope_id / source_id 等标识符保留字符串开放，
  具体含义由实例层配置赋予，内核不硬编码业务实体。
"""

from __future__ import annotations

import time
import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ConsentAction(StrEnum):
    """同意门的原子动作。事件溯源只允许这三种状态转换。"""

    GRANT = "grant"  # 授予：明确同意纳入范围
    REVOKE = "revoke"  # 撤回：一键关闭同步
    EXTEND = "extend"  # 扩展：在已同意基础上追加新范围


class ConsentState(StrEnum):
    """某 scope 投影后的当前状态。"""

    ACTIVE = "active"  # 同步生效中
    REVOKED = "revoked"  # 已撤回，同步停止
    PENDING = "pending"  # 未明确表态（默认不纳入）


class KnowledgeScope(BaseModel):
    """单个被纳入的知识范围。

    MVP 最小版：一条记录 = 一个数据源（connector）下的知识集合。
    字段保持开放：source_type 是连接器注册名，source_ref 是连接器侧的稳定引用。
    """

    scope_id: str
    source_type: str
    source_ref: str
    display_name: str
    description: str = ""
    language: str = "zh"  # P1 语种结构预留，恒填 zh（本期），不写死成枚举
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("language")
    @classmethod
    def _iso_639_1_pattern(cls, v: str) -> str:
        if len(v) < 2 or len(v) > 3:
            raise ValueError("language must follow ISO 639-1 (2-letter) or 639-2 (3-letter)")
        return v


class ConsentScope(BaseModel):
    """一次同意覆盖的一组知识范围 + 当前状态投影。"""

    consent_id: str
    actor_id: str
    state: ConsentState = ConsentState.PENDING
    scopes: list[KnowledgeScope] = Field(default_factory=list)
    granted_at: str | None = None
    revoked_at: str | None = None
    last_event_id: str | None = None


class ConsentEvent(BaseModel):
    """事件溯源的单条不可变记录。

    红线上的约束：
    - 只追加（append-only），不允许修改或删除；
    - event_id 由服务端生成，带时间戳便于顺序回放；
    - payload 字段不存 PII（actor_id 是内部标识，不是手机号/身份证）。
    """

    event_id: str
    action: ConsentAction
    actor_id: str
    consent_id: str
    timestamp: str
    scope_ids: list[str] = Field(default_factory=list)
    trace_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def build(
        cls,
        action: ConsentAction,
        actor_id: str,
        consent_id: str,
        scope_ids: list[str] | None = None,
        trace_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> ConsentEvent:
        """按服务端时钟构造事件（不让客户端决定时间）。"""
        ts = (
            time.strftime("%Y-%m-%dT%H:%M:%S.")
            + f"{int(time.time() * 1000) % 1000:03d}"
            + time.strftime("%z", time.localtime())
        )
        return cls(
            event_id="EVT-" + uuid.uuid4().hex,
            action=action,
            actor_id=actor_id,
            consent_id=consent_id,
            timestamp=ts,
            scope_ids=list(scope_ids or []),
            trace_id=trace_id,
            payload=dict(payload or {}),
        )

    def frozen_key(self) -> str:
        """用于校验不可篡改性的摘要输入（返回拼接原文供外部 hash）。"""
        return "|".join(
            [
                self.event_id,
                self.action.value,
                self.actor_id,
                self.consent_id,
                self.timestamp,
                ",".join(sorted(self.scope_ids)),
                self.trace_id or "",
                str(sorted(self.payload.items())),
            ]
        )


class RevocationNotice(BaseModel):
    """下游 webhook 的撤回通知载荷。

    字段保持最小：只告诉下游「谁撤回了哪个 consent、影响哪些 scope」。
    PII 一律不随 webhook 发出。
    """

    notice_id: str
    consent_id: str
    actor_id: str
    revoked_at: str
    scope_ids: list[str]
    trace_id: str | None = None
    downstream_target: str
