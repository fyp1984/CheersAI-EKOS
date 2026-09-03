"""模型契约 Stub —— E0 本机开发的**关键设施**（60.90.03 §3.7）。

存在理由
--------
开发机为 Apple Silicon，**没有 NVIDIA GPU，vLLM / CUDA 无法运行**。
但除"真实推理"外的所有环节都该能在本机开发。所以起一个遵守**同一契约**
的假模型服务，让 E0 与 E1 之间只差一个环境变量。

严格模拟的冻结行为（依据 60.90.03 §3.6）
--------------------------------------
1. 接口：OpenAI-compatible /v1/chat/completions
2. 双 Profile：report(温度 0) / reasoning(温度 0.6、top_p 0.95)
3. 输出上限：GPU/云端 4,000 Token；CPU 2,000 Token，超限返回 finish_reason=length
4. Reasoning 与 content **分离返回**，使"Reasoning 不写入报告"在结构上成立

刻意保留的缺陷（不要"修"）
------------------------
- **Token 计数是估算**（按字符数折算），非真实 tokenizer。因此 Stub **不能**
  用于验证真实 Token 上限行为，只能验证"调用方是否正确处理了 length 截断"。
- **不模拟时延、显存、并发**。这些只有 E1 能验，E0 产出的性能数字一律无效。

所有响应带 `x-stub: 1` 头，且 model 名以 `stub-` 开头 —— 让假数据一眼可辨，
防止混进实测表。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Response

from .contract import (
    MAX_OUTPUT_TOKENS,
    REASONING_FIELD,
    STUB_HEADER,
    STUB_MODEL_PREFIX,
    ChatCompletionRequest,
    ChatCompletionResponse,
    Choice,
    Profile,
    Usage,
    resolve_params,
)

# 估算系数：中文约 1.5 字符/token，英文约 4 字符/token，取折中值。
# 仅用于触发 length 截断分支，不具备任何真实计量意义。
CHARS_PER_TOKEN = 2.0

DEFAULT_FIXTURE = Path("deploy/fixtures/stub_responses.json")

_TEMPLATE_CONTENT = (
    "[stub] 这是模型契约 Stub 的固定响应。"
    "真实推理只在 E1（x86_64 真机）进行，本响应不具备任何质量含义。"
)
_TEMPLATE_REASONING = "[stub] 模拟推理过程。该字段不得写入报告、日志或多轮历史。"


def estimate_tokens(text: str) -> int:
    """粗估 Token 数。仅用于触发截断分支，不可用于任何计量结论。"""
    return max(1, int(len(text) / CHARS_PER_TOKEN))


def _load_fixture(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return data
    except (OSError, json.JSONDecodeError):
        # fixture 损坏不应让开发中断，退化为模板响应
        return {}


def _pick_response(req: ChatCompletionRequest, fixture: dict[str, Any]) -> tuple[str, str | None]:
    """按最后一条 user 消息的哈希取预设响应；未命中则回退模板。

    用哈希而不是原文做键，是为了让 fixture 文件里不出现样本原文 ——
    真实样本不进仓库（数据红线）。
    """
    last_user = ""
    for msg in reversed(req.messages):
        if msg.role == "user":
            last_user = msg.content
            break
    key = hashlib.sha256(last_user.encode("utf-8")).hexdigest()[:16]

    table: dict[str, Any] = fixture.get("responses", {})
    hit = table.get(key)
    if isinstance(hit, dict):
        content = str(hit.get("content", _TEMPLATE_CONTENT))
        reasoning = hit.get(REASONING_FIELD)
        return content, (str(reasoning) if reasoning is not None else None)

    default = fixture.get("default")
    if isinstance(default, dict):
        content = str(default.get("content", _TEMPLATE_CONTENT))
        reasoning = default.get(REASONING_FIELD)
        return content, (str(reasoning) if reasoning is not None else None)

    return _TEMPLATE_CONTENT, None


def create_app(fixture_path: Path | None = None) -> FastAPI:
    app = FastAPI(title="EKOS 模型契约 Stub", version="0.1.0")
    fixture = _load_fixture(fixture_path)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "stub": True, "deploy_max_tokens": MAX_OUTPUT_TOKENS}

    @app.get("/v1/models")
    def list_models() -> dict[str, Any]:
        return {
            "object": "list",
            "data": [
                {"id": f"{STUB_MODEL_PREFIX}qwen3-8b", "object": "model"},
                {"id": f"{STUB_MODEL_PREFIX}qwen3-8b-reasoning", "object": "model"},
            ],
        }

    @app.post("/v1/chat/completions")
    def chat(req: ChatCompletionRequest, response: Response) -> dict[str, Any]:
        profile = req.profile or (
            Profile.REASONING
            if (req.temperature is not None and req.temperature > 0)
            else Profile.REPORT
        )
        params = resolve_params(
            profile,
            deploy=_infer_deploy(req.model),
            override_temperature=req.temperature,
            override_top_p=req.top_p,
        )
        max_tokens = int(params["max_tokens"])
        if req.max_tokens is not None:
            max_tokens = min(max_tokens, req.max_tokens)

        content, reasoning_override = _pick_response(req, fixture)

        # 触发长度截断：让调用方在本机就能验证 finish_reason=length 的处理
        finish_reason: str = "stop"
        if estimate_tokens(content) > max_tokens:
            content = content[: int(max_tokens * CHARS_PER_TOKEN)]
            finish_reason = "length"

        message: dict[str, Any] = {"role": "assistant", "content": content}
        if profile is Profile.REASONING:
            # 与 content 分离返回 —— 调用方取 content 即不可能误带走推理过程
            message[REASONING_FIELD] = reasoning_override or _TEMPLATE_REASONING

        prompt_text = "".join(m.content for m in req.messages)
        p_tokens = estimate_tokens(prompt_text)
        c_tokens = estimate_tokens(content)

        # 采样参数挂到响应头：既方便契约测试断言，又不污染 OpenAI-compatible 响应体
        response.headers[STUB_HEADER] = "1"
        response.headers["x-stub-profile"] = profile.value
        response.headers["x-stub-temperature"] = str(params["temperature"])
        response.headers["x-stub-top-p"] = str(params["top_p"])
        response.headers["x-stub-max-tokens"] = str(max_tokens)
        resp = ChatCompletionResponse(
            id=f"chatcmpl-stub-{int(time.time())}",
            created=int(time.time()),
            model=f"{STUB_MODEL_PREFIX}{req.model}",
            choices=[Choice(message=message, finish_reason=finish_reason)],  # type: ignore[arg-type]
            usage=Usage(
                prompt_tokens=p_tokens,
                completion_tokens=c_tokens,
                total_tokens=p_tokens + c_tokens,
            ),
        )
        return resp.model_dump()

    return app


def _infer_deploy(model: str) -> str:
    """从 model 名推断部署档位，决定 Token 上限。"""
    lowered = model.lower()
    if "cpu" in lowered or "gguf" in lowered:
        return "cpu"
    if "cloud" in lowered or "api" in lowered:
        return "cloud"
    return "gpu"


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="EKOS 模型契约 Stub")
    parser.add_argument("--port", type=int, default=18000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    args = parser.parse_args()
    uvicorn.run(create_app(args.fixture), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
