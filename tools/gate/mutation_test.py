"""门禁脚本变异测试：把每条检查的判定改坏，确认自检断言真的会红。

为什么必须有这个脚本
--------------------
断言绿了只能说明"这条路径走到了"，说明不了"如果实现坏了它会不会红"。
**不会红的断言是装饰品** —— 它给出的是虚假的安全感，比没有断言更危险。

2026-09-03 教训
---------------
变异点字符串若不唯一，替换不会生效，但 selftest 仍会跑完并打印 PASS ——
你会据此误判"这条改动无害"。所以本脚本对非唯一定位**硬失败**，
绝不打印任何结论。这是本脚本唯一不能简化的部分。

为什么放在仓库里而不是 /tmp
---------------------------
它曾经在 /tmp。/tmp 会被系统清理，而**它消失时不会有人注意到** ——
下次有人想跑变异测试，命令找不到文件，最可能的反应是跳过这一步。
一个会静默消失的验证工具，等于没有验证工具。

用法
----
    uv run python tools/gate/mutation_test.py

退出码：0 = 全部变异被捕获；1 = 存在未被捕获的变异（断言需补强）
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

# 从脚本自身位置推导仓库根，不写死绝对路径：
# 一写死，换台机器或迁仓就指向错误目录，而脚本不会报错，只会去改别处的文件。
REPO = pathlib.Path(__file__).resolve().parent.parent.parent
SRC = REPO / "tools" / "gate" / "gate_check.py"

MUTATIONS = [
    (
        "TR-7 代码检查永不命中",
        "            if pat.search(_strip_comment(line)):",
        "            if False and pat.search(_strip_comment(line)):",
    ),
    (
        "TR-7 compose 冻结段判定失效",
        "            if host_port in frozen:",
        "            if False and host_port in frozen:",
    ),
    (
        "TR-4 主版本匹配失效（退回精确匹配）",
        '        major_mode = c.get("match") == "major"',
        "        major_mode = False",
    ),
    (
        "TR-4 主版本匹配过度宽松（任何版本都过）",
        '            matched = sorted(g for g in got if g.split(".")[0] == frozen.split(".")[0])',
        "            matched = sorted(got)",
    ),
    (
        "TR-8 敏感标识命中被吞掉",
        "            if m:\n                hits.append((str(f.relative_to(root)), label))",
        # 以下字符串是「变异点」，必须与 gate_check.py 源码逐字符一致才能命中，
        # 折行即失效（而失效时脚本会硬失败，不会静默放过）→ 行长规则在此让位。
        "            if False and m:\n                hits.append((str(f.relative_to(root)), label))",  # noqa: E501
    ),
    (
        "TR-8 空样本目录改判 PASS（假装查过了）",
        '                "TR-8",\n                SKIP,\n                "-",\n                "样本目录存在但为空',  # noqa: E501
        '                "TR-8",\n                PASS,\n                "-",\n                "样本目录存在但为空',  # noqa: E501
    ),
]

# 备份放临时目录而非仓库内：避免备份文件本身被门禁扫描或误提交。
BAK = pathlib.Path(tempfile.mkdtemp(prefix="gate-mutation-")) / "gate_check.pristine.py"


def main() -> int:
    shutil.copy(SRC, BAK)
    results: list[tuple[str, str, str]] = []
    try:
        for desc, old, new in MUTATIONS:
            s = SRC.read_text(encoding="utf-8")
            n = s.count(old)
            if n != 1:
                results.append((desc, "ABORT", f"变异点命中 {n} 次（应恰好 1 次），结果无效"))
                continue
            SRC.write_text(s.replace(old, new), encoding="utf-8")
            # noqa: S603 —— 命令与参数是硬编码常量，无外部输入，不存在不可信执行
            r = subprocess.run(  # noqa: S603
                [sys.executable, str(SRC), "selftest"],
                cwd=REPO,
                capture_output=True,
                text=True,
            )
            out = r.stdout
            red = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("FAILED")]
            if "存在未捕获项" in out and red:
                caught = "; ".join(x.replace("FAILED ", "") for x in red)
                results.append((desc, "CAUGHT", caught))
            else:
                results.append((desc, "MISSED", "断言仍全绿 —— 这条断言没有牙！"))
            shutil.copy(BAK, SRC)
    finally:
        shutil.copy(BAK, SRC)

    print("=" * 78)
    print("变异测试结果（CAUGHT = 断言抓到了变异，MISSED = 断言失效）")
    print("=" * 78)
    missed = 0
    for desc, verdict, detail in results:
        print(f"  [{verdict:6s}] {desc}")
        if detail:
            print(f"            {detail}")
        if verdict != "CAUGHT":
            missed += 1
    print("=" * 78)
    if missed == 0:
        print("全部变异被捕获 —— 断言有牙")
    else:
        print(f"有 {missed} 条变异未被捕获，断言需补强")

    # noqa: S603 —— 同上，参数硬编码，无外部输入
    r = subprocess.run(  # noqa: S603
        [sys.executable, str(SRC), "selftest"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    print("还原后自检：", "全部通过" if "全部通过" in r.stdout else "异常，请检查")
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
