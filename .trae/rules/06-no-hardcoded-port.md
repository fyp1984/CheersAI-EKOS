---
description: TR-7 端口与配置零硬编码。写服务地址、连接串、配置项时必须遵守。
globs: apps/**/*.py,packages/**/*.py,tests/**/*.py,deploy/**
alwaysApply: false
---

# 06 · 端口与配置零硬编码（TR-7）

## 为什么

交付端口（`60.90.03` §3.8）写死在代码里，代价要**等切环境时才暴露**：
E0 用 `15xxx` 段、E1 用冻结段，同一份代码两种行为，
而报错永远指向「连接被拒」而不是「端口写死」——**排查方向从一开始就是错的**。

## 硬规则

1. **代码中禁止出现 `host:port` 字面量**（`localhost:8000` / `127.0.0.1:5432` / `0.0.0.0:8080`）
2. **一律走环境变量注入**，配置项从 `configs/runtime.yaml` 读
3. **compose 的主机侧端口（冒号左边）不得落在冻结段**

冻结段：`8080 / 8000 / 8001 / 8002 / 5432 / 6379 / 9000 / 9001`

## 什么不算违规（别误伤）

```yaml
# ✓ 容器侧端口是各服务标准端口，改不得也不必改
- "${PGPORT_HOST:-15432}:5432"     # 主机侧 15432 避开冻结段，容器侧 5432 是 PG 标准端口

# ✓ 容器内健康检查用容器侧地址
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
```

**判的是主机侧（冒号左边）+ 代码里的字面量，不是容器侧。**

## 正例

```python
# ✗ 错
client = httpx.Client(base_url="http://localhost:8000")

# ✓ 对
settings = get_settings()  # 从环境变量 + configs/runtime.yaml 读
client = httpx.Client(base_url=settings.modelgw_url)
```

## 开发环境端口对照

| 服务 | 容器侧 | 开发主机侧 | 交付冻结段 |
|---|---|---|---|
| API | 8000 | 18000 | 8000 |
| PostgreSQL | 5432 | 15432 | 5432 |
| Redis | 6379 | 16379 | 6379 |
| MinIO API | 9000 | 19000 | 9000 |
| MinIO Console | 9001 | 19001 | 9001 |
