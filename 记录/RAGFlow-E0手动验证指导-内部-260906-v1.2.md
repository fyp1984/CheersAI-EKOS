---
title: RAGFlow E0 手动验证指导（借用实例）
version: v1.2
date: 2026-09-06
owner: 西北人（起服务）/ 用户（手动执行）
source: 手册01 T1.07 + 决策记录 底座分工决策记录-内部-260906-v1.0.md
scope: 现代金租首期 POC · 补 T1.07 挂账项「取 API Key / 建数据集 / 解析 / 检索各一次」
---

# RAGFlow E0 手动验证指导 v1.2

> **为什么有这份文档**：T1.07 的三条判据里，「容器 healthy」和「API 可调」已在 E0 达成，**「可建数据集」一直挂账**——没有 API Key，且唯一在跑的实例是他项目在用环境，写入即污染。
> 现在用**零侵入反向代理**把该实例的 Web/API 暴露到本机 `19380`，由你手动走一遍流程。**不新建容器、不改他项目任何配置、不额外占用 ES/RAGFlow 内存**（代理容器约 5 MB）。

---

## 一、结论（先看这里）

| 项 | 内容 |
|---|---|
| **做什么** | 浏览器手动完成：注册账号 → 取 API Key → 建数据集 → 上传合成样本 → 解析 → 检索各一次 |
| **谁做** | 你手动操作（我不代做，避免污染责任不清） |
| **输入** | 浏览器 + 合成样本 `deploy/fixtures/e0-verify-sample.pdf`（已生成，2 页中文含表格，**零真实数据**） |
| **输出** | 一份验证记录：dataset_id、chunk 数、检索命中条数与首条内容摘要 |
| **完成标准** | 解析后 chunk 数 > 0，且检索能命中样本内容 → T1.07「可建数据集」在 E0 补齐 |
| **不做什么** | 不配 LLM、不取任何性能/质量数字、不上传真实客户材料 |

**三条纪律**：

1. **不需要配置 LLM**。解析（DeepDoc）与检索（向量+全文）都不依赖 Chat 模型，只有"AI 对话"才需要。别卡在这一步。
2. **本轮结论不得写成「已验证」**。这是借用他项目实例 + amd64 模拟运行，只能证明"流程可达、接口可用"，不能证明交付能力。E1 必须用**本项目自有实例 + x86_64 官方镜像**重做一次。
3. **用完即清理**：删数据集、删 API Key。清理步骤见第五节。

---

## 二、前置：服务已起好（我已执行）

```
容器 ekos-ragflow-proxy   Up  0.0.0.0:19380->80/tcp
宿主 UI  http://127.0.0.1:19380/              -> 200
宿主 API http://127.0.0.1:19380/api/v1/datasets -> 401（鉴权有效）
```

- 被代理目标：`filebay-knowledge-trial-ragflow-cpu-1`（镜像 `infiniflow/ragflow:v0.26.4`，**架构 amd64**，本机 arm64 模拟运行）
- 网络：`filebay-knowledge-trial_ragflow`（与该实例同网络，宿主零暴露）
- 代理配置：见附件 A（含一键重建命令，容器或配置丢了可自行恢复）

---

## 三、手动操作步骤

### 步骤 0 · 起 Embedding 服务（只有走 B / B2 方案才需要）

由我执行，一键脚本已入库：

```bash
cd /Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS
./deploy/scripts/start-embedding-e0.sh
```

脚本自检五步：权重存在 → 18001 端口空闲 → Docker 内存 ≥14 GB → 起容器 → 轮询就绪并验向量维度（bge-m3 应为 1024）。

**前置：B 方案需 Docker 内存 ≥14 GB 且当前余量 ≥9 GB**（B2 小模型不需要调内存）。调整路径：

```
Docker Desktop → Settings → Resources → Memory → 16 GB → Apply & Restart
```

