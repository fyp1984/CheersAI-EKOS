"""P-07 100 题 CSV 题库 · 召回调优 T-C4 端到端评估

复用 P-10 26 题脚本的切分/索引/查询链路：
- 同一 24 份真实语料（4 根目录） → LocalDocConnector → 切 chunk → InMemory 索引
- 题目来源：P-07 CSV / ekos_seed_questions_v1.csv
- 口径：Recall@8 / MRR@8 / 拒答率（若 CSV 备注含“拒答”则 kind=refuse）
"""
# ruff: noqa: E402, E501

from __future__ import annotations

import csv
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from apps.worker.connectors import LocalDocConnector
from packages.retrieval.service import (
    Filters,
    IndexChunk,
    RetrievalService,
    RetrieveRequest,
    mrr_at_k,
    recall_at_k,
)

REAL_CORPUS_ROOTS: list[Path] = [
    Path(
        "/Users/FYP/Documents/@西北人的成长/10-Projects/职业项目/CheersAI-EKOS/01_需求/03_需求文档"
    ),
    Path("/Users/FYP/Documents/@西北人的成长/10-Projects/职业项目/CheersAI-EKOS/02_方案"),
    Path("/Users/FYP/Documents/@西北人的成长/10-Projects/职业项目/CheersAI-EKOS/03_实施/实施手册"),
    Path("/Users/FYP/Documents/@西北人的成长/10-Projects/职业项目/CheersAI-EKOS/03_实施/契约"),
]
P07_CSV = Path(
    "/Users/FYP/Documents/@西北人的成长/10-Projects/职业项目/CheersAI-EKOS/03_实施/分布式验证-E0/samples/ekos_seed_questions_v1.csv"
)
OUT_DIR = REPO_ROOT / "results" / "P-10-tc4"
ALLOWED_SUFFIX = {".md"}
EXCLUDE_DIR_NAMES = {"assets", ".git"}
TOP_K = 8
LANGUAGE_FROM_CONFIG: str | None = None

# 24 份真实语料 stem 固定表（扫 4 个根目录确认）——不写死行业词
CORPUS_STEMS: tuple[str, ...] = (
    "00-实施任务清单-内部-260904-v1.1",
    "01-环境与软件准备手册-内部-260904-v1.0",
    "01_EKOS技术方案-开源集成架构-v2.1-260830",
    "01_PRD-EKOS-v5.2-260830-01",
    "02-范围锚定与底座选型手册-内部-260904-v1.0",
    "02_EKOS产品定位声明-v1.0-260821",
    "02_EKOS研发里程碑路径-v1.0-260830",
    "03-开发实施手册-内部-260904-v1.0",
    "03_EKOS整体解决方案总览-v1.1-260907",
    "04-测试手册-内部-260904-v1.0",
    "04_EKOS业务方案-商业模式与定价-v1.0-260828",
    "05-质量管控与门禁手册-内部-260904-v1.0",
    "05_EKOS运营方案-v1.0-260828",
    "06-性能与容量手册-内部-260904-v1.0",
    "06_EKOS实施理念与精益创业主线-v1.0-260824",
    "07-安全与脱敏手册-内部-260904-v1.0",
    "07_EKOS知识库操作系统理论框架与维度对照表-v1.0-内部-260824",
    "08-部署与发布手册-内部-260904-v1.0",
    "08_Onyx开源底座可继承性技术探针-v1.0-内部-260829",
    "09-运维与维护手册-内部-260904-v1.0",
    "09_MVP开源产品调研-第二轮-v1.0-内部-260830",
    "10_EKOS企业级部署分发SOP-跨终端组网与固定寻址-v1.0-内部-260907",
    "11_EKOS外部依赖与制品统一目录规范-v1.0-内部-260910",
    "X1-X4-接口契约-内部-260906-v1.4",
)

