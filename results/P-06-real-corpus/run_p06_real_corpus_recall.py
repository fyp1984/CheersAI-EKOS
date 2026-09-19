"""P-06 准出②独立语料召回实测 · 端到端脚本

真实语料（24 份，非合成）→ LocalDocConnector 扫描 → 切 chunk →
灌入 InMemory 索引 → 独立题库（tests/golden/p06_real_corpus_question_bank_v1.json）
查询 → Recall@8 / MRR@8 / 拒答率 如实记录。

零行业词、零硬编码语种、零硬编码端口（TR-1/6/7）。
语种配置通过 configs/runtime.yaml 和 configs/chunking.yaml 读取，不写 DEFAULT_LANGUAGE。
"""
# ruff: noqa: E402, E501

from __future__ import annotations

import json
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

# ----------------------------- 语料根目录（按任务 4 条路径）-----------------------
REAL_CORPUS_ROOTS: list[Path] = [
    Path(
        "/Users/FYP/Documents/@西北人的成长/10-Projects/职业项目/CheersAI-EKOS/01_需求/03_需求文档"
    ),
    Path("/Users/FYP/Documents/@西北人的成长/10-Projects/职业项目/CheersAI-EKOS/02_方案"),
    Path("/Users/FYP/Documents/@西北人的成长/10-Projects/职业项目/CheersAI-EKOS/03_实施/实施手册"),
    Path("/Users/FYP/Documents/@西北人的成长/10-Projects/职业项目/CheersAI-EKOS/03_实施/契约"),
]

QB_PATH = REPO_ROOT / "tests" / "golden" / "p06_real_corpus_question_bank_v1.json"
OUT_DIR = REPO_ROOT / "results" / "P-06-real-corpus"
PLAN_OUT_DIR = Path(
    "/Users/FYP/Documents/@西北人的成长/10-Projects/职业项目/CheersAI-EKOS/03_实施/分布式验证-E0/results"
)
ALLOWED_SUFFIX = {".md"}
EXCLUDE_DIR_NAMES = {"assets", ".git"}
TOP_K = 8
LANGUAGE_FROM_CONFIG: str | None = None


# ----------------------------- 辅助：加载切分配置（来自 configs，零写死）----------
def _load_chunk_cfg() -> dict[str, Any]:
    """从 configs/chunking.yaml 读 zh（或 default）档，TR-6：不在代码里写中文切分阈值。"""
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


# ----------------------------- 辅助：切分 Markdown（粗粒度，保证覆盖率）------------
def _split_markdown(
    text: str, *, max_chars: int, overlap_chars: int, min_chars: int, separators: list[str]
) -> list[tuple[str, int]]:
    """按字符数 + 分隔符切分，返回 (chunk_text, start_char_offset) 列表。

    不写死 zh 正则（TR-6）：分隔符列表从配置层注入，空列表 fallback 到纯字符数。
    """
    if not text:
        return []
    n = len(text)
    out: list[tuple[str, int]] = []
    step = max(1, max_chars - overlap_chars)
    i = 0
    idx = 0
    while i < n:
        end = min(n, i + max_chars)
        # 尝试在分隔符上断开（若提供了分隔符），避免从句子中间切开
        if separators and end < n:
            window = text[i:end]
            best = -1
            for sep in separators:
                pos = window.rfind(sep)
                if pos > best:
                    best = pos
            if best >= min_chars:
                end = i + best + len(separators[0])  # 包含分隔符本身
        piece = text[i:end].rstrip()
        if len(piece) >= min_chars:
            out.append((piece, i))
            idx += 1
        if end >= n:
            break
        i = i + step
    if not out and len(text) >= 1:
        out.append((text, 0))
    return out


