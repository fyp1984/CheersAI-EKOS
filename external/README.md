# EKOS 外部制品仓（L2）

这里放**大文件**：模型权重、Docker 镜像、安装包、二进制、许可证、样本。

## 三条铁律

1. **这里的东西不进 git**（`.gitignore` 已排除），**也不进 Syncthing**（`.stignore` 已排除）。
2. **两台机器之间传这里的文件，走 AirDrop / U 盘，不走网络。**
   实测链路：Tailscale 0.10 MB/s、LAN 直连 0.03 MB/s → 2.1G 要传 6–19 小时。
3. **账本在别处**：所有组件的登记信息（版本/许可证/哈希/状态）在
   文档仓 `03_实施/外部依赖/INVENTORY.yaml`——那个文件随 Syncthing 同步，两台都看得见。

## 目录说明

| 目录 | 放什么 |
|---|---|
| `models/<厂商>/<模型>@<版本前8位>/` | 模型权重，如 `BAAI/bge-m3@5617a9f/` |
| `images/` | Docker 镜像 tar，命名 `<命名空间>__<镜像>__<标签>.tar` |
| `packages/python/` `packages/node/` | 离线依赖包 |
| `binaries/macos/` `binaries/linux/` | 安装包与可执行文件 |
| `licenses/` | 许可证原文（配合 INVENTORY 的 `license` 字段做合规复核） |
| `samples/` | 合成样本（**真实客户数据一律不进这里**） |
| `_incoming/` | **暂存区**：AirDrop/U盘先落这里，校验 SHA256 通过后再归位 |
| `_unused/` | 暂时不用但不想删的（如 bge-m3 的 onnx 2.1G） |

## `_incoming/` 为什么必须有

直接把传输来的文件丢进正式目录，一旦传输中断就会留下残缺文件污染制品树
（2026-09-09 传 bge-m3 时就留过 34MB 的残片）。**先落 `_incoming/`，校验哈希，再 `mv` 归位。**

## 归位后记得做两件事

1. 在 `INVENTORY.yaml` 里把该组件的 `status` 从 `pending` 改成 `ready`，填 `digest`
2. 若许可证是 AGPL / SSPL 类，它会进 `license_review_pending`——Q6 前必须出结论

---
规范正文：`02_方案/11_EKOS外部依赖与制品统一目录规范-v1.0-内部-260910.md`