# 证据来源常见关键词 → stem 别名映射（子串包含，不含 CJK 正则，TR-6 合规）
_EVIDENCE_KEYWORD_ALIASES: tuple[tuple[str, str], ...] = (
    ("产品定位声明", "02_EKOS产品定位声明-v1.0-260821"),
    ("业务方案", "04_EKOS业务方案-商业模式与定价-v1.0-260828"),
    ("整体解决方案", "03_EKOS整体解决方案总览-v1.1-260907"),
    ("MVP任务拆解", "02_EKOS研发里程碑路径-v1.0-260830"),
    ("研发里程碑", "02_EKOS研发里程碑路径-v1.0-260830"),
    ("里程碑路径", "02_EKOS研发里程碑路径-v1.0-260830"),
    ("PRD ", "01_PRD-EKOS-v5.2-260830-01"),
    ("§8.4", "01_PRD-EKOS-v5.2-260830-01"),
    ("§8.5", "01_PRD-EKOS-v5.2-260830-01"),
    ("§8.6", "01_PRD-EKOS-v5.2-260830-01"),
    ("准出①", "01_PRD-EKOS-v5.2-260830-01"),
    ("准出②", "01_PRD-EKOS-v5.2-260830-01"),
    ("准出⑨", "01_PRD-EKOS-v5.2-260830-01"),
    ("准出④", "01_PRD-EKOS-v5.2-260830-01"),
    ("准出⑤", "01_PRD-EKOS-v5.2-260830-01"),
    ("准出⑧", "01_PRD-EKOS-v5.2-260830-01"),
    ("准出十条", "01_PRD-EKOS-v5.2-260830-01"),
    ("技术方案", "01_EKOS技术方案-开源集成架构-v2.1-260830"),
    ("开源选型", "01_EKOS技术方案-开源集成架构-v2.1-260830"),
    ("集成架构", "01_EKOS技术方案-开源集成架构-v2.1-260830"),
    ("Onyx", "08_Onyx开源底座可继承性技术探针-v1.0-内部-260829"),
    ("开源底座", "08_Onyx开源底座可继承性技术探针-v1.0-内部-260829"),
    ("探针", "08_Onyx开源底座可继承性技术探针-v1.0-内部-260829"),
    ("外部依赖", "11_EKOS外部依赖与制品统一目录规范-v1.0-内部-260910"),
    ("目录规范", "11_EKOS外部依赖与制品统一目录规范-v1.0-内部-260910"),
    ("SOP", "10_EKOS企业级部署分发SOP-跨终端组网与固定寻址-v1.0-内部-260907"),
    ("企业级部署", "10_EKOS企业级部署分发SOP-跨终端组网与固定寻址-v1.0-内部-260907"),
    ("部署分发", "10_EKOS企业级部署分发SOP-跨终端组网与固定寻址-v1.0-内部-260907"),
    ("实施理念", "06_EKOS实施理念与精益创业主线-v1.0-260824"),
    ("精益创业", "06_EKOS实施理念与精益创业主线-v1.0-260824"),
    ("实施手册", "03-开发实施手册-内部-260904-v1.0"),
    ("开发实施", "03-开发实施手册-内部-260904-v1.0"),
    ("测试手册", "04-测试手册-内部-260904-v1.0"),
    ("T4.07", "04-测试手册-内部-260904-v1.0"),
    ("T4.", "04-测试手册-内部-260904-v1.0"),
    ("T5.", "04-测试手册-内部-260904-v1.0"),
    ("质量管控", "05-质量管控与门禁手册-内部-260904-v1.0"),
    ("QA-1b", "05-质量管控与门禁手册-内部-260904-v1.0"),
    ("QA-1", "05-质量管控与门禁手册-内部-260904-v1.0"),
    ("EVAL", "05-质量管控与门禁手册-内部-260904-v1.0"),
    ("Q1", "00-实施任务清单-内部-260904-v1.1"),
    ("任务清单", "00-实施任务清单-内部-260904-v1.1"),
    ("范围锚定", "02-范围锚定与底座选型手册-内部-260904-v1.0"),
    ("底座选型", "02-范围锚定与底座选型手册-内部-260904-v1.0"),
    ("安全", "07-安全与脱敏手册-内部-260904-v1.0"),
    ("TR-8", "07-安全与脱敏手册-内部-260904-v1.0"),
    ("脱敏", "07-安全与脱敏手册-内部-260904-v1.0"),
    ("部署与发布", "08-部署与发布手册-内部-260904-v1.0"),
    ("部署发布", "08-部署与发布手册-内部-260904-v1.0"),
    ("环境与软件", "01-环境与软件准备手册-内部-260904-v1.0"),
    ("运维", "09-运维与维护手册-内部-260904-v1.0"),
    ("维护", "09-运维与维护手册-内部-260904-v1.0"),
    ("性能", "06-性能与容量手册-内部-260904-v1.0"),
    ("容量", "06-性能与容量手册-内部-260904-v1.0"),
    ("接口契约", "X1-X4-接口契约-内部-260906-v1.4"),
    ("X1-", "X1-X4-接口契约-内部-260906-v1.4"),
    ("oRPC", "X1-X4-接口契约-内部-260906-v1.4"),
    ("gate_baseline", "02-范围锚定与底座选型手册-内部-260904-v1.0"),
    ("gate Q3", "05-质量管控与门禁手册-内部-260904-v1.0"),
    ("gate Q4", "05-质量管控与门禁手册-内部-260904-v1.0"),
    ("gate ", "05-质量管控与门禁手册-内部-260904-v1.0"),
    ("MVP", "02_EKOS研发里程碑路径-v1.0-260830"),
    ("知识库操作系统理论", "07_EKOS知识库操作系统理论框架与维度对照表-v1.0-内部-260824"),
    ("理论框架", "07_EKOS知识库操作系统理论框架与维度对照表-v1.0-内部-260824"),
    ("维度对照", "07_EKOS知识库操作系统理论框架与维度对照表-v1.0-内部-260824"),
    ("MVP开源产品调研", "09_MVP开源产品调研-第二轮-v1.0-内部-260830"),
    ("开源产品调研", "09_MVP开源产品调研-第二轮-v1.0-内部-260830"),
    ("运营方案", "05_EKOS运营方案-v1.0-260828"),
)