- 该操作**会重启 Docker**，本机其他在跑的容器（filebay 试验环境、Dify 等）会短暂中断。
- 2026-09-06 实测：用户已将内存调至 **15.6 GB**，TEI 以 **float32/ORT+MKL** 成功加载（常驻约 8–11 GB，warmup 约 3–8 分钟）；之前 11.67 GB 下必 OOM 是因为 filebay 已占 7.5 GB，余量仅 2.45 GB。
- 期望：脚本打印 `服务就绪` 且 `实测维度: 1024`。
- **不通过怎么办**：
  - `Exited (137)` 或日志停在 `Warming up model` → 内存不足，**先停掉占用内存的其它容器**（见第六节），再重跑脚本。
  - 容器启动即退出且报 `libfakeintel` 相关错误 → 镜像版本有变，把 `docker logs ekos-tei-embedding` 给我。
  - **warmup 超过 10 分钟仍未就绪** → 大概率是误传了 `--dtype float16`：ORT 不支持 fp16 会回退 candle CPU backend，在 Rosetta 下极慢（实测 16+ 分钟未好）。**务必用 float32（默认，不传 --dtype）**。

**服务起好后，RAGFlow 里这样填**（步骤 3 用到）：

| 字段 | 取值 |
|---|---|
| 模型供应商 | `OpenAI-API-Compatible` |
| Base URL | `http://host.docker.internal:18001` |
| 模型名称 | `bge-m3`（B）或 `bge-small-zh-v1.5`（B2） |
| API Key | 留空（TEI 未启用鉴权） |

> 这两个值已实测：`host.docker.internal` 在 RAGFlow 容器内可解析，且**容器内访问宿主端口映射返回 `200`**（用 19380 代理做的等价验证）。若届时仍不通，改用宿主机局域网 IP。

### 步骤 1 · 打开并注册

浏览器打开 **http://127.0.0.1:19380**

- 期望：出现 RAGFlow 登录页。
- 首次访问需注册。建议用**专用测试标识**，避免与他项目账号混淆：
  - 邮箱：`ekos-e0-verify@example.local`
  - 昵称：`ekos-e0-verify`
- **不通过怎么办**：
  - 打不开 / 连接被拒 → 代理容器挂了，执行附件 A 的重建命令；仍不行则查 `docker ps --filter name=ekos-ragflow-proxy`。
  - 页面能开但接口报错 → 执行 `docker logs ekos-ragflow-proxy --tail 20` 把输出给我。

### 步骤 2 · 取 API Key

登录后：右上角头像 → **API** → 生成 API Key（有些版本叫 "API Token"）。

- 期望：得到一串 Key。**复制保存**，后面 curl 要用。
- **不通过怎么办**：若界面无 API 入口，改用命令行登录取 token：

```bash
curl -s -X POST http://127.0.0.1:19380/api/v1/user/login \
  -H "Content-Type: application/json" \
  -d '{"email":"ekos-e0-verify@example.local","password":"你的密码"}'
```

返回里的 `data.access_token` 即 Bearer Token，等价于 API Key。

### 步骤 3 · 建数据集

数据集页面 → 新建，建议参数：

| 参数 | 取值 | 理由 |
|---|---|---|
| 名称 | `ekos-e0-verify` | 一眼可辨是临时验证，方便清理 |
| 语言 | 中文 | 与 §3.5 中→中测试集口径一致 |
| **Embedding 模型** | **见下方「必须配置」** | 镜像不带权重，不配就建不了索引 |
| 解析方法 | General（默认） | 先用默认，不调参 |

