"""L1 单元测试：模型网关纯函数。

这一层只测不依赖 IO 的纯逻辑：采样参数解析与 Token 粗估。
为什么单独有一层：L2 契约测试要起 Stub 服务、走 HTTP，跑一次几百毫秒；
纯函数不该付出这个代价，而且纯函数最容易写死 bug 又最难在集成层发现。

期望值纪律（重要）
------------------
所有期望值一律写 60.90.03 §3.6 的**文档字面值**，严禁从被测常量
（PROFILE_PARAMS / MAX_OUTPUT_TOKENS）取期望值。
那等于让被测代码给自己发通行证：常量被改，期望值同步改，断言永远成立。
2026-09-03 变异测试已实证——把 CPU 上限从 2000 改成 4000，
引用常量的断言静默通过，写死字面值的断言立刻抓到。
"""

from __future__ import annotations

import pytest
from packages.modelgw.contract import Profile, resolve_params
from packages.modelgw.stub import estimate_tokens


@pytest.mark.unit()
def test_report_profile_uses_frozen_params() -> None:
    """§3.6 第 2 条：report 温度必须为 0（报告要可复现）。"""
    p = resolve_params(Profile.REPORT)
    assert p["temperature"] == 0.0
    assert p["top_p"] == 1.0


@pytest.mark.unit()
def test_reasoning_profile_uses_frozen_params() -> None:
    """§3.6 第 2 条：reasoning 温度 0.6、top_p 0.95。"""
    p = resolve_params(Profile.REASONING)
    assert p["temperature"] == 0.6
    assert p["top_p"] == 0.95


@pytest.mark.unit()
@pytest.mark.parametrize(
    ("deploy", "expected"),
    [
        # 期望值写死为 60.90.03 §3.6 第 3 条的字面值，不取被测常量。
        ("gpu", 4000),
        ("cloud", 4000),
        ("cpu", 2000),
    ],
)
def test_max_tokens_per_deploy(deploy: str, expected: int) -> None:
    """§3.6 第 3 条：GPU/云端 4,000，CPU 2,000。"""
    assert resolve_params(Profile.REPORT, deploy=deploy)["max_tokens"] == expected


@pytest.mark.unit()
def test_unknown_deploy_falls_back_to_gpu_cap() -> None:
    """未登记的部署档不得低于 GPU 档——宁可给多，不可静默给少。

    静默给少的后果是报告被截断而调用方以为是完整输出，这比报错更糟。
    """
    assert resolve_params(Profile.REPORT, deploy="unknown-tier")["max_tokens"] == 4000


@pytest.mark.unit()
def test_override_wins_over_frozen_value() -> None:
    """显式 override 用于 Spike 对比，必须能盖掉冻结值。"""
    p = resolve_params(Profile.REPORT, override_temperature=0.9, override_top_p=0.5)
    assert p["temperature"] == 0.9
    assert p["top_p"] == 0.5


@pytest.mark.unit()
def test_override_zero_is_not_swallowed_by_falsy_check() -> None:
    """override_temperature=0 必须生效，不能被 `or` 之类假值判断吞掉。

    report 档的冻结温度恰好就是 0，这个用例是防「改判断方式时踩坑」的护栏。
    """
    p = resolve_params(Profile.REASONING, override_temperature=0.0)
    assert p["temperature"] == 0.0


@pytest.mark.unit()
def test_estimate_tokens_never_returns_zero() -> None:
    """空串也必须返回 1：0 会让截断分支永远不触发，等于上限失效。"""
    assert estimate_tokens("") >= 1


@pytest.mark.unit()
def test_estimate_tokens_grows_with_text() -> None:
    """粗估只需保证单调性——它是截断分支的触发器，不是计量工具。"""
    assert estimate_tokens("a" * 100) > estimate_tokens("a" * 10)
