"""L1 单测 · 审计留痕（T-D2 · 准出⑦）。

覆盖：
  - 三要素（人/时间/动作）恒有
  - 事件语义接口：retrieve/mask/unmask/login/share/deny 都能记
  - 覆盖率：N 次操作 -> 至少 N 条 audit 记录（留痕覆盖率）
  - 能按 trace_id 关联
"""

from __future__ import annotations

import json

import pytest
from packages.common.audit import AuditLogger, get_audit


@pytest.mark.unit()
def test_every_event_has_actor_timestamp_action() -> None:
    """T-D2 三要素恒有：人(actor_id) / 时间(timestamp) / 动作(action)。"""
    audit = AuditLogger()
    ev = audit.record_raw("test.noop", actor_id="u-001", trace_id="T-1")
    assert ev.actor_id == "u-001"
    assert isinstance(ev.timestamp, str) and len(ev.timestamp) >= 10
    assert ev.action == "test.noop"


@pytest.mark.unit()
def test_event_semantic_api_retrieve() -> None:
    audit = AuditLogger()
    ev = audit.record_retrieve(
        "u-007", query="示例查询", hits_count=3, refused=False, trace_id="T-RETR"
    )
    assert ev.action == "retrieval.query"
    assert ev.extra["query"] == "示例查询"
    assert ev.extra["hits_count"] == 3
    assert ev.extra["refused"] is False
    assert ev.trace_id == "T-RETR"


@pytest.mark.unit()
def test_event_semantic_api_mask_unmask_login_share_deny() -> None:
    audit = AuditLogger()
    e1 = audit.record_mask("u-1", asset_id="A1", policy_id="P-default", masked_fields=5)
    e2 = audit.record_unmask("u-2", asset_id="A1", reason="法务复核", approved=True)
    e3 = audit.record_login("u-3", method="sso", success=True)
    e4 = audit.record_share(
        "u-4", resource_ref="doc:D1", audience="group:legal", share_mode="collaborator"
    )
    e5 = audit.record_permission_deny(
        "u-5",
        action="retrieve",
        resource_ref="doc:classified",
        reason="no matching relation",
    )
    # 每个 action 必须不重样（证明每个语义接口有独立 action 常量，不是全部打成同一个）
    actions = {e.action for e in (e1, e2, e3, e4, e5)}
    assert len(actions) == 5, f"事件语义接口应生成不同 action，实际 = {actions}"
    # 三要素每个都有
    for e in (e1, e2, e3, e4, e5):
        assert e.actor_id
        assert e.timestamp
        assert e.action


@pytest.mark.unit()
def test_audit_coverage_count_matches_operations() -> None:
    """留痕覆盖率：执行 n_ops 次敏感操作 -> 至少 n_ops 条事件。"""
    audit = AuditLogger()
    n_ops = 12
    for i in range(n_ops):
        audit.record_retrieve(
            f"u{i}", query=f"q{i}", hits_count=i, refused=(i == 0), trace_id=f"T{i}"
        )
    assert len(audit.events) >= n_ops, (
        f"执行 {n_ops} 次操作，审计记录仅 {len(audit.events)} 条 —— 留痕覆盖率不足"
    )
    actors = {e.actor_id for e in audit.events}
    # 每一条 actor 都不同，说明是真的独立记录不是重复写入
    assert len(actors) == n_ops


@pytest.mark.unit()
def test_logger_emits_structured_json_line() -> None:
    """Q6 运维最低档：结构化日志，可 grep JSON。"""
    import io
    import logging

    stream = io.StringIO()
    logger = logging.getLogger("ekos.audit.test-stream")
    logger.handlers.clear()
    h = logging.StreamHandler(stream)
    h.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(h)
    logger.setLevel(logging.INFO)
    audit = AuditLogger(logger=logger)
    audit.record_login("u-stream", method="pwd", success=True)
    output = stream.getvalue()
    # 输出是 JSON 行，含 actor/action/timestamp
    parsed = json.loads(output.strip().splitlines()[-1])
    assert parsed["actor_id"] == "u-stream"
    assert parsed["action"] == "identity.login"
    assert "timestamp" in parsed


@pytest.mark.unit()
def test_get_audit_is_singleton() -> None:
    a1 = get_audit()
    a2 = get_audit()
    assert a1 is a2
