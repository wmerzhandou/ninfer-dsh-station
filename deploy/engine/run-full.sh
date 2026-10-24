#!/usr/bin/env bash
# run-full.sh — 满血引擎启动器 :8092（官方件 + DFlash2 K=7 配方）
# 与日常 ninfer-serve(:8091) 并存；显存互斥，同时只跑一台。
#
# 模型路径可用环境变量覆盖（默认见下）：
#   NINFER_MODEL=/path/to/your.ninfer bash run-full.sh
# 运行库 rootfs（含 CUDA 运行库，随引擎构建产出）也可用环境变量覆盖：
#   NINFER_ROOTFS=/path/to/rootfs bash run-full.sh
# systemd 场景下在 unit 里加 Environment=NINFER_MODEL=... 即可。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOTFS="${NINFER_ROOTFS:-$HOME/ninfer-dsh-station/build/rootfs}"
L="$ROOTFS/usr/lib/x86_64-linux-gnu"
BIN="$HERE/dist/ninfer-serve-full"
MODEL="${NINFER_MODEL:-$HOME/models/qwen3_8_27b.ninfer}"

[[ -d "$ROOTFS" ]] || { echo "rootfs missing: $ROOTFS（用 NINFER_ROOTFS=... 指定，或按 build/BUILD.md 构建）" >&2; exit 1; }
[[ -f "$BIN" ]]  || { echo "binary missing: $BIN（先按 build/BUILD.md 构建）" >&2; exit 1; }
[[ -f "$MODEL" ]] || { echo "model missing: $MODEL（用 NINFER_MODEL=... 指定）" >&2; exit 1; }

# 若日常引擎在跑，提示显存互斥（不强行代劳）
if curl -s --max-time 2 http://127.0.0.1:8091/v1/models >/dev/null 2>&1; then
    echo "注意: 日常引擎(:8091) 正在运行，24G 显存跑不了两台。" >&2
    echo "先: systemctl --user stop ninfer-serve" >&2
    exit 1
fi

exec "$L/ld-linux-x86-64.so.2" --library-path "$L:$ROOTFS/usr/local/cuda-libs" \
    "$BIN" "$MODEL" \
    --host 127.0.0.1 --port 8092 \
    --model-id qwen3.8-27b-full \
    --max-context 249856 \
    --kv-capacity 249856 \
    --kv-dtype rk4v4 \
    --prefill-cublas --prefill-chunk 2048 --gdn-state-fp16 \
    --spec dflash2 --draft-tokens 7 --lm-head-draft \
    --vision --vision-residency overlay \
    --max-concurrency 1 --no-thinking --ngram-draft-tokens 15