def _load_chunk_cfg() -> dict[str, Any]:
    cfg_path = REPO_ROOT / "configs" / "chunking.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    runtime_cfg = (
        yaml.safe_load((REPO_ROOT / "configs" / "runtime.yaml").read_text(encoding="utf-8")) or {}
    )
    default_lang = runtime_cfg.get("default_language", "zh")
    langs = (cfg.get("languages") or {}) or {}
    lang_block = langs.get(default_lang) or langs.get("zh") or {}
    if not lang_block:
        lang_block = cfg.get("default") or {}
    global LANGUAGE_FROM_CONFIG  # noqa: PLW0603
    LANGUAGE_FROM_CONFIG = default_lang
    return {
        "max_chars": int(lang_block.get("max_chars", 800)),
        "overlap_chars": int(lang_block.get("overlap_chars", 80)),
        "min_chars": int(lang_block.get("min_chars", 40)),
        "separators": list(lang_block.get("separators") or []),
        "language": default_lang,
    }


def _split_markdown(
    text: str, *, max_chars: int, overlap_chars: int, min_chars: int, separators: list[str]
) -> list[tuple[str, int]]:
    if not text:
        return []
    n = len(text)
    out: list[tuple[str, int]] = []
    step = max(1, max_chars - overlap_chars)
    i = 0
    while i < n:
        end = min(n, i + max_chars)
        if separators and end < n:
            window = text[i:end]
            best = -1
            for sep in separators:
                pos = window.rfind(sep)
                if pos > best:
                    best = pos
            if best >= min_chars:
                end = i + best + len(separators[0])
        piece = text[i:end].rstrip()
        if len(piece) >= min_chars:
            out.append((piece, i))
        if end >= n:
            break
        i = i + step
    if not out and len(text) >= 1:
        out.append((text, 0))
    return out


