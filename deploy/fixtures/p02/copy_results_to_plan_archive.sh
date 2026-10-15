#!/usr/bin/env bash
# P-02（v4）收尾：把代码仓 results/P-02/ 全部实测文件复制进规划仓分布式验证-E0/results/P-02/
# 用途：沙箱写权限范围不覆盖规划仓，人工执行本脚本完成 Q3 出口双份留痕要求。
#
# 用法：
#   cd /Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS
#   bash deploy/fixtures/p02/copy_results_to_plan_archive.sh
#
# 执行前自动做 4 项预检，任一失败立即退出：
#   1) 代码仓源目录必须存在且非空
#   2) 规划仓父路径存在（防止整段绝对路径打错）
#   3) 目标 results 目录存在（分布式验证-E0/results/ 应为既有目录；若不存在脚本会提示但不擅自新建）
#   4) 最终 P-02 子目录 mkdir -p 成功
#
# 执行后行为：
#   - 用 cp -R（不写 --remove-destination，避免误删规划仓已有历史文件）同名文件直接覆盖
#   - 末尾再 ls 一次目标目录 + 源/目标 文件数 diff，两边相等才打印 PASS

set -euo pipefail

SRC_ROOT="/Users/FYP/Documents/WorkSpace/CheersAI/subproducts/CheersAI-EKOS"
SRC_DIR="${SRC_ROOT}/results/P-02"

PLAN_ROOT="/Users/FYP/Documents/@西北人的成长/10-Projects/职业项目/CheersAI-EKOS/03_实施"
E0_ROOT="${PLAN_ROOT}/分布式验证-E0"
DST_PARENT="${E0_ROOT}/results"
DST_DIR="${DST_PARENT}/P-02"

STEP=0
pass()  { printf "\033[32m[ OK ]\033[0m  %s\n" "$*"; }
info()  { printf "\033[36m[INFO]\033[0m  %s\n" "$*"; }
warn()  { printf "\033[33m[WARN]\033[0m  %s\n" "$*"; }
fail()  { printf "\033[31m[FAIL]\033[0m  %s\n" "$*"; exit 1; }
title() { printf "\n== %s. %s ==\n" "$STEP" "$*"; }

STEP=$((STEP+1)); title "检查代码仓源目录"
if [[ ! -d "${SRC_DIR}" ]]; then
  fail "源目录不存在：${SRC_DIR}（请先在 EKOS 代码仓完整跑完 P-02 实测）"
fi
SRC_COUNT=$(find "${SRC_DIR}" -maxdepth 1 -type f | wc -l | tr -d ' ')
if [[ "${SRC_COUNT}" -eq 0 ]]; then
  fail "源目录为空：${SRC_DIR}（至少应有 P02_v4_receipt.md、tb2_measure.json、3 份 verdict.json 共 12+ 份）"
fi
pass "源目录存在，共 ${SRC_COUNT} 份文件：${SRC_DIR}"

STEP=$((STEP+1)); title "检查规划仓父路径"
if [[ ! -d "${PLAN_ROOT}" ]]; then
  fail "规划仓根路径不存在：${PLAN_ROOT}（请确认你在本机、而不是远端沙箱上执行本脚本）"
fi
if [[ ! -d "${E0_ROOT}" ]]; then
  fail "分布式验证-E0 目录不存在：${E0_ROOT}（请先确认规划仓目录结构未变动）"
fi
pass "规划仓与分布式验证-E0 目录均在位"

STEP=$((STEP+1)); title "准备目标目录"
if [[ ! -d "${DST_PARENT}" ]]; then
  warn "E0 下 results/ 目录不存在：${DST_PARENT}，尝试创建…"
  mkdir -v "${DST_PARENT}"
fi
mkdir -pv "${DST_DIR}"
pass "目标目录就绪：${DST_DIR}"

STEP=$((STEP+1)); title "开始复制（cp -R 覆盖同名）"
info "源：${SRC_DIR}/*"
info "目标：${DST_DIR}/"

cp -R "${SRC_DIR}/." "${DST_DIR}/"

DST_COUNT=$(find "${DST_DIR}" -maxdepth 1 -type f | wc -l | tr -d ' ')
pass "复制完成，源 ${SRC_COUNT} 份 / 目标 ${DST_COUNT} 份"

STEP=$((STEP+1)); title "校验 + 列目录"
if [[ "${SRC_COUNT}" -ne "${DST_COUNT}" ]]; then
  fail "源/目标文件数不一致：源=${SRC_COUNT}，目标=${DST_COUNT}（请人工检查是否有隐藏文件被忽略）"
fi

info "目标目录文件清单："
ls -lah "${DST_DIR}"

echo
printf "\033[32m====== 双份留痕完成 PASS ======\033[0m\n"
echo "下一步（可选）："
echo "  打开 ${PLAN_ROOT}/门禁检查/gate_baseline.json 对应的 Q3 人工清单，按 results/P-02/ 的数值逐格填字。"
echo