> **⚠️ 必须配置 Embedding 模型（这条是我实测后改的，原写「用镜像内置默认」是错的）**
>
> 实测三条（2026-09-06，取自运行中的 v0.26.4 实例）：
> 1. **镜像不携带 embedding 权重**——容器内无 HF 缓存目录，`/ragflow/rag/res/` 下只有 `deepdoc`（OCR/布局）权重；
> 2. `service_conf.yaml` 里 `default_models.embedding_model` 是**注释态**（模板原文 `# name: 'bge-m3'`），即官方默认就没启用；
> 3. `llm_factories.json` 提供 `OpenAI-API-Compatible`／`VLLM`／`Xinference`／`LocalAI`／`Ollama`／`FastEmbed`／**`Builtin`** 等 provider。
>
> ⇒ 结论：**不配置 embedding 模型就无法建索引**，这一步绕不过去。二选一：
>
> **⚠️ v1.1 实测更新：原 A 方案（Builtin + bge-m3）已被证伪，不要选。**
>
> | 选项 | 做法 | 实测可行性（2026-09-06 实测，非推测） |
> |---|---|---|
> | ~~A. Builtin + bge-m3~~ | 供应商 `Builtin`，模型 `bge-m3` | **不可行**。RAGFlow 容器内存**硬限 3.00 GB**（`docker inspect HostConfig.Memory` 实读），而 bge-m3 fp32 权重 **4.3 GB** → 加载必被 SIGKILL |
> | A2. Builtin + 小模型 | 供应商 `Builtin`，模型选 `bge-small-zh` 一类（约 90 MB） | 可行。但模型**非冻结值**，且 Revision 由 RAGFlow 决定、不受我们控制 |
> | **B. 外部服务 + bge-m3（契约口径）** | 我先起 TEI `:18001` 加载 bge-m3，RAGFlow 用 `OpenAI-API-Compatible` 指向它 | **需先把 Docker 内存调到 ≥16 GB**。现状：Docker 总量 11.67 GB、余 2.45 GB；实测直接起 bge-m3 → 容器 `Exited (137)`，日志停在 `Warming up model` |
> | B2. 外部服务 + 小模型 | 同上，但加载 bge-small-zh-v1.5 | 可行，**无需调内存**。模型非冻结值，仅验链路 |
>
> **契约仍按 B 写**（`X1-X4-接口契约-内部-260906-v1.4.md` §X4.6，标注待拍板）。B 是唯一同时满足「版本冻结 + §3.5 共用模型 + 向量同源」的方案；**在 E0 本机它也是 bge-m3 的物理唯一可行路径**——RAGFlow 自己那个容器装不下，只能放到外部服务里。
>
> 若走 A2 / B2（小模型），**必须在记录里写明「模型非冻结值，仅验链路」**，不得作为 §3.7 达标证据。

- 期望：数据集创建成功，页面给出 `dataset_id`（也可以在 URL 里看到）。
- **不通过怎么办**：
  - 提示"请先配置 embedding 模型" → 正常，按上面二选一配置即可。
  - Builtin 下载卡住/超时 → amd64 模拟下慢属正常，等 10–15 分钟；仍失败则告诉我，我改走 B 方案。
  - 模型列表为空 → 执行 `docker exec filebay-knowledge-trial-ragflow-cpu-1 cat /ragflow/conf/llm_factories.json | head -50` 把输出给我。

### 步骤 4 · 上传合成样本

上传文件：`deploy/fixtures/e0-verify-sample.pdf`（绝对路径见下）

```
/Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS/deploy/fixtures/e0-verify-sample.pdf
```

**只用这一个文件，不要上传任何真实客户材料**（数据红线第 1、2 条）。

### 步骤 5 · 触发解析

选中该文件 → 点击**解析**（有的版本是"开始解析"/播放按钮）。

- 期望：状态从 parsing 变 SUCCESS，**chunk 数 > 0**（本样本约 10-20 个 chunk）。
- **不通过怎么办**：
  - 一直 parsing → amd64 模拟下 DeepDoc 较慢，给 3-5 分钟；仍不动则 `docker logs filebay-knowledge-trial-ragflow-cpu-1 --tail 50`。
  - chunk = 0 → 大概率是解析失败，把文件解析日志给我。

### 步骤 6 · 检索验证（关键一步）

数据集页面有**检索测试**入口，输入下面的查询词：

| 查询词 | 期望 |
|---|---|
| `资产负债率` | 命中第 2 页表格行 |
| `营业收入` | 命中第 1 页正文 + 第 2 页表格 |
| `净利润 7300 万元` | 命中第 1 页对应句 |