def _evidence_to_expected_stems(evidence_text: str) -> set[str]:
    """把 CSV「证据来源」单元格 → expected_asset_stems 集合。"""
    if not evidence_text:
        return set()
    stems: set[str] = set()
    et = evidence_text
    # 1) Q 数字缩写：regex 英文数字边界，TR-6合规（CJK 不走 regex）
    q_shortcuts = (
        (r"\bQ1\b", "00-实施任务清单-内部-260904-v1.1"),
        (r"\bQ2\b", "02-范围锚定与底座选型手册-内部-260904-v1.0"),
        (r"\bQ3\b", "01-环境与软件准备手册-内部-260904-v1.0"),
        (r"\bQ4\b", "05-质量管控与门禁手册-内部-260904-v1.0"),
        (r"\bQ5\b", "10_EKOS企业级部署分发SOP-跨终端组网与固定寻址-v1.0-内部-260907"),
    )
    for pat, target in q_shortcuts:
        if re.search(pat, et) and target in CORPUS_STEMS:
            stems.add(target)
    # 2) 子串别名匹配（全部走字符串包含，CJK不出现在re模式中，TR-6合规）
    qa_aliases = ("QA", "准出", "召回率", "拒答率", "质量", "MRR", "Recall")
    if any(k.lower() in et.lower() for k in qa_aliases):
        stems.add("05-质量管控与门禁手册-内部-260904-v1.0")
    deploy_aliases = ("DEPLOY", "部署要求", "私有化部署", "轻量私有化", "NFR-DEPLOY")
    if any(k.lower() in et.lower() for k in deploy_aliases):
        stems.add("10_EKOS企业级部署分发SOP-跨终端组网与固定寻址-v1.0-内部-260907")
    for kw, target in _EVIDENCE_KEYWORD_ALIASES:
        if kw in et and target in CORPUS_STEMS:
            stems.add(target)
    # 3) 直接包含 stem 或 stem 去版本前缀后仍匹配（子串
    for s in CORPUS_STEMS:
        if s in et:
            stems.add(s)
            continue
        core = s.split("-v1", 1)[0].split("-v2", 1)[0].split("_v2", 1)[0].split("_v1", 1)[0]
        if len(core) >= 5 and core in et:
            stems.add(s)
    return stems


_DIFFICULTY_MAP = {"易": "easy", "中": "medium", "难": "hard", "—": "medium", "": "medium"}


def _load_csv_qb() -> list[dict[str, Any]]:
    rows_out: list[dict[str, Any]] = []
    with P07_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            qid = (row.get("编号") or "").strip()
            query = (row.get("题目") or "").strip()
            if not qid or not query:
                continue
            evidence = (row.get("证据来源") or "").strip()
            diff_raw = (row.get("难度") or "").strip()
            difficulty = _DIFFICULTY_MAP.get(diff_raw, "medium")
            cat = (row.get("分类") or "").strip()
            note = (row.get("备注") or "") + cat
            kind = (
                "refuse"
                if (cat == "拒答" or "拒答" in note or "无答案" in note or "真无" in note)
                else "answerable"
            )
            stems = _evidence_to_expected_stems(evidence) if kind == "answerable" else set()
            rows_out.append(
                {
                    "qid": qid,
                    "query": query,
                    "kind": kind,
                    "difficulty": difficulty,
                    "expected_asset_stems": sorted(stems),
                    "expected_points": row.get("期望答案要点") or "",
                    "evidence_source_raw": evidence,
                }
            )
    return rows_out


