"""⑩ M14 外部知识源 · 判据验证（L3 integration）

判据（PRD §6 F-M14 / gate Q4:EXT-1）：
  ① ≥1 个外部源接入，并可作为检索源被召回；
  ② 外部知识 **100% 标注来源与生效日期**；
  ③ 过期内容 **100% 被标记**，且在**检索结果中不可见**。

样本：`deploy/fixtures/external-sources/`（合成数据，零真实客户信息，守 TR-8）。
"""

from __future__ import annotations

from typing import Any

import pytest
from apps.worker.external_sources import ExternalSourceConnector, to_index_chunks
from packages.retrieval.service import (
    Filters,
    IndexChunk,
    RetrievalService,
    RetrieveRequest,
    RetrieveResult,
)

SOURCE_ID = "ext-standard-001"
SOURCE_NAME = "通用数据管理标准委员会（样例）"
QUERY_VALID = "文档归档 保留期限"
QUERY_EXPIRED = "旧版指标口径 换算系数"


def _build() -> tuple[RetrievalService, list[IndexChunk], list[dict[str, Any]]]:
    conn = ExternalSourceConnector(source_id=SOURCE_ID, source_name=SOURCE_NAME)
    records = conn.execute()
    expired = conn.mark_expired(records)
    chunks = to_index_chunks(records, language="zh")
    svc = RetrievalService(policy=None)
    svc.ingest(iter(chunks))
    return svc, chunks, expired


def _retrieve(svc: RetrievalService, query: str, req_id: str) -> RetrieveResult:
    return svc.retrieve(
        RetrieveRequest(
            request_id=req_id,
            query=query,
            top_k=8,
            trace_id=f"TRACE-{req_id}",
            filters=Filters(permission_domain="D-external"),
        )
    )


@pytest.mark.integration
def test_external_source_is_recallable() -> None:
    """判据①：外部源接入后可作为检索源被召回。"""
    svc, chunks, _ = _build()
    assert chunks, "外部源未产出任何可检索单元 —— 判据①不成立"
    res = _retrieve(svc, QUERY_VALID, "REQ-EXT-01")
    assert res.hits, f"外部知识未被召回（refused={res.refused} reason={res.refused_reason}）"


@pytest.mark.integration
def test_recalled_external_hits_carry_source_and_effective_date() -> None:
    """判据②（检索结果侧）：召回的每条外部知识都带来源与生效日期。"""
    svc, _, _ = _build()
    res = _retrieve(svc, QUERY_VALID, "REQ-EXT-02")
    assert res.hits
    for hit in res.hits:
        assert SOURCE_NAME in hit.evidence, f"证据链缺来源：{hit.evidence}"
        assert "｜生效 " in hit.evidence, f"证据链缺生效日期：{hit.evidence}"


@pytest.mark.integration
def test_all_external_chunks_carry_source_and_effective_date() -> None:
    """判据②（入库侧）：外部知识 100% 标注来源与生效日期。"""
    _, chunks, _ = _build()
    missing = [c.chunk_id for c in chunks if not c.source_name or not c.effective_date]
    assert not missing, f"{len(missing)} 条外部 chunk 缺来源/生效日期：{missing[:3]}"
    assert all(c.is_external for c in chunks), "存在未标记为外部的 chunk"


@pytest.mark.integration
def test_expired_external_knowledge_is_marked() -> None:
    """判据③（标记侧）：过期内容 100% 被标记。"""
    _, chunks, expired = _build()
    assert expired, "样本中应含过期项，否则判据③无法验证"
    marked_ids = {str(r["doc_id"]) for r in expired}
    indexed_ids = {c.asset_id for c in chunks}
    assert marked_ids <= indexed_ids, f"过期项未被标记即入库：{marked_ids - indexed_ids}"


@pytest.mark.integration
def test_expired_external_knowledge_hidden_from_retrieval() -> None:
    """判据③（不可见侧）：过期内容不出现在检索结果中。"""
    svc, _, expired = _build()
    expired_ids = {str(r["doc_id"]) for r in expired}
    res = _retrieve(svc, QUERY_EXPIRED, "REQ-EXT-03")
    leaked = {h.asset_id for h in res.hits} & expired_ids
    assert not leaked, f"过期内容仍出现在检索结果：{leaked}"