- 期望：三条都能召回，且返回的 chunk **带页码/位置信息**（这是我们"100% 带证据链"的基础）。
- 命令行等价验证（可选）：

```bash
export KEY="你的APIKey"
export DSID="你的dataset_id"
curl -s -X POST "http://127.0.0.1:19380/api/v1/retrieval" \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d "{\"dataset_ids\":[\"$DSID\"],\"question\":\"资产负债率\",\"top_k\":8}"
```

### 步骤 7 · 记录结果

把下面四项贴给我（我会写进 E0 台账）：

```
dataset_id   :
上传文件      : e0-verify-sample.pdf（2 页）
chunk 总数   :
检索命中数   : 资产负债率=__ 条，营业收入=__ 条
证据字段     : 是否带页码/位置（是/否，字段名）
```

---

## 四、执行策略（为什么会这样设计）

| 决策 | 理由 |
|---|---|
| 用反向代理而不是重建实例 | 本机 Docker 只分到 **11.7 GB**，该实例已占约 7.5 GB（ES 4.3 + RAGFlow 2.4）。再起一套需要约 7 GB，合计 14.5 GB **必崩**。代理容器只占约 5 MB。 |
| 不自构建 arm64 镜像 | 官方明文不维护 ARM 镜像，自构建耗时且 **E1 是 x86_64 + 官方镜像，构建产物在交付环境用不上**。 |
| 只用合成样本 | 数据红线第 1、2 条；也避免污染他项目持久化数据卷（该卷已有业务批次数据）。 |
| 结论只写"流程可达" | 借用实例 + amd64 模拟 → 性能与质量数字一律无效，写"已验证"等于伪造验收。 |
| **Embedding 走外部服务而非 Builtin** | 不只是版本冻结的考量——**物理上也不行**：RAGFlow 容器硬限 3.00 GB，bge-m3 fp32 权重 4.3 GB 装不进去。 |
| **bge-m3 需先调 Docker 内存** | 实测 Docker 总量 11.67 GB、余 2.45 GB；直接起 TEI 加载 bge-m3 → `Exited (137)`（SIGKILL），日志停在 `Warming up model`。缺口约 2.3 GB。 |
| 权重从 /tmp 迁入仓库 `models/` | `/tmp` 会被系统清理。仓库 `.gitignore` 第 36 行已预留 `models/`，与 `sha256-models.txt`「E1 落 /data/models」的目录语义对齐。 |

---

## 五、清理（验证完必做）

1. 删除数据集 `ekos-e0-verify`（数据集页面 → 删除）
2. 删除 API Key / 退出登录
3. 代理容器保留即可（不占资源，后续还要用）；若要停：

```bash
docker stop ekos-ragflow-proxy
docker rm ekos-ragflow-proxy
```

4. 若停了代理，下次用附件 A 的重建命令即可，**不需要重新注册账号**（账号已在该实例数据库里）。

---

## 附件 A · 代理配置与重建命令（配置丢了可自行恢复，不依赖我）

**nginx 配置**（保存为 `/tmp/ekos-ragflow-proxy/nginx.conf`）：

```nginx
events {
    worker_connections 64;
}

http {
    access_log off;

    server {
        listen 80;

        location / {
            proxy_pass http://filebay-knowledge-trial-ragflow-cpu-1:80;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_read_timeout 300s;
            proxy_send_timeout 300s;
            client_max_body_size 200m;
        }
    }
}
```

**重建命令**：

```bash
mkdir -p /tmp/ekos-ragflow-proxy
# 把上面的配置写进 /tmp/ekos-ragflow-proxy/nginx.conf
docker rm -f ekos-ragflow-proxy 2>/dev/null
docker run -d --name ekos-ragflow-proxy \
  --network filebay-knowledge-trial_ragflow \
  -p 19380:80 \
  -v /tmp/ekos-ragflow-proxy/nginx.conf:/etc/nginx/nginx.conf:ro \
  nginx:1.27-alpine
sleep 3
curl -s -o /dev/null -w "UI=%{http_code}\n" http://127.0.0.1:19380/
curl -s -o /dev/null -w "API=%{http_code}\n" http://127.0.0.1:19380/api/v1/datasets
```

