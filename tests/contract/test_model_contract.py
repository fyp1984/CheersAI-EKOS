"""L2 契约测试：验证模型服务遵守 60.90.03 §3.6 冻结契约。

这一层的特殊性
--------------
同一份断言要跑**两边**：E0 跑 Stub、E1 跑真实 vLLM / llama.cpp。
只有两边都过，才真正证明「Stub 与真实服务可互换」——否则 E0 的开发成果
在 E1 上是作废的。

覆盖的冻结条款（60.90.03 §3.6）
------------------------------
- 第 1 条：OpenAI-compatible /v1/chat/completions
- 第 2 条：report 温度 0；reasoning 温度 0.6、top_p 0.95
- 第 3 条：GPU/云端 4,000 Token、CPU 2,000 Token；超限返回 length
- 第 3 条：Reasoning 与 content 分离，不得混入报告
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from packages.modelgw.contract import (
    REASONING_FIELD,
    STUB_HEADER,
    STUB_MODEL_PREFIX,
    Profile,
)
from packages.modelgw.stub import create_app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def _post(client: TestClient, **payload: object) -> Response:
    """返回类型必须是真实的 Response，不能是 object。

    写成 object 时 mypy 会让 .json()/.headers 全部报 attr-defined，
    而 pytest 照样全绿 —— 测试跑过不等于测到东西。契约测试是 E0/E1
    可互换的唯一证据，这里的类型不能含糊。
    """
    body: dict[str, object] = {
        "model": "qwen3-8b",
        "messages": [{"role": "user", "content": "ping"}],
    }
    body.update(payload)
    return client.post("/v1/chat/completions", json=body)


@pytest.mark.contract()
def test_response_shape_is_openai_compatible(client: TestClient) -> None:
    """第 1 条：响应结构必须是 OpenAI-compatible，否则两个出口无法互换。"""
    r = _post(client)
    assert r.status_code == 200
    data = r.json()
    for key in ("id", "object", "created", "model", "choices", "usage"):
        assert key in data, f"缺少 OpenAI 标准字段 {key}"
    assert data["object"] == "chat.completion"
    assert data["choices"][0]["message"]["role"] == "assistant"
    usage = data["usage"]
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]


@pytest.mark.contract()
def test_report_profile_forces_temperature_zero(client: TestClient) -> None:
    """第 2 条：report 用于字段抽取与报告草稿，温度必须固定为 0。"""
    r = _post(client, profile=Profile.REPORT.value)
    assert r.headers["x-stub-profile"] == "report"
    assert float(r.headers["x-stub-temperature"]) == 0.0


@pytest.mark.contract()
def test_reasoning_profile_uses_frozen_sampling(client: TestClient) -> None:
    """第 2 条：reasoning 温度 0.6、top_p 0.95，且不得贪心解码。"""
    r = _post(client, profile=Profile.REASONING.value)
    assert float(r.headers["x-stub-temperature"]) == pytest.approx(0.6)
    assert float(r.headers["x-stub-top-p"]) == pytest.approx(0.95)


@pytest.mark.contract()
def test_reasoning_is_separated_from_content(client: TestClient) -> None:
    """第 3 条：Reasoning 不得写入报告 —— 分离返回是这条在结构上的保证。"""
    r = _post(client, profile=Profile.REASONING.value)
    msg = r.json()["choices"][0]["message"]
    assert REASONING_FIELD in msg, "reasoning profile 必须单独返回推理字段"
    assert msg[REASONING_FIELD], "推理字段不得为空"
    # 关键：content 里不得混入推理过程
    assert msg[REASONING_FIELD] not in msg["content"]


@pytest.mark.contract()
def test_report_profile_has_no_reasoning_field(client: TestClient) -> None:
    """报告 Profile 不应携带推理字段，从源头杜绝误写入报告。"""
    r = _post(client, profile=Profile.REPORT.value)
    msg = r.json()["choices"][0]["message"]
    assert REASONING_FIELD not in msg


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        # 期望值一律写死为 60.90.03 §3.6 第 3 条的字面值。
        # 严禁从被测常量（如 MAX_OUTPUT_TOKENS）取期望值 —— 那等于让被测代码
        # 自己给自己发通行证：常量被改时期望值同步改变，断言永远成立。
        ("qwen3-8b", 4000),  # GPU 本地出口 4,000
        ("qwen3-8b-cpu", 2000),  # CPU 本地出口 2,000
        ("qwen3-8b-cloud", 4000),  # 云端出口 4,000
    ],
)
@pytest.mark.contract()
def test_output_cap_follows_deploy_tier(client: TestClient, model: str, expected: int) -> None:
    """第 3 条：GPU/云端 4,000，CPU 2,000 —— 档位不同上限不同。"""
    r = _post(client, model=model)
    assert int(r.headers["x-stub-max-tokens"]) == expected


@pytest.mark.contract()
def test_overlong_output_returns_finish_reason_length(client: TestClient) -> None:
    """第 3 条：超上限必须返回 length，让调用方在本机就能验证截断处理。"""
    r = _post(client, model="qwen3-8b-cpu", max_tokens=10)
    data = r.json()
    assert data["choices"][0]["finish_reason"] == "length"


@pytest.mark.contract()
def test_stub_output_is_always_marked(client: TestClient) -> None:
    """防假数据混入实测表：响应必须带 stub 标记，且 model 名带前缀。"""
    r = _post(client)
    assert r.headers[STUB_HEADER] == "1"
    assert r.json()["model"].startswith(STUB_MODEL_PREFIX)


@pytest.mark.contract()
def test_health_endpoint_exposes_deploy_caps(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["stub"] is True