# ----------------------------- 主流程 ---------------------------------------------
def run() -> dict[str, Any]:
    t0 = time.time()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    chunk_cfg = _load_chunk_cfg()
    assert LANGUAGE_FROM_CONFIG is not None

    # 1) LocalDocConnector 扫描四个目录（T-B1 连接器复用，P-02 同源）
    manifest_records: list[dict[str, Any]] = []
    for root in REAL_CORPUS_ROOTS:
        if not root.is_dir():
            print(f"[WARN] 语料目录不存在，跳过：{root}")
            continue
        connector = LocalDocConnector(root)
        h = connector.health()
        print(f"[INFO] LocalDocConnector health={h['status']} root={h['root']}")
        recs = connector.execute(trace_id=f"P06-REAL-{root.name}")
        # 过滤：只留 .md + 排除 assets/.git 等目录
        for r in recs:
            rel = r["source_uri"].replace("local://", "")
            rel_parts = rel.replace("\\", "/").split("/")
            if any(part in EXCLUDE_DIR_NAMES for part in rel_parts):
                continue
            title = r.get("title", "") or ""
            if not any(title.endswith(suf) for suf in ALLOWED_SUFFIX):
                continue
            # 补绝对路径，用于后续 read_text
            abs_path = root / Path(rel)
            r["_abs_path"] = abs_path
            r["_asset_stem"] = abs_path.stem
            r["_rel_root"] = str(root)
            manifest_records.append(r)

    print(f"[INFO] 扫描到候选文档 {len(manifest_records)} 份（已过滤非 md/assets）")

    # 2) 读文件 → 切 chunk → 构造 IndexChunk（灌入 InMemory 索引）
    chunks: list[IndexChunk] = []
    doc_manifest: list[dict[str, Any]] = []
    for r in manifest_records:
        path: Path = r["_abs_path"]
        try:
            raw = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            print(f"[WARN] 读取失败（跳过）：{path} | {exc}")
            continue
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

    # 3) 构建 RetrievalService（policy=None：纯索引场景，跳过权限过滤，单用户本机）
    svc = RetrievalService(policy=None)
    svc.ingest(iter(chunks))
    print("[INFO] Ingest 完成：三路引擎（keyword/vector/graph）均已写入")

    # 4) 加载独立题库
    qb = json.loads(QB_PATH.read_text(encoding="utf-8"))
    questions = qb.get("questions") or []
    print(f"[INFO] 独立题库 {len(questions)} 题")

    # 5) 跑查询 + 指标
    rows: list[dict[str, Any]] = []
    answerable_recalls: list[float] = []
    answerable_mrrs: list[float] = []
    refused_correct = 0
    refused_total = 0
    refused_wrong_answered = 0  # refuse 题却召回了（误召回）
    answerable_refused = 0  # answerable 题却 refused（漏召回）

    for q in questions:
        qid = q["qid"]
        query = q["query"]
        kind = q.get("kind", "answerable")
        expected_stems = set(q.get("expected_asset_stems") or [])
        req = RetrieveRequest(
            request_id=f"P06-{qid}",
            query=query,
            top_k=TOP_K,
            trace_id=f"P06-REAL-{qid}",
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
            r_val = recall_at_k(expected_stems, ranked_doc_ids, TOP_K)
            m_val = mrr_at_k(expected_stems, ranked_doc_ids, TOP_K)
            answerable_recalls.append(r_val)
            answerable_mrrs.append(m_val)
            if (res.refused is True) or (len(res.hits) == 0):
                answerable_refused += 1
            rows.append(
                {
                    "qid": qid,
                    "kind": kind,
                    "difficulty": q.get("difficulty"),
                    "query": query,
                    "expected": sorted(expected_stems),
                    "hits": ranked_doc_ids,
                    "hit_snippets": [
                        {"asset_id": h.asset_id, "score": h.score, "evidence": h.evidence}
                        for h in res.hits
                    ],
                    "refused": bool(res.refused),
                    "refused_reason": res.refused_reason,
                    "recall_at_8": round(r_val, 4),
                    "mrr_at_8": round(m_val, 4),
                }
            )

    n_answerable = len(answerable_recalls)
    avg_recall = round(sum(answerable_recalls) / n_answerable, 4) if n_answerable else 0.0
    avg_mrr = round(sum(answerable_mrrs) / n_answerable, 4) if n_answerable else 0.0
    refusal_rate = round(refused_correct / refused_total, 4) if refused_total else 0.0
    error_budget = {
        "answerable_refused": answerable_refused,
        "refused_wrong_answered": refused_wrong_answered,
    }

    # 中→中组（按难度 easy/medium 聚合）
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
        "run_id": f"P06-REAL-{int(time.time())}",
        "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note": "P-06 准出②独立语料召回实测：语料与题库不同源，题目自然提问不抄原词",
        "env": {
            "python": sys.version.split()[0],
            "uv_lock": (REPO_ROOT / "uv.lock").is_file(),
            "chunk_cfg": chunk_cfg,
            "corpus_roots": [str(p) for p in REAL_CORPUS_ROOTS],
        },
        "corpus": {
            "n_docs": len(doc_manifest),
            "n_chunks": len(chunks),
            "docs": doc_manifest,
        },
        "question_bank": {
            "bank_id": qb.get("bank_id"),
            "n_questions": len(questions),
            "n_answerable": n_answerable,
            "n_refuse": refused_total,
            "refuse_threshold_required": qb.get("refuse_threshold", 5),
        },
        "metrics": {
            "avg_recall_at_8": avg_recall,
            "avg_mrr_at_8": avg_mrr,
            "mid_group_recall_at_8": mid_recall,
            "mid_group_mrr_at_8": mid_mrr,
            "refuse_rate": refusal_rate,
            "synthetic_baseline": {
                "avg_recall_at_8": 1.0,
                "avg_mrr_at_8": 1.0,
                "note": "P-06 同源自测（语料与题库同文档）——本任务为补不作弊的真证据，低于该值属正常",
            },
            "gap_to_synthetic": {
                "recall": round(1.0 - avg_recall, 4),
                "mrr": round(1.0 - avg_mrr, 4),
            },
            "errors": error_budget,
        },
        "criteria": {
            "required_recall_ge": 0.85,
            "required_mid_recall_ge": 0.90,
            "required_mid_mrr_ge": 0.80,
            "recall_pass": avg_recall >= 0.85,
            "mid_recall_pass": mid_recall >= 0.90,
            "mid_mrr_pass": mid_mrr >= 0.80,
        },
        "rows": rows,
        "elapsed_sec": round(time.time() - t0, 3),
    }

    # 6) 写入 JSON + Markdown
    json_out = OUT_DIR / "p06_real_corpus_recall_metrics_v1.json"
    md_out = OUT_DIR / "p06_real_corpus_recall_report_v1.md"
    json_out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    md_out.write_text(_render_md(result), encoding="utf-8")

    # 7) 同步到规划仓指定目录
    try:
        PLAN_OUT_DIR.mkdir(parents=True, exist_ok=True)
        (PLAN_OUT_DIR / "p06_real_corpus_recall_metrics_v1.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (PLAN_OUT_DIR / "p06_real_corpus_recall_report_v1.md").write_text(
            _render_md(result), encoding="utf-8"
        )
        print(f"[INFO] 已同步至规划仓：{PLAN_OUT_DIR}")
    except OSError as exc:
        print(f"[WARN] 同步规划仓失败（不影响本任务主结果）：{exc}")

    print()
    print("=" * 70)
    print("  P-06 准出②独立语料召回实测 · 核心指标")
    print("=" * 70)
    print(f"  真实语料份数     : {len(doc_manifest)}")
    print(f"  Chunk 总数        : {len(chunks)}")
    print(
        f"  题库总题数       : {len(questions)}（answerable {n_answerable} / refuse {refused_total}）"
    )
    print(
        f"  Recall@8         : {avg_recall:.4f}   （≥0.85  PASS={result['criteria']['recall_pass']}）"
    )
    print(f"  MRR@8            : {avg_mrr:.4f}")
    print(
        f"  中→中组 Recall@8 : {mid_recall:.4f}   （≥0.90  PASS={result['criteria']['mid_recall_pass']}）"
    )
    print(
        f"  中→中组 MRR@8    : {mid_mrr:.4f}   （≥0.80  PASS={result['criteria']['mid_mrr_pass']}）"
    )
    print(
        f"  拒答率           : {refusal_rate:.4f}（拒答题 {refused_correct}/{refused_total} 正确拒答）"
    )
    print(
        f"  合成集差距       : Recall gap {result['metrics']['gap_to_synthetic']['recall']} / MRR gap {result['metrics']['gap_to_synthetic']['mrr']}"
    )
    print(
        f"  未达标(误/漏)    : answerable 被拒答 {answerable_refused} / refuse 被误召回 {refused_wrong_answered}"
    )
    print(f"  总耗时           : {result['elapsed_sec']} s")
    print(f"  结果落位(代码仓) : {OUT_DIR}")
    print(f"  结果落位(规划仓) : {PLAN_OUT_DIR}")
    print("=" * 70)

    # 屏幕回执短版（任务要求的 5 条）
    print()
    print("--- 回执 ---")
    print(f"  real_docs       = {len(doc_manifest)}")
    print(f"  bank_questions  = {len(questions)}（ans={n_answerable}, refuse={refused_total}）")
    print(
        f"  Recall@8={avg_recall}, MRR@8={avg_mrr}, mid Recall@8={mid_recall}, mid MRR@8={mid_mrr}"
    )
    print(
        f"  vs-synthetic    : recall_gap={result['metrics']['gap_to_synthetic']['recall']}, mrr_gap={result['metrics']['gap_to_synthetic']['mrr']}"
    )
    print(f"  refuse_rate     = {refusal_rate}")

    return result


