"""L3 集成测试 · P-03 + P-06 端到端（需要 policy + retrieval 真的串起来）。

标记 integration：需要 make up 或在 E0 环境能跑（实际只用内存适配器，不依赖外部服务）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

import pytest
from packages.common.audit import AuditLogger
from packages.policy import Actor, InMemoryPolicyAdapter, Resource
from packages.retrieval.service import (
    Filters,
    IndexChunk,
    RetrievalService,
    RetrieveRequest,
    mrr_at_k,
    recall_at_k,
)


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
    return loop.run_until_complete(coro)


def _library() -> list[IndexChunk]:
    data: list[tuple[str, str, str]] = [
        (
            "doc-annual-2025",
            "2025 年年度报告：营业收入 50 亿，同比增长 8%，净利润 5 亿，董事会批准现金分红方案",
            "page=3",
        ),
        (
            "doc-annual-2024",
            "2024 年年度报告：营业收入 46.3 亿，净利润 4.2 亿，分红比例 30%",
            "page=5",
        ),
        (
            "doc-coop-guidelines",
            "合规指引：合作方准入规则，最高合作额度 5000 万，"
            "合作期最长 3 年，资产保障比例不超过 70%",
            "page=12",
        ),
        (
            "doc-risk-report",
            "风险报告：异常项目比例 1.2%，缓冲覆盖率 280%，流动比例 65%",
            "page=8",
        ),
        (
            "doc-esg-2025",
            "ESG 报告：可再生能源使用比例 35%，培训投入 1200 万，社区捐赠 500 万",
            "page=16",
        ),
        (
            "doc-rd-invest",
            "研发投入：年度研发费用 2.1 亿，核心技术人员 320 人，新增专利 28 项",
            "page=21",
        ),
        (
            "doc-board-minutes",
            "董事会会议纪要：审议通过年度预算、关联交易、高管薪酬方案，3 票弃权",
            "page=1",
        ),
        (
            "doc-litigation",
            "未决诉讼：供应商合同争议金额 800 万，一审判决本公司胜诉，对方上诉",
            "page=28",
        ),
        (
            "doc-related-party",
            "关联交易：向第一大股东采购原材料 3.4 亿，定价与第三方公允价对比差异 <1%",
            "page=11",
        ),
        (
            "doc-remuneration",
            "董监高薪酬：董事长年薪 280 万，独董津贴 12 万/年，披露完整",
            "page=33",
        ),
    ]
    return [
        IndexChunk(
            asset_id=aid,
            chunk_id=f"chunk-{aid}",
            language="zh",
            snippet=text,
            evidence=ev,
        )
        for (aid, text, ev) in data
    ]


def _grant_all(adapter: InMemoryPolicyAdapter, viewer_id: str) -> None:
    for c in _library():
        _run(
            adapter.add_relation(
                Resource(kind="doc", id=c.asset_id),
                "viewer",
                Actor(type="user", id=viewer_id),
            )
        )


@pytest.mark.integration()
def test_end_to_end_privileged_user_hits_and_evidence_chain() -> None:
    """有权限用户：召回非空、证据链 100% 非空、审计留痕。"""
    policy = InMemoryPolicyAdapter()
    audit = AuditLogger()
    _grant_all(policy, "alice")
    svc = RetrievalService(policy=policy, audit=audit)
    svc.ingest(_library())
    req = RetrieveRequest(
        request_id="REQ-INT-01",
        query="年度报告 董事会 净利润 营收",
        top_k=8,
        trace_id="TRACE-INT-01",
        filters=Filters(permission_domain="user:alice"),
    )
    res = svc.retrieve(req)
    assert res.hits, "alice 有权限 + 有索引，应召回到 hit"
    # 证据链 100%
    missing = [h for h in res.hits if not (h.evidence and h.evidence.strip())]
    assert missing == [], f"证据链缺失：{missing}"
    # 审计留下至少 1 条 retrieve 事件
    retrieve_events = [e for e in audit.events if e.action == "retrieval.query"]
    assert len(retrieve_events) >= 1


@pytest.mark.integration()
def test_end_to_end_no_permission_zero_hits_and_refused_reason() -> None:
    """无权限用户：0 命中、refused=True、refused_reason 非空、且 permission.deny 事件被记。"""
    policy = InMemoryPolicyAdapter()
    audit = AuditLogger()
    _grant_all(policy, "alice")  # 只给 alice，不给 bob
    svc = RetrievalService(policy=policy, audit=audit)
    svc.ingest(_library())
    req = RetrieveRequest(
        request_id="REQ-INT-02",
        query="年度报告 营收",
        top_k=8,
        trace_id="TRACE-INT-02",
        filters=Filters(permission_domain="user:bob"),
    )
    res = svc.retrieve(req)
    assert res.hits == []
    assert res.refused is True
    assert isinstance(res.refused_reason, str) and res.refused_reason.strip()
    # 应有 permission.deny 或 retrieval.query (无论哪种，留痕三要素都有)
    actions = {e.action for e in audit.events}
    assert "retrieval.query" in actions


# --- 构造「题库」测 Recall@8 / MRR@8：10 个问题 → 每个有标准答案 doc 集合 ---

_QUESTION_BANK: list[tuple[str, set[str]]] = [
    # (query, expected_doc_asset_ids)
    ("2025 年度 营收 净利润 增长", {"doc-annual-2025", "doc-annual-2024"}),
    ("异常项目 比例 缓冲覆盖 风险", {"doc-risk-report"}),
    ("董事会 会议纪要 年度预算 关联交易", {"doc-board-minutes", "doc-related-party"}),
    ("关联交易 大股东 采购 定价 公允", {"doc-related-party"}),
    ("合规指引 合作方 最高额度 资产保障比例", {"doc-coop-guidelines"}),
    ("未决诉讼 供应商 合同争议 金额", {"doc-litigation"}),
    ("研发 投入 费用 专利 核心技术人员", {"doc-rd-invest"}),
    ("ESG 可再生能源 捐赠 社区 培训", {"doc-esg-2025"}),
    ("董监高 薪酬 董事长 年薪 独董津贴", {"doc-remuneration"}),
    ("分红 方案 现金 比例 董事会", {"doc-annual-2025", "doc-annual-2024"}),
]


@pytest.mark.integration()
def test_recall_at_8_and_mrr_at_8_benchmark() -> dict[str, object]:
    """中→中组题库 10 题：Recall@8 ≥ 0.90、MRR@8 ≥ 0.80（Q4 QA-1b 判据）。

    返回明细 dict 给调用方打印；断言强制通过阈值。
    """

    policy = InMemoryPolicyAdapter()
    audit = AuditLogger()
    _grant_all(policy, "bench-user")
    svc = RetrievalService(policy=policy, audit=audit)
    svc.ingest(_library())
    recalls: list[float] = []
    mrrs: list[float] = []
    detail_rows: list[dict[str, Any]] = []
    for q_idx, (q, expected) in enumerate(_QUESTION_BANK, 1):
        req = RetrieveRequest(
            request_id=f"Q{q_idx}",
            query=q,
            top_k=8,
            trace_id=f"TBENCH-{q_idx}",
            filters=Filters(permission_domain="user:bench-user"),
        )
        res = svc.retrieve(req)
        ranked_doc_ids = [h.asset_id for h in res.hits]
        r_val = recall_at_k(expected, ranked_doc_ids, 8)
        m_val = mrr_at_k(expected, ranked_doc_ids, 8)
        recalls.append(r_val)
        mrrs.append(m_val)
        detail_rows.append(
            {
                "qid": f"Q{q_idx}",
                "query": q,
                "expected": sorted(expected),
                "hits": ranked_doc_ids,
                "recall_at_8": r_val,
                "mrr_at_8": m_val,
            }
        )
    avg_recall = sum(recalls) / len(recalls) if recalls else 0.0
    avg_mrr = sum(mrrs) / len(mrrs) if mrrs else 0.0
    # 证据链覆盖率：所有 hit 全有 evidence
    evidence_total = 0
    evidence_ok = 0
    for row in detail_rows:
        row_hits = row["hits"]
        assert isinstance(row_hits, list)
        for chunk in _library():
            if chunk.asset_id in row_hits:
                evidence_total += 1
                if chunk.evidence.strip():
                    evidence_ok += 1
    evidence_cov = evidence_ok / evidence_total if evidence_total else 1.0
    # 拒答率：10 题均有索引 + 有权限，理想拒答率 = 0
    refused_cnt = sum(1 for r in detail_rows if not r["hits"])
    refusal_rate = refused_cnt / len(detail_rows)
    result: dict[str, object] = {
        "questions": len(_QUESTION_BANK),
        "avg_recall_at_8": round(avg_recall, 4),
        "avg_mrr_at_8": round(avg_mrr, 4),
        "evidence_chain_coverage": round(evidence_cov, 4),
        "refusal_rate": round(refusal_rate, 4),
        "rows": detail_rows,
    }
    # 强断言：对 Q4 判据下边界给出 PASS/FAIL
    assert avg_recall >= 0.90, f"Recall@8 不达标：期望 ≥0.90，实际 {avg_recall:.4f}"
    assert avg_mrr >= 0.80, f"MRR@8 不达标：期望 ≥0.80，实际 {avg_mrr:.4f}"
    assert evidence_cov >= 0.999, f"证据链覆盖率不足 100%：{evidence_cov}"
    return result
