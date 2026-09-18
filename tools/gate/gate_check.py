#!/usr/bin/env python3
"""
双线推进门禁检查脚本 · 现代金租 × EKOS MVP
==========================================

用途
----
把《双线推进方案-最小任务集与门禁验证-内部-260903-v2.2.md》§4.2 的常检中
**可自动化的六条**变成可执行的门禁，挂在每个里程碑出口跑，不靠人自觉。

    TR-1  内核零行业词      共用层代码零信贷术语（金租特有逻辑必须落配置层）
    TR-2  开源组件零侵入    vendor 目录无本地修改（git status / hash 基线）
    TR-4  版本零漂移        与 60.90.03 §3.7 冻结版本表逐项 diff
    TR-6  语种未写死在内核  共用层无硬编码语种分支 + P1/P2/P3 结构预留在位
    TR-7  端口零硬编码      代码内无 host:port 字面量；compose 主机侧端口避开冻结段
    TR-8  样本目录零真实数据 fixtures/golden 无身份证、银行卡、统一社会信用代码、手机号

判据源：`gate_baseline.json`。**改那个文件等于改门禁**，任何修改须写入变更记录，
禁止为通过门禁而放宽判据——放一次，后面所有合规论据全废。

用法
----
    # 1. 自动检查（每次门禁都跑，5 分钟）
    python3 gate_check.py auto
    python3 gate_check.py auto --root /path/to/repo        # 指定被检仓库
    python3 gate_check.py auto --json                      # 机器可读输出
    python3 gate_check.py auto --out 记录/Q3-auto.md       # 存留痕文件

    # 2. 人工检查清单（每道门禁导出一次，逐条填写，不得留空）
    python3 gate_check.py checklist Q3
    python3 gate_check.py checklist Q3 --out 记录/Q3-人工清单.md

    # 3. 生成 vendor hash 基线（引入开源组件的当天必跑一次）
    python3 gate_check.py baseline

    # 4. 自检：造一个含违规的样例仓库，验证各条检查真的抓得到
    python3 gate_check.py selftest

退出码
------
    0  通过（无 FAIL，WARN 需人工确认）
    1  未通过（存在 FAIL）
    2  用法或配置错误

约定
----
- 仅用 Python 标准库，无任何第三方依赖 —— 甲方干净机、离线环境都能跑。
  **唯一例外**：TR-8 解析 PDF 样本时用 PyMuPDF（§3.7 已冻结的运行时依赖）。
  取不到就降级，**但降级不看做干净** —— 扫不了的文件一律报 WARN 逐个列出，
  由人确认。「没查到」不等于「没有」，这条对整个脚本都成立。
- 只读，不修改被检仓库任何文件（`baseline` 只写本目录下的 vendor_hashes.json）。
- 「本期不覆盖」≠「下调阈值」：脚本不因某组件未落地而静默放行，一律记为 WARN 并显式列出。

作者：西北人 · 维护：勤思技术侧
"""

import argparse
import ast
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_ERR = 2

FAIL, WARN, INFO, SKIP, PASS = "FAIL", "WARN", "INFO", "SKIP", "PASS"
ICON = {FAIL: "[FAIL]", WARN: "[WARN]", INFO: "[INFO]", SKIP: "[SKIP]", PASS: "[ OK ]"}

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "gate_baseline.json"
VENDOR_HASH_FILE = "vendor_hashes.json"

MAX_REPORT_PER_CHECK = 30  # 单项最多打印多少条，避免刷屏淹没结论

# 自动化常检清单 —— 只在此处定义一次。
# run_auto（决定跑哪些）与 render_text（决定显示哪些）都从这里取。
# 之前两处各写一份，加一条检查时只改一处就会出现「跑了不显示」或「显示没跑」；
# 这种双口径的恶果是：报告上看着全绿，其实新检查一次都没执行过。
AUTO_CHECKS = ("TR-1", "TR-2", "TR-4", "TR-6", "TR-7", "TR-8")


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------
class Finding:
    __slots__ = ("check", "level", "where", "message")

    def __init__(self, check, level, where, message):
        self.check = check
        self.level = level
        self.where = where
        self.message = message

    def as_dict(self):
        return {
            "check": self.check,
            "level": self.level,
            "where": self.where,
            "message": self.message,
        }


def load_config(path):
    p = Path(path).resolve()
    if not p.is_file():
        sys.stderr.write("配置不存在：%s\n" % p)
        sys.exit(EXIT_ERR)
    try:
        return json.loads(p.read_text(encoding="utf-8")), p
    except Exception as e:
        sys.stderr.write("配置解析失败：%s\n" % e)
        sys.exit(EXIT_ERR)


def resolve_root(cfg, cfg_path, override):
    if override:
        return Path(override).resolve()
    return (cfg_path.parent / cfg["scan"]["root"]).resolve()


def is_exempt(rel_path, exempt_dirs):
    """路径任一级目录名命中豁免，或整体前缀命中（支持 'a/b' 形式）"""
    parts = rel_path.parts
    for name in parts:
        if name in exempt_dirs:
            return True
    rel_str = str(rel_path)
    for ex in exempt_dirs:
        if "/" in ex and rel_str.startswith(ex.rstrip("/") + "/"):
            return True
    return False


def _self_rel_dir(root):
    """门禁脚本自身所在目录（相对扫描根）；不在扫描根下则返回 None。

    为什么必须自排除：selftest 为了验证「门禁抓得到违规」，会在本文件里
    写入违规样例字符串（如 '授信额度'、'if lang == "zh"'）。若扫描自身，
    这些样例会把门禁自己判成 FAIL —— 这是误报，而误报的真实代价是逼人
    去放宽判据来「消灾」，最终把门禁废掉。

    为什么用运行时计算而不是往 exempt_dirs 写死一条路径：目录一迁移，
    写死的豁免立刻失效并静默恢复误报；自排除跟随脚本，不会腐烂。
    """
    try:
        return Path(__file__).resolve().parent.relative_to(Path(root).resolve())
    except ValueError:
        return None


def _is_under(rel, parent):
    """rel 是否位于 parent 目录之下（含同目录）"""
    return rel.parts[: len(parent.parts)] == parent.parts


def iter_code_files(root, cfg, exts=None):
    """遍历被检代码/配置文件，应用豁免目录与扩展名过滤"""
    scan = cfg["scan"]
    exts = tuple(exts if exts is not None else (scan["code_ext"] + scan["config_ext"]))
    root = Path(root)
    self_rel = _self_rel_dir(root)
    for f in root.rglob("*"):
        if not f.is_file():
            continue
        try:
            rel = f.relative_to(root)
        except ValueError:
            continue
        if self_rel is not None and _is_under(rel, self_rel):
            continue
        if is_exempt(rel, scan["exempt_dirs"]):
            continue
        if exts and not str(f).endswith(exts):
            continue
        try:
            if f.stat().st_size > scan.get("max_file_bytes", 2 * 1024 * 1024):
                continue
        except OSError:
            continue
        yield f, rel


