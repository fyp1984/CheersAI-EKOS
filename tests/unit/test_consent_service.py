"""L1 unit test: 知情同意门服务（T-D3 · 准出⑧）。

覆盖四条验证红线：
1. 可见性：list_in_scopes 能返回 config 中预置的默认纳入范围
2. 关闭生效：revoke 后 scope 清空、state 变 REVOKED，后续「同步检查」返回不纳入
3. webhook 通知：revoke 时下游 notifier 被调用，载荷正确（scope_ids/actor_id/revoked_at）
4. 事件溯源不可篡改：事件链校验通过；人为破坏 chain 时校验失败

所有输入为合成数据，零真实 PII（TR-8）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from packages.consent.service import ConsentService
from packages.consent.store import AppendError, InMemoryEventStore
from packages.consent.types import (
    ConsentAction,
    ConsentEvent,
    ConsentState,
    KnowledgeScope,
    RevocationNotice,
)
from packages.consent.webhook import RevocationNotifier


def _fixture_config(tmp_path: Path) -> Path:
    """每个测试用独立的 config，不读仓库根的 configs/consent.yaml。"""
    cfg = tmp_path / "consent-fixture.yaml"
    cfg.write_text(
        """
version: 1
default_consent_id: "consent-fixture"
default_scopes:
  - scope_id: "scope-a"
    source_type: "local-doc"
    source_ref: "fixtures/a"
    display_name: "文档库A"
    description: "测试文档库A"
    language: "zh"
  - scope_id: "scope-b"
    source_type: "feishu-ingest"
    source_ref: "fixtures/b"
    display_name: "飞书库B"
    language: "zh"
