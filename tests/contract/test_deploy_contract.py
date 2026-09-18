"""L2 契约测试：验证部署层遵守 X4 部署接口契约（03_实施/契约 X1-X4 §X4）。

为什么这一层需要单独建文件
--------------------------
T2.09 校验时 X4 的四条判据全是「写在文档里、靠人手工跑的 grep 命令」，零自动化
覆盖。后果在本次校验中立刻兑现，三条判据各有一种失效方式：

1. **「密钥不入库」查错了对象**。原命令按**整行** `grep -v` 过滤，只要行内含任意
   变量引用就整行放过。实测 `POSTGRES_PASSWORD: ${PGPASSWORD:-ekos_dev_only}`
   与 `MINIO_ROOT_PASSWORD: ${MINIO_PASSWORD:-ekos_dev_only}` 两行——**明文开发
   口令就在 git 里，却从未报警**，因为这两行自己就带 `${`。要查的是
   `${VAR:-默认值}` 里**默认值本身**，不是这一行长什么样。

2. **「开发端口避开冻结段」文档判据弱于门禁实现**。X4.5 里写的那条 grep 只列出
   ports 行由人眼确认段位，本身不与 §3.8 比对。需要说明的是，**门禁 TR-7 内建了
   更严格的实现**（「compose 主机侧端口均避开冻结段」）：实测把默认端口改成
   5432，`gate_check.py auto` 立刻 FAIL。所以这里不是「门禁漏了」，而是**文档的
   弱判据与门禁的强实现长期脱节**——照文档执行的人以为只有手工 grep 这一道。
   本文件把该断言自动化，并把扫描范围从编排文件扩到 `deploy/` 全部（含 `.sh`，
   TR-7 不扫脚本），另增加「是否落在开发段」检查。

3. **「十步顺序」只查存在性**。手册 8 有自己的十步，判据 PASS，却查不出它与
   §5.3 原文的十二步并不一致。

此外原判据的扫描范围只有 `*.yml`/`*.yaml`，`deploy/scripts/backup_restore.sh`
里同样的明文默认口令**连被查的资格都没有**——该脚本第 3 行还写着「Secrets
never hardcoded in this script」。注释声明与实现相反，是最容易被放过的那类。

本文件的四条纪律
----------------
- **§3.8 冻结端口表字面写死**（`FROZEN_PORTS`），不从契约文档导入：契约是被判对象，
  拿它的值验证它自己等于自己给自己判卷。
- **「未找到」一律 SKIP，绝不 PASS**：契约文档在仓库外，不可达时 SKIP 并写明原因。
- **扫描范围覆盖 yml/yaml/sh**：原判据漏 shell 脚本是实测发现，不是理论推演。
- **glob 落空即 FAIL**：静默通过比报错更危险。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = REPO_ROOT / "deploy"
COMPOSE_FILE = DEPLOY_DIR / "compose" / "docker-compose.dev.yml"

# §3.8 固定端口与磁盘预算（交付环境冻结值）—— 权威依据，字面写死。
# 来源：02_方案/60.90.03 §3.8 表格。改动此表 = 改技术基线，须走甲方变更流程。
FROZEN_PORTS = {
    8080: "Web/API",
    8000: "模型服务",
    8001: "Embedding",
    8002: "Reranker",
    5432: "PostgreSQL",
    6379: "Redis",
    9000: "MinIO API",
    9001: "MinIO Console",
}

# 开发默认允许落在的端口段（避开冻结段，防止「本机跑通、交付冲突」）
_DEV_PORT_PREFIXES = ("15", "16", "18", "19")

# 密钥类键名：其默认值不得为明文
_SECRET_KEY_RE = re.compile(r"(?i)(password|passwd|secret|token)")

# ${VAR:-默认值} 形式：对密钥类键属违规（应改为 ${VAR} 或 ${VAR:?提示}）
_DEFAULT_VALUE_RE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:-([^}]*)\}")

# 部署相关文件：原判据只扫 yml/yaml，漏了 sh（实测 backup_restore.sh 有明文口令）
DEPLOY_SCAN_GLOBS = ("**/*.yml", "**/*.yaml", "**/*.sh")


def _deploy_files() -> list[Path]:
    files: list[Path] = []
    for pattern in DEPLOY_SCAN_GLOBS:
        files.extend(sorted(DEPLOY_DIR.glob(pattern)))
    # .env 真值文件不参与扫描（它本就不该进 git），但 .env.example 要扫
    return [f for f in files if f.name != ".env"]


def _host_side(entry: str) -> str:
    """取端口条目的宿主侧（容器端口之前的部分）。

    不能简单 `split(":", 1)`：`${PGPORT_HOST:-5432}:5432` 这类条目里，
    变量默认值语法自带冒号，按第一个冒号切会把 `${PGPORT_HOST` 当成宿主侧，
    `:-5432` 被吞掉——实测变异「把开发默认改成冻结端口 5432」因此完全漏判。
    变量形态须先找到配对的 `}`。
    """
    if entry.startswith("${"):
        end = entry.find("}")
        if end >= 0:
            return entry[: end + 1]
    return entry.split(":", 1)[0]


def _compose_port_lines() -> list[str]:
    """取 compose 中 ports 段下的条目行。

    只取 `- "..."` 形式的条目，靠缩进判断处于 ports 之下。
    """
    lines = COMPOSE_FILE.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    in_ports = False
    for line in lines:
        stripped = line.strip()
        if re.match(r"^ports:\s*$", stripped):
            in_ports = True
            continue
        if in_ports:
            if stripped.startswith("- "):
                # 剥掉行尾注释再取端口：注释里若出现 ${X:-端口} 之类举例会被误判，
                # 且残留的尾引号（`"15432:5432"  # 注释`）会让解析结果脏污。
                item = stripped[2:].split("#", 1)[0].strip()
                item = item.strip('"').strip("'").strip()
                if item:
                    out.append(item)
            elif stripped and not stripped.startswith("#"):
                in_ports = False
    return out


def _contract_doc() -> Path | None:
    """定位契约文档。仓库外文件，不可达时返回 None（调用方须 SKIP）。"""
    env = os.environ.get("EKOS_CONTRACT_DOC")
    if env:
        p = Path(env)
        return p if p.is_file() else None
    candidates = sorted(
        Path.home().glob(
            "Documents/@西北人的成长/10-Projects/项目商机/01_商机项目/现代金租/03_实施/契约/X1-X4-接口契约-内部-*.md"
        )
    )
    return candidates[-1] if candidates else None


# ---------------------------------------------------------------- 判据 1


@pytest.mark.contract()
def test_compose_ports_have_no_literal_host_port() -> None:
    """X4.2：compose 的宿主端口一律 `${VAR}`，禁止字面端口（TR-7）。

    只看宿主侧（第一个冒号前）；容器侧端口是镜像内固定值（如 5432），
    本就必须是字面量，不能误伤。
    """
    assert COMPOSE_FILE.is_file(), f"compose 文件不存在：{COMPOSE_FILE}"
    entries = _compose_port_lines()
    assert entries, "未解析到任何 ports 条目，glob/解析落空会让本守卫静默通过"

    offenders = [e for e in entries if "${" not in _host_side(e)]
    assert not offenders, "宿主端口写死了字面值，须改为 ${VAR:-默认} 形式（TR-7 / X4.2）：" + str(
        offenders
    )


@pytest.mark.contract()
def test_dev_default_ports_avoid_frozen_ports() -> None:
    """X4.2：开发默认端口不得与 §3.8 冻结端口重合。

    原判据只检查「是否落在 15xxx/16xxx/18xxx/19xxx」，不与 §3.8 比对——
    端口可以在这几段里随便写，或与冻结值重复，判据照样 PASS。
    这里把 §3.8 冻结表作为字面期望值逐个比对。
    """
    entries = _compose_port_lines()
    assert entries, "未解析到 ports 条目"

    clashes: list[str] = []
    for entry in entries:
        # 在整条上找默认值：不能先切宿主侧，变量语法自带冒号会让切分提前中断
        for m in re.finditer(r":-(\d+)\}", entry):
            port = int(m.group(1))
            if port in FROZEN_PORTS:
                clashes.append(f"{entry} → 撞 §3.8 冻结端口 {port}（{FROZEN_PORTS[port]}）")
            elif not port.__str__().startswith(_DEV_PORT_PREFIXES):
                clashes.append(
                    f"{entry} → 默认端口 {port} 不在开发段"
                    f"（{'/'.join(_DEV_PORT_PREFIXES)}xxx），可能误用交付端口"
                )
    assert not clashes, f"开发默认端口与 §3.8 冲突或越段：{clashes}"


# ---------------------------------------------------------------- 判据 2


@pytest.mark.contract()
def test_no_plaintext_secret_defaults_in_deploy_files() -> None:
    """X4.4：部署文件中密钥类键不得带明文默认值。

    这是本轮最关键的判据。原命令按**整行** `grep -v '\\${'` 过滤，
    `POSTGRES_PASSWORD: ${PGPASSWORD:-ekos_dev_only}` 这种行自带 `${`，
    于是整行被放过，明文口令从未报警。

    这里的判据是「取 ${VAR:-默认值} 的默认值本身」：`${VAR}` 与
    `${VAR:?提示}` 合规，只有 `${VAR:-明文}` 违规。
    """
    files = _deploy_files()
    assert files, "deploy/ 下未扫描到任何文件，glob 落空会让本守卫静默通过"

    offenders: list[str] = []
    for path in files:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            # 剥离注释后再判：注释里出现 ${X:-明文} 只是举例（本文件注释就有），
            # 不是真的写了默认值。不剥离会把说明文字当成违规，属误报。
            code = line.split("#", 1)[0]
            if not _SECRET_KEY_RE.search(code):
                continue
            if not _DEFAULT_VALUE_RE.search(code):
                continue
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno} {line.strip()}")
    assert not offenders, (
        "密钥类键带明文默认值（须改为 ${VAR} 或 ${VAR:?提示}，真值放 .env）：" + str(offenders)
    )


@pytest.mark.contract()
def test_env_example_contains_only_placeholders() -> None:
    """X4.4：`.env.example` 只放占位值，不得出现真值。

    `.env.example` 是唯一允许进 git 的环境文件，一旦混入真值等于泄密。
    占位值约定见该文件注释；这里拦的是「看起来像真口令」的写法。
    """
    example = DEPLOY_DIR / "compose" / ".env.example"
    assert example.is_file(), ".env.example 不存在，开发者将无从得知需要哪些变量"

    offenders: list[str] = []
    for lineno, line in enumerate(example.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip().startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if not _SECRET_KEY_RE.search(key):
            continue
        value = value.strip()
        if value in {"change_me", "REPLACE_ME", ""}:
            continue
        offenders.append(f"第 {lineno} 行 {key.strip()} = {value}（疑似真值）")
    assert not offenders, f".env.example 中出现非占位值：{offenders}"


@pytest.mark.contract()
def test_env_file_is_gitignored() -> None:
    """X4.4：`.env` 必须被 git 忽略，且 `.env.example` 必须不被忽略。

    只检查 .gitignore 规则本身，不依赖 git 命令（测试环境可能不在仓库内）。
    """
    gitignore = REPO_ROOT / ".gitignore"
    assert gitignore.is_file(), "仓库缺少 .gitignore"

    rules = [
        line.strip() for line in gitignore.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert ".env" in rules, ".gitignore 未忽略 .env，真值一旦提交即泄密"
    assert "!.env.example" in rules, (
        ".gitignore 缺少 !.env.example 例外，模板文件将无法提交，开发者无从参照"
    )


# ---------------------------------------------------------------- 判据 3


@pytest.mark.contract()
def test_contract_delivery_ports_match_frozen_table() -> None:
    """X4.2 交付默认列必须与 §3.8 冻结表一致（契约文档可达时）。

    v1.2 的端口表后三行与 §3.8 错位：`API_HOST` 填 8000（§3.8 属模型服务，
    Web/API 应为 8080）、`MODEL_HOST` 填 8100（§3.8 无此值）、`VAULT_HOST`
    填 8080（§3.8 属 Web/API）。照它填交付环境会同时撞出两处端口冲突。

    契约文档在仓库外，不可达时 SKIP（不可达 ≠ 通过）。
    """
    doc = _contract_doc()
    if doc is None:
        pytest.skip("未定位到契约文档，设 EKOS_CONTRACT_DOC 环境变量后本断言生效")

    text = doc.read_text(encoding="utf-8")
    section = _extract_section(text, "### X4.2", "### X4.3")
    assert section, "契约文档未找到 X4.2 章节"

    # 解析表格行：| `VAR` | 用途 | 开发默认 | 交付默认 | compose 落地 |
    # 判据：以 | 开头，且含反引号包裹的大写变量名（PGPORT_HOST 形态）
    rows = [
        ln
        for ln in section.splitlines()
        if ln.strip().startswith("|") and re.search(r"`[A-Z][A-Z0-9_]+`", ln)
    ]
    assert rows, "X4.2 未解析到变量行"

    mismatches: list[str] = []
    for row in rows:
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        var = cells[0].strip("`")
        delivery = cells[3]
        if delivery.startswith("**待拍板**"):
            continue  # VAULT_HOST：§3.8 未定义，不得擅自选定，跳过数值比对
        m = re.search(r"`?(\d{4,5})`?", delivery)
        if not m:
            continue
        port = int(m.group(1))
        if port not in FROZEN_PORTS:
            mismatches.append(f"{var} 交付默认 {port} 不在 §3.8 冻结表内")
    assert not mismatches, f"契约 X4.2 交付默认与 §3.8 不一致：{mismatches}"


@pytest.mark.contract()
def test_contract_deploy_steps_are_twelve() -> None:
    """X4.3 必须是 §5.3 原文的十二步，不是十步。

    §5.3 原文含第 11 步「离线验证」与第 12 步「交接签字」。v1.2 写成十步且
    内容自成一派，与 §5.3 只有前三步相同。原判据只查「手册 8 含十步」的
    存在性，查不出这个不一致。
    """
    doc = _contract_doc()
    if doc is None:
        pytest.skip("未定位到契约文档，设 EKOS_CONTRACT_DOC 环境变量后本断言生效")

    text = doc.read_text(encoding="utf-8")
    section = _extract_section(text, "### X4.3", "### X4.4")
    assert section, "契约文档未找到 X4.3 章节"

    numbered = re.findall(r"^\s*(\d+)\.\s+\*\*", section, flags=re.MULTILINE)
    assert numbered, "X4.3 未解析到编号步骤"
    assert len(numbered) == 12, (
        f"X4.3 部署步数为 {len(numbered)}，应为 §5.3 原文的 12 步"
        f"（含第 11 离线验证、第 12 交接签字）"
    )
    assert numbered == [str(i) for i in range(1, 13)], f"步骤编号不连续：{numbered}"

    # 第 11、12 步是 v1.2 漏掉的两步，单独确认存在
    assert "离线验证" in section, "X4.3 缺第 11 步「离线验证」（§5.3 原文）"
    assert "交接签字" in section, "X4.3 缺第 12 步「交接签字」（§5.3 原文）"


def _extract_section(text: str, start: str, end: str) -> str:
    """取 [start, end) 之间的文本；end 缺失时取到文末。"""
    i = text.find(start)
    if i < 0:
        return ""
    j = text.find(end, i + len(start))
    return text[i:] if j < 0 else text[i:j]


# ---------------------------------------------------------------- 判据 4


@pytest.mark.contract()
def test_backup_script_is_executable() -> None:
    """X4.4 附带：备份脚本须有执行权限。

    实测 `./deploy/scripts/backup_restore.sh` 报 permission denied——
    一个不能执行的备份脚本，等于没有备份能力，而门禁看不出来。
    """
    script = DEPLOY_DIR / "scripts" / "backup_restore.sh"
    assert script.is_file(), "备份脚本不存在"
    assert os.access(script, os.X_OK), (
        f"备份脚本缺执行权限：chmod +x {script.relative_to(REPO_ROOT)}"
    )