def run() -> dict[str, Any]:
    t0 = time.time()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    chunk_cfg = _load_chunk_cfg()
    assert LANGUAGE_FROM_CONFIG is not None

    manifest_records: list[dict[str, Any]] = []
    for root in REAL_CORPUS_ROOTS:
        if not root.is_dir():
            print(f"[WARN] 语料目录不存在，跳过：{root}")
            continue
        connector = LocalDocConnector(root)
        h = connector.health()
        print(f"[INFO] LocalDocConnector health={h['status']} root={h['root']}")
        recs = connector.execute(trace_id=f"P07-SEED-{root.name}")
        for r in recs:
            rel = r["source_uri"].replace("local://", "")
            rel_parts = rel.replace("\\", "/").split("/")
            if any(part in EXCLUDE_DIR_NAMES for part in rel_parts):
                continue
            title = r.get("title", "") or ""
            if not any(title.endswith(suf) for suf in ALLOWED_SUFFIX):
                continue
            abs_path = root / Path(rel)
            r["_abs_path"] = abs_path
            r["_asset_stem"] = abs_path.stem
            r["_rel_root"] = str(root)
            manifest_records.append(r)
    print(f"[INFO] 扫描到候选文档 {len(manifest_records)} 份（已过滤非 md/assets）")

    chunks: list[IndexChunk] = []
    doc_manifest: list[dict[str, Any]] = []
    for r in manifest_records:
        path: Path = r["_abs_path"]
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"[WARN] 读取失败（跳过）：{path} | {exc}")
            continue
        pieces = _split_markdown(
            raw,
            max_chars=chunk_cfg["max_chars"],
            overlap_chars=chunk_cfg["overlap_chars"],
            min_chars=chunk_cfg["min_chars"],
            separators=chunk_cfg["separators"],
        )
        asset_id = r["_asset_stem"]
        for i, (piece, offset) in enumerate(pieces):
            chunk_id = f"{asset_id}-chunk-{i:04d}"
            evidence = f"source={asset_id};chunk_idx={i};char_offset={offset};path={path.name}"
            chunks.append(
                IndexChunk(
                    asset_id=asset_id,
                    chunk_id=chunk_id,
                    language=chunk_cfg["language"],
                    snippet=piece,
                    evidence=evidence,
                )
            )
        doc_manifest.append(
            {
                "asset_id": asset_id,
                "doc_id": r["doc_id"],
                "source_uri": r["source_uri"],
                "abs_path": str(path),
                "title": r["title"],
                "n_chunks": len(pieces),
                "n_chars": len(raw),
            }
        )
    print(f"[INFO] 真实语料 {len(doc_manifest)} 份 → {len(chunks)} 个 chunk")

    svc = RetrievalService(policy=None)
    svc.ingest(iter(chunks))
    print("[INFO] Ingest 完成：三路引擎（keyword/vector/graph）均已写入")

    questions = _load_csv_qb()
    print(f"[INFO] P-07 CSV 题库 {len(questions)} 题")

    rows: list[dict[str, Any]] = []
    answerable_recalls: list[float] = []
    answerable_mrrs: list[float] = []
    refused_correct = 0
    refused_total = 0
    refused_wrong_answered = 0
    answerable_refused = 0
    answerable_zero_hits = 0
    missing_expected_stems = 0

    for q in questions:
        qid = q["qid"]
        query = q["query"]
        kind = q.get("kind", "answerable")
        expected_stems = set(q.get("expected_asset_stems") or [])
        if kind == "answerable" and not expected_stems:
            missing_expected_stems += 1
        req = RetrieveRequest(
            request_id=f"P10-P07-{qid}",
            query=query,
            top_k=TOP_K,
            trace_id=f"P07-SEED-{qid}",
            filters=Filters(),
        )
        res = svc.retrieve(req)
        ranked_doc_ids = [h.asset_id for h in res.hits]

        if kind == "refuse":
            refused_total += 1
            refused = (res.refused is True) or (len(res.hits) == 0)
            if refused:
                refused_correct += 1
            else:
                refused_wrong_answered += 1
            rows.append(
                {
                    "qid": qid,
                    "kind": "refuse",
                    "query": query,
                    "expected": [],
                    "hits": ranked_doc_ids,
                    "refused": bool(res.refused),
                    "refused_reason": res.refused_reason,
                    "refuse_correct": refused,
                    "recall_at_8": None,
                    "mrr_at_8": None,
                }
            )
        else:
            r_val = recall_at_k(expected_stems, ranked_doc_ids, TOP_K) if expected_stems else 0.0
            m_val = mrr_at_k(expected_stems, ranked_doc_ids, TOP_K) if expected_stems else 0.0
            if expected_stems:
                answerable_recalls.append(r_val)
                answerable_mrrs.append(m_val)
            if res.refused is True:
                answerable_refused += 1
            elif len(res.hits) == 0:
                answerable_zero_hits += 1
            rows.append(
                {
                    "qid": qid,
                    "kind": kind,
                    "difficulty": q.get("difficulty"),
                    "query": query,
                    "expected": sorted(expected_stems),
                    "evidence_source_raw": q.get("evidence_source_raw", ""),
                    "hits": ranked_doc_ids,
                    "hit_snippets": [
                        {"asset_id": h.asset_id, "score": round(h.score, 4), "evidence": h.evidence}
                        for h in res.hits
                    ],
                    "refused": bool(res.refused),
                    "refused_reason": res.refused_reason,
                    "recall_at_8": round(r_val, 4),
                    "mrr_at_8": round(m_val, 4),
                    "expected_stems_mapped": bool(expected_stems),
                }
            )

    n_answerable = len(answerable_recalls)
    avg_recall = round(sum(answerable_recalls) / n_answerable, 4) if n_answerable else 0.0
    avg_mrr = round(sum(answerable_mrrs) / n_answerable, 4) if n_answerable else 0.0
    refusal_rate = round(refused_correct / refused_total, 4) if refused_total else 0.0
    error_budget = {
        "answerable_refused": answerable_refused,
        "answerable_zero_hits": answerable_zero_hits,
        "refused_wrong_answered": refused_wrong_answered,
        "missing_expected_stems": missing_expected_stems,
    }

    mid_rows = [
        r for r in rows if r["kind"] != "refuse" and r.get("difficulty") in {"easy", "medium"}
    ]
    if mid_rows:
        r_list = [r["recall_at_8"] for r in mid_rows if isinstance(r["recall_at_8"], float)]
        m_list = [r["mrr_at_8"] for r in mid_rows if isinstance(r["mrr_at_8"], float)]
        mid_recall = round(sum(r_list) / len(r_list), 4) if r_list else 0.0
        mid_mrr = round(sum(m_list) / len(m_list), 4) if m_list else 0.0
    else:
        mid_recall = 0.0
        mid_mrr = 0.0

    result: dict[str, Any] = {
        "run_id": f"P10-TC4-P07-100Q-{int(time.time())}",
        "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note": "P-10 T-C4 召回调优（RRF+停用词+拒答阈值）：P-07 100 题 CSV 题库实测",
        "env": {
            "python": sys.version.split()[0],
            "uv_lock": (REPO_ROOT / "uv.lock").is_file(),
            "chunk_cfg": chunk_cfg,
            "corpus_roots": [str(p) for p in REAL_CORPUS_ROOTS],
            "csv_source": str(P07_CSV),
        },
        "corpus": {
            "n_docs": len(doc_manifest),
            "n_chunks": len(chunks),
            "docs": doc_manifest,
        },
        "question_bank": {
            "source": str(P07_CSV),
            "n_total": len(questions),
            "n_answerable": sum(1 for q in questions if q["kind"] != "refuse"),
            "n_refuse": sum(1 for q in questions if q["kind"] == "refuse"),
        },
        "metrics": {
            "recall_at_8": avg_recall,
            "mrr_at_8": avg_mrr,
            "mid_recall_at_8": mid_recall,
            "mid_mrr_at_8": mid_mrr,
            "refuse_rate": refusal_rate,
            "pass_recall_ge_085": avg_recall >= 0.85,
            "pass_mid_recall_ge_090": mid_recall >= 0.90,
            "pass_mid_mrr_ge_080": mid_mrr >= 0.80,
            "error_budget": error_budget,
        },
        "per_question": rows,
        "elapsed_sec": round(time.time() - t0, 3),
    }

    metrics_path = OUT_DIR / "p10_tc4_recall_p07_100q_metrics_v1.json"
    report_path = OUT_DIR / "p10_tc4_recall_p07_100q_report_v1.md"
    with metrics_path.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    lines: list[str] = []
    lines.append("# P-10 T-C4 召回调优 · P-07 100 题 CSV 实测报告 v1\n")
    lines.append(f"- run_id: {result['run_id']}")
    lines.append(f"- run_at: {result['run_at']}")
    lines.append(
        f"- 语料: {result['corpus']['n_docs']} docs / {result['corpus']['n_chunks']} chunks"
    )
    lines.append(
        f"- 题库: {result['question_bank']['n_total']} 题（"
        f"answerable {result['question_bank']['n_answerable']} / "
        f"refuse {result['question_bank']['n_refuse']}）\n"
    )
    lines.append("## 核心指标\n")
    m = result["metrics"]
    lines.append(f"- Recall@8: **{m['recall_at_8']:.4f}**（≥0.85 PASS={m['pass_recall_ge_085']}）")
    lines.append(f"- MRR@8: **{m['mrr_at_8']:.4f}**")
    lines.append(
        f"- 中→中 Recall@8: **{m['mid_recall_at_8']:.4f}**（≥0.90 PASS={m['pass_mid_recall_ge_090']}）"
    )
    lines.append(
        f"- 中→中 MRR@8: **{m['mid_mrr_at_8']:.4f}**（≥0.80 PASS={m['pass_mid_mrr_ge_080']}）"
    )
    lines.append(
        f"- 拒答率: **{m['refuse_rate']:.4f}**（{refused_correct}/{refused_total} 正确拒答）"
    )
    lines.append(f"- 错误预算: {json.dumps(error_budget, ensure_ascii=False)}\n")
    lines.append("## 逐题明细（节选 Top10 recall=0 或 MRR<0.3）\n")
    bad = [
        r
        for r in rows
        if r["kind"] != "refuse" and (r["recall_at_8"] == 0.0 or (r["mrr_at_8"] or 0.0) < 0.3)
    ]
    if not bad:
        lines.append("_无低分项（所有 answerable 题 recall>0 且 MRR≥0.3）_\n")
    for r in bad[:20]:
        lines.append(
            f"- {r['qid']}（{r.get('difficulty', '-')}）: recall={r['recall_at_8']}, mrr={r['mrr_at_8']}  \n"
        )
        lines.append(f"  Query: {r['query'][:70]}{'…' if len(r['query']) > 70 else ''}  \n")
        lines.append(f"  Expected: {', '.join(r['expected']) if r['expected'] else '(空映射)'}  \n")
        lines.append(f"  Actual Top: {', '.join(r['hits'][:3])}  \n")
    lines.append(f"\n总耗时: {result['elapsed_sec']:.1f} s\n")
    report_path.write_text("\n".join(lines), encoding="utf-8")

    print(
        "\n======================================================================"
        "\n  P-10 T-C4 召回调优 · P-07 100 题 CSV 实测核心指标"
        "\n======================================================================"
    )
    print(f"  真实语料份数     : {result['corpus']['n_docs']}")
    print(f"  Chunk 总数        : {result['corpus']['n_chunks']}")
    print(
        f"  题库总题数       : {len(questions)}（answerable {result['question_bank']['n_answerable']} / "
        f"refuse {result['question_bank']['n_refuse']}）"
    )
    print(
        f"  Recall@8         : {m['recall_at_8']:.4f}   （≥0.85  PASS={m['pass_recall_ge_085']}）"
    )
    print(f"  MRR@8            : {m['mrr_at_8']:.4f}")
    print(
        f"  中→中组 Recall@8 : {m['mid_recall_at_8']:.4f}   （≥0.90  PASS={m['pass_mid_recall_ge_090']}）"
    )
    print(
        f"  中→中组 MRR@8    : {m['mid_mrr_at_8']:.4f}   （≥0.80  PASS={m['pass_mid_mrr_ge_080']}）"
    )
    print(f"  拒答率           : {m['refuse_rate']:.4f}（{refused_correct}/{refused_total}）")
    print(f"  未映射 expected  : {missing_expected_stems} 道（evidence_source 未匹配任何 stem）")
    print(
        f"  未达标(误/漏)    : answerable 被拒答 {answerable_refused} / refuse 被误召回 {refused_wrong_answered}"
    )
    print(f"  总耗时           : {result['elapsed_sec']:.3f} s")
    print(f"  结果落位(代码仓) : {OUT_DIR}")
    print("======================================================================\n")
    return result


if __name__ == "__main__":
    run()