期望：`UI=200`、`API=401`（401 说明鉴权生效，不是坏了）。

## 附件 B · 合成样本生成脚本（可复现，不含任何真实数据）

```python
import fitz

doc = fitz.open()
page = doc.new_page()
page.insert_text((72, 90), "E0 验证用合成样本", fontsize=18, fontname="china-s")
page.insert_text(
    (72, 130),
    "本文件为自动化生成的合成数据，不含任何真实客户信息。",
    fontsize=11,
    fontname="china-s",
)

lines = [
    "一、主体概况",
    "示例科技股份有限公司成立于 2015 年，注册资本 12000 万元，",
    "注册地为某市高新技术产业开发区，主营智能制造装备的研发与销售。",
    "公司现有员工 480 人，其中研发人员 126 人，占比 26.25%。",
    "",
    "二、经营情况",
    "2024 年度公司实现营业收入 86000 万元，同比增长 12.4%；",
    "实现净利润 7300 万元，同比增长 8.1%。经营活动现金流净额 9100 万元。",
    "公司主要客户集中在华东与华南地区，前五大客户合计占比 41.3%。",
    "",
    "三、资产与负债",
    "截至 2024 年末，公司总资产 152000 万元，总负债 78000 万元，",
    "资产负债率 51.32%。其中流动负债 62000 万元，非流动负债 16000 万元。",
    "货币资金余额 21500 万元，应收账款 34800 万元，存货 26700 万元。",
    "",
    "四、风险提示",
    "公司存在原材料价格波动风险、应收账款回收风险以及行业竞争加剧风险。",
    "公司已建立相应的风险管理制度并计提了相应的资产减值准备。",
]

y = 175
for line in lines:
    if line:
        page.insert_text((72, y), line, fontsize=11, fontname="china-s")
    y += 22

page2 = doc.new_page()
page2.insert_text((72, 90), "附表：近三年主要财务指标", fontsize=14, fontname="china-s")

table_data = [
    ["指标", "2022 年", "2023 年", "2024 年"],
    ["营业收入（万元）", "68000", "76500", "86000"],
    ["净利润（万元）", "5400", "6755", "7300"],
    ["总资产（万元）", "118000", "134000", "152000"],
    ["资产负债率", "48.90%", "50.15%", "51.32%"],
]

y = 130
for row in table_data:
    x = 72
    for cell in row:
        page2.insert_text((x, y), cell, fontsize=11, fontname="china-s")
        x += 130
    y += 28

y += 20
page2.insert_text(
    (72, y),
    "注：以上数值均为合成数据，仅用于验证解析、切分与检索链路。",
    fontsize=10,
    fontname="china-s",
)

doc.save("deploy/fixtures/e0-verify-sample.pdf")
doc.close()
```

执行：`cd 仓库根目录 && uv run python <脚本路径>`，产物 2 页、含中文与表格，PyMuPDF 1.25.1 可读回。

---

## 六、E0 本机资源约束与串行验证（2026-09-06 实测补充）

**核心结论：TEI 与完整 RAGFlow 栈不能同时在本机运行，E0 验证必须串行。**

### 6.1 内存账（Docker 已调至 15.6 GB）

| 组件 | 常驻内存 | 备注 |
|---|---|---|
| TEI（bge-m3, float32/ORT） | **8–11 GB** | 实测 11.25 GiB；权重 4.3 GB + MKL 运行时开销 |
| RAGFlow 完整栈（借用实例） | **约 7.5 GB** | ES 4.3 + RAGFlow 2.4 + MySQL 0.65 + MinIO 0.1 |
| Dify 等其它容器 | 约 3.7 GB | Docker 重启后自动恢复的部分 |
| **合计** | **≈ 19.7 GB** | **> 15.6 GB 上限 → 必 OOM** |

⇒ 同一时刻只能留一个重型服务。推荐顺序：

