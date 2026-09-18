采集时间: 2026-09-04 17:58:52
主机名  : YungRocMacBook.local
架构    : arm64
内核    : Darwin 25.2.0
CPU核数 : 4
物理内存: 24 GB
根盘剩余: 240Gi
Docker  : 29.6.2
Compose : 5.3.1

## 与 §3.7 冻结值的差异
- Docker Engine: 实测 29.6.2 vs 冻结 27.3.1 → 高于，开发环境允许
- Compose     : 实测 5.3.1 vs 冻结 2.29.7 → 高于，开发环境允许
- 处置：compose 文件禁用新版专有语法（见 T1.04 策略）

## RAGFlow 底座（T1.07 · E0 接口验证）
- 实例：既有 filebay-knowledge-trial-ragflow-cpu-1（项目 filebay-knowledge-trial，既有试验环境，非本轮交付实例；数据不进交付物）
- 镜像：infiniflow/ragflow:v0.26.4
- 架构：amd64 镜像，在 arm64 Mac 上经 Docker Desktop 模拟运行（非原生 arm64）→ 性能/质量数字一律无效
- 状态：Up 5 weeks (healthy)；容器内部 :80 Web 返回 200
- 端口：未映射宿主（宿主不可直连）；E0 仅接口验证，浏览器可视化延 E1（x86_64 交付环境，端口发布）
- 归属：RAGFlow 不在 §3.7 冻结表（属 §5.2 适配，承担连接器+深度解析+三索引检索）；版本进 SBOM(T2.05) 与选型记录(T2.03)，非 TR-4 比对对象
- E1 约束：必须用真实 x86_64 真机 + 官方镜像（arm64 无官方镜像，本机模拟路径 E1 不可复现）

## 端口与磁盘预算（T1.11 · E0 核验）
- 端口：15432/16379/19000/19001 归本项目容器；8080 空闲；18000 stub 双重占用（LAN 口被他容器占，记为 E0 已知非阻塞，E1 真 modelgw 用 8000 规避）。详情见 `记录/ports-disk.md`。
- 磁盘：根盘剩余 229Gi（充裕）；`/data` 不存在（E0 裁法，真实数据落 E1 `/data`）。

## 网络与出网路径（T1.12 · E0 核验）
- 出网白名单：只 `dashscope.aliyuncs.com`（qwen-plus 受控 HTTPS 出口，E1/E2 经甲方批准脱敏策略后启用）。详见 `记录/network.md`。
- dashscope 可达性实测：HTTP 404（可达，非 000）；E0 不发起真实云端请求。
- 禁止出网：Vault 脱敏映射表 / 原始客户材料 / 事实库 / 日志 一律本地留存，绝不出网（数据红线）。
- E0 满足「映射表不出网」方式：stub 绑 127.0.0.1 + 无云端密钥 → 根本不走出网路径；真实抓包证据留 T7.03（E1 运行期执行）。

## CheersAI Vault 脱敏服务（T2.06 集成 · E0 对接）
- **定位**：脱敏能力由 **CheersAI-Vault 工程提供**，EKOS 只做适配器集成，**不自研**（方案 60.90.03 §四；EKOS 方案 F26「安全差异化内核 = CheersAI Vault」）。详见 `记录/Vault集成方案-260905.md`。
- **在用实例（不可用于测试）**：容器 `cheersai-vault-runtime`，宿主 :8787 → 容器 :8787，镜像 `cheersai-vault-vault-runtime-api:latest`，自 2026-08-05 起运行。
  - 数据卷 `cheersai-vault_vault_runtime_data` 内有 **7-8 月历史业务批次数据**；敏感词库内有业务词条（智网数科/ZWSK、管网/GW）。
  - **属在生产级在用服务**，测试写入会污染持久化数据卷 → 2026-09-05 已完成污染清理（零删除，备份在 `/tmp/`）。
- **隔离实例（EKOS 开发测试用）**：容器 `ekos-vault-dev`，宿主 :8788 → 容器 :8787，数据卷 `ekos-vault-dev-data`（独立），2026-09-05 建立。
- **服务版本**：`0.1.0`；规则 7 条（`id_card` / `phone` / `email` / `bank_card` / `ipv4` / `passport` / `use_sensitive_terms`）。
- **接入方式**：地址走环境变量 `VAULT_API_BASE_URL` 注入（TR-7 零硬编码），无默认值；规则映射走 `packages/vault/masking_policy.json`。
- **E0 实测结论**：`mask` / `restore` 往返还原通过；人名脱敏依赖 Vault 敏感词库配置（空词库时人名残留且 `/health` 如实报 `config_dependent` → 行为与报告一致，未静默）。
- **挂账**：USCI 规则缺失、护照/地址规则、`restore` 承诺范围、敏感词库运营责任 → `记录/挂账清单-Vault待推-260905.md`（G-1～G-5）。

## 2026-09-06 复核订正（E0 环境全面核查）

来源：`记录/E0环境核查-开源应用清单-内部-260906-v1.0.md`。**原文不删，只追加订正**，保留 2026-09-04 采集时用户验收痕迹。

- **CPU 核数订正**：原文「CPU核数 : 4」→ 实测 `sysctl -n hw.physicalcpu` = **8**（Apple M2）。原文 4 疑为采集脚本取值口径不同。**以 8 为准**；无论 4 还是 8，均低于 §3.4 CPU 档的 32 物理核要求，不改变 E0 裁法结论。
- **根盘剩余为动态值**：原文「240Gi」（2026-09-04 17:58）→ 2026-09-06 17:44 复采 **224Gi**。本项不得当静态值引用，引用时须带采集时间。
- **已复验一致项**（本次实测与原文一致）：Docker 29.6.2 / Compose 5.3.1 / arm64 / 24 GB / RAGFlow v0.26.4 amd64 模拟 / 18000 双重占用 / Vault 0.1.0。
- **新增证据（比原文更强的证明）**：pgvector **0.8.0 已在库内实际创建**（`pg_extension` 查询，非仅镜像标签推断）；PostgreSQL 实测 **16.10**（落在 gate_baseline.json `match:major` 接受窗口内）。
- **未变结论**：E0 开发范围达标；E1/E2 交付环境待真机到位（T6.03 唯一硬阻塞）。
