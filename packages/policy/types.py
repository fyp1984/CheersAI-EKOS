"""Policy / access-control type definitions.

Pure data containers with zero industry semantics (TR-1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Actor:
    """主体：人 / 机器人 / 服务账号。

    type + id 组成唯一标识（与 SpiceDB object_id 语义 1:1 对应）。
    """

    type: str
    id: str
    tenant_id: str | None = None

    @property
    def ref(self) -> str:
        return f"{self.type}:{self.id}"


@dataclass(frozen=True)
class Resource:
    """客体：文档 / 知识库 / 分享链接 / 任何被保护的对象。"""

    kind: str
    id: str
    tenant_id: str | None = None

    @property
    def ref(self) -> str:
        return f"{self.kind}:{self.id}"


AccessAction = Literal["view", "edit", "admin", "share", "retrieve"]


@dataclass(frozen=True)
class AccessCheck:
    """单次权限判定请求。"""

    actor: Actor
    action: AccessAction
    resource: Resource
    trace_id: str | None = None


@dataclass(frozen=True)
class AccessDecision:
    """单次权限判定结果。"""

    check: AccessCheck
    allowed: bool
    reason: str | None = None
    latency_ms: float | None = None


@dataclass(frozen=True)
class FilteredResources:
    """批量过滤：返回 actor 对某类资源有 action 权限的 id 列表。

    用于 P-06 T-C2 的检索侧双层过滤：
      1. 先拿到 actor 能看的资源 id 白名单
      2. 再在检索结果上再做一次单条校验（防止并发窗口期漏网）
    """

    actor: Actor
    action: AccessAction
    resource_kind: str
    allowed_ids: list[str]
    trace_id: str | None = None