def _render_md(r: dict[str, Any]) -> str:
    m = r["metrics"]
    c = r["criteria"]
    qb = r["question_bank"]
    cp = r["corpus"]
    lines: list[str] = []
    lines.append("# P-06 准出②独立语料召回实测报告")
    lines.append("")
    lines.append(f"- 运行 ID：`{r['run_id']}`")
    lines.append(f"- 运行时间：{r['run_at']}")
    lines.append(f"- 说明：{r['note']}")
    lines.append("")
    lines.append("## 一、语料与题库")
    lines.append("")
    lines.append("| 项目 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| 真实语料份数 | {cp['n_docs']}（⛔ 非合成）|")
    lines.append(f"| Chunk 总数 | {cp['n_chunks']} |")
    lines.append(f"| 题库 ID | {qb['bank_id']} |")
    lines.append(f"| 题库总题数 | {qb['n_questions']} |")
    lines.append(f"| - answerable（可答） | {qb['n_answerable']} |")
    lines.append(
        f"| - refuse（拒答） | {qb['n_refuse']}（要求 ≥{qb['refuse_threshold_required']}）|"
    )
    lines.append("")
    lines.append("## 二、核心指标（如实记录）")
    lines.append("")
    lines.append("| 指标 | 实测值 | 判据 | 是否通过 |")
    lines.append("|---|---|---|---|")
    lines.append(
        f"| Recall@8（全 answerable 平均） | {m['avg_recall_at_8']:.4f} | ≥ 0.85 | {'✅ PASS' if c['recall_pass'] else '❌ FAIL'} |"
    )
    lines.append(f"| MRR@8（全 answerable 平均） | {m['avg_mrr_at_8']:.4f} | — | — |")
    lines.append(
        f"| 中→中组 Recall@8 | {m['mid_group_recall_at_8']:.4f} | ≥ 0.90 | {'✅ PASS' if c['mid_recall_pass'] else '❌ FAIL'} |"
    )
    lines.append(
        f"| 中→中组 MRR@8 | {m['mid_group_mrr_at_8']:.4f} | ≥ 0.80 | {'✅ PASS' if c['mid_mrr_pass'] else '❌ FAIL'} |"
    )
    lines.append(f"| 拒答率（拒答题正确拒答比例） | {m['refuse_rate']:.4f} | ≥ 0.80（建议） | — |")
    lines.append("")
    lines.append("### 与合成集（1.0 / 1.0）差距")
    lines.append("")
    lines.append("| 指标 | 合成基线 | 实测 | 差距 |")
    lines.append("|---|---|---|---|")
    lines.append(
        f"| Recall@8 | 1.0000 | {m['avg_recall_at_8']:.4f} | {m['gap_to_synthetic']['recall']:.4f} |"
    )
    lines.append(
        f"| MRR@8 | 1.0000 | {m['avg_mrr_at_8']:.4f} | {m['gap_to_synthetic']['mrr']:.4f} |"
    )
    lines.append("")
    lines.append(
        "> 说明：合成基线是「语料与题库同源」的自洽 1.0/1.0，本任务补的是不作弊真证据，低于该值完全正常；差距来源主要是自然提问措辞、跨文档语义聚合、chunk 粒度三方面。"
    )
    lines.append("")
    lines.append("## 三、错误预算")
    lines.append("")
    lines.append(f"- answerable 题却被拒答（漏召回）：**{m['errors']['answerable_refused']}** 题")
    lines.append(
        f"- refuse 题却被召回到了内容（误召回）：**{m['errors']['refused_wrong_answered']}** 题"
    )
    lines.append("")
    lines.append("## 四、逐题明细")
    lines.append("")
    for row in r["rows"]:
        if row["kind"] == "refuse":
            lines.append(
                f"- **{row['qid']} (refuse)** | 拒答正确={row['refuse_correct']} | `{row['query'][:60]}…`"
            )
        else:
            lines.append(
                f"- **{row['qid']} ({row.get('difficulty', '?')})** | R@8={row['recall_at_8']}  MRR@8={row['mrr_at_8']} | 期望={','.join(row['expected'][:3])} | hit={','.join(row['hits'][:5]) or '（空）'}"
            )
    lines.append("")
    lines.append("## 五、未达判据时的原因分析（若达标，本节是 P-10 优化提示）")
    lines.append("")
    if not (c["recall_pass"] and c["mid_recall_pass"] and c["mid_mrr_pass"]):
        lines.append("当前有判据未通过，主要原因（可直接作为 P-10 召回调优输入）：")
        lines.append("")
        reasons: list[str] = []
        if m["avg_recall_at_8"] < 0.90:
            reasons.append(
                "1. **语料难度**：真实文档长、话题杂，题库用自然提问，存在「同一概念在不同文档以不同措辞表达」，三路引擎（keyword/vector/graph）的 overlap / 伪向量 / 图谱邻接均未能完全跨措辞命中。切分粒度当前是 800 字符，长文档核心段落被拆散，证据证据链不完整。"
            )
        if m["mid_group_recall_at_8"] < 0.90:
            reasons.append(
                "2. **切分策略**：中组题（easy/medium）通常跨标题/跨章节的语义聚合，而当前切分是纯字符+标点，没有按 Markdown 标题层级（#、##）做边界保护，标题本身通常短、被切到下一个 chunk 的头部 overlap 只有 80 字符，导致「标题 + 正文」的关联弱。"
            )
        if m["avg_mrr_at_8"] < 0.80:
            reasons.append(
                "3. **召回排序**：E0 用的是伪向量（minhash sign cosine）+ bag-of-words overlap，两路权重 0.45 / 0.40，graph 仅 0.15；词频权重未做 TF-IDF，停用词（「的、了、与、是」等中文虚词）会稀释 overlap 分数，导致正确答案常排到 3-5 位、拉低 MRR。"
            )
        if not reasons:
            reasons.append(
                "- 整体偏差较小，主要源于自然提问与语料措辞的语义鸿沟。E1 替换真实 embedding 后，Recall/MRR 应有明显提升。"
            )
        for line in reasons:
            lines.append(line)
    else:
        lines.append("全判据通过。以下为 P-10 可继续提分的方向：")
        lines.append("")
        lines.append("- 切分层加 Markdown 标题边界保护（避免把一个小节的题头与正文拆散）")
        lines.append("- 关键词引擎加 TF-IDF 权重与中文停用词表，降低虚词对 overlap 的稀释")
        lines.append(
            "- E1 接真实 embedding 后，把 vector 权重从 0.45 提到 0.60-0.70，替代当前伪向量"
        )
        lines.append("- 图谱引擎补实体同义词归一（别名表已在 aliases.yaml，需接入实体提取阶段）")
    lines.append("")
    lines.append(f"_总耗时 {r['elapsed_sec']} s_")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    run()
