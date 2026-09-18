"""Retrieval layer (shared kernel).

Implements the X3 contract: ``retrieve`` returns hits plus a ``refused`` flag.
Chunking and alias strategy are read from config, never hardcoded (TR-6). The
real vector index is filled on E1; this skeleton returns ``refused=True`` with no
evidence yet, exercising the "refuse, do not fabricate" contract.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel

_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "configs"


class Filters(BaseModel):
    tenant_id: str | None = None
    permission_domain: str | None = None
    doc_ids: list[str] | None = None
    date_range: list[str] | None = None


class RetrieveRequest(BaseModel):
    request_id: str
    query: str
    top_k: int = 6
    filters: Filters = Filters()
    language_hint: str | None = None
    trace_id: str


class Hit(BaseModel):
    asset_id: str
    chunk_id: str
    score: float
    evidence: str
    language: str
    snippet: str


class RetrieveResult(BaseModel):
    request_id: str
    hits: list[Hit] = []
    refused: bool = False


def _load_aliases() -> dict[str, str]:
    path = _CONFIG_DIR / "aliases.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: dict[str, str] = {}
    for lang_block in (data.get("languages") or {}).values():
        for entry in lang_block.get("aliases", []):
            canonical = entry.get("canonical")
            for variant in entry.get("variants", []):
                out[variant] = canonical
    return out


class RetrievalService:
    def __init__(self) -> None:
        self._aliases = _load_aliases()
        # 变体按长度降序排：正则 alternation 在同一位置取第一个命中的分支，
        # 降序即「最长匹配优先」，避免短变体把长变体截断。
        variants = sorted((v for v in self._aliases if v), key=len, reverse=True)
        self._alias_pattern: re.Pattern[str] | None = (
            re.compile("|".join(re.escape(v) for v in variants)) if variants else None
        )

    def normalize(self, query: str) -> str:
        """按配置层别名表做同义归一。

        单遍扫描、非重叠替换：命中后从匹配末尾继续，**不重新扫描已写入的
        canonical**。朴素 substring 逐条 replace 会在前一条的输出上继续匹配，
        把「股份有限公司」改成「股份有限责任公司」（有回归断言钉住）。
        """
        if self._alias_pattern is None:
            return query
        return self._alias_pattern.sub(lambda m: self._aliases[m.group(0)], query)

    def retrieve(self, req: RetrieveRequest) -> RetrieveResult:
        # 归一化来自配置层（无内联别名判断）。骨架阶段索引未接入，结果暂无消费方；
        # T3.04 接入向量索引后，查询改写须使用这里的结果，不得另起一套。
        _ = self.normalize(req.query)
        # Skeleton: no index wired yet -> refuse rather than fabricate evidence.
        return RetrieveResult(request_id=req.request_id, hits=[], refused=True)
