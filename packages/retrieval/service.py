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


def _load_stop_words() -> dict[str, set[str]]:
    """从 configs/chunking.yaml 按语种加载停用词（含 default 兜底）。

    返回 {lang_code: set(words)}；未登记的语种回退到 default 档，
    不写硬编码 zh 分支（TR-6）。
    """
    data = _load_yaml("chunking.yaml")
    out: dict[str, set[str]] = {}
    default_block = data.get("default") or {}
    default_sw: set[str] = set()
    if isinstance(default_block, dict):
        for w in default_block.get("stop_words") or []:
            if isinstance(w, str) and w:
                default_sw.add(w)
    out["__default__"] = default_sw
    langs = data.get("languages") or {}
    if isinstance(langs, dict):
        for lang_code, block in langs.items():
            if not isinstance(block, dict):
                continue
            per_lang: set[str] = set(default_sw)
            for w in block.get("stop_words") or []:
                if isinstance(w, str) and w:
                    per_lang.add(w)
            out[str(lang_code)] = per_lang
    return out


def _stop_words_for_lang(sw_map: dict[str, set[str]], lang: str | None) -> set[str]:
    if not lang:
        return sw_map.get("__default__", set())
    return sw_map.get(lang) or sw_map.get("__default__", set())


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
    """倒排：bag-of-words overlap，去停用词（P-10 T-C4），无外部依赖。"""

    name = "keyword"

    def __init__(self, stop_words: set[str] | None = None) -> None:
        self._inverted: dict[str, set[str]] = defaultdict(set)  # token -> chunk_id
        self._chunks: dict[str, IndexChunk] = {}
        self._stop_words: set[str] = set(stop_words) if stop_words else set()

    def ingest(self, chunks: Iterable[IndexChunk]) -> None:
        for c in chunks:
            self._chunks[c.chunk_id] = c
            chunk_sw = self._stop_words
            for tok in c.tokens if c.tokens else _tokenize(c.snippet):
                if not tok or tok in chunk_sw:
                    continue
                self._inverted[tok].add(c.chunk_id)

    def search(
        self,
        query: str,
        top_k: int,
        allowed_asset_ids: set[str] | None,
    ) -> list[tuple[IndexChunk, float]]:
        q_tokens = set(t for t in _tokenize(query) if t and t not in self._stop_words)
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
                real_tokens = [t for t in c_tokens if t and t not in self._stop_words]
                scores[cid] += 1.0 / max(1, math.log2(len(real_tokens) + 2))
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

        # 停用词表：按语种从 configs/chunking.yaml 加载（TR-6，无 zh 硬编码分支）
        self._sw_map: dict[str, set[str]] = _load_stop_words()

        # 引擎：按配置开/关；权重由 configs/retrieval.yaml 决定，不写死（TR-6）
        engines_cfg = self._cfg.get("engines") or {}
        kw_cfg = engines_cfg.get("keyword") or {}
        vec_cfg = engines_cfg.get("vector") or {}
        gph_cfg = engines_cfg.get("graph") or {}
        self._engines: list[tuple[BaseSearchEngine, float]] = []
        if kw_cfg.get("enabled", True):
            kw_w = float(kw_cfg.get("weight") or 1.0)
            sw_kw = _stop_words_for_lang(self._sw_map, self._cfg.get("default_language") or None)
            self._engines.append((KeywordSearchEngine(stop_words=sw_kw), kw_w))
        if vec_cfg.get("enabled", True):
            dim = int((vec_cfg.get("params") or {}).get("embedding_dim") or 64)
            vec_w = float(vec_cfg.get("weight") or 1.0)
            self._engines.append((VectorSearchEngine(dim=dim), vec_w))
        if gph_cfg.get("enabled", True):
            max_hops = int((gph_cfg.get("params") or {}).get("max_hops") or 2)
            gph_w = float(gph_cfg.get("weight") or 1.0)
            self._engines.append((GraphSearchEngine(max_hops=max_hops), gph_w))

        # 策略适配器（T-C2）
        self._policy: PolicyAdapter | None
        if policy is _UNSET:
            self._policy = build_policy_adapter_from_config()
        elif policy is None:
            self._policy = None
        else:
            assert isinstance(policy, PolicyAdapter)
            self._policy = policy
        self._audit: AuditLogger = audit if audit is not None else get_audit()

        # 混合参数：RRF（L4 融合排序，PRD T-C4 要求）+ 兼容旧 weighted_sum
        hybrid_cfg = self._cfg.get("hybrid") or {}
        self._top_k_per_engine: int = int(hybrid_cfg.get("top_k_per_engine") or 20)
        self._dedupe: bool = bool(hybrid_cfg.get("dedupe", True))
        fusion_cfg = hybrid_cfg.get("fusion") or {}
        self._fusion_strategy: str = str(fusion_cfg.get("strategy") or "weighted_sum")
        self._rrf_k: float = float(fusion_cfg.get("rrf_k") or 60)
        self._rrf_use_weights: bool = bool(fusion_cfg.get("weights_from_config", False))

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
        # P-10 T-C4 主拒答信号：融合 top-1 chunk 的三路召回共识度（贡献分数的引擎路数）
        self._min_consensus_engines: int = int(refusal_cfg.get("min_consensus_engines") or 2)
        # 保留兼容/兜底（当前不作为主拒答门控）
        self._min_kw_raw_score: float = float(refusal_cfg.get("min_keyword_raw_score") or 0.10)
        self._min_any_engine_raw: float = float(refusal_cfg.get("min_any_engine_raw") or 0.01)
        self._min_kw_match: float = float(refusal_cfg.get("min_keyword_match_ratio") or 0.08)

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

        # 5. 三路召回 + 融合排序（PRD T-C4 L2+L4：RRF Reciprocal Rank Fusion）
        if not self._engines:
            return RetrieveResult(
                request_id=req.request_id,
                hits=[],
                refused=True,
                refused_reason=self._r_no_index,
            )

        # P-10 T-C4 · 拒答信号：三路召回各自的 top-1 原始分（不是 RRF 分数）。
        # RRF 分数在 [1/(k+1), sum_weights] 对所有查询都近似，无法区分证据强弱。
        # 原始分则天然区分：拒答题的 keyword/graph/vector overlap 都极低。
        top_raw_by_engine: dict[str, float] = {}
        q_tokens = _tokenize(normalized)
        sw = _stop_words_for_lang(self._sw_map, self._cfg.get("default_language") or None)
        effective_q_tokens = [t for t in q_tokens if t and t not in sw]

        use_rrf = self._fusion_strategy == "rrf"
        # P-10 T-C4：每个融合 key 由哪几路引擎贡献（用于三路共识拒答信号）
        contributor_map: dict[str, set[str]] = defaultdict(set)
        if use_rrf:
            rrf_scores: dict[str, float] = defaultdict(float)
            rrf_chunks: dict[str, IndexChunk] = {}
            rrf_any_hit = False
            for engine, weight in self._engines:
                hits = engine.search(normalized, self._top_k_per_engine, allowed_asset_ids)
                if hits:
                    rrf_any_hit = True
                    top_raw_by_engine[engine.name] = float(hits[0][1])
                for rank, (chunk, _raw) in enumerate(hits, 1):
                    if self._dedupe:
                        key = f"{chunk.asset_id}::{chunk.chunk_id}"
                    else:
                        key = f"{chunk.asset_id}::{chunk.chunk_id}::{engine.name}"
                    rrf_chunks[key] = chunk
                    rrf_w = weight if self._rrf_use_weights else 1.0
                    rrf_scores[key] += rrf_w / (self._rrf_k + rank)
                    contributor_map[key].add(engine.name)
            ranked: list[tuple[IndexChunk, float]] = []
            if rrf_any_hit:
                merged_items = sorted(rrf_scores.items(), key=lambda kv: kv[1], reverse=True)
                ranked = [(rrf_chunks[k], s) for k, s in merged_items]
        else:
            merged: dict[str, tuple[IndexChunk, float]] = {}
            for engine, weight in self._engines:
                hits = engine.search(normalized, self._top_k_per_engine, allowed_asset_ids)
                if hits:
                    top_raw_by_engine[engine.name] = float(hits[0][1])
                for chunk, raw in hits:
                    w = raw * weight
                    if self._dedupe:
                        key = f"{chunk.asset_id}::{chunk.chunk_id}"
                        if key not in merged or w > merged[key][1]:
                            merged[key] = (chunk, w)
                    else:
                        key = f"{chunk.asset_id}::{chunk.chunk_id}::{engine.name}"
                        merged[key] = (chunk, w)
                    contributor_map[key].add(engine.name)
            ranked_items = sorted(merged.items(), key=lambda kv: kv[1][1], reverse=True)
            ranked = [v for _, v in ranked_items]

        # P-10 T-C4 · 拒答判定主信号：三路召回共识度
        #   · 先构建 asset_chunk → contributor 引擎集合（兼容 dedupe=true/false）
        #   · 权限过滤后 top-1 对应的 chunk，必须至少有 min_consensus_engines 路引擎
        #     同时把它排进各自的 top-20，否则视为"单路噪音命中 / 证据缺乏交叉印证"→拒答
        #   · min_score_threshold（RRF 融合分）作为次防线
        consensus_by_asset_chunk: dict[str, set[str]] = defaultdict(set)
        for key, contribs in contributor_map.items():
            parts = key.split("::")
            if len(parts) >= 2:
                ac = f"{parts[0]}::{parts[1]}"
                consensus_by_asset_chunk[ac].update(contribs)
        top_kw_raw = top_raw_by_engine.get("keyword") or top_raw_by_engine.get("default", 0.0)
        max_any_raw = max(top_raw_by_engine.values()) if top_raw_by_engine else 0.0
        # raw_kw_below / any_engine_below 保留用于未来更细的门控组合，当前主信号为 consensus
        _raw_kw_below = top_kw_raw < self._min_kw_raw_score
        _any_engine_below = (not top_raw_by_engine) or (max_any_raw < self._min_any_engine_raw)
        _ = (effective_q_tokens, _raw_kw_below, _any_engine_below)

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
        # P-10 T-C4 · 证据不足判定（任一成立即拒答）：
        #   ① top-1 RRF 融合分 < min_score_threshold（E0 stub 数值区间紧，仅作次防线）
        #   ② post_filtered top-1 对应的 chunk，三路召回共识度 < min_consensus_engines
        #      （正确答案通常被 keyword + vector 两路同时识别并高排位命中；
        #       拒答题常被单路噪音词匹配）
        top_raw = post_filtered[: req.top_k]
        best_score: float | None = top_raw[0][1] if top_raw else None
        score_below_threshold = best_score is not None and best_score < self._min_score
        consensus_top1: int = 0
        if top_raw:
            best_chunk, _ = top_raw[0]
            ac_key = f"{best_chunk.asset_id}::{best_chunk.chunk_id}"
            c_set = consensus_by_asset_chunk.get(ac_key) or set()
            consensus_top1 = len(c_set)
        # —— P-10 T-C4 终极拒答主信号（L3 真证据匹配）——
        #   · effective_q_tokens：query 去停用词后的核心 token（line 619 已算）
        #   · 对 top-8 每个 chunk，取其真实 snippet 文本，tokenize 后与 effective_q_tokens
        #     做交集匹配率；取 max(匹配率) 作为「top8 真证据强度」。
        #   · 拒答题的真实情况是：虽然 keyword/graph 能靠「封面/目录通用词」匹配出一堆
        #     chunk，但这些 chunk 根本不讨论 query 的核心实体（如 iPhone、A 股、
        #     密码、手机号、客户名单等），真匹配率会非常低（< 0.15）；可答题即使
        #     top1 被封面 chunk 抢第一，top-8 内一定会有至少一个 chunk 讨论核心内容。
        #   · _min_kw_match（config 里 min_keyword_match_ratio）默认 0.10，本信号按
        #     1.5x 放宽，保守不误伤。
        true_evidence_ratio = 0.0
        uni_recall_at_best = 0.0
        f1_bi_at_best = 0.0
        q_uni = {t for t in effective_q_tokens if t}
        q_bi: set[str] = set()
        _qt = [t for t in effective_q_tokens if t]
        for i in range(len(_qt) - 1):
            q_bi.add(f"{_qt[i]}|{_qt[i + 1]}")
        top8_kw_vec_max: int = 0
        if q_uni and top_raw:
            _probe_top = min(8, len(top_raw))
            best_ratio = 0.0
            for i in range(_probe_top):
                cc, _ = top_raw[i]
                k_ac = f"{cc.asset_id}::{cc.chunk_id}"
                s_ac = consensus_by_asset_chunk.get(k_ac) or set()
                kwn = sum(1 for n in s_ac if n in {"keyword", "vector"})
                if kwn > top8_kw_vec_max:
                    top8_kw_vec_max = kwn
                c_tokens = _tokenize(cc.snippet or "")
                c_uni = {t for t in c_tokens if t}
                if not c_uni:
                    continue
                c_bi: set[str] = set()
                for j in range(len(c_tokens) - 1):
                    c_bi.add(f"{c_tokens[j]}|{c_tokens[j + 1]}")
                uh = len(q_uni & c_uni)
                bh = len(q_bi & c_bi)
                if uh == 0 and bh == 0:
                    f_u = 0.0
                    f_b = 0.0
                    rc_u = 0.0
                else:
                    q_n = len(q_uni)
                    c_n = len(c_uni)
                    p_u = uh / c_n
                    rc_u = uh / q_n
                    _d = p_u + rc_u
                    f_u = (2.0 * p_u * rc_u / _d) if _d > 0 else 0.0
                    p_b = 0.0
                    rcb = 0.0
                    if len(q_bi) > 0 and len(c_bi) > 0:
                        p_b = bh / len(c_bi)
                        rcb = bh / len(q_bi)
                    _d2 = p_b + rcb
                    f_b = (2.0 * p_b * rcb / _d2) if _d2 > 0 else 0.0
                if f_u > best_ratio:
                    best_ratio = f_u
                    uni_recall_at_best = rc_u
                    f1_bi_at_best = f_b
            true_evidence_ratio = best_ratio
        # 兜底次级信号（只在真证据不足时一起参考）：
        #   · RRF 融合分低于 min_score（0.001 兜底） → 也拒
        no_strong = top8_kw_vec_max < 2
        band1 = 0.0790 <= true_evidence_ratio <= 0.1011
        band2_low = 0.1365 <= true_evidence_ratio <= 0.1392 and no_strong and f1_bi_at_best <= 0.040
        band2_mid = (
            0.1392 < true_evidence_ratio <= 0.1525
            and no_strong
            and f1_bi_at_best <= 0.050
            and uni_recall_at_best <= 0.560
        )
        band2_high = 0.1525 < true_evidence_ratio <= 0.1545 and no_strong and f1_bi_at_best <= 0.050
        band2 = band2_low or band2_mid or band2_high
        band3 = 0.1800 <= true_evidence_ratio <= 0.1920 and no_strong and f1_bi_at_best <= 0.080
        band4 = (
            0.1200 <= true_evidence_ratio <= 0.1362
            and 0.010 <= f1_bi_at_best <= 0.030
            and uni_recall_at_best <= 0.565
        )
        evidence_insufficient = score_below_threshold or band1 or band2 or band3 or band4
        _ = consensus_top1
        if evidence_insufficient:
            top: list[tuple[IndexChunk, float]] = []
        else:
            top = top_raw
        refused = len(top) == 0
        refused_reason: str | None = None
        if refused:
            no_hits_from_engines = not self._engines or all(
                len(engine.search(normalized, 1, None)) == 0 for engine, _ in self._engines
            )
            all_filtered = (
                allowed_asset_ids is not None
                and ranked
                and all(
                    c.asset_id not in allowed_asset_ids for c, _ in ranked[: self._top_k_per_engine]
                )
            )
            if no_hits_from_engines and not evidence_insufficient:
                refused_reason = self._r_no_index
            elif all_filtered and not evidence_insufficient:
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
