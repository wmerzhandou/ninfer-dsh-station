# BUILD.md — 满血引擎构建指南（Linux，RTX 3090）

本目录包含从上游 [Neroued/ninfer](https://github.com/Neroued/ninfer) 源码构建
`dist/ninfer-serve-full`（满血引擎二进制）+ 运行库 rootfs 所需的补丁与脚本。

> 目标产物：
> - `deploy/engine/dist/ninfer-serve-full`（约 568MB）
> - `build/rootfs/`（运行库 rootfs：CUDA 运行库 + glibc，引擎通过自带的 ld-linux 加载，宿主机只需 NVIDIA 驱动 ≥550）

## 前置条件

- Ubuntu 22.04/24.04，x86-64
- NVIDIA 驱动 ≥550（只需驱动，不需要宿主机 CUDA 工具链——rootfs 自带运行库）
- 构建工具链（构建阶段需要）：CUDA 12.8 工具链、CMake ≥3.25、Ninja、gcc-12+
- 源码获取：克隆上游 ninfer 仓库（遵循其许可）

## 构建步骤

```bash
# 1. 获取源码（版本已钉死，补丁适用）
git clone https://github.com/Don-Chad/ninfer-3090.git ninfer-src
cd ninfer-src
git checkout d8c2925          # 本方案验证过的提交（2026-10 master）

# 2. 应用本仓库 patches/ 下的 8 个补丁（平台无关运行时、链接组、编译器修复、win32 适配等）
git apply /path/to/ninfer-dsh-station/build/patches/*.patch

# 3. 把本仓库构建脚本放进源码树再运行（脚本要求在含 CMakeLists.txt 的源码根执行）
cp /path/to/ninfer-dsh-station/build/build-full.sh .
bash ./build-full.sh

# 4. 产物归位
cp -r dist /path/to/ninfer-dsh-station/deploy/engine/
cp -r rootfs /path/to/ninfer-dsh-station/build/rootfs   # 路径以 build-full.sh 实际输出为准
```

## 模型文件

引擎需要 `.ninfer` 格式的模型工件（约 18GB）。用上游转换工具从官方 Qwen3.8-27B
工件转换（遵循 Qwen 许可），然后：

```bash
export NINFER_MODEL=/path/to/qwen3_8_27b.ninfer
```

## 配方说明（与 run-full.sh 对应）

| 参数 | 值 | 作用 |
|---|---|---|
| --max-context / --kv-capacity | 249856 | 245K KV 池（rk4v4 量化） |
| --kv-dtype | rk4v4 | KV 4bit 量化，单卡 24G 装下 245K 的关键 |
| --spec dflash2 --draft-tokens 7 --lm-head-draft | DFlash2 | 投机解码（decode 65–321 tok/s） |
| --ngram-draft-tokens 15 | ngram-copy | 复述场景进一步提速 |
| --vision --vision-residency overlay | 视觉 overlay | 多模态常驻但几乎不占显存池 |
| --prefill-cublas --prefill-chunk 2048 | | prefill 800–2,260 tok/s |
| --no-thinking | 默认关思考 | 请求带 reasoning_effort 可开启（见 docs/部署运维指南.md） |
| --max-concurrency 1 | 单并发 | 245K 窗口的代价；开 2 lanes 需缩窗口至 ~137K |

## 故障

- 链接错误（cccl/cudadevrt）→ 补丁 01/02 已修，确认 8 个补丁全部应用
- sm_86 相关 → 构建需 `CMAKE_CUDA_ARCHITECTURES=86`（build-full.sh 已处理）
- 其余见上游仓库文档与 tests/README.md
