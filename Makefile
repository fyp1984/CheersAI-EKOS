# EKOS 共用层内核 · 统一命令入口
# 用法：make help

.DEFAULT_GOAL := help
PY   := uv run
GATE := python tools/gate/gate_check.py

.PHONY: help setup lint fmt type test-unit test-contract test-integration test-golden \
        gate gate-selftest gate-mutation gate-checklist up down stub clean

help: ## 显示帮助
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n",$$1,$$2}'

# ================= 环境 =================
setup: ## 建虚拟环境并装依赖（Python 3.11.10）
	uv venv --python 3.11.10
	# 裸 uv sync 即可装齐（dev 组在 [dependency-groups]，PEP 735）。
	# 不写 --extra/--all-extras：那要求人记得加参数，忘了就撞
	# "Failed to spawn: ruff" 这种完全不提示真实原因的错。
	uv sync
	@echo "下一步：source .venv/bin/activate && make gate"

# ================= 质量 =================
# 全仓库口径（不列子目录）：列子目录会与 pre-commit 的 ruff 钩子形成
# 「make gate 查 3 个目录、提交时查全部」的双口径 —— 于是有些问题只有提交时
# 才暴露，本地跑 make gate 是一片绿。作用域只在一处定义，避免第二种静默放行。
lint: ## Ruff 静态检查
	$(PY) ruff check .
	$(PY) ruff format --check .

fmt: ## 自动格式化
	$(PY) ruff check --fix .
	$(PY) ruff format .

type: ## mypy 类型检查
	# 不传路径：命令行传路径会覆盖 pyproject 的 files/exclude 配置，
	# 造成「make type 查 10 个文件、mypy 查 16 个」的双口径。作用域交给配置管。
	$(PY) mypy

# ================= 测试金字塔 =================
test-unit: ## L1 单测（秒级，无外部依赖）
	$(PY) pytest tests/unit -m unit -q

test-contract: ## L2 模型服务契约测试（Stub）
	$(PY) pytest tests/contract -m contract -q

test-integration: ## L3 集成测试（需先 make up）
	$(PY) pytest tests/integration -m integration -q

test-golden: ## L4 Golden Test（仅 E1 真实模型环境）
	@echo "⚠️  本目标只能在 E1（x86_64 + 真实模型）运行"
	@echo "    E0/Stub 环境产出的分数无效，不得写入实测表"
	$(PY) pytest tests/golden -m golden -q

# ================= 门禁 =================
gate: lint type test-unit test-contract ## 全量门禁（提交前必跑）
	# 常检 17 条 = 推进方案 v2.2 §4.2 的 15 条 + 新增 TR-7/TR-8。
	# 其中 6 条可自动化（TR-1/2/4/6/7/8）由脚本跑，其余人工清单逐条填。
	@echo "--- 运行可自动化常检（6 条）---"
	$(GATE) auto

gate-selftest: ## 门禁脚本自检（改脚本后必跑）
	$(GATE) selftest

gate-mutation: ## 变异测试：把判据改坏，验证断言真的会红
	# 为什么单独设一个目标：靠人记得跑的命令等于没有这条命令。
	# 断言绿只说明路径走到了，说明不了实现坏了它会不会红 ——
	# 不会红的断言是装饰品，比没有断言更危险（它给出虚假的安全感）。
	$(PY) python tools/gate/mutation_test.py

gate-checklist: ## 生成指定门禁人工清单，用法：make gate-checklist Q=Q1
	$(GATE) checklist $(Q) --out 记录/$(Q)-人工清单-$$(date +%y%m%d).md

# ================= 依赖服务 =================
up: ## 启动本地依赖服务（arm64 原生）
	cd deploy/compose && docker compose --profile dev up -d
	@echo "等待服务就绪..."; sleep 5
	cd deploy/compose && docker compose ps

down: ## 停止依赖服务
	cd deploy/compose && docker compose --profile dev down

stub: ## 启动模型契约 Stub（端口 18000）
	$(PY) -m packages.modelgw.stub --port 18000

# ================= 清理 =================
clean: ## 清理缓存
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -prune -exec rm -rf {} +
	find . -type d -name ".mypy_cache" -prune -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -prune -exec rm -rf {} +
