"""T-B2 同步延迟与权限验证（集成测试 · 仅模拟数据）。

运行: make test-integration
或: .venv/bin/pytest tests/integration -m integration

判据:
  - 同步延迟 ≤ 300s (5min)
  - 权限域 100% 随记录透传 + 内核可识别
"""

import hashlib
import tempfile
import time
from pathlib import Path

import pytest
from apps.worker.connectors import FeishuIngestConnector, LocalDocConnector

KNOWN_DOMAINS = {"D-local", "D-reviewer", "D-ops", "D-decision", "D-unknown"}
BATCH_SIZE = 50


def _write_batch(tmpdir: Path, n: int = BATCH_SIZE) -> Path:
    batch = tmpdir / "batch_b2"
    batch.mkdir(exist_ok=True)
    for i in range(n):
        fn = f"样例文档-{i + 1:02d}.md"
        payload = (
            f"# 样例文档 {i + 1}（非真实数据）\n"
            "内容仅供 T-B2 同步与权限验证。\n"
            f"sha256-16={hashlib.sha256(str(i).encode()).hexdigest()[:16]}\n"
        )
        (batch / fn).write_text(payload, encoding="utf-8")
    return batch


@pytest.mark.integration
class TestConnectorSyncPerf:
    @pytest.mark.integration
    def test_latency_within_5min(self) -> None:
        """T-B2 判据①: 50 份模拟文档批同步延迟 ≤ 300s（5min）。"""
        with tempfile.TemporaryDirectory() as td:
            batch = _write_batch(Path(td))
            conn = LocalDocConnector(batch)
            assert conn.health()["status"] == "ok"

            t0 = time.time()
            recs = conn.execute(trace_id="TB2-integration-perf")
            elapsed = time.time() - t0

            assert len(recs) == BATCH_SIZE, "连接器应返回 50 条记录"
            assert elapsed <= 300, f"同步延迟 {elapsed:0.4f}s 超过 5min 阈值"

    @pytest.mark.integration
    def test_permission_domain_100pct_hit(self) -> None:
        """T-B2 判据②: 每条记录带非空 permission_domain，且 100% 能被内核识别。"""
        # 本地文档库连接器
        with tempfile.TemporaryDirectory() as td:
            batch = _write_batch(Path(td))
            local = LocalDocConnector(batch)
            lrecs = local.execute(trace_id="TB2-integration-perm-local")
            for r in lrecs:
                assert r.get("permission_domain"), "每条本地记录必须带 permission_domain"
                assert r["permission_domain"] in KNOWN_DOMAINS, "域 {} 不在内核已知域集合内".format(
                    r["permission_domain"]
                )

        # 飞书连接器（fixture 干跑）
        feishu = FeishuIngestConnector()
        frecs = feishu.execute(dry=True, trace_id="TB2-integration-perm-feishu")
        assert len(frecs) >= 2, "fixture 至少 2 条记录"
        for r in frecs:
            assert r.get("permission_domain"), "每条飞书记录必须带 permission_domain"
            assert r["permission_domain"] in KNOWN_DOMAINS, "域 {} 不在内核已知域集合内".format(
                r["permission_domain"]
            )
