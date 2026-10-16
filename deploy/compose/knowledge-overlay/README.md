# knowledge-overlay · 知识引擎运行包

本目录是 **EKOS 知识引擎（RAGFlow）** 的运行载荷，2026-10-15 由 `CheersAI-FileBay/deploy/knowledge/`
迁入 EKOS。归属变更的原因：Askoda（数据需求智能分析助手）的知识底座物理上一直跑在 FileBay 栈里，
造成"两个产品共用一个栈、编排归属不清、故障时找不到主人"。

## 目录结构

```
knowledge-overlay/
├── ragflow/                  部署期定制件（EKOS 自己的增量）
│   ├── .env                  生效的环境变量副本（含凭据，**已 gitignore，权限 600**）
│   ├── filebay-entrypoint.sh 启动包装：注入前端外壳后 exec 上游 entrypoint.sh
│   ├── filebay-shell.css     前端外壳样式
│   ├── filebay-shell.js      前端外壳脚本
│   └── user_api.py.patched   对上游 user_api.py 的补丁（15 行差异）
└── ragflow-upstream/         上游 RAGFlow docker 目录的**平台侧唯一副本**
    ├── .env                  上游默认环境变量（含凭据，**已 gitignore**）
    ├── docker-compose.yml    入口编排（include base）
    ├── docker-compose-base.yml 13 个服务的完整定义（**已去掉 4 个卷的 driver: local**）
    ├── entrypoint.sh         上游 entrypoint，被 filebay-entrypoint.sh exec
    ├── service_conf.yaml.template
    ├── init.sql
    └── ragflow-logs/         运行日志（44 MB，**已 gitignore**）
```

## 为什么要 `include:` 而不是把服务抄进 EKOS 编排

RAGFlow 的依赖编排里，**关键配置是上游 `docker-compose-base.yml` 硬编码的**，不在 `.env` 里：

| 服务 | 硬编码项 | 漏了会怎样 |
|---|---|---|
| `es01` | `node.name` / `discovery.type=single-node` / `xpack.security.*` / `cluster.routing.*` | **bootstrap checks failed，容器反复重启** |
| `mysql` | `MYSQL_ROOT_PASSWORD=${MYSQL_PASSWORD}` | root 口令不对，连不上 |
| `minio` | `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | 鉴权失败，RAGFlow 读不到原始文档 |

实测教训：手抄编排时漏掉 ES 那7 个键，容器 `RestartCount=4`、端口 9200 根本listen 不了。
所以这里沿用 FileBay 已验证的 `include:` 模式——上游编排原样引入，本文件只做四处覆盖
（改端口 / 改内存 / 换卷 / 挂前端外壳）。

## 与 FileBay 的关系

- **卷没有搬**：全部 `external:` 原地引用 `filebay-knowledge-trial_*`，**零数据迁移**。
  卷名保留旧前缀是为了让数据能被找到，改名会丢数据。
- **FileBay 侧的编排已停用但保留**（12 个容器全部 `Exited`，未删除），作为回滚点。
  回滚只需 `docker compose -f deploy/knowledge/docker-compose.trial.yml up -d`，卷原样可用。
- FileBay 侧仍留有一份上游副本（`third_party/ragflow/docker/`）。**待办**：FileBay 若不再
  自主运行知识栈，删掉那份副本，只保留 EKOS 这一份，避免将来升级要同步两处。

## 运维要点

```bash
cd deploy/compose
export COMPOSE_PROFILES=elasticsearch,cpu      # 上游用 profile 控制启哪几个服务

docker compose -f docker-compose.knowledge.yml ps
docker compose -f docker-compose.knowledge.yml logs -f ragflow-cpu
docker compose -f docker-compose.knowledge.yml up -d ragflow-cpu
docker compose -f docker-compose.knowledge.yml down                # 回滚用这个，卷不受影响
```

- **入口仍是 `ekos-ragflow-proxy` 的宿主 :19380**，本组服务不对宿主开端口。
- `ragflow-cpu` 锁 `linux/amd64`：上游只发布 amd64，本机 arm64 走 Rosetta（常驻 2.5 GiB）。
  其余 4 个服务都是多架构镜像，用原生 arm64。
- 排障读日志：`ragflow-upstream/ragflow-logs/task_executor_common_*.log`
  （`docker logs` 会被 jwt warning 刷屏淹没）。