1. **先起 TEI（步骤 0）**，验证向量服务就绪（维度 1024）；
2. **再停 TEI**，`docker stop ekos-tei-embedding`；
3. **起 RAGFlow 栈**（见 6.2），做步骤 1–7 手动验证；
4. 验证完按需保留或清理。

> 若不想串行，可用 **B2 小模型**（bge-small-zh-v1.5，约 90 MB）跑 TEI——只吃约 0.5 GB，可与 RAGFlow 栈共存。代价：模型非冻结值，记录须注明「仅验链路」。

### 6.2 RAGFlow 栈重启（Docker 重启后已全停）

Docker 内存调整触发重启，filebay 全套被 OOM 连环杀（多个 `Exited (137)`）。代理 `ekos-ragflow-proxy` 已恢复，但上游 RAGFlow 实例未起，故 19380 当前返回 **502**。

恢复 RAGFlow 实例（需先停 TEI 腾内存）：

```bash
cd /Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-FileBay/deploy/knowledge
docker compose -f docker-compose.trial.yml up -d
```

- 若报 `undefined service "ragflow-cpu"`：该 compose 用 `include` 引了上游 RAGFlow 快照，且 ragflow-cpu 被 profile 挡住。先确认 `.env` 中 `COMPOSE_PROFILES` 含对应 profile，或显式 `docker start filebay-knowledge-trial-ragflow-cpu-1`（依赖 es01/mysql/minio/redis 需先起）。
- 起好后 `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:19380/` 应返回 `200`。

### 6.3 不要再踩的坑

| 坑 | 现象 | 正确做法 |
|---|---|---|
| 传 `--dtype float16` 起 TEI | ORT 拒 fp16 → 回退 candle → Rosetta 下 warmup 16+ 分钟不就绪 | **不传 --dtype**，走默认 float32/ORT+MKL（已验证快路径） |
| TEI 与 RAGFlow 栈同时起 | 合计超 15.6 GB → OOM 连环杀 | 串行：先 TEI 后 RAGFlow，或改用 B2 小模型 |
| 只查 Docker 总内存不查余量 | 总量够但 filebay 已占 7.5 GB → 静默 OOM | 脚本已加**当前余量 ≥9 GB**预检（步骤 0 之 [3/5]） |

---

## 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-09-06 | 初版：七步手动验证 + 代理配置与重建命令 + 合成样本生成脚本 |
| v1.1 | 2026-09-06 | ① **证伪 A 方案**：RAGFlow 容器内存硬限 3.00 GB，Builtin 方式加载 bge-m3（fp32 4.3 GB）必 OOM，原「用镜像内置默认」路径不可用；② 新增**步骤 0**（起 Embedding 服务 + RAGFlow 外部服务字段填法，`host.docker.internal` 已实测容器内可达）；③ 实测 bge-m3 需 Docker 内存 ≥16 GB（当前 11.67 GB、余 2.45 GB，直接起返回 `Exited (137)`）；④ bge-m3 权重由 `/tmp` 迁入仓库 `models/`（已被 `.gitignore` 忽略）；⑤ 一键脚本 `deploy/scripts/start-embedding-e0.sh` 入库 |
| v1.2 | 2026-09-06 | ① 用户已将 Docker 内存调至 **15.6 GB**，实测 **float32/ORT+MKL** 路径可成功加载 bge-m3（常驻 8–11 GB，warmup 3–8 分钟），内存约束由「总量 ≥16 GB」改为「总量 ≥14 GB 且**当前余量 ≥9 GB**」；② 新增**第六节·本机资源约束与串行验证**：TEI(8–11 GB) 与 RAGFlow 完整栈(约 7.5 GB) 合计超 15.6 GB，**不能同时运行，须串行**；③ 订正 `--dtype float16` 会回退慢速 candle backend（实测 16+ 分钟不就绪），明确走默认 float32；④ 补 RAGFlow 栈 Docker 重启后全停、代理已恢复（19380 暂返 502）、重启命令入手册 |
