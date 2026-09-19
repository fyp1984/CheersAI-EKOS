"""L1 单测 · P-03 权限模型（T-D1）。

覆盖判据：
  1. 层级继承（doc -> space -> org）不用自己写遍历，adapter 自带
  2. 非层级场景（协作者 collaborator、分享链接 user:*）能表达
  3. 组 + 嵌套组（group member 继承）能表达
  4. list_allowed 批量白名单可用
  5. 新增一个权限源：只加 Adapter、内核零改动
     （断言：package 结构不变，service 不 import 具体 adapter class）
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest
from packages.policy import (
    AccessCheck,
    Actor,
    InMemoryPolicyAdapter,
    Resource,
)


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
    return loop.run_until_complete(coro)


@pytest.mark.unit()
def test_direct_viewer_can_retrieve() -> None:
    adapter = InMemoryPolicyAdapter()
    user = Actor(type="user", id="alice")
    doc = Resource(kind="doc", id="D1")
    _run(adapter.add_relation(doc, "viewer", user))
    d = _run(adapter.check(AccessCheck(actor=user, action="retrieve", resource=doc)))
    assert d.allowed is True


@pytest.mark.unit()
def test_collaborator_non_hierarchy_is_expressible() -> None:
    """非层级：协作者 collaborator，属于分享场景，不属于树层级。"""
    adapter = InMemoryPolicyAdapter()
    bob = Actor(type="user", id="bob")
    doc = Resource(kind="doc", id="D2")
    _run(adapter.add_relation(doc, "collaborator", bob))
    d = _run(adapter.check(AccessCheck(actor=bob, action="retrieve", resource=doc)))
    assert d.allowed is True


@pytest.mark.unit()
def test_share_link_wildcard_is_expressible() -> None:
    """分享链接：任何 user 都可看（通配符 user:*）——非层级的典型场景。

    T-A7（前置）补结论：SpiceDB 的 `doc:x#share-viewer@user:*` 语义等价，
    因此「含协作者 / 分享链接等非层级场景」SpiceDB 完全能表达。
    """
    adapter = InMemoryPolicyAdapter()
    stranger = Actor(type="user", id="stranger-001")
    shareable = Resource(kind="doc", id="D-published")
    wildcard = Actor(type="user", id="*")
    _run(adapter.add_relation(shareable, "share-viewer", wildcard))
    d = _run(adapter.check(AccessCheck(actor=stranger, action="view", resource=shareable)))
    assert d.allowed is True, "分享链接通配符应对任何 user 生效"


@pytest.mark.unit()
def test_hierarchy_parent_inheritance_no_manual_walk() -> None:
    """层级：org#admin@alice -> space#parent@org -> doc#parent@space，
    alice 应能 view doc（adapter 自己递归，调用方零遍历）。"""
    adapter = InMemoryPolicyAdapter()
    alice = Actor(type="user", id="alice")
    org = Resource(kind="org", id="O1")
    space = Resource(kind="space", id="S1")
    doc = Resource(kind="doc", id="D100")
    _run(adapter.add_relation(org, "admin", alice))
    _run(adapter.add_relation(space, "parent", org))
    _run(adapter.add_relation(doc, "parent", space))
    d = _run(adapter.check(AccessCheck(actor=alice, action="view", resource=doc)))
    assert d.allowed is True, "层级继承应在 adapter 内自动生效，无需调用方遍历"


@pytest.mark.unit()
def test_group_membership_with_nesting() -> None:
    """组嵌套：user -> 子组 -> 父组 -> doc#editor@父组。"""
    adapter = InMemoryPolicyAdapter()
    carol = Actor(type="user", id="carol")
    sub_g = Resource(kind="group", id="sub")
    parent_g = Resource(kind="group", id="parent")
    doc = Resource(kind="doc", id="D-Nested")
    _run(adapter.add_relation(sub_g, "member", carol))
    _run(adapter.add_relation(parent_g, "member", sub_g))
    _run(adapter.add_relation(doc, "editor", parent_g))
    d = _run(adapter.check(AccessCheck(actor=carol, action="edit", resource=doc)))
    assert d.allowed is True


