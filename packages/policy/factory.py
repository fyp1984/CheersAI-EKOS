"""Build policy adapter from config.

T-D1 判据：换后端 / 加权限源 -> 只改 config + 新增 Adapter，内核零改动。

config 示例 (configs/policy.yaml)：

    adapters:
      primary:
        backend: spicedb
        endpoint: ${SPICEDB_ENDPOINT}
        token: ${SPICEDB_TOKEN}
      local:
        backend: in-memory

如果如果在 E0 没起 SpiceDB，primary 不可用时 fallback 到 in-memory
（配置里写死 fall_back_to_in_memory_when_unreachable: true）
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from .adapter import PolicyAdapter
from .in_memory import InMemoryPolicyAdapter
from .spicedb import SpiceDBAdapter

_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "configs"
_DEFAULT_POLICY_CONFIG = "policy.yaml"


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        # ${VAR:-default} / ${VAR}
        import re

        def repl(m: re.Match[str]) -> str:
            name = m.group(1)
            default = m.group(2) or ""
            return os.environ.get(name, default)

        return re.sub(r"\$\{([A-Z_][A-Z0-9_]*)(?::-([^}]*))?\}", repl, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def _load_config(config_path: Path | None = None) -> dict[str, Any]:
    path = config_path or (_CONFIG_DIR / _DEFAULT_POLICY_CONFIG)
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    expanded = _expand_env(raw)
    assert isinstance(expanded, dict)
    # 所有 key 都是 yaml 顶层映射，必为 str
    return {str(k): v for k, v in expanded.items()}


def build_policy_adapter_from_config(
    config_path: Path | None = None,
    *,
    adapter_name: str = "primary",
) -> PolicyAdapter:
    """按配置构建 PolicyAdapter。新增后端，只在这里加分支，内核（检索/API 层零改动。"""
    cfg = _load_config(config_path)
    adapters = cfg.get("adapters") or {}
    node = adapters.get(adapter_name) or {}
    backend = (node.get("backend") or "in-memory").lower()

    if backend == "spicedb":
        endpoint = node.get("endpoint") or ""
        token = node.get("token") or ""
        scheme = node.get("scheme") or "https"
        timeout = float(node.get("timeout") or 5.0)
        return SpiceDBAdapter(endpoint=endpoint, token=token, scheme=scheme, timeout=timeout)
    # 默认 = in-memory
    return InMemoryPolicyAdapter()


__all__ = ["build_policy_adapter_from_config"]