webhook_targets: []
""".strip(),
        encoding="utf-8",
    )
    return cfg


def _fake_scope(scope_id: str, display_name: str) -> KnowledgeScope:
    return KnowledgeScope(
        scope_id=scope_id,
        source_type="local-doc",
        source_ref="fixtures/" + scope_id,
        display_name=display_name,
        language="zh",
    )


@dataclass
class FakeNotifier(RevocationNotifier):
    """可注入的下游通知桩，记录被调用的参数用于断言 webhook 通知。"""

    calls: list[RevocationNotice] = field(default_factory=list)
    fail_next: bool = False

    def send(self, notice: RevocationNotice) -> tuple[bool, str]:
        self.calls.append(notice)
        if self.fail_next:
            self.fail_next = False
            return False, "stub_fail"
        return True, "stub_ok"


# ---------------------------------------------------------------------------
# 验证 1：纳入清单可见性（准出⑧ 判据 1）
# ---------------------------------------------------------------------------


@pytest.mark.unit()
def test_list_scopes_returns_config_defaults(tmp_path: Path) -> None:
    """未做任何 grant，仅读 config 就能看到默认纳入范围（MVP 场景）。"""
    cfg = _fixture_config(tmp_path)
    svc = ConsentService(config_path=cfg)

    result = svc.list_in_scopes(actor_id="user-001")

    # 有且仅有一条来自 system-bootstrap 的 consent
    assert len(result) == 1
    consent = result[0]
    assert consent.consent_id == "consent-fixture"
    # state 是 ACTIVE（bootstrap 写入了 GRANT）
    assert consent.state == ConsentState.ACTIVE
    # 默认的两个 scope 都在
    scope_ids = {s.scope_id for s in consent.scopes}
    assert scope_ids == {"scope-a", "scope-b"}
    # 每个 scope 的 display_name 都可被终端用户直接理解
    names = {s.display_name for s in consent.scopes}
    assert names == {"文档库A", "飞书库B"}


@pytest.mark.unit()
def test_list_scopes_shows_only_active_after_revoke(tmp_path: Path) -> None:
    """revoke 之后 list_scopes 返回 state=REVOKED + 空 scopes。"""
    cfg = _fixture_config(tmp_path)
    svc = ConsentService(config_path=cfg)
    svc.revoke(actor_id="user-001", reason="opt-out")

    result = svc.list_in_scopes(actor_id="user-001")
    consent = result[0]
    assert consent.state == ConsentState.REVOKED
    assert consent.scopes == []
    assert consent.revoked_at is not None


# ---------------------------------------------------------------------------
# 验证 2：一键关闭同步生效（准出⑧ 判据 2）
# ---------------------------------------------------------------------------


def _sync_allowed(svc: ConsentService, actor_id: str, scope_id: str) -> bool:
    """模拟「连接器同步前的准入检查」：scope 必须在某个 ACTIVE consent 中。

    这就是「关闭后同步停止生效」的实际实现：所有同步动作先问 consent 门。
    revoke 之后这里返回 False，等于后续同步全被拦住。
    """
    active_scope_ids: set[str] = set()
    for c in svc.list_in_scopes(actor_id):
        if c.state == ConsentState.ACTIVE:
            active_scope_ids.update(s.scope_id for s in c.scopes)
    return scope_id in active_scope_ids


@pytest.mark.unit()
def test_revoke_blocks_subsequent_sync(tmp_path: Path) -> None:
    """关闭前 scope-a 允许同步；关闭后 scope-a 和 scope-b 都被拦。"""
    cfg = _fixture_config(tmp_path)
    svc = ConsentService(config_path=cfg)

    # 关闭前（默认开启）
    assert _sync_allowed(svc, "user-001", "scope-a") is True
    assert _sync_allowed(svc, "user-001", "scope-b") is True

    # 一键关闭
    out = svc.revoke(actor_id="user-001", reason="I want to opt out")
    assert out.state == ConsentState.REVOKED
    assert out.scope_ids == []  # 全部清空

    # 关闭后：后续同步全部不生效
    assert _sync_allowed(svc, "user-001", "scope-a") is False
    assert _sync_allowed(svc, "user-001", "scope-b") is False


@pytest.mark.unit()
def test_revoke_is_idempotent_double_revoke_still_revoked(tmp_path: Path) -> None:
    """连续两次 revoke，结果仍是 REVOKED，不抛异常，链上多出两条事件。"""
    cfg = _fixture_config(tmp_path)
    svc = ConsentService(config_path=cfg)

    r1 = svc.revoke(actor_id="user-001", reason="first")
    r2 = svc.revoke(actor_id="user-001", reason="second")

    assert r1.state == ConsentState.REVOKED
    assert r2.state == ConsentState.REVOKED
    events = svc.list_events(actor_id="user-001")
    revoke_events = [e for e in events if e.action == ConsentAction.REVOKE]
    assert len(revoke_events) == 2


# ---------------------------------------------------------------------------
# 验证 3：撤回 webhook 通知下游（日志可查）
# ---------------------------------------------------------------------------


@pytest.mark.unit()
def test_revoke_triggers_webhook_with_correct_payload(tmp_path: Path) -> None:
    """revoke 时下游 FakeNotifier 收到一次通知，字段完整且不携带 PII。"""
    cfg = _fixture_config(tmp_path)
    notifier = FakeNotifier()
    svc = ConsentService(config_path=cfg, notifier=notifier)

    # 先给 actor 显式 grant 一个 scope-c，再 revoke：验证 webhook 的 scope_ids 覆盖全部
    svc.grant(
        actor_id="user-002",
        scopes=[_fake_scope("scope-c", "附加库C")],
        consent_id="consent-personal",
    )
    result = svc.revoke(
        actor_id="user-002",
        reason="full stop",
        consent_id="consent-personal",
        trace_id="TRACE-WEBHOOK-1",
    )

    # ① 主流程返回值带 webhook_ok
    assert result.webhook_ok is True
    assert result.webhook_note == "stub_ok"

    # ② notifier 被调用一次（不重复、不少次）
    assert len(notifier.calls) == 1
    notice: RevocationNotice = notifier.calls[0]
    assert notice.consent_id == "consent-personal"
    assert notice.actor_id == "user-002"  # 只带内部 actor_id，不带手机号/邮箱
    assert notice.scope_ids == ["scope-c"]
    assert notice.trace_id == "TRACE-WEBHOOK-1"
    assert notice.revoked_at is not None
    # revoked_at 和事件的 timestamp 是同一个服务端时钟
    last_revoke = [
        e
        for e in svc.list_events(consent_id="consent-personal")
        if e.action == ConsentAction.REVOKE
    ][-1]
    assert notice.revoked_at == last_revoke.timestamp


@pytest.mark.unit()
def test_webhook_failure_does_not_break_revoke(tmp_path: Path) -> None:
    """下游挂了也不能影响主流程：revoke 必须完成，只是 webhook_ok=False。

    红线：撤回同意是用户权利，下游通知失败绝不能把同意状态回滚成 ACTIVE。
    """
    cfg = _fixture_config(tmp_path)
    notifier = FakeNotifier()
    notifier.fail_next = True
    svc = ConsentService(config_path=cfg, notifier=notifier)

    result = svc.revoke(actor_id="user-003", reason="downstream is down")

    assert result.state == ConsentState.REVOKED  # 状态已撤回
    assert result.webhook_ok is False
    assert result.webhook_note == "stub_fail"
    # 本地同步准入仍然是拦的
    assert _sync_allowed(svc, "user-003", "scope-a") is False
    # notifier 仍然被调用过（不能静默跳过）
    assert len(notifier.calls) == 1


# ---------------------------------------------------------------------------
# 验证 4：事件溯源 + 不可篡改（同意记录可溯源）
# ---------------------------------------------------------------------------


@pytest.mark.unit()
def test_every_grant_revoke_is_an_event(tmp_path: Path) -> None:
    """同意记录可溯源：每次 grant/revoke 都有完整事件链，能回放重建当前状态。"""
    cfg = _fixture_config(tmp_path)
    svc = ConsentService(config_path=cfg)

    svc.grant(actor_id="u1", scopes=[_fake_scope("s1", "库1")], consent_id="c1", trace_id="TR-1")
    svc.grant(actor_id="u1", scopes=[_fake_scope("s2", "库2")], consent_id="c2", trace_id="TR-2")
    svc.revoke(actor_id="u1", consent_id="c1", reason="stop c1", trace_id="TR-3")

    events = svc.list_events(actor_id="u1")
    actions = [e.action for e in events]
    # 顺序：系统 bootstrap 的 GRANT（默认 fixture 两个 scope）
    # 然后是 u1 的两次 GRANT、一次 REVOKE
    assert ConsentAction.REVOKE in actions
    grant_count = sum(1 for a in actions if a == ConsentAction.GRANT)
    revoke_count = sum(1 for a in actions if a == ConsentAction.REVOKE)
    assert grant_count >= 2
    assert revoke_count >= 1

    # 每个事件都有 event_id / timestamp / trace_id（留痕要素齐）
    for e in events:
        assert e.event_id.startswith("EVT-")
        assert e.timestamp
        # 不是 u1 做的事件可能是 system-bootstrap，不强求 trace_id
        if e.actor_id == "u1":
            assert e.trace_id in {"TR-1", "TR-2", "TR-3"}


@pytest.mark.unit()
def test_event_store_append_only_duplicate_id_raises(tmp_path: Path) -> None:
    """红线：事件只追加不修改。同 event_id 写入第二次必须抛 AppendError。"""
    store = InMemoryEventStore()
    ev = ConsentEvent.build(
        action=ConsentAction.GRANT,
        actor_id="u",
        consent_id="c",
        scope_ids=["s1"],
    )
    store.append(ev)
    assert len(store) == 1
    with pytest.raises(AppendError):
        store.append(ev)


@pytest.mark.unit()
def test_integrity_chain_passes_intact_fails_when_tampered(tmp_path: Path) -> None:
    """验证防篡改：正常链 PASS；人为改 payload 后 FAIL。

    注意：这里不走 svc.revoke()，因为 svc 不暴露修改入口。
    我们直接操作 store 的私有属性模拟「数据库被改了一行」的场景，
    然后用同一个 verify_chain() 算法再校验一次——它必须能抓住。
    """
    cfg = _fixture_config(tmp_path)
    svc = ConsentService(config_path=cfg)
    # 产生至少两条事件（bootstrap + 一个 revoke）
    svc.revoke(actor_id="tamper-target", reason="baseline")

    intact, checked, detail = svc.verify_integrity()
    assert intact is True
    assert checked >= 2
    assert detail == "ok"

    # 模拟「篡改中间一条事件的 payload」——直接写 store 内部列表
    store = svc._store  # 仅限测试：产品代码里不能有这个访问
    assert isinstance(store, InMemoryEventStore)
    if len(store._events) >= 2:
        # 人为破坏：把第 1 条（索引 0 或 1 都可）的 payload 改一个字符
        victim = store._events[1]
        victim.payload["tampered"] = True  # 破坏 frozen_key
        # 注意：chain 不会自动更新——这正是「篡改后对不上」的前提
        broken, b_idx, b_detail = store.verify_chain()
        assert broken is False
        assert b_idx == 1  # 第 1 条索引开始的链就断了（通常 b_idx 指向破坏点或下一条）
        # b_detail 是失败的 event_id，不等于 "ok"
        assert b_detail != "ok"


@pytest.mark.unit()
def test_language_field_is_open_string_not_enum(tmp_path: Path) -> None:
    """TR-6（语种结构预留 P1）：KnowledgeScope.language 是开放字符串。

    测试先喂一个非 zh 的合法 ISO 639-1 代码，确保 schema 接受；
    同时验证写成长度不合规的会被拒绝。
    """
    cfg = _fixture_config(tmp_path)
    svc = ConsentService(config_path=cfg)

    # 合法：韩文（本期暂不执行但结构仍须接受）
    ko_scope = KnowledgeScope(
        scope_id="s-ko",
        source_type="local-doc",
        source_ref="f/ko",
        display_name="한글자료",
        language="ko",
    )
    g = svc.grant(actor_id="u-lang", scopes=[ko_scope], consent_id="c-lang")
    assert g.state == ConsentState.ACTIVE

    # 非法：长度 4（不是 ISO 639-1/2）
    with pytest.raises(ValueError):
        KnowledgeScope(
            scope_id="s-bad",
            source_type="t",
            source_ref="r",
            display_name="d",
            language="zhxx",
        )


@pytest.mark.unit()
def test_no_industry_words_in_consent_source(tmp_path: Path) -> None:
    """TR-1（内核零行业词）：consent 包的所有文件不含行业词。

    与 test_vault_service.test_adapter_holds_no_masking_patterns 同一写法：
    扫描源码字面量，保证内核文件不偷偷把行业特有词写进去。
    注意：
    1. configs/ 不在此扫描（实例层允许有行业词）；
    2. 行业词表从 gate_baseline.json 读取，不在测试源码中硬写字面量
       （否则 TR-1 会把这份测试本身判定成违规——悖论）。
    """
    import json as _json

    baseline_path = Path("tools/gate/gate_baseline.json")
    assert baseline_path.exists(), "gate_baseline.json 丢失，无法执行 TR-1 自检"
    with open(baseline_path, encoding="utf-8") as _fh:
        _baseline: dict[str, Any] = _json.load(_fh)
    forbidden: list[str] = list(_baseline["TR-1"].get("industry_words", []))
    assert forbidden, "TR-1 industry_words 为空，判据源异常"

    files = sorted(Path("packages/consent").glob("*.py"))
    assert files, "packages/consent glob matched nothing"
    for path in files:
        text = path.read_text(encoding="utf-8")
        for word in forbidden:
            assert word not in text, (
                f"{path} contains industry word {word!r}; "
                "industry logic must live in configs/, not in kernel code"
            )


@pytest.mark.unit()
def test_no_hardcoded_host_port_in_consent_source(tmp_path: Path) -> None:
    """TR-7（端口零硬编码）：consent 包代码中不出现 localhost:xxxx。"""
    import re as _re

    pattern = _re.compile(r"(?:localhost|127\.0\.0\.1|0\.0\.0\.0)\s*:\s*[0-9]{2,5}")
    files = sorted(Path("packages/consent").glob("*.py"))
    for path in files:
        text = path.read_text(encoding="utf-8")
        # 只查代码字面量（排除 triple-quoted docstring 内的反例说明）
        for lineno, line in enumerate(text.splitlines(), start=1):
            if "TR-7" in line or "反例" in line or "不出现" in line:
                continue
            assert not pattern.search(line), (
                f"{path}:{lineno} hardcodes host:port -- violate TR-7: {line!r}"
            )
