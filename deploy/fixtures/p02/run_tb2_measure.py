"""T-B2 实测脚本：50 份模拟文档批同步延迟 + 权限命中率。

结果写入 results/P-02/tb2_measure.json（仅用模拟数据，不触线上）。
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.worker.connectors import FeishuIngestConnector, LocalDocConnector  # noqa: E402

KNOWN_DOMAINS = {"D-local", "D-reviewer", "D-ops", "D-decision", "D-unknown"}
BATCH_SIZE = 50


def _write_batch(tmpdir: Path, n: int = BATCH_SIZE) -> Path:
    batch = tmpdir / "batch_tb2_real"
    batch.mkdir(exist_ok=True)
    for i in range(n):
        fn = f"样例文档-{i + 1:02d}.md"
        payload = (
            f"# 样例文档 {i + 1}（非真实数据）\n"
            "内容仅供 T-B2 同步与权限实测。\n"
            f"sha256-16={hashlib.sha256(str(i).encode()).hexdigest()[:16]}\n"
        )
        (batch / fn).write_text(payload, encoding="utf-8")
    return batch


def main() -> None:
    out = ROOT / "results" / "P-02" / "tb2_measure.json"
    out.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        batch = _write_batch(Path(td))
        local = LocalDocConnector(batch)
        t0 = time.time()
        lrecs = local.execute(trace_id="TB2-measure-local-real")
        elapsed = time.time() - t0

        hit = 0
        missing: list[str] = []
        for r in lrecs:
            domain = r.get("permission_domain")
            if domain and domain in KNOWN_DOMAINS:
                hit += 1
            else:
                missing.append(f"{r.get('doc_id')} -> {domain!r}")
        local_rate = (hit / len(lrecs) * 100.0) if lrecs else 0.0

        feishu = FeishuIngestConnector()
        frecs = feishu.execute(dry=True, trace_id="TB2-measure-feishu-real")
        fhit = 0
        fmissing: list[str] = []
        for r in frecs:
            domain = r.get("permission_domain")
            if domain and domain in KNOWN_DOMAINS:
                fhit += 1
            else:
                fmissing.append(f"{r.get('doc_id')} -> {domain!r}")
        feishu_rate = (fhit / len(frecs) * 100.0) if frecs else 0.0

    payload = {
        "trace_id": "P-02-TB2-260919",
        "batch_size": BATCH_SIZE,
        "local_records": len(lrecs),
        "local_elapsed_sec": round(elapsed, 4),
        "local_elapsed_min": round(elapsed / 60.0, 6),
        "local_domain_hit_pct": round(local_rate, 4),
        "local_domain_missing_samples": missing[:5],
        "feishu_records": len(frecs),
        "feishu_domain_hit_pct": round(feishu_rate, 4),
        "feishu_domain_missing_samples": fmissing[:5],
        "latency_threshold_min": 5.0,
        "latency_pass": elapsed <= 300.0,
        "permission_pass": local_rate == 100.0 and feishu_rate == 100.0,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("T-B2 实测已写:", out)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
