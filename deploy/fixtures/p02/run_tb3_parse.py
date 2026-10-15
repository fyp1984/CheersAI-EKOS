"""T-B3 三类中文极端场景实测跑批。

对 scan_cn.pdf / crosstable_cn.pdf / stamp_cn.pdf 逐份调用
packages.document.parser.parse() + to_markdown()，产出：
  - {base}.parsed.json   ParsedDocument 全量
  - {base}.parsed.md     标准 Markdown
  - {base}.verdict.json  判词 + 指标（BIZ-2 三选一硬度）
"""

import json
from pathlib import Path

from packages.document.parser import parse, to_markdown

ROOT = Path(__file__).resolve().parent.parent.parent
if ROOT.name == "deploy":
    ROOT = ROOT.parent
FIXTURES = ROOT / "deploy" / "fixtures" / "p02"
OUT = ROOT / "results" / "P-02"
OUT.mkdir(parents=True, exist_ok=True)

CASES = [
    ("scan_cn.pdf", "扫描件（无文本层）"),
    ("crosstable_cn.pdf", "跨页表格"),
    ("stamp_cn.pdf", "盖章遮挡"),
]


def run_one(pdf: Path, trace_label: str):
    base = pdf.stem
    trace_id = f"TB3-{trace_label}-{pdf.stat().st_size}"
    doc = parse(str(pdf), "application/pdf", "default", trace_id=trace_id)
    parsed_json = OUT / f"{base}.parsed.json"
    parsed_md = OUT / f"{base}.parsed.md"
    verdict_json = OUT / f"{base}.verdict.json"

    payload = doc.model_dump(mode="json")
    verdict = getattr(doc, "verdict", None)
    metrics = getattr(doc, "_metrics", None)
    if verdict is not None:
        payload.setdefault("verdict", verdict)
    if metrics is not None:
        payload.setdefault("_metrics", metrics)

    parsed_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    parsed_md.write_text(to_markdown(doc), encoding="utf-8")
    verdict_json.write_text(
        json.dumps(
            {
                "input": str(pdf),
                "trace_id": trace_id,
                "verdict": verdict,
                "metrics": metrics,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return verdict, metrics


def main():
    summary = []
    for name, label in CASES:
        pdf = FIXTURES / name
        if not pdf.exists():
            print("MISSING:", pdf)
            continue
        v, m = run_one(pdf, name)
        conclusion = v.get("conclusion") if v else None
        summary.append((name, label, conclusion, m))
        print(f"=== {name} / {label} ===")
        print("  判词结论 :", conclusion)
        print("  场景     :", v.get("scenario") if v else None)
        print("  明细     :", v.get("detail") if v else None)
        if m:
            print(
                "  指标     : blocks={} tables_pages={} scanned={} stamps={} elapsed={}s".format(
                    m.get("n_blocks"),
                    m.get("n_tables_pages"),
                    m.get("scanned"),
                    m.get("n_stamps"),
                    m.get("elapsed_sec"),
                )
            )
    return summary


if __name__ == "__main__":
    main()
