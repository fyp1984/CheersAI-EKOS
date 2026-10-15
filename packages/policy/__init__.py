"""L2 Policy adapter layer (permission model).

Design principles (PRD T-D1)
----------------------------
1. 新增权限源 = 新增一个 Adapter 实现，内核零改动。
2. 不自己写层级遍历与一致性检查——交给 SpiceDB（Zanzibar 模型）。
3. Adapter 接口是通用语义（actor/resource/action），无行业语义（TR-1）。
4. 非层级场景（协作者、分享链接等）用 SpiceDB 的 relation + subjectset 表达。
"""

from __future__ import annotations

from .adapter import (
    AccessCheck,
    AccessDecision,
    Actor,
    FilteredResources,
    PolicyAdapter,
    Resource,
)
from .factory import build_policy_adapter_from_config
from .in_memory import InMemoryPolicyAdapter
from .spicedb import SpiceDBAdapter

__all__ = [
    "AccessCheck",
    "AccessDecision",
    "Actor",
    "FilteredResources",
    "PolicyAdapter",
    "Resource",
    "InMemoryPolicyAdapter",
    "SpiceDBAdapter",
    "build_policy_adapter_from_config",
]
