"""Policy adapter contract.

T-D1 判据：
  - 不用自己写层级遍历与一致性（交给 SpiceDB / 等价实现）
  - 新增权限源 = 新增一个 Adapter，内核零改动
  - 接口是通用语义（actor/resource/action），无行业词（TR-1）
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from .types import (
    AccessAction,
    AccessCheck,
    AccessDecision,
    Actor,
    FilteredResources,
    Resource,
)


class PolicyAdapter(ABC):
    """L2 Policy 适配器。新增权限源 = 新增一个子类。"""

    @abstractmethod
    async def health(self) -> dict[str, object]:
        """健康探针：调用方能判断后端是否在线。"""

    @abstractmethod
    async def check(self, check: AccessCheck) -> AccessDecision:
        """单条权限判定。"""

    async def check_batch(self, checks: Iterable[AccessCheck]) -> list[AccessDecision]:
        """默认批量 = 逐条跑。子类有原生批量就重写。"""
        return [await self.check(c) for c in checks]

    @abstractmethod
    async def list_allowed(
        self,
        actor: Actor,
        action: AccessAction,
        resource_kind: str,
        *,
        trace_id: str | None = None,
    ) -> FilteredResources:
        """批量白名单：actor 在某类资源上有 action 权限的全部 id。"""

    # ---- 关系写入（供同步连接器写入权限图）----
    @abstractmethod
    async def add_relation(
        self,
        resource: Resource,
        relation: str,
        subject: Actor | Resource,
        *,
        trace_id: str | None = None,
    ) -> None:
        """写入一条关系：`resource#relation@subject`。

        典型用例（飞书权限模型）：
          doc:123#viewer@user:alice
          doc:123#editor@group:legal-dept
          doc:123#parent@space:internal-wiki      -> 层级继承
          share-link:abc#viewer@user:*            -> 分享链接（非层级）
          doc:123#collaborator@user:bob            -> 协作者（非层级）
        """

    @abstractmethod
    async def remove_relation(
        self,
        resource: Resource,
        relation: str,
        subject: Actor | Resource,
        *,
        trace_id: str | None = None,
    ) -> None:
        """删除一条关系。"""


__all__ = [
    "AccessAction",
    "AccessCheck",
    "AccessDecision",
    "Actor",
    "FilteredResources",
    "PolicyAdapter",
    "Resource",
]
