#!/usr/bin/env bash
# build-full.sh — 编译 Don-Chad 满血引擎（Linux x86_64 sm86）
# 产物: dist/ninfer-serve（官方件 + DFlash2 的指定引擎）
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="docker.m.daocloud.io/nvidia/cuda:13.3.0-devel-ubuntu24.04"
CONTAINER="ninfer-full-build-$$"
BUILD_ROOT="/tmp/ninfer-full-build-$$"
PARALLEL="${NINFER_PARALLEL:-6}"

die() { echo "error: $*" >&2; exit 1; }
say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

[[ -d "$REPO_DIR/src-tree" || -f "$REPO_DIR/CMakeLists.txt" ]] || die "run from the ninfer-3090-full checkout"
docker info >/dev/null 2>&1 || die "docker daemon unavailable"

cleanup() {
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    rm -rf "$BUILD_ROOT"
}
trap cleanup EXIT INT TERM

say "copying source tree"
mkdir -p "$BUILD_ROOT"
cp -a "$REPO_DIR" "$BUILD_ROOT/src"

say "starting build container (tmpfs 12G, memory 20G, -j$PARALLEL)"
docker run -d --name "$CONTAINER" \
    --memory 20g --memory-swap 20g \
    --tmpfs "/tmpfs:size=12g" \
    -v "$BUILD_ROOT/src:/src:ro" \
    "$IMAGE" sleep infinity >/dev/null

say "installing toolchain"
docker exec "$CONTAINER" bash -c '
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        cmake ninja-build gcc-13 g++-13 pkg-config \
        libavcodec-dev libavformat-dev libavutil-dev libswscale-dev libcurl4-openssl-dev \
        >/tmp/apt.log 2>&1 || { tail -30 /tmp/apt.log; exit 1; }
' || die "toolchain install failed"

say "building ninfer-serve (expect 30-60 min; small_t_i8 is slow, that is normal)"
docker exec "$CONTAINER" bash -c "
    export CC=/usr/bin/gcc-13 CXX=/usr/bin/g++-13
    export CUDACXX=/usr/local/cuda/bin/nvcc PATH=/usr/local/cuda/bin:\$PATH
    cmake -S /src -B /tmpfs/build -G Ninja \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CUDA_ARCHITECTURES=86 \
        -DNINFER_BUILD_BENCHMARKS=OFF \
        -DCMAKE_C_COMPILER=/usr/bin/gcc-13 \
        -DCMAKE_CXX_COMPILER=/usr/bin/g++-13 \
        -DCMAKE_CUDA_COMPILER=/usr/local/cuda/bin/nvcc \
        -DCMAKE_CUDA_HOST_COMPILER=/usr/bin/g++-13 \
    && cmake --build /tmpfs/build --target ninfer-serve --parallel $PARALLEL
" || die "build failed"

say "collecting artifact"
mkdir -p "$REPO_DIR/dist"
docker cp "$CONTAINER:/tmpfs/build/apps/ninfer-serve" "$REPO_DIR/dist/ninfer-serve-full"
chmod +x "$REPO_DIR/dist/ninfer-serve-full"
ls -lh "$REPO_DIR/dist/ninfer-serve-full"
echo "sha256: $(sha256sum "$REPO_DIR/dist/ninfer-serve-full" | cut -d' ' -f1)"
say "done — 用 run-full.sh 启动"