def read_lines(f):
    try:
        return f.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []


def run_git(args, cwd, timeout=20):
    try:
        r = subprocess.run(
            ["git"] + args,
            cwd=str(cwd),
            timeout=timeout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return r.returncode, r.stdout.decode("utf-8", "ignore")
    except Exception as e:
        return -1, str(e)


# --------------------------------------------------------------------------
# TR-1 内核零行业词
# --------------------------------------------------------------------------
def check_tr1(root, cfg, out):
    spec = cfg.get("TR-1", {})
    words = spec.get("industry_words", [])
    if not words:
        out.append(Finding("TR-1", SKIP, "-", "未配置行业词表"))
        return 0

    hits, scanned = [], 0
    for f, rel in iter_code_files(
        root, cfg, exts=tuple(cfg["scan"]["code_ext"] + cfg["scan"]["config_ext"])
    ):
        scanned += 1
        # 与 TR-6 同口径：注释与文档字符串不参与执行，写在里面不改变任何行为。
        #
        # 不排除的代价反而更大 —— 在 schema 里写「内核一旦长出信贷字段就会退化成
        # 报告生成器」这类警示性注释会被判 FAIL。人要消掉这条 FAIL，只有两条路：
        # 删掉警示（后人不认识这条红线），或放宽判据（门禁失真）。
        # 两者都比「注释里出现过这个词」糟得多。
        #
        # 补偿控制：真实值仍在扫描范围内 —— 变量 / 字段 / 函数命名、字符串常量、
        # pyproject 的 description。行业语义要落地必须落在这些地方，
        # 注释里写一百遍也运行不起来。
        doc_lines = _docstring_lines(f) if str(f).endswith(".py") else set()
        for i, line in enumerate(read_lines(f), 1):
            if i in doc_lines:
                continue
            code = _strip_comment(line)
            if not code.strip():
                continue
            for w in words:
                if w in code:
                    hits.append((str(rel), i, w, line.strip()[:120]))
                    break  # 一行只报一次，避免同义词刷屏

    if not hits:
        out.append(Finding("TR-1", PASS, "-", "命中 0 处（扫描 %d 个文件）" % scanned))
        return scanned

    for rel, ln, w, text in hits[:MAX_REPORT_PER_CHECK]:
        out.append(Finding("TR-1", FAIL, "%s:%d" % (rel, ln), "行业词「%s」→ %s" % (w, text)))
    if len(hits) > MAX_REPORT_PER_CHECK:
        out.append(
            Finding("TR-1", FAIL, "-", "另有 %d 处命中未展开" % (len(hits) - MAX_REPORT_PER_CHECK))
        )
    return scanned


# --------------------------------------------------------------------------
# TR-2 开源组件零侵入
# --------------------------------------------------------------------------
def _vendor_files(vdir, hash_ext):
    vdir = Path(vdir)
    for f in sorted(vdir.rglob("*")):
        if not f.is_file():
            continue
        if hash_ext and not str(f).endswith(tuple(hash_ext)):
            continue
        if ".git/" in str(f.relative_to(vdir)):
            continue
        yield f


def _hash_vendor(vdir, hash_ext):
    h = hashlib.sha256()
    for f in _vendor_files(vdir, hash_ext):
        rel = f.relative_to(vdir)
        h.update(str(rel).encode("utf-8"))
        try:
            h.update(hashlib.sha256(f.read_bytes()).hexdigest().encode("ascii"))
        except OSError:
            h.update(b"<unreadable>")
    return h.hexdigest()


def check_tr2(root, cfg, out, cfg_path, do_baseline=False):
    spec = cfg.get("TR-2", {})
    dirs = spec.get("vendor_dirs", [])
    hash_ext = spec.get("hash_ext", [])
    root = Path(root)
    hash_store = cfg_path.parent / VENDOR_HASH_FILE
    store = {}
    if hash_store.is_file():
        try:
            store = json.loads(hash_store.read_text(encoding="utf-8"))
        except Exception:
            store = {}

    found_any = False
    for name in dirs:
        vdir = root / name
        if not vdir.is_dir():
            continue
        found_any = True

        if (vdir / ".git").exists():
            rc, so = run_git(["status", "--porcelain"], vdir)
            if rc != 0:
                out.append(Finding("TR-2", WARN, name, "git status 执行失败：%s" % so.strip()[:80]))
                continue
            dirty = [ln for ln in so.splitlines() if ln.strip()]
            if dirty:
                for ln in dirty[:MAX_REPORT_PER_CHECK]:
                    out.append(
                        Finding("TR-2", FAIL, name, "vendor 内已被修改：%s" % ln.strip()[:120])
                    )
                if len(dirty) > MAX_REPORT_PER_CHECK:
                    out.append(
                        Finding(
                            "TR-2",
                            FAIL,
                            name,
                            "另有 %d 处修改" % (len(dirty) - MAX_REPORT_PER_CHECK),
                        )
                    )
            else:
                out.append(Finding("TR-2", PASS, name, "git 工作区干净，无本地修改"))
            continue

        digest = _hash_vendor(vdir, hash_ext)
        if do_baseline or name not in store:
            store[name] = {
                "sha256": digest,
                "generated": datetime.now().isoformat(timespec="seconds"),
            }
            out.append(Finding("TR-2", INFO, name, "已写入 hash 基线 %s…" % digest[:16]))
            continue
        if store[name].get("sha256") != digest:
            out.append(
                Finding(
                    "TR-2",
                    FAIL,
                    name,
                    "内容与基线不一致（基线 %s…，当前 %s…）→ 疑似改动了开源组件，"
                    "确认是有意升级请先走变更记录再重建基线"
                    % (store[name]["sha256"][:16], digest[:16]),
                )
            )
        else:
            out.append(Finding("TR-2", PASS, name, "与 hash 基线一致，无本地修改"))

    if do_baseline:
        hash_store.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")

    if not found_any:
        out.append(
            Finding(
                "TR-2",
                SKIP,
                "-",
                "未发现 vendor 目录（%s）；引入开源组件当天必须跑 `baseline` 建基线"
                % "、".join(dirs),
            )
        )
    return store


# --------------------------------------------------------------------------
# TR-4 版本零漂移
# --------------------------------------------------------------------------
# 组件名与版本号之间允许出现的分隔符（一次性定义清楚，不要逐个写法打补丁）。
# 每漏一种分隔符，就多一次「未找到」伪装成的静默放行 —— 这比报错更危险。
#   空白 | 引号 | PEP 508 extras [standard] | 比较操作符 == >= ~= | 结构分隔符 , : . / - _
_SEP_ANY = r"""(?:[a-z0-9._-]*[/:]|\[[^\]]*\]|["']|[=:~<>!]+|[,:._\s-])*"""


def _strip_comment(line):
    """去掉行注释后再做版本匹配。

    为什么必须去注释：compose 与 SQL 的注释里写着「冻结 PostgreSQL 16.6 +
    pgvector 0.8.0」，而实际装的是 pgvector 0.5.0 —— 门禁却判定「0.8.0 在位」。
    **让注释满足版本校验，等于拿文档顶替现实**：文档永远写对的，环境未必。
    这比「未找到」更糟 —— 「未找到」是没结论，这个是**肯定的错误结论**。

    只去行注释；版本号里不会出现 #，且要求 # 前有空白，故引号内的 # 不受影响。
    """
    s = line.strip()
    if s.startswith("#") or s.startswith("--") or s.startswith("//"):
        return ""
    idx = line.find(" #")
    if idx != -1:
        line = line[:idx]
    return line


def check_tr4(root, cfg, out):
    spec = cfg.get("TR-4", {})
    comps = spec.get("components", [])
    root = Path(root)

    targets = []
    for pat in spec.get("match_files", []):
        for f in root.rglob(pat):
            if f.is_file() and not is_exempt(f.relative_to(root), cfg["scan"]["exempt_dirs"]):
                targets.append(f)
    targets = sorted(set(targets))

    if not targets:
        out.append(
            Finding(
                "TR-4",
                WARN,
                "-",
                "未找到版本声明文件（%s），无法核对 §3.7 冻结表"
                % "、".join(spec.get("match_files", [])),
            )
        )

    blob = []
    for f in targets:
        for i, line in enumerate(read_lines(f), 1):
            # 去注释后再入 blob：注释不得满足版本校验，也不得触发 latest 禁令
            blob.append((f.relative_to(root), i, _strip_comment(line)))
    # 禁止 :latest
    if spec.get("forbid_latest", True):
        for rel, ln, line in blob:
            if re.search(r"[:=]\s*latest\b", line, re.I):
                out.append(
                    Finding(
                        "TR-4",
                        FAIL,
                        "%s:%d" % (rel, ln),
                        "使用 latest 标签 —— 禁止自动拉取 latest，必须锁死版本",
                    )
                )

    not_found = []
    for c in comps:
        # 松化组件名与版本号之间的连接：真实写法五花八门 ——
        #   image: vllm/vllm-openai:0.7.0        （Docker 镜像含路径与连字符）
        #   FROM python:3.11.10-slim             （基础镜像）
        #   uvicorn[standard]==0.34.0            （PEP 508 extras + 精确锁定）
        #   requires-python = "==3.11.10"        （TOML：引号 + 双等号 + 空格）
        #   fastapi==0.115.6, celery>=5.4.0      （PEP 508 比较操作符）
        # 配置里统一写作 `名[\s:=_-]*(版本)`，这里把分隔符集一次性展开。
        #
        # 用 lambda 作为 re.sub 的 repl：替换模板会把 \s \[ \- 当转义序列解析并
        # 抛 bad escape，传函数则原样返回，正则元字符免转义。
        loose = re.sub(r"\[\\s[^\]]*\]\*", lambda _m: _SEP_ANY, c["regex"])
        rx = re.compile(loose, re.I)
        got = set()
        for _, _, line in blob:
            for m in rx.finditer(line):
                got.add(m.group(1))
        # 匹配粒度：默认精确到补丁号；match="major" 时只比主版本。
        #
        # 为什么需要 major 档：PostgreSQL 16.6 的小版本钉不住 ——
        # pgvector/pgvector 的镜像标签只编码主版本（0.8.0-pg16-bookworm），
        # 查 Docker Hub 确认没有任何标签带 16.6。
        # 此时若改用「写个注释/环境变量假装声明」来消掉告警，是更糟的做法：
        # 那正是刚修掉的「声明顶替现实」。正确做法是**显式声明**这一项接受
        # 主版本匹配，同时把偏差记进台账，而不是悄悄放宽。
        frozen = c["frozen"]
        major_mode = c.get("match") == "major"
        if major_mode:
            matched = sorted(g for g in got if g.split(".")[0] == frozen.split(".")[0])
        else:
            matched = [frozen] if frozen in got else []

        if matched:
            others = sorted(got - set(matched))
            if others:
                out.append(
                    Finding(
                        "TR-4",
                        WARN,
                        c["key"],
                        "%s 冻结版本 %s 在位（%s）；但另有声明 %s，请人工确认是否为无关位置"
                        % (
                            c["label"],
                            frozen,
                            "主版本匹配" if major_mode else "精确匹配",
                            "、".join(others),
                        ),
                    )
                )
            else:
                suffix = "（主版本匹配，小版本滚动）" if major_mode else ""
                out.append(
                    Finding(
                        "TR-4",
                        PASS,
                        c["key"],
                        "%s = %s%s" % (c["label"], "、".join(matched), suffix),
                    )
                )
        elif got:
            out.append(
                Finding(
                    "TR-4",
                    FAIL,
                    c["key"],
                    "%s 期望 %s，实得 %s → 版本漂移，须改回或走变更记录"
                    % (
                        c["label"],
                        frozen + ("（主版本）" if major_mode else ""),
                        "、".join(sorted(got)),
                    ),
                )
            )
        else:
            not_found.append("%s %s" % (c["label"], frozen))

    if not_found:
        out.append(
            Finding(
                "TR-4",
                WARN,
                "未落地",
                "%d 个组件未在版本声明文件中找到：%s → 早期门禁属正常，Q1 出口（版本表落地）须全部补齐"
                % (len(not_found), "；".join(not_found)),
            )
        )
    return len(targets)


# --------------------------------------------------------------------------
# TR-6 语种未写死在内核
# --------------------------------------------------------------------------
def _docstring_lines(path):
    """返回 Python 文件中所有文档字符串覆盖的行号集合（1-based）。

    判据 TR-6 的 warn_cjk_literal.desc 白纸黑字写着「排除注释与文档字符串」，
    但行首判断只能拦住三引号起始行，拦不住文档字符串的正文 —— 结果是整段
    中文说明被当成「疑似被写死的中文规则」报出来。误报的代价不是多打几行字，
    而是人会对 WARN 脱敏，这条通道就废了。

    为什么用 ast 而不是继续给行首判断打补丁：文档字符串不参与执行，写在里面
    的文本不影响任何行为；而作为值使用的三引号字符串（RULES = \"\"\"...\"\"\"）
    不是 docstring，ast 取不到，仍会被检出。判据的意图由此被准确实现。
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError, OSError):
        return set()
    lines = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if not isinstance(first, ast.Expr):
            continue
        if not isinstance(first.value, ast.Constant) or not isinstance(first.value.value, str):
            continue
        start = first.lineno
        end = getattr(first, "end_lineno", start) or start
        lines.update(range(start, end + 1))
    return lines


def _is_comment_or_doc(line):
    s = line.strip()
    if s.startswith("#") or s.startswith("//") or s.startswith("*"):
        return True
    if s.startswith('"""') or s.startswith("'''") or s.startswith('r"""'):
        return True
    return False


def _order_hits(hits, shared_prefixes=("packages/", "apps/")):
    """命中排序：共用层优先，其次按路径与行号稳定排序。

    为什么需要这一层排序
    --------------------
    TR-6 的 WARN 每类只显示前 10 条。若直接按 `rglob` 的遍历顺序取前 10，
    测试层的中文断言消息（数量天然最多）会把共用层的命中挤出可见列表——
    实测（2026-09-05）：中文字面量实际命中 45 条，可见 10 条全是 `tests/`，
    `packages/modelgw/stub.py` 的 5 条共用层命中一次都没露出来。

    WARN 是要人工签字的。签字的人看不到共用层那几条，就无从判断它们无害，
    只能对着满屏测试层噪音默认放行——「查了但看不见」对签字人与「没查」等价。

    排序只影响显示顺序，不影响命中集合，也不触碰任何判据正则。
    """
    return sorted(
        hits,
        key=lambda h: (0 if str(h[0]).startswith(shared_prefixes) else 1, str(h[0]), h[1]),
    )


def check_tr6(root, cfg, out):
    import fnmatch

    spec = cfg.get("TR-6", {})
    root = Path(root)
    code_ext = tuple(cfg["scan"]["code_ext"])

    fail_rx = [re.compile(p) for p in spec.get("fail_patterns", [])]
    warn_rx = [re.compile(p) for p in spec.get("warn_patterns", [])]
    cjk_cfg = spec.get("warn_cjk_literal", {}) or {}
    cjk_on = cjk_cfg.get("enabled", False)
    cjk_min = cjk_cfg.get("min_cjk_len", 4)
    cjk_rx = re.compile(r"['\"]([^'\"]*[\u4e00-\u9fff]{%d,}[^'\"]*)['\"]" % cjk_min)

    fail_hits, warn_hits, cjk_hits, scanned = [], [], [], 0
    for f, rel in iter_code_files(root, cfg, exts=code_ext):
        scanned += 1
        doc_lines = _docstring_lines(f) if str(f).endswith(".py") else set()
        for i, line in enumerate(read_lines(f), 1):
            if i in doc_lines:
                continue
            for rx in fail_rx:
                if rx.search(line):
                    fail_hits.append((str(rel), i, line.strip()[:120]))
                    break
            else:
                if _is_comment_or_doc(line):
                    continue
                for rx in warn_rx:
                    if rx.search(line):
                        warn_hits.append((str(rel), i, line.strip()[:120]))
                        break
                else:
                    if cjk_on:
                        m = cjk_rx.search(line)
                        if m:
                            cjk_hits.append((str(rel), i, line.strip()[:120]))

    if not (fail_hits or warn_hits or cjk_hits):
        out.append(
            Finding("TR-6", PASS, "-", "共用层无硬编码语种分支（扫描 %d 个代码文件）" % scanned)
        )

    # 先排序再截断：保证被截掉的是测试层噪音，而不是共用层命中
    warn_hits = _order_hits(warn_hits)
    cjk_hits = _order_hits(cjk_hits)

    for rel, ln, text in fail_hits[:MAX_REPORT_PER_CHECK]:
        out.append(Finding("TR-6", FAIL, "%s:%d" % (rel, ln), "硬编码语种分支 → %s" % text))
    for rel, ln, text in warn_hits[:10]:
        out.append(
            Finding(
                "TR-6", WARN, "%s:%d" % (rel, ln), "出现语种字面量，确认是否在共用层 → %s" % text
            )
        )
    for rel, ln, text in cjk_hits[:10]:
        out.append(
            Finding(
                "TR-6",
                WARN,
                "%s:%d" % (rel, ln),
                "代码内中文字符串字面量（疑似被写死的规则）→ %s" % text,
            )
        )
    # 截断必须自报数量：只显示 10 条却不说是 45 条里的 10 条，
    # 签字的人会以为全库就这 10 处，据此签的「已确认」是假的。
    for label, hits in (("语种字面量", warn_hits), ("中文字面量", cjk_hits)):
        if len(hits) > 10:
            out.append(
                Finding(
                    "TR-6",
                    INFO,
                    "-",
                    "%s WARN 实为 %d 条，此处显示前 10 条（已按共用层优先排序，"
                    "被折叠的多为测试层断言消息）" % (label, len(hits)),
                )
            )
    if len(fail_hits) > MAX_REPORT_PER_CHECK:
        out.append(
            Finding(
                "TR-6",
                FAIL,
                "-",
                "另有 %d 处硬编码分支未展开" % (len(fail_hits) - MAX_REPORT_PER_CHECK),
            )
        )

    # P1 / P2 / P3 结构预留
    all_files = []
    for f in root.rglob("*"):
        if f.is_file() and not is_exempt(f.relative_to(root), cfg["scan"]["exempt_dirs"]):
            all_files.append(f)

    for r in spec.get("structure_reservations", []):
        rid = r.get("id", "?")
        desc = r.get("desc", "")
        ok = False
        if "expect_config_dirs" in r:
            for d in r["expect_config_dirs"]:
                cd = root / d
                if cd.is_dir():
                    pats = r.get("expect_files", ["*"])
                    # 必须展开生成器再判真值。
                    # `any(cd.rglob(p) for p in pats)` 恒为 True —— rglob 返回的是
                    # 生成器对象，对象本身永远真值，一个文件都没匹配到也照样通过。
                    # 后果：只要建了空的 configs/ 目录，P2 就变绿，
                    # 而「切分规则与别名表」可能一条都没落 —— 这正是 P2 要防的事。
                    # 判据写的是「落在配置层」，空目录不是配置层。
                    if any(True for p in pats for _ in cd.rglob(p)):
                        ok = True
                        break
        else:
            rx = re.compile(r.get("expect_regex", ""))
            for f in all_files:
                name = f.name
                if any(fnmatch.fnmatch(name, p) for p in r.get("expect_file_patterns", [])):
                    if rx.search(f.read_text(encoding="utf-8", errors="ignore")):
                        ok = True
                        break
        if ok:
            out.append(Finding("TR-6", PASS, rid, "结构预留 %s 在位：%s" % (rid, desc)))
        else:
            out.append(
                Finding(
                    "TR-6",
                    WARN,
                    rid,
                    "结构预留 %s 未检出：%s → 下期恢复成本 90%% 取决于结构在不在，这一条不能拖"
                    % (rid, desc),
                )
            )
    return scanned


# --------------------------------------------------------------------------
# TR-7 端口零硬编码
# --------------------------------------------------------------------------
def check_tr7(root, cfg, out):
    spec = cfg.get("TR-7", {})
    if not spec:
        out.append(Finding("TR-7", SKIP, "-", "未配置 TR-7 判据"))
        return

    # ---- 1) 代码里的 host:port 字面量 ----
    # 只判代码，不判 compose 的容器侧端口：5432/6379/9000 是各服务在容器内的
    # 标准端口，改不得也没必要改。真正要防的是「代码里写死地址」——
    # 那种写法在切环境的当天才炸，而报错是「连接被拒」，没人会想到是端口写死。
    code_spec = spec.get("code", {})
    pat = re.compile(code_spec.get("pattern", r"(?!)"))
    scan_dirs = set(code_spec.get("scan_dirs", []))
    hits, scanned = [], 0
    for f, rel in iter_code_files(root, cfg):
        if scan_dirs and not (rel.parts and rel.parts[0] in scan_dirs):
            continue
        scanned += 1
        for i, line in enumerate(read_lines(f), 1):
            if pat.search(_strip_comment(line)):
                hits.append((str(rel), i, line.strip()[:120]))

    if hits:
        for rel, ln, text in hits[:MAX_REPORT_PER_CHECK]:
            out.append(
                Finding("TR-7", FAIL, "%s:%d" % (rel, ln), "代码内硬编码 host:port → %s" % text)
            )
        if len(hits) > MAX_REPORT_PER_CHECK:
            out.append(
                Finding("TR-7", FAIL, "-", "另有 %d 处未展开" % (len(hits) - MAX_REPORT_PER_CHECK))
            )
    else:
        out.append(
            Finding("TR-7", PASS, "-", "代码中无 host:port 字面量（扫描 %d 个文件）" % scanned)
        )

    # ---- 2) compose 主机侧端口是否占用冻结段 ----
    compose_spec = spec.get("compose", {})
    frozen = set(compose_spec.get("frozen_ports", []))
    host_pat = re.compile(compose_spec.get("host_port_pattern", r"(?!)"))
    c_hits, c_scanned = [], 0
    for f, rel in iter_code_files(root, cfg):
        if f.suffix not in (".yml", ".yaml"):
            continue
        c_scanned += 1
        for i, line in enumerate(read_lines(f), 1):
            m = host_pat.match(line)
            if not m:
                continue
            host_port = m.group(1)
            if host_port in frozen:
                c_hits.append((str(rel), i, host_port, line.strip()[:120]))

    if c_hits:
        for rel, ln, port, text in c_hits[:MAX_REPORT_PER_CHECK]:
            out.append(
                Finding(
                    "TR-7",
                    FAIL,
                    "%s:%d" % (rel, ln),
                    "compose 主机侧端口 %s 占用交付冻结段（应改到 15xxx 段）→ %s" % (port, text),
                )
            )
    elif c_scanned:
        out.append(
            Finding(
                "TR-7",
                PASS,
                "-",
                "compose 主机侧端口均避开冻结段（扫描 %d 个编排文件）" % c_scanned,
            )
        )
    else:
        out.append(Finding("TR-7", SKIP, "-", "未发现编排文件，主机侧端口未校验"))


# --------------------------------------------------------------------------
# TR-8 样本目录零真实数据
# --------------------------------------------------------------------------
def _extract_sample_text(f):
    """把样本文件转成可搜索文本；**无法提取时返回 None**（不是返回空串）。

    为什么必须能读 PDF：真实样本最可能就是 PDF，而 PDF 是压缩二进制，
    按文本 grep 一个字都匹配不到。不做提取的样本检查，等于对最主要的威胁视而不见。

    为什么返回 None 而不是抛异常：本脚本承诺「仅用标准库」，PyMuPDF 取不到时
    （甲方干净机、离线环境）必须能降级。但降级由调用方报 WARN 逐个列出未扫文件，
    绝不静默当作干净 —— 「没查到」不等于「没有」。
    """
    suf = f.suffix.lower()
    if suf in (".txt", ".md", ".csv", ".json"):
        try:
            return f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None
    if suf == ".pdf":
        try:
            import fitz  # PyMuPDF（§3.7 冻结的运行时依赖）
        except ImportError:
            return None
        try:
            with fitz.open(f) as doc:
                return "\n".join(page.get_text() for page in doc)
        except Exception:
            return None
    if suf in (".docx", ".xlsx"):
        import zipfile

        target = "word/document.xml" if suf == ".docx" else "xl/sharedStrings.xml"
        try:
            with zipfile.ZipFile(f) as z:
                xml = z.read(target).decode("utf-8", "ignore")
            return re.sub(r"<[^>]+>", " ", xml)
        except Exception:
            return None
    # 图片类无法 OCR（PaddleOCR 留 E1 验证），诚实报「未扫描」
    return None


def check_tr8(root, cfg, out):
    spec = cfg.get("TR-8", {})
    if not spec:
        out.append(Finding("TR-8", SKIP, "-", "未配置 TR-8 判据"))
        return

    dirs = spec.get("sample_dirs", [])
    exts = tuple(spec.get("sample_ext", []))
    pats = [(k, re.compile(v)) for k, v in spec.get("patterns", {}).items()]

    existed = [(d, root / d) for d in dirs if (root / d).is_dir()]
    if not existed:
        # 目录都不存在时判 SKIP 而不是 PASS：一个样本都没扫过，
        # 报 PASS 会让人以为「检查过了，干净」，实际上什么都没查。
        out.append(
            Finding(
                "TR-8",
                SKIP,
                "-",
                "未发现任何样本目录（%s）→ 未扫描。Q1 出口须人工确认样本策略" % "、".join(dirs),
            )
        )
        return

    files = []
    for _d, base in existed:
        for f in sorted(base.rglob("*")):
            if f.is_file() and (not exts or str(f).lower().endswith(exts)):
                files.append(f)

    if not files:
        out.append(
            Finding(
                "TR-8",
                SKIP,
                "-",
                "样本目录存在但为空（%s）→ 未扫描任何文件。空目录不等于安全"
                % "、".join(d for d, _ in existed),
            )
        )
        return

    scanned, unscanned, hits = 0, [], []
    for f in files:
        try:
            if f.stat().st_size > cfg["scan"].get("max_file_bytes", 2 * 1024 * 1024):
                unscanned.append((str(f.relative_to(root)), "超过大小上限，未扫描"))
                continue
        except OSError:
            continue
        text = _extract_sample_text(f)
        if text is None:
            unscanned.append((str(f.relative_to(root)), "内容无法提取，未扫描"))
            continue
        scanned += 1
        for label, pat in pats:
            m = pat.search(text)
            if m:
                hits.append((str(f.relative_to(root)), label))
                break  # 一个文件只报一次，避免同一个人被刷屏

    if hits:
        for rel, label in hits[:MAX_REPORT_PER_CHECK]:
            out.append(Finding("TR-8", FAIL, rel, "样本中检出疑似真实「%s」" % label))
        if len(hits) > MAX_REPORT_PER_CHECK:
            out.append(
                Finding(
                    "TR-8", FAIL, "-", "另有 %d 个文件未展开" % (len(hits) - MAX_REPORT_PER_CHECK)
                )
            )
    else:
        out.append(Finding("TR-8", PASS, "-", "已扫描 %d 个样本文件，未检出真实敏感标识" % scanned))

    # 扫不了的文件必须显式列出 —— 静默跳过是最危险的「通过」
    for rel, why in unscanned[:MAX_REPORT_PER_CHECK]:
        out.append(Finding("TR-8", WARN, rel, "%s → 需人工确认" % why))


# --------------------------------------------------------------------------
# 汇总与渲染
# --------------------------------------------------------------------------
def run_auto(root, cfg, cfg_path, do_baseline=False):
    findings = []
    # 调度表与 AUTO_CHECKS 对齐：清单里写了却没有实现的检查，
    # 会被显式记成 SKIP 而不是悄悄跳过 —— 「清单有、实现无」正是双口径的入口，
    # 静默跳过的后果是报告里这一项整条消失，没人知道它从未执行过。
    dispatch = {
        "TR-1": lambda: check_tr1(root, cfg, findings),
        "TR-2": lambda: check_tr2(root, cfg, findings, cfg_path, do_baseline=do_baseline),
        "TR-4": lambda: check_tr4(root, cfg, findings),
        "TR-6": lambda: check_tr6(root, cfg, findings),
        "TR-7": lambda: check_tr7(root, cfg, findings),
        "TR-8": lambda: check_tr8(root, cfg, findings),
    }
    for cid in AUTO_CHECKS:
        fn = dispatch.get(cid)
        if fn is None:
            findings.append(Finding(cid, SKIP, "-", "AUTO_CHECKS 已登记但无对应实现"))
            continue
        fn()
    return findings


def _authority(cfg):
    """从配置的 authority_doc 文件名里取权威文档名与版本号。
    只改配置里的路径一处，脚本输出自动跟着走——避免升版时遗漏硬编码版本号。"""
    path = cfg.get("meta", {}).get("authority_doc", "")
    name = Path(path).name
    m = re.search(r"v\d+(?:\.\d+)*", name)
    return name, (m.group(0) if m else "")


def count_levels(findings):
    c = {FAIL: 0, WARN: 0, INFO: 0, SKIP: 0, PASS: 0}
    for f in findings:
        c[f.level] = c.get(f.level, 0) + 1
    return c


def render_text(findings, root, cfg):
    lines = []
    # 标题随 AUTO_CHECKS 自动生成，不写死 —— 否则加一条检查，标题还写着旧的四条
    lines.append("=== 门禁自动检查 · %s ===" % " / ".join(AUTO_CHECKS))
    lines.append("时间：%s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("被检根目录：%s" % root)
    lines.append("判据源：gate_baseline.json（= 《%s》§4.2）" % _authority(cfg)[0])
    lines.append("")
    for check in AUTO_CHECKS:
        items = [f for f in findings if f.check == check]
        spec = cfg.get(check, {})
        lines.append("[%s %s]" % (check, spec.get("name", "")))
        if spec.get("principle"):
            lines.append("  判据：%s" % spec["principle"])
        if not items:
            lines.append("  （无结果）")
        for f in items:
            loc = "" if f.where == "-" else "%s → " % f.where
            lines.append("  %-7s %s%s" % (ICON[f.level], loc, f.message))
        lines.append("")
    c = count_levels(findings)
    lines.append("—— 汇总 ——")
    lines.append(
        "FAIL %d / WARN %d / INFO %d / SKIP %d / PASS %d"
        % (c[FAIL], c[WARN], c[INFO], c[SKIP], c[PASS])
    )
    if c[FAIL]:
        lines.append(
            "结论：FAIL 未通过。按《推进方案 %s》§4.3 对应门禁的「不通过怎么办」处置，不得放行。"
            % _authority(cfg)[1]
        )
    else:
        lines.append(
            "结论：PASS 通过（无 FAIL）。WARN 项须人工确认后签字，不得因 WARN 未看而默认放行。"
        )
    return "\n".join(lines)


def render_checklist(gate_id, cfg):
    gates = cfg.get("gates", {})
    g = gates.get(gate_id.upper())
    if not g:
        sys.stderr.write("未知道禁：%s（可选：%s）\n" % (gate_id, "、".join(sorted(gates))))
        sys.exit(EXIT_ERR)

    auto_items = [i for i in g["items"] if i["type"] == "auto"]
    man_items = [i for i in g["items"] if i["type"] != "auto"]

    L = []
    L.append("# %s · %s · 门禁检查清单" % (gate_id.upper(), g["name"]))
    L.append("")
    L.append(
        "> 里程碑 **%s** ｜ 计划出口 **%s** ｜ 生成时间 %s"
        % (g.get("milestone", "-"), g.get("date", "-"), datetime.now().strftime("%Y-%m-%d %H:%M"))
    )
    L.append("> 判据出处：《%s》§4.3" % _authority(cfg)[0])
    L.append("")
    L.append("## 出口物")
    L.append("")
    for o in g.get("outputs", []):
        L.append("- [ ] %s" % o)
    L.append("")
    L.append("## 一、自动检查（`python3 gate_check.py auto` 覆盖）")
    L.append("")
    if auto_items:
        L.append("| 编号 | 判据 | 结果 |")
        L.append("|---|---|---|")
        for i in auto_items:
            L.append("| %s | %s | 由脚本输出填入 |" % (i["id"], i["text"]))
    else:
        L.append("本道门禁无自动化常检项，全部人工判据。")
    L.append("")
    L.append("## 二、人工检查（逐条填写，**不得留空**）")
    L.append("")
    L.append(
        "| 编号 | 判据 | 结论（通过 / 不通过 / 本期不覆盖） | 证据（文件、实测值、见证人） | 填写人 |"
    )
    L.append("|---|---|---|---|---|")
    for i in man_items:
        L.append("| %s | %s | | | |" % (i["id"], i["text"]))
    L.append("")
    L.append(
        (
            "> **口径提醒**：「本期不覆盖」≠「下调阈值」。前者须在《推进方案 %s》附件 E 挂账并写明恢复条件；"
            "后者门禁不通过。实测表**不得留空、不得填 0**——留空被读成忘了测，填 0 被读成测了没过。"
        )
        % _authority(cfg)[1]
    )
    L.append("")
    if g.get("note"):
        L.append("> %s" % g["note"])
        L.append("")
    L.append("## 三、不通过怎么办")
    L.append("")
    L.append("> %s" % g.get("on_fail", "（未定义）"))
    L.append("")
    L.append("## 四、签字")
    L.append("")
    L.append("| 角色 | 姓名 | 结论 | 日期 |")
    L.append("|---|---|---|---|")
    L.append("| 技术（西北人） | | | |")
    L.append("| 业务（田总） | | | |")
    return "\n".join(L)


# --------------------------------------------------------------------------
# selftest：造违规样例，验证四条检查真的抓得到
# --------------------------------------------------------------------------
def selftest(cfg_path):
    cfg, cfg_path = load_config(cfg_path)
    tmp = Path(tempfile.mkdtemp(prefix="gate_selftest_"))
    try:
        repo = tmp / "repo"
        (repo / "src/core").mkdir(parents=True)
        (repo / "vendor/ragflow").mkdir(parents=True)
        (repo / "configs").mkdir(parents=True)
        (repo / "tests").mkdir()

        # TR-1 违规：内核里出现信贷术语
        (repo / "src/core/report.py").write_text(
            'def build():\n    limit = row["授信额度"]\n    return limit\n', encoding="utf-8"
        )
        # TR-1 反例：行业词只出现在文档字符串与注释里 —— 不参与执行，不得判 FAIL。
        # 若这条被判 FAIL，等于禁止在内核里写「不要写信贷逻辑」的警示；
        # 人要消掉它只有两条路：删掉警示（后人不认识红线），或放宽判据（门禁失真）。
        (repo / "src/core/warning_only.py").write_text(
            '"""本模块不得出现信贷语义：内核长了信贷字段就会退化成信贷报告生成器。"""\n'
            "def build():\n"
            "    # 禁止在此做授信额度相关计算 —— 行业逻辑一律走 configs/ 与 prompts/\n"
            "    return None\n",
            encoding="utf-8",
        )
        # TR-6 违规：硬编码语种分支 + 中文正则
        (repo / "src/core/split.py").write_text(
            "import re\n"
            'PAT = re.compile(r"[\\u4e00-\\u9fa5]+")\n'
            "def split(t, lang):\n"
            '    if lang == "zh":\n'
            "        return PAT.findall(t)\n",
            encoding="utf-8",
        )
        # TR-4：postgres 正确、vllm 漂移、redis 用 latest
        # 同时给 pg 配一个主机侧占用冻结段的端口映射（TR-7 违规样例）：
        # 容器侧 5432 是标准端口不用改，错的是主机侧也写 5432 —— 会与交付环境抢端口。
        (repo / "docker-compose.yml").write_text(
            "services:\n"
            '  pg:\n    image: postgres:16.6\n    ports:\n      - "5432:5432"\n'
            "  llm:\n    image: vllm/vllm-openai:0.7.0\n"
            "  cache:\n    image: redis:latest\n"
            # 注释里写对冻结版本、实际声明却是旧版 —— 必须仍判漂移。
            # 真实事故：pgvector 装的是 0.5.0，注释写着 0.8.0，门禁却报「0.8.0 在位」。
            "  store:\n    image: minio/minio:RELEASE.2023-01-01T00-00-00Z\n"
            "# 冻结 MinIO RELEASE.2024-11-07T00-52-20Z（注释写对，实际是旧版）\n",
            encoding="utf-8",
        )
        # P2 结构预留：配置层有 yaml
        (repo / "configs/chunk.yaml").write_text("rules: []\n", encoding="utf-8")
        # 干净文件（不应命中）
        (repo / "src/core/generic.py").write_text("def run(x):\n    return x\n", encoding="utf-8")
        # TR-6 正反例：文档字符串里的中文属文档、不该报；
        #             三引号字符串「常量值」参与执行、必须报。
        (repo / "src/core/docs.py").write_text(
            '"""本模块说明：这是一段中文文档字符串，用于说明切分规则的设计。"""\n'
            'RULES = """中文切分规则表：按句号与分号切分，再按段落合并。"""\n',
            encoding="utf-8",
        )
        # TR-7 违规：代码里写死 host:port（应走环境变量注入）
        (repo / "tests/hardcoded.py").write_text(
            "import httpx\n"
            "BASE = 'http://localhost:8000/v1'\n"
            "def ping():\n    return httpx.get(BASE + '/health')\n",
            encoding="utf-8",
        )
        # TR-8 违规：样本里混入真实身份证号
        (repo / "tests/fixtures").mkdir(parents=True)
        (repo / "tests/fixtures/sample.txt").write_text(
            "客户姓名：张三\n身份证号：110101199003076578\n", encoding="utf-8"
        )

        cfg_local = tmp / "gate_baseline.json"
        shutil.copy(cfg_path, cfg_local)
        cfg_local_cfg = json.loads(cfg_local.read_text(encoding="utf-8"))
        cfg_local_cfg["scan"]["root"] = str(repo)
        cfg_local.write_text(json.dumps(cfg_local_cfg, ensure_ascii=False), encoding="utf-8")

        # 先建 vendor 基线，再改文件 → 应被捕获
        (repo / "vendor/ragflow/api.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        out = []
        check_tr2(repo, cfg_local_cfg, out, cfg_local, do_baseline=True)
        (repo / "vendor/ragflow/api.py").write_text(
            "def f():\n    return 2  # 本地改了一行\n", encoding="utf-8"
        )

        findings = run_auto(repo, cfg_local_cfg, cfg_local)
        text = render_text(findings, repo, cfg_local_cfg)
        print(text)
        print()

        # P2 反例：只有空的 configs/ 目录、里面一个配置文件都没有，P2 必须判不通过。
        # 真实缺陷：判据写的是「切分规则与别名表落在配置层」，
        # 而 any(cd.rglob(p) for p in pats) 里 rglob 返回生成器对象、恒为真，
        # 于是「建个空目录」就算「落在配置层」了 —— 这条断言专门盯着它。
        empty = tmp / "empty_repo"
        (empty / "configs").mkdir(parents=True)
        out_empty = []
        check_tr6(empty, cfg_local_cfg, out_empty)
        p2_empty_passed = any(
            f.check == "TR-6" and f.level == PASS and f.where == "P2" for f in out_empty
        )

        # TR-8 反例：样本目录存在但为空，必须判 SKIP 而不是 PASS。
        # 判 PASS 意味着「检查过了，干净」，而实际上一个字节都没扫 ——
        # 这正是本项目反复出现的「看起来通过了，其实没查」。
        empty_samples = tmp / "empty_samples"
        (empty_samples / "tests/golden").mkdir(parents=True)
        out_tr8_empty = []
        check_tr8(empty_samples, cfg_local_cfg, out_tr8_empty)
        tr8_empty_skipped = any(f.level == SKIP for f in out_tr8_empty) and not any(
            f.level == PASS for f in out_tr8_empty
        )

        # TR-4 主版本匹配：放宽的是「粒度」，不是「放弃检查」。
        # 这两条断言专门盯住这个区别 —— 只断言 16.9 能过是不够的，
        # 必须同时断言 17.1 仍然过不了，否则「接受 16.x 滚动」随时会滑成「随便什么版本都行」。
        def tr4_verdict(image_line, tag):
            t = tmp / ("tr4_%s" % tag)
            t.mkdir(parents=True, exist_ok=True)
            (t / "docker-compose.yml").write_text(
                "services:\n  pg:\n    image: %s\n" % image_line, encoding="utf-8"
            )
            o = []
            check_tr4(t, cfg_local_cfg, o)
            return [f.level for f in o if f.where == "postgres"]

        tr4_169_ok = tr4_verdict("postgres:16.9", "a") == [PASS]
        tr4_171_fail = tr4_verdict("postgres:17.1", "b") == [FAIL]

        def has(check, level, keyword=""):
            return any(
                f.check == check and f.level == level and keyword in f.message for f in findings
            )

        def has_at(check, level, where_kw=""):
            """按位置（文件:行）匹配，而不是按 message。

            has() 只匹配描述文本，区分不出「哪一行被判 FAIL」。而反例断言要问的
            恰恰是这个：某个文件必须**一条 FAIL 都没有**。只能靠 where 判断。
            """
            return any(
                f.check == check and f.level == level and where_kw in str(f.where) for f in findings
            )

        asserts = [
            ("TR-1 抓到内核信贷术语", has("TR-1", FAIL, "授信额度")),
            ("TR-1 注释与文档字符串中的行业词不误报", not has_at("TR-1", FAIL, "warning_only")),
            ("TR-7 抓到代码内硬编码 host:port", has_at("TR-7", FAIL, "hardcoded")),
            ("TR-7 抓到 compose 主机侧占用冻结端口", has("TR-7", FAIL, "占用交付冻结段")),
            ("TR-8 抓到样本中的真实身份证号", has("TR-8", FAIL, "身份证")),
            ("TR-8 空样本目录判 SKIP 而非 PASS", tr8_empty_skipped),
            ("TR-2 抓到 vendor 内容漂移", has("TR-2", FAIL, "基线不一致")),
            ("TR-4 抓到 vLLM 版本漂移", has("TR-4", FAIL, "vLLM")),
            ("TR-4 抓到 latest 标签", has("TR-4", FAIL, "latest")),
            ("TR-4 注释里的冻结版本不能顶替实际声明", has("TR-4", FAIL, "MinIO")),
            (
                "TR-4 认可 PostgreSQL 冻结版本",
                any(
                    f.check == "TR-4" and f.level == PASS and "PostgreSQL" in f.message
                    for f in findings
                ),
            ),
            ("TR-4 主版本匹配认可 16.9", tr4_169_ok),
            ("TR-4 主版本不匹配时仍判漂移（17.1）", tr4_171_fail),
            ("TR-6 抓到硬编码语种分支", has("TR-6", FAIL, "硬编码语种分支")),
            (
                "TR-6 P2 结构预留检出",
                any(f.check == "TR-6" and f.level == PASS and f.where == "P2" for f in findings),
            ),
            ("干净文件未被误报", not any("generic.py" in (f.where or "") for f in findings)),
            ("TR-6 文档字符串中的中文不误报", not has("TR-6", WARN, "本模块说明")),
            ("TR-6 三引号字符串常量仍被抓到", has("TR-6", WARN, "中文切分规则表")),
            ("P2 空配置目录不得判为通过", not p2_empty_passed),
        ]
        print("=== 自检断言 ===")
        ok = True
        for name, passed in asserts:
            print("  %s %s" % ("PASS" if passed else "FAILED", name))
            ok = ok and passed
        print()
        print("自检结论：%s" % ("全部通过，门禁脚本可用" if ok else "存在未捕获项，脚本需修"))
        return EXIT_OK if ok else EXIT_FAIL
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="双线推进门禁检查（TR-1/TR-2/TR-4/TR-6 自动化 + 八道门禁人工清单）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("command", choices=["auto", "checklist", "baseline", "selftest"])
    ap.add_argument("gate", nargs="?", help="checklist 的门禁编号，如 Q3")
    ap.add_argument("--root", help="被检仓库根目录（默认取配置 scan.root）")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG), help="判据配置文件路径")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--out", help="结果写入文件（留痕用）")
    args = ap.parse_args()

    if args.command == "selftest":
        sys.exit(selftest(args.config))

    cfg, cfg_path = load_config(args.config)

    if args.command == "checklist":
        if not args.gate:
            sys.stderr.write("checklist 需指定门禁编号，如：gate_check.py checklist Q3\n")
            sys.exit(EXIT_ERR)
        text = render_checklist(args.gate, cfg)
        print(text)
        if args.out:
            Path(args.out).write_text(text + "\n", encoding="utf-8")
            sys.stderr.write("已写入 %s\n" % args.out)
        sys.exit(EXIT_OK)

    root = resolve_root(cfg, cfg_path, args.root)
    if not root.is_dir():
        sys.stderr.write("被检根目录不存在：%s\n" % root)
        sys.exit(EXIT_ERR)

    do_baseline = args.command == "baseline"
    findings = run_auto(root, cfg, cfg_path, do_baseline=do_baseline)

    if args.json:
        payload = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "root": str(root),
            "summary": count_levels(findings),
            "findings": [f.as_dict() for f in findings],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_text(findings, root, cfg))

    if args.out:
        Path(args.out).write_text(render_text(findings, root, cfg) + "\n", encoding="utf-8")
        sys.stderr.write("已写入 %s\n" % args.out)

    sys.exit(EXIT_FAIL if count_levels(findings)[FAIL] else EXIT_OK)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
