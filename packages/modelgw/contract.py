"""两个模型出口的统一契约定义（60.90.03 §3.6 §3.7）。

GPU 档用 vLLM 0.8.5，CPU 档用 llama.cpp b5401，二者通过**同一个
OpenAI-compatible 契约**接入（60.90.03 §3.7）。本模块把该契约固化成
Pydantic 模型，使 Stub 与真实服务可互换，且任何一方违约都会被契约测试抓到。

为什么契约要单独成文件：它是 E0（Stub）与 E1（真实模型）之间的唯一接口。
两边都依赖它，就没人能单方面改口径。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Profile(StrEnum):
    """生成参数 Profile（60.90.03 §3.6 第 2 条）。

    report    —— 字段抽取、结构化 JSON、报告草稿。温度固定 0（确定性要求）。
    reasoning —— 复杂冲突分析。温度 0.6、top_p 0.95，不得使用贪心解码。
    """

    REPORT = "report"
    REASONING = "reasoning"


# 冻结的采样参数（60.90.03 §3.6 第 2 条）
PROFILE_PARAMS: dict[Profile, dict[str, float]] = {
    Profile.REPORT: {"temperature": 0.0, "top_p": 1.0},
    Profile.REASONING: {"temperature": 0.6, "top_p": 0.95},
}

# 单次最终输出上限（60.90.03 §3.6 第 3 条）
# GPU 本地出口与云端出口 4,000；CPU 本地出口 2,000。
MAX_OUTPUT_TOKENS: dict[str, int] = {
    "gpu": 4000,
    "cloud": 4000,
    "cpu": 2000,
}

# Reasoning 内容不得写入报告、日志或多轮历史，只保存最终结论和引用
# （60.90.03 §3.6 第 3 条）。字段名与 vLLM/Qwen3 实际返回保持一致。
REASONING_FIELD = "reasoning_content"

# Stub 标记：所有经 Stub 产出的内容都必须带它，防假数据混入实测表
STUB_HEADER = "x-stub"
STUB_MODEL_PREFIX = "stub-"


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatCompletionRequest(BaseModel):
    """OpenAI-compatible 请求体（只声明本轮用到的字段）。"""

    model: str
    messages: list[ChatMessage]
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stream: bool = False

    # 本轮扩展：指定 Profile，由模型网关按部署方案映射到实际采样参数
    profile: Profile | None = Field(default=None)


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class Choice(BaseModel):
    index: int = 0
    message: dict[str, Any]
    finish_reason: Literal["stop", "length"] = "stop"


class ChatCompletionResponse(BaseModel):
    """OpenAI-compatible 响应体。

    reasoning_content 与 content 分离返回 —— 这是 §3.6 第 3 条「Reasoning
    不写入报告」在数据结构层面的落点：调用方只取 content 即可，取不到
    reasoning 就不可能误写进报告。
    """

    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[Choice]
    usage: Usage


def resolve_params(
    profile: Profile,
    *,
    deploy: str = "gpu",
    override_temperature: float | None = None,
    override_top_p: float | None = None,
) -> dict[str, float | int]:
    """按 Profile 解析最终采样参数与输出上限。

    显式 override 优先（用于 Spike 对比），否则一律取冻结值。
    """
    base = PROFILE_PARAMS[profile]
    return {
        "temperature": (
            override_temperature if override_temperature is not None else base["temperature"]
        ),
        "top_p": override_top_p if override_top_p is not None else base["top_p"],
        "max_tokens": MAX_OUTPUT_TOKENS.get(deploy, MAX_OUTPUT_TOKENS["gpu"]),
    }
