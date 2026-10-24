# ninfer-dsh-station

**一张二手 RTX 3090，跑满 245K 长上下文的本地智能体工作站。**
NInfer 满血引擎 + dsh (DeepSeek Harness) + billion-context 长上下文压缩层的完整部署方案。

- 🧠 满血版 Qwen3.8-27B：245,760 token 上下文 · 视觉多模态 · DFlash2 投机解码（decode 65–321 tok/s）
- ⚡ 缓存命中秒回（<2s TTFT），逐轮加料只 prefill 新增部分（实测 207K 上下文 1.3s 回读）
- 🗜 超过 245K 由 billion-context 智能压缩兜底，会话无限续聊
- 🖥 双引擎一键切换（满血 249K / 日常 180K），开机全链路自启
- 🤖 **自带部署智能体手册（AGENTS.md）**：克隆本仓库后让任意本地编码智能体（Kimi Code / Claude Code / Codex）读它，即可完成全自动部署

> 硬件选型与 2026-10 市场价格核实：见 [docs/硬件方案.md](docs/硬件方案.md)
> 整机预算约 ¥9,000–13,000（二手 3090 + 工作站/AM4/AM5 平台，三档方案）

## 架构

```
                ┌──────────────────────────────────────────────┐
                │        codex-model-gateway :18003            │
  dsh ─┐        │  对外统一模型名 qwen3.8-27b-ninfer              │
       ├─ bili ─┤     ↓ 识别后改写为引擎真实 id 转发               │
  codex┘ :8787  │  满血: qwen3.8-27b-full → 127.0.0.1:8092      │
  (自管理)      │  日常: qwen3.8-27b-ninfer → 127.0.0.1:8091    │
                └──────────────────────────────────────────────┘
     bili = billion-context 长上下文压缩层（窗口随引擎自动切换）
```

对外（codex / dsh / 任何 OpenAI 客户端）永远只用 `http://<本机IP>:18003/v1` + 模型名 `qwen3.8-27b-ninfer`，切引擎对客户端透明。

## 快速开始

### 路线 A：让智能体帮你部署（推荐）

```bash
git clone https://github.com/wmerzhandou/ninfer-dsh-station.git ~/ninfer-dsh-station
cd ~/ninfer-dsh-station
cp config/config.env.example config/config.env   # 改 LAN_IP 和模型路径
# 然后用任意本地编码智能体：
#   "请阅读 AGENTS.md，把这套系统部署到本机"
```

### 路线 B：手动部署

按 [docs/部署运维指南.md](docs/部署运维指南.md) 操作（服务管理、切换、监控、排查全在里面）。

## 前提条件

| 项 | 要求 |
|---|---|
| GPU | **≥24GB 显存**（RTX 3090/4090 24G；实测满血版占 23.3GB） |
| 系统 | Ubuntu 22.04/24.04，NVIDIA 驱动 ≥550 |
| 内存 | 32GB 底线，64GB 宽裕 |
| 磁盘 | 60GB+（模型 18GB + 系统） |
| 模型 | `qwen3_8_27b.ninfer`（约 18GB，由 ninfer 官方件转换；引擎构建见 [build/BUILD.md](build/BUILD.md)） |

## 目录

```
config/    部署变量（config.env.example，唯一要改的东西）
deploy/    全部部署资产（systemd 单元、脚本、网关、dsh/bili 配置模板）
build/     引擎源码构建（patches + build-full.sh）
docs/      硬件方案 / 部署运维指南 / 小白使用指南
AGENTS.md  给部署智能体的工作手册（路线 A 的入口）
```

## 实测数据（3090 单卡，2026-10-24）

| 场景 | 结果 |
|---|---|
| decode | 短回复 150–320 tok/s；长文 65–90 tok/s |
| prefill | 806–2,263 tok/s（随上下文长度递减） |
| 207K 上下文回读 | 缓存命中 99.99%，全程 **1.3s** |
| 逐轮加料到 207K | 每轮仅对新增部分 prefill |
| 满载功耗 | GPU 330W，整机约 450–500W |

## 致谢与说明

- 推理引擎基于 [Neroued/ninfer](https://github.com/Neroued/ninfer)（C++/CUDA 单卡推理引擎），本仓库不含其源码，仅含构建补丁与部署资产
- dsh = [DeepSeek Harness](https://github.com/ranxianglei/billion-context) 生态；bili = [billion-context](https://github.com/ranxianglei/billion-context) 长上下文压缩代理
- 模型权重请遵循 Qwen 许可自行获取转换
- 本项目按 MIT 许可发布（deploy/ 内脚本同 MIT；上游组件遵循各自许可）