@pytest.mark.unit()
def test_deny_user_has_zero_access_to_restricted() -> None:
    """P-03 验证 1：无权限账号检索受控内容 → 0 命中（由 list_allowed 返回空）。"""
    adapter = InMemoryPolicyAdapter()
    owner = Actor(type="user", id="owner")
    nobody = Actor(type="user", id="nobody")
    doc1 = Resource(kind="doc", id="R1")
    doc2 = Resource(kind="doc", id="R2")
    # 只给 owner
    _run(adapter.add_relation(doc1, "viewer", owner))
    _run(adapter.add_relation(doc2, "viewer", owner))
    # nobody 白名单空
    filt = _run(adapter.list_allowed(nobody, "retrieve", "doc"))
    assert filt.allowed_ids == []


@pytest.mark.unit()
def test_list_allowed_for_viewer_pulls_all_visible() -> None:
    adapter = InMemoryPolicyAdapter()
    u = Actor(type="user", id="viewer-1")
    for i in range(5):
        _run(
            adapter.add_relation(
                Resource(kind="doc", id=f"D{i}"),
                "viewer" if i % 2 == 0 else "collaborator",
                u,
            )
        )
    filt = _run(adapter.list_allowed(u, "retrieve", "doc"))
    # viewer/collaborator 都蕴含 retrieve 动作（ACTION_ROLES 中 retrieve 含 6 项角色）
    assert sorted(filt.allowed_ids) == ["D0", "D1", "D2", "D3", "D4"]


@pytest.mark.unit()
def test_spicedb_adapter_imports_no_kernel_change() -> None:
    """T-D1 新增权限源 = 新增 Adapter 类（factroy 按 backend 名字查表），
    内核（packages/retrieval/service.py）只 import 抽象 PolicyAdapter，
    不依赖任何具体 adapter 类名。

    断言用 AST 扫描防止未来出现「检索层直接 import SpiceDBAdapter」的耦合。
    """
    import ast

    svc = Path(__file__).resolve().parents[2] / "packages" / "retrieval" / "service.py"
    tree = ast.parse(svc.read_text(encoding="utf-8"))
    imports: list[tuple[str, str | None]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module:
                for alias in node.names:
                    imports.append((node.module, alias.name))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, None))
    # 只允许导入 PolicyAdapter（抽象）+ factory.build + 通用 types，不允许具体实现类
    concrete: list[tuple[str, str]] = []
    for mod, cls in imports:
        if cls is not None and cls in {"SpiceDBAdapter", "InMemoryPolicyAdapter"}:
            concrete.append((mod, cls))
    assert concrete == [], (
        f"packages/retrieval/service.py 不得直接引用具体 adapter 类："
        f"新增权限源应只加新类 + 改 factory，内核检索服务零改动。实际耦合 {concrete}"
    )


@pytest.mark.unit()
def test_cycle_inheritance_is_broken() -> None:
    """防循环：space A#parent@B, B#parent@A —— 不能无限递归。"""
    adapter = InMemoryPolicyAdapter()
    u = Actor(type="user", id="u")
    a = Resource(kind="space", id="A")
    b = Resource(kind="space", id="B")
    doc = Resource(kind="doc", id="D-cycle")
    _run(adapter.add_relation(b, "viewer", u))
    _run(adapter.add_relation(a, "parent", b))
    _run(adapter.add_relation(b, "parent", a))  # A <-> B 环
    _run(adapter.add_relation(doc, "parent", a))
    d = _run(adapter.check(AccessCheck(actor=u, action="view", resource=doc)))
    assert d.allowed is True  # 应能通过 B->A->doc 到达，且不会 RecursionError
