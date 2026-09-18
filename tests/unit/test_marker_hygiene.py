"""L1 元守卫：tests/ 下每一条测试都必须带 pytest 层级标记。

为什么需要这一条
----------------
实测发现（2026-09-05，T2.08）：`tests/unit/` 有 32 条测试，但 `make test-unit`
跑的是 `pytest tests/unit -m unit`，实际只执行 20 条——`test_vault_service.py`(8)、
`test_document_parser.py`(2)、`test_retrieval_service.py`(2) 共 12 条没有
`@pytest.mark.unit`，**从未被任何 make 目标执行过**。

后果不是「少跑几条测试」这么轻：那 12 条里包含 T2.06 补的脱敏红线断言
（`test_mask_returns_ref_not_plaintext`、架构守卫 `test_adapter_holds_no_masking_patterns`）
和 T2.07 补的解析层断言。它们被人工验证时是通过的，但在门禁里是死的——
门禁报告「unit 20 passed」绿得很干净，谁也看不出少了 12 条。

这是最典型的静默放行：**测试写了、跑过一次、从那以后再没人执行过**。
本守卫把它变成一条会红的断言。

为什么用 ast 而不是按行扫装饰器
--------------------------------
首版用「从 def 向上取连续以 @ 开头的行」，实测把已标记的
`test_max_tokens_per_deploy` 误报成未标记——它的 `@pytest.mark.parametrize(`
跨了 9 行，扫描在 `)` 处就断了。按行扫分不清「装饰器的续行」和「装饰器结束」，
AST 天然没有这个问题。
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).resolve().parents[1]

# 四个层级标记（pyproject.toml [tool.pytest.ini_options] markers 中定义，
# 与 Makefile 的 test-unit / test-contract / test-integration / test-golden 一一对应）
LAYER_MARKS = ("unit", "contract", "integration", "golden")

# 非层级但合法的 pytest 标记：它们不参与 -m 筛选，出现不算违约
NON_LAYER_MARKS = (
    "parametrize",
    "skip",
    "skipif",
    "xfail",
    "filterwarnings",
    "usefixtures",
    "asyncio",
    "timeout",
)


def _test_files() -> list[Path]:
    files = sorted(p for p in TESTS_ROOT.rglob("test_*.py") if "__pycache__" not in p.parts)
    assert files, "glob 落空会让本守卫静默通过"
    return files


def _iter_test_funcs(path: Path) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith(
            "test_"
        ):
            yield node


def _decorator_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """取装饰器点号全名，如 `pytest.mark.unit()` → `pytest.mark.unit`"""
    names = []
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        parts: list[str] = []
        while isinstance(target, ast.Attribute):
            parts.append(target.attr)
            target = target.value
        if isinstance(target, ast.Name):
            parts.append(target.id)
            if parts[-1] == "pytest":
                names.append(".".join(reversed(parts)))
    return names


def _layer_marks_of(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    out = []
    for name in _decorator_names(node):
        prefix = "pytest.mark."
        if name.startswith(prefix) and name[len(prefix) :] in LAYER_MARKS:
            out.append(name[len(prefix) :])
    return out


@pytest.mark.unit()
def test_every_test_function_carries_a_layer_mark() -> None:
    unmarked: list[str] = []
    for path in _test_files():
        rel = path.relative_to(TESTS_ROOT)
        for node in _iter_test_funcs(path):
            if not _layer_marks_of(node):
                unmarked.append(f"{rel}::{node.name}")
    assert not unmarked, f"以下测试缺层级标记，将不会被 make test-* 任何目标执行：{unmarked}"


@pytest.mark.unit()
def test_no_unregistered_layer_marks() -> None:
    """用到的标记必须是四层之一，或是已知的非层级标记。

    `--strict-markers` 只挡「未登记」的标记；随手写的 `@pytest.mark.fast`
    一旦被登记进 pyproject，就再没人会发现没有任何 make 目标会选它。
    """
    stray: list[str] = []
    for path in _test_files():
        for node in _iter_test_funcs(path):
            for name in _decorator_names(node):
                prefix = "pytest.mark."
                if not name.startswith(prefix):
                    continue
                mark = name[len(prefix) :]
                if mark in LAYER_MARKS or mark in NON_LAYER_MARKS:
                    continue
                stray.append(f"{path.relative_to(TESTS_ROOT)}::{node.name} {name}")
    assert not stray, f"使用了非四层标记，make test-* 无法筛到它：{stray}"
