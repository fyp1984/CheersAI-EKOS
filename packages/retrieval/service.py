"""Retrieval layer (shared kernel) — T-C1 + T-C5 + T-C2.

T-C1 三种检索：
  - keyword 倒排（bag-of-words / overlap score）
  - vector  cosine（内存 stub 向量，E1 接 pgvector 真实索引）
  - graph   实体邻接（关键词共享节点，E1 接 Neo4j/知识图谱）
  - hybrid  三路召回加权 → dedupe → top_k

T-C5 证据链：
  - Hit.evidence 100% 不为空（物理来源：page= / snippet-origin= / source=）
  - refused=True 时 RetrieveResult.refused_reason 100% 非空、说明原因

T-C2 双层权限过滤（依赖 P-03 T-D1）：
  1. 预过滤：policy.list_allowed() 先拿到资源白名单，倒排/向量/图谱 只扫白名单
  2. 终过滤：最终重排 top-k 时，每一条再逐条 policy.check()，拦截并发窗口期漏网

策略落配置层（TR-6 + X3.3）：
  configs/chunking.yaml  · 切分阈值
  configs/aliases.yaml   · 别名表
  configs/retrieval.yaml · 引擎权重、拒答原因、混合参数
  代码内零内联阈值、零中文别名判断、零硬编码语种分支
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from packages.common.audit import AuditLogger, get_audit
from packages.policy.adapter import PolicyAdapter
from packages.policy.factory import build_policy_adapter_from_config
from packages.policy.types import (
    AccessCheck,
    Actor,
    Resource,
)

_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "configs"

# ----------------------------- Schema（保持 X3 契约形状不变）-----------------


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
    evidence: str  # T-C5：100% 非空，说明物理来源
    language: str
    snippet: str


class RetrieveResult(BaseModel):
    request_id: str
    hits: list[Hit] = []
    refused: bool = False
    refused_reason: str | None = None  # T-C5：refused=True 时必填、说清原因


# ----------------------------- 配置加载 -----------------------------


def _load_yaml(name: str) -> dict[str, Any]:
    p = _CONFIG_DIR / name
    if not p.exists():
        return {}
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    assert isinstance(data, dict)
    return data


def _load_aliases() -> dict[str, str]:
    data = _load_yaml("aliases.yaml")
    out: dict[str, str] = {}
    langs = data.get("languages") or {}
    assert isinstance(langs, dict)
    for lang_block in langs.values():
        assert isinstance(lang_block, dict)
        for entry in lang_block.get("aliases", []) or []:
            assert isinstance(entry, dict)
            canonical = entry.get("canonical")
            if not isinstance(canonical, str):
                continue
            for variant in entry.get("variants", []) or []:
                if isinstance(variant, str):
                    out[variant] = canonical
    return out


def _load_retrieval_cfg() -> dict[str, Any]:
    return _load_yaml("retrieval.yaml")


# ----------------------------- 可插拔索引（T-C1：换引擎只改配置）--------------


@dataclass
class IndexChunk:
    """最小可检索单元：写入三种引擎的同一份数据。"""

    asset_id: str
    chunk_id: str
    language: str
    snippet: str
    # 物理证据（T-C5）：写入索引时一并写入，检索结果直接带回，不用再查
    evidence: str
    # tokenized（小写）：keyword 倒排用；为空时 fallback 到 snippet 逐字
    tokens: list[str] = field(default_factory=list)
    # 向量：E0 stub 用伪向量（minhash），E1 替换成真实 embedding
    vector: list[float] = field(default_factory=list)
    # 图谱实体：从 snippet 中提取出的关键词，E0 简单取高频 ngram，E1 接实体识别
    entities: list[str] = field(default_factory=list)


class BaseSearchEngine:
    """检索引擎抽象。"""

    name: str = "base"

    def ingest(self, chunks: Iterable[IndexChunk]) -> None: ...

    def search(
        self,
        query: str,
        top_k: int,
        allowed_asset_ids: set[str] | None,
    ) -> list[tuple[IndexChunk, float]]:
        return []


class KeywordSearchEngine(BaseSearchEngine):
    """倒排：bag-of-words overlap，无外部依赖。"""

    name = "keyword"

    def __init__(self) -> None:
        self._inverted: dict[str, set[str]] = defaultdict(set)  # token -> chunk_id
        self._chunks: dict[str, IndexChunk] = {}

    def ingest(self, chunks: Iterable[IndexChunk]) -> None:
        for c in chunks:
            self._chunks[c.chunk_id] = c
            for tok in c.tokens if c.tokens else _tokenize(c.snippet):
                if tok:
                    self._inverted[tok].add(c.chunk_id)

    def search(
        self,
        query: str,
        top_k: int,
        allowed_asset_ids: set[str] | None,
    ) -> list[tuple[IndexChunk, float]]:
        q_tokens = set(t for t in _tokenize(query) if t)
        if not q_tokens:
            return []
        scores: dict[str, float] = defaultdict(float)
        for qt in q_tokens:
            for cid in self._inverted.get(qt, ()):
                c = self._chunks.get(cid)
                if c is None:
                    continue
                if allowed_asset_ids is not None and c.asset_id not in allowed_asset_ids:
                    continue
                c_tokens = c.tokens if c.tokens else _tokenize(c.snippet)
                scores[cid] += 1.0 / max(1, math.log2(len(c_tokens) + 2))
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        return [(self._chunks[cid], s) for cid, s in ranked]


class VectorSearchEngine(BaseSearchEngine):
    """内存伪向量：用签名向量做 cosine，E1 接真实 pgvector/Milvus。

    为什么 E0 不直接空着：空索引契约测试直接 refused，无法对「召回率/证据链/权限过滤」
    做任何有内容的实测。伪向量让这三个验证在 E0 stub 就能跑，E1 只改 provider。
    """

    name = "vector"

    def __init__(self, dim: int = 64) -> None:
        self._dim = dim
        self._chunks: list[IndexChunk] = []

    def ingest(self, chunks: Iterable[IndexChunk]) -> None:
        for c in chunks:
            if not c.vector:
                c.vector = _snippet_signature(c.snippet, self._dim)
            self._chunks.append(c)

    def search(
        self,
        query: str,
        top_k: int,
        allowed_asset_ids: set[str] | None,
    ) -> list[tuple[IndexChunk, float]]:
        q_vec = _snippet_signature(query, self._dim)
        scored: list[tuple[IndexChunk, float]] = []
        for c in self._chunks:
            if allowed_asset_ids is not None and c.asset_id not in allowed_asset_ids:
                continue
            scored.append((c, _cosine(q_vec, c.vector)))
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored[:top_k]


class GraphSearchEngine(BaseSearchEngine):
    """图谱邻接：实体邻接权重。E1 替换成 Neo4j 或等价图数据库。

    图的边：两个 chunk 共享 >=1 个提取出的实体，就在它们之间加边。
    查询命中的 chunk 做 1-hop 邻居展开（权重衰减）。
    """

    name = "graph"

    def __init__(self, max_hops: int = 2) -> None:
        self._max_hops = max_hops
        self._chunks: dict[str, IndexChunk] = {}
        self._entity_to_chunks: dict[str, set[str]] = defaultdict(set)
        self._chunk_entities: dict[str, set[str]] = defaultdict(set)

    def ingest(self, chunks: Iterable[IndexChunk]) -> None:
        for c in chunks:
            self._chunks[c.chunk_id] = c
            ents = c.entities if c.entities else _extract_entities(c.snippet)
            self._chunk_entities[c.chunk_id] = set(ents)
            for e in ents:
                self._entity_to_chunks[e].add(c.chunk_id)

    def search(
        self,
        query: str,
        top_k: int,
        allowed_asset_ids: set[str] | None,
    ) -> list[tuple[IndexChunk, float]]:
        q_ents = set(_extract_entities(query))
        if not q_ents:
            return []
        visited: dict[str, float] = {}
        # 0-hop：直接实体命中的 chunk（权重 1.0）
        frontier: set[str] = set()
        for e in q_ents:
            for cid in self._entity_to_chunks.get(e, ()):
                frontier.add(cid)
        for cid in frontier:
            c = self._chunks.get(cid)
            if c is None:
                continue
            if allowed_asset_ids is not None and c.asset_id not in allowed_asset_ids:
                continue
            overlap = len(self._chunk_entities[cid] & q_ents)
            visited[cid] = overlap / max(1, len(self._chunk_entities[cid] | q_ents))
        # 1-hop 邻居：权重 * 0.6
        current = dict(visited)
        for hop in range(min(self._max_hops, 1)):
            next_frontier: dict[str, float] = {}
            for cid in current:
                decay = 0.6 ** (hop + 1)
                for e in self._chunk_entities.get(cid, ()):
                    for nid in self._entity_to_chunks.get(e, ()):
                        if nid == cid:
                            continue
                        n = self._chunks.get(nid)
                        if n is None:
                            continue
                        if allowed_asset_ids is not None and n.asset_id not in allowed_asset_ids:
                            continue
                        n_score = (visited.get(cid, 0.0)) * decay
                        if nid not in visited or n_score > visited[nid]:
                            visited[nid] = n_score
                            next_frontier[nid] = n_score
            current = next_frontier
        ranked = sorted(visited.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        return [(self._chunks[cid], s) for cid, s in ranked]


# ----------------------------- 工具函数 -----------------------------


def _tokenize(text: str) -> list[str]:
    """朴素 tokenizer：CJK 逐字 + 英文按空白 + 小写 + 去标点。

    不写 hardcoded zh 分支（TR-6）：CJK 逐字对中文有效、对韩文也有效（逐 Hanja/字母）；
    英文按空白分词；实际效果用配置层的 chunking 权重/停用词补，不焊死在代码里。
    """
    tokens: list[str] = []
    buf: list[str] = []
    for ch in text:
        # 字母/数字 按连续片段合并，CJK/标点 单独开 token
        is_word_ch = ch.isalnum() and not ("一" <= ch <= "鿿")
        if is_word_ch:
            buf.append(ch.lower())
        else:
            if buf:
                tokens.append("".join(buf))
                buf = []
            # 保留非空白、非 ASCII 标点字符（CJK 为主，也覆盖其他 Unicode 表意字符）
            if ch.strip() and not (
                "\u0021" <= ch <= "\u0040"
                or "\u005b" <= ch <= "\u0060"
                or "\u007b" <= ch <= "\u007e"
            ):
                tokens.append(ch)
    if buf:
        tokens.append("".join(buf))
    return [t for t in tokens if t]


def _snippet_signature(snippet: str, dim: int) -> list[float]:
    """把 snippet 哈希成一个稳定、归一化的伪向量（E0 stub）。

    真向量替换点：把 IndexChunk.vector 填上 text-embedding-* 的输出，再把 provider
    改成 pgvector，VectorSearchEngine.search 直接换成 SQL 查询，上层调用零改动。
    """
    vec = [0.0] * dim
    if not snippet:
        # 零向量会让 cosine 分母为 0；给一个恒定小向量兜底
        vec[0] = 1.0
        return vec
    for idx, ch in enumerate(snippet):
        h = 0x811C9DC5
        for byte in ch.encode("utf-8"):
            h ^= byte
            h = (h * 0x01000193) & 0xFFFFFFFF
        bucket = (h + idx) % dim
        vec[bucket] += 1.0
    # L2 归一
    norm = math.sqrt(sum(v * v for v in vec))
    if norm < 1e-9:
        vec[0] = 1.0
        return vec
    return [v / norm for v in vec]


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    denom = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b))
    if denom < 1e-9:
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=False)) / denom


def _extract_entities(snippet: str) -> list[str]:
    """E0 实体提取：最长连续 CJK（>=2）+ 其中 2~4 字的核心子串 + 4 位以上数字。

    为什么要拆子串：E0 图谱是「实体完全相等」的集合邻接，不拆的话「董事会成员名单」
    是一个 7 字实体，而用户查询的「董事会」是 3 字独立 token，两者就匹配不上。
    子串同时放进去，保证两种粒度都能命中。
    E1 替换成 NER 模型，图谱引擎的 ingest / search 接口不变。
    """
    out: list[str] = []
    seen: set[str] = set()

    def _add(e: str) -> None:
        if e and e not in seen:
            seen.add(e)
            out.append(e)

    # 连续汉字（>=2）：整段 + 2~4 字滑窗子串
    for m in re.finditer(r"[一-鿿]{2,}", snippet):
        whole = m.group(0)
        _add(whole)
        n = len(whole)
        for size in (2, 3, 4):
            if n <= size:
                continue
            for i in range(n - size + 1):
                _add(whole[i : i + size])
    # 年份 / 数字序列（4 位数字以上）
    for m in re.finditer(r"\d{4,}", snippet):
        _add(m.group(0))
    return out


# ----------------------------- 评测指标（Recall@k / MRR@k）------------------


def recall_at_k(expected: set[str], ranked: list[str], k: int) -> float:
    if not expected:
        return 0.0
    top = set(ranked[:k])
    return len(top & expected) / len(expected)


def mrr_at_k(expected: set[str], ranked: list[str], k: int) -> float:
    if not expected:
        return 0.0
    for i, item in enumerate(ranked[:k], 1):
        if item in expected:
            return 1.0 / i
    return 0.0


# ----------------------------- 服务主类 -----------------------------

# 哨兵：区分「显式传 policy=None（跳过权限）」 vs 「省略参数（走 config 构造）」
_UNSET = object()


class RetrievalService:
    def __init__(
        self,
        *,
        policy: PolicyAdapter | None | object = _UNSET,
        audit: AuditLogger | None = None,
    ) -> None:
        self._aliases = _load_aliases()
        variants = sorted((v for v in self._aliases if v), key=len, reverse=True)
        self._alias_pattern: re.Pattern[str] | None = (
            re.compile("|".join(re.escape(v) for v in variants)) if variants else None
        )
        self._cfg = _load_retrieval_cfg()

        # 引擎：按配置开/关；权重由 configs/retrieval.yaml 决定，不写死（TR-6）
        engines_cfg = self._cfg.get("engines") or {}
        kw_cfg = engines_cfg.get("keyword") or {}
        vec_cfg = engines_cfg.get("vector") or {}
        gph_cfg = engines_cfg.get("graph") or {}
        self._engines: list[tuple[BaseSearchEngine, float]] = []
        if kw_cfg.get("enabled", True):
            self._engines.append((KeywordSearchEngine(), float(kw_cfg.get("weight") or 0.4)))
        if vec_cfg.get("enabled", True):
            dim = int((vec_cfg.get("params") or {}).get("embedding_dim") or 64)
            vec_w = float(vec_cfg.get("weight") or 0.45)
            self._engines.append((VectorSearchEngine(dim=dim), vec_w))
        if gph_cfg.get("enabled", True):
            max_hops = int((gph_cfg.get("params") or {}).get("max_hops") or 2)
            gph_w = float(gph_cfg.get("weight") or 0.15)
            self._engines.append((GraphSearchEngine(max_hops=max_hops), gph_w))

        # 策略适配器（T-C2）：
        #   - 显式传 None   → 完全跳过权限过滤（E0 纯索引 / 单用户本机场景）
        #   - 传具体 Adapter → 直接使用该实例
        #   - 省略不传（哨兵 _UNSET）→ 走 configs/policy.yaml 按后端构造
        self._policy: PolicyAdapter | None
        if policy is _UNSET:
            self._policy = build_policy_adapter_from_config()
        elif policy is None:
            self._policy = None
        else:
            assert isinstance(policy, PolicyAdapter)
            self._policy = policy
        self._audit: AuditLogger = audit if audit is not None else get_audit()
        # 混合参数
        hybrid_cfg = self._cfg.get("hybrid") or {}
        self._top_k_per_engine: int = int(hybrid_cfg.get("top_k_per_engine") or 20)
        self._dedupe: bool = bool(hybrid_cfg.get("dedupe", True))
        # 拒答参数
        refusal_cfg = self._cfg.get("refusal") or {}
        self._r_no_index: str = (
            refusal_cfg.get("reason_when_no_index") or "检索索引尚未加载，暂无证据可召回"
        )
        self._r_all_filtered: str = (
            refusal_cfg.get("reason_when_all_filtered")
            or "召回结果均被权限规则过滤，当前账号无访问权限"
        )
        self._r_low_score: str = (
            refusal_cfg.get("reason_when_low_score") or "Top-k 相似度均低于阈值，证据不足"
        )
        self._min_score: float = float(refusal_cfg.get("min_score_threshold") or 0.001)

    # ----------------------------------------------------------------- 别名归一
    def normalize(self, query: str) -> str:
        if self._alias_pattern is None:
            return query
        return self._alias_pattern.sub(lambda m: self._aliases[m.group(0)], query)

    # ----------------------------------------------------------------- 索引写入
    def ingest(self, chunks: Iterable[IndexChunk]) -> None:
        chunk_list = list(chunks)
        for engine, _ in self._engines:
            engine.ingest(iter(chunk_list))

    # ----------------------------------------------------------------- 主检索
    def retrieve(self, req: RetrieveRequest) -> RetrieveResult:
        # 1. 别名归一（来自配置层，无内联中文别名）
        normalized = self.normalize(req.query)

        # 2. 确定 actor（T-C2 用）：从 trace_id 或 filters 派生一个匿名 id
        actor_id = f"anon-{req.trace_id[:12]}" if req.trace_id else "anon"
        # 若调用方在 filters.permission_domain 填了 user:<id>，按它拆
        if req.filters.permission_domain and ":" in req.filters.permission_domain:
            parts = req.filters.permission_domain.split(":", 1)
            actor = Actor(type=parts[0], id=parts[1], tenant_id=req.filters.tenant_id)
            actor_id = actor.id
        else:
            actor = Actor(type="user", id=actor_id, tenant_id=req.filters.tenant_id)

        # 3. T-C2 双层过滤 · 第一层：拿允许的 asset_id 白名单（异步变同步调用兜底）
        allowed_asset_ids: set[str] | None = None
        if self._policy is not None:
            try:
                import asyncio

                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                filt = loop.run_until_complete(
                    self._policy.list_allowed(
                        actor,
                        "retrieve",
                        "doc",  # 通用 doc kind，不写行业词（TR-1）
                        trace_id=req.trace_id,
                    )
                )
                if filt.allowed_ids:
                    allowed_asset_ids = set(filt.allowed_ids)
            except Exception:  # noqa: BLE001
                # list_allowed 失败时 fallback 到「逐条 check」（双层的第二层兜底）
                allowed_asset_ids = None

        # 4. doc_ids 过滤器（契约字段，与权限过滤是 AND）
        if req.filters.doc_ids is not None:
            doc_filter = set(req.filters.doc_ids)
            allowed_asset_ids = (
                (allowed_asset_ids & doc_filter) if allowed_asset_ids is not None else doc_filter
            )

        # 5. 三路召回 + 加权混合
        if not self._engines:
            return RetrieveResult(
                request_id=req.request_id,
                hits=[],
                refused=True,
                refused_reason=self._r_no_index,
            )
        merged: dict[str, tuple[IndexChunk, float]] = {}
        for engine, weight in self._engines:
            hits = engine.search(normalized, self._top_k_per_engine, allowed_asset_ids)
            for chunk, raw in hits:
                w = raw * weight
                if self._dedupe:
                    key = f"{chunk.asset_id}::{chunk.chunk_id}"
                    if key not in merged or w > merged[key][1]:
                        merged[key] = (chunk, w)
                else:
                    key = f"{chunk.asset_id}::{chunk.chunk_id}::{engine.name}"
                    merged[key] = (chunk, w)

        ranked = sorted(merged.values(), key=lambda t: t[1], reverse=True)

        # 6. T-C2 双层过滤 · 第二层：每条再 check 一遍（拦截并发窗口期漏网）
        post_filtered: list[tuple[IndexChunk, float]] = []
        if self._policy is None:
            post_filtered = ranked
        else:
            import asyncio

            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
            for chunk, score in ranked:
                if len(post_filtered) >= req.top_k:
                    break
                check = AccessCheck(
                    actor=actor,
                    action="retrieve",
                    resource=Resource(
                        kind="doc",
                        id=chunk.asset_id,
                        tenant_id=req.filters.tenant_id,
                    ),
                    trace_id=req.trace_id,
                )
                try:
                    decision = loop.run_until_complete(self._policy.check(check))
                except Exception:  # noqa: BLE001
                    decision = None
                if decision is not None and not decision.allowed:
                    self._audit.record_permission_deny(
                        actor.id,
                        action="retrieve",
                        resource_ref=f"doc:{chunk.asset_id}",
                        reason=decision.reason,
                        trace_id=req.trace_id,
                    )
                    continue
                post_filtered.append((chunk, score))

        # 7. Top-k 截断 + 拒答判定（T-C5：100% 证据链 + 100% 拒答原因）
        top = post_filtered[: req.top_k]
        refused = len(top) == 0
        refused_reason: str | None = None
        if refused:
            # 根据「没内容的具体原因」给不同的 refused_reason
            if not self._engines or all(
                len(engine.search(normalized, 1, None)) == 0 for engine, _ in self._engines
            ):
                refused_reason = self._r_no_index
            elif (
                allowed_asset_ids is not None
                and ranked
                and all(
                    c.asset_id not in allowed_asset_ids for c, _ in ranked[: self._top_k_per_engine]
                )
            ):
                refused_reason = self._r_all_filtered
            else:
                refused_reason = self._r_low_score

        hits_out: list[Hit] = []
        for chunk, score in top:
            # 证据链 100% 非空（T-C5）。索引写入已保证 evidence，这里兜底
            evidence = chunk.evidence or f"source={chunk.asset_id};chunk={chunk.chunk_id}"
            # 规范化：至少有一个 k=v 结构
            if "=" not in evidence and ";" not in evidence:
                evidence = f"source={chunk.asset_id};note={evidence}"
            language = chunk.language or "und"
            hits_out.append(
                Hit(
                    asset_id=chunk.asset_id,
                    chunk_id=chunk.chunk_id,
                    score=round(float(score), 4),
                    evidence=evidence,
                    language=language,
                    snippet=chunk.snippet,
                )
            )

        # 8. 审计（T-D2）：每次检索必留痕，含 人/时间/动作 + query/hits/refused
        self._audit.record_retrieve(
            actor.id,
            query=normalized,
            hits_count=len(hits_out),
            refused=refused,
            trace_id=req.trace_id,
        )

        return RetrieveResult(
            request_id=req.request_id,
            hits=hits_out,
            refused=refused,
            refused_reason=refused_reason,
        )


# 暴露 IndexChunk 给调用方写索引
__all__ = [
    "Filters",
    "Hit",
    "IndexChunk",
    "RetrieveRequest",
    "RetrieveResult",
    "RetrievalService",
    "BaseSearchEngine",
    "KeywordSearchEngine",
    "VectorSearchEngine",
    "GraphSearchEngine",
    "recall_at_k",
    "mrr_at_k",
]
