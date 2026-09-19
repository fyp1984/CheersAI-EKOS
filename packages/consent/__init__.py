"""知情同意门内核模块（T-D3 · 准出⑧）。

事件溯源存同意记录 + 撤回用 webhook 通知下游。
架构参考 apicuron-cmp：所有状态变更通过追加事件实现，
当前状态由事件投影得出，记录天然不可篡改。
"""

from .service import ConsentService
from .types import (
    ConsentAction,
    ConsentEvent,
    ConsentScope,
    ConsentState,
    KnowledgeScope,
    RevocationNotice,
)

__all__ = [
    "ConsentAction",
    "ConsentEvent",
    "ConsentScope",
    "ConsentService",
    "ConsentState",
    "KnowledgeScope",
    "RevocationNotice",
]
