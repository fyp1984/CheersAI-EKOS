#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/../.." && pwd)
# 权重路径不再硬编码散落目录：优先读环境变量，默认取规范路径。
# 出处：03_实施/外部依赖/INVENTORY.yaml（组件 id: model-bge-m3）；
# 目录规范见 02_方案/11_EKOS外部依赖与制品统一目录规范-v1.0。
# ⚠️ 旧路径 $REPO_ROOT/models/bge-m3 已于 2026-09-10 迁入 external/，勿改回。
MODEL_DIR="${EKOS_MODEL_BGE_M3:-$REPO_ROOT/external/models/BAAI/bge-m3@5617a9f}"
PORT=18001
CONTAINER=ekos-tei-embedding
IMAGE=infiniflow/text-embeddings-inference:cpu-1.8

echo "[1/5] 检查权重目录"
if [ ! -d "$MODEL_DIR" ]; then
  echo "未找到权重: $MODEL_DIR"
  echo "不通过怎么办: bge-m3 权重应落在 external/models/BAAI/bge-m3@5617a9f/"
  echo "  （登记见 03_实施/外部依赖/INVENTORY.yaml，组件 id: model-bge-m3）"
  echo "  也可用 EKOS_MODEL_BGE_M3=/your/path bash $0 指定其他位置。"
  exit 1
fi
echo "  权重目录 OK: $MODEL_DIR"

echo "[2/5] 检查端口 $PORT 是否空闲"
if lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
  echo "端口 $PORT 已被占用"
  echo "不通过怎么办: 换端口需同步改契约 X4 与指导手册；或先停掉占用进程。"
  lsof -nP -iTCP:$PORT -sTCP:LISTEN | tail -n +2 | head -3
  exit 1
fi
echo "  端口 $PORT 空闲"

echo "[3/5] 检查 Docker 内存（总量 >= 14 GB 且当前余量 >= 9 GB）"
DOCKER_MEM_BYTES=$(docker info --format '{{.MemTotal}}' 2>/dev/null || echo 0)
DOCKER_MEM_GB=$(awk -v b="$DOCKER_MEM_BYTES" 'BEGIN{printf "%.1f", b/1073741824}')
echo "  当前 Docker 总量: ${DOCKER_MEM_GB} GB"
if awk -v g="$DOCKER_MEM_GB" 'BEGIN{exit !(g < 14)}'; then
  echo "Docker 内存不足 14 GB，加载 bge-m3（fp32 约 4.3 GB）会被 SIGKILL（退出码 137）。"
  echo "不通过怎么办: Docker Desktop → Settings → Resources → Memory 调到 16 GB → Apply & Restart。"
  echo "  该操作会重启 Docker，其他在跑的容器（filebay / Dify 等）会中断，请确认后再执行。"
  exit 1
fi
# 当前余量：bge-m3 fp32 实测常驻约 8-11 GB，需留足余量，否则会与其它容器抢内存被 OOM
USED_GB=$(docker stats --no-stream --format '{{.MemUsage}}' 2>/dev/null | awk -F'[ /GiB]+' '{s+=$1} END{printf "%.1f", s}')
FREE_GB=$(awk -v t="$DOCKER_MEM_GB" -v u="$USED_GB" 'BEGIN{printf "%.1f", t-u}')
echo "  当前容器已用: ${USED_GB} GB  余量: ${FREE_GB} GB"
if awk -v g="$FREE_GB" 'BEGIN{exit !(g < 9)}'; then
  echo "当前可用内存余量 ${FREE_GB} GB < 9 GB，float32 加载 bge-m3 会被 OOM。"
  echo "不通过怎么办: 先停掉占用内存的其它容器（如 filebay 全套约 7.5 GB、Dify 等），"
  echo "  再跑本脚本。注意：TEI(约 8 GB) 与完整 RAGFlow 栈(约 7.5 GB) 在本机 15.6 GB 上限下"
  echo "  不能同时运行，E0 验证须串行——先起 TEI 验证向量，再停 TEI 起 RAGFlow 做手动步骤。"
  exit 1
fi

echo "[4/5] 启动 TEI（amd64 镜像经 Rosetta 转译，需 libfakeintel 兼容层；默认 float32 → ORT+MKL，最快路径）"
echo "  注意: 切勿传 --dtype float16 —— ORT 不支持 fp16，会回退到 candle CPU backend，"
echo "        在 Rosetta 下 warmup 极慢（实测 16+ 分钟仍未就绪）。float32/ORT 是已验证快路径。"
docker rm -f $CONTAINER >/dev/null 2>&1 || true
docker run -d \
  --name $CONTAINER \
  --platform linux/amd64 \
  -p $PORT:80 \
  -e LD_PRELOAD=/usr/local/libfakeintel.so \
  -e MKL_ENABLE_INSTRUCTIONS=AVX512_E4 \
  -v "$MODEL_DIR:/models/bge-m3" \
  $IMAGE \
  --model-id /models/bge-m3 --auto-truncate --max-batch-tokens 8192 >/dev/null

echo "[5/5] 等待模型加载（Rosetta 转译下 warmup 实测约 3-8 分钟，最长等 10 分钟）"
READY=0
for i in $(seq 1 200); do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 3 "http://127.0.0.1:$PORT/health" 2>/dev/null || echo 000)
  if [ "$CODE" = "200" ]; then READY=1; break; fi
  if ! docker ps --filter name=$CONTAINER --format '{{.Names}}' | grep -q $CONTAINER; then
    echo "容器已退出，加载失败"
    docker logs $CONTAINER 2>&1 | tail -15
    echo "不通过怎么办: 若日志末行为 'Warming up model' 且退出码 137，即内存不足，见步骤 [3/5]。"
    exit 1
  fi
  sleep 3
done

if [ "$READY" != "1" ]; then
  echo "等待 180 秒后仍未就绪"
  docker logs $CONTAINER 2>&1 | tail -15
  exit 1
fi

echo
echo "=== 服务就绪 ==="
curl -s -m 5 "http://127.0.0.1:$PORT/info"
echo
echo
echo "=== 向量自检（bge-m3 应为 1024 维） ==="
RESP=$(curl -s -m 15 -X POST "http://127.0.0.1:$PORT/embeddings" \
  -H 'Content-Type: application/json' \
  -d '{"input":"现代金融租赁有限公司营业收入","model":"bge-m3","normalize":true}' 2>/dev/null)
DIM=$(echo "$RESP" | /Users/FYP/.workbuddy/binaries/python/versions/3.13.12/bin/python3 -c '
import sys,json
try:
    d=json.loads(sys.stdin.read())
    data=d.get("data")
    v=data[0]["embedding"] if data else None
    print(len(v) if v else "NONE")
except Exception as e:
    print("解析失败")
' 2>/dev/null)
echo "实测维度: $DIM"
if [ "$DIM" != "1024" ]; then
  echo "维度不是 1024，模型可能不是 bge-m3"
  echo "不通过怎么办: 核对 /info 返回的 model_id 是否为 /models/bge-m3；字段须用 input（单数）而非 inputs。"
  exit 1
fi
echo "自检通过"
echo
echo "后续: RAGFlow 配置外部 Embedding 时，base_url 填 http://host.docker.internal:$PORT"
