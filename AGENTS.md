# AGENTS.md — 部署智能体工作手册

> 你是被派来部署 **ninfer-dsh-station** 的编码智能体。目标：把本仓库部署到一台
> 全新的 Ubuntu 机器上，使其成为"开机自启、浏览器即可用"的本地长上下文智能体工作站。
> 逐步执行，每步都有【命令】、【预期结果】、【失败排查】。不要跳过验证。
> 涉及用户已运行的服务时先询问（可能已有模型服务在跑）。

## 阶段 0：前置检查（必须全过）

```bash
# GPU ≥24GB
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
# 预期：RTX 3090/4090 且 24576 MiB 或 16384+8192(拆分方案不适用,必须单卡≥24G)
# 失败 → 停止并报告：硬件不满足，参考 docs/硬件方案.md

# 驱动与内存
nvidia-smi -q | grep Driver; free -g | head -2; nproc
# 预期：Driver ≥550，内存 ≥31G，CPU ≥6 核

# 读取配置
cat config/config.env   # 已由用户从 config.env.example 复制并填写
# 必须确认：LAN_IP（本机局域网 IP）、MODEL_PATH（.ninfer 模型文件存在，约 18GB）
```

## 阶段 1：安装脚本与 systemd 单元

```bash
INSTALL_DIR=$(pwd)
mkdir -p ~/.local/bin ~/.config/systemd/user ~/bin
cp deploy/bin/ninfer-mode deploy/bin/ninfer-ctl deploy/bin/ninfer-mon deploy/bin/dsh-launch.sh ~/.local/bin/
chmod +x ~/.local/bin/* deploy/engine/run-full.sh
cp deploy/systemd/ninfer-serve-full.service ~/.config/systemd/user/
systemctl --user daemon-reload
# 预期：daemon-reload 无报错
```

把 `INSTALL_DIR` 记进 `~/.bashrc`：`echo "export NINFER_STATION=$INSTALL_DIR" >> ~/.bashrc`

## 阶段 2：引擎

两条路任选：

**A. 二进制就绪**（用户已提供 `dist/ninfer-serve-full`，568MB）：确认
`deploy/engine/run-full.sh` 里 `BIN`/`MODEL` 路径正确（可用 `NINFER_MODEL` 环境变量覆盖模型路径），
并确认 `ROOTFS`（运行库 rootfs，随引擎构建产出，含 CUDA 运行库）路径存在。
**B. 源码构建**：按 `build/BUILD.md` 从上游源码 + `build/patches/` 构建，
产出 `dist/ninfer-serve-full` 与 rootfs 后回到阶段 2A。构建约 30–60 分钟，需要 CUDA 工具链。

## 阶段 3：网关（模型路由 + LAN 访问）

```bash
cp deploy/gateway/codex-model-gateway.py ~/.local/bin/codex-model-gateway
chmod +x ~/.local/bin/codex-model-gateway
# LAN 访问令牌（自动生成，勿用弱口令）
mkdir -p ~/.codex; python3 -c "import secrets;print(secrets.token_hex(32))" > ~/.codex/gateway.token
# 确认网关监听端口与 config.env 的 GATEWAY_PORT 一致（默认 18003）
# 注意：~/.local/bin 需在 PATH；若用户 shell PATH 不含它，把 ExecStart 改成绝对 python3 调用：
#   python3 /home/<user>/.local/bin/codex-model-gateway
```

## 阶段 4：billion-context（长上下文压缩层）

```bash
mkdir -p ~/.config/billion-context
cp deploy/bili/billion-context.json.example ~/.config/billion-context/billion-context.json
# upstream 保持 http://127.0.0.1:18003（网关）；窗口会被 ninfer-mode 自动管理，勿手改
npm i -g billion-context   # 提供 bili CLI；或按上游 README 安装
```

## 阶段 5：dsh 配置

```bash
# 已装 dsh 后，把三个 profile 的 cordis.patch.yml 合并/替换为模板（保留用户已有的其它 provider）：
#   deploy/dsh/cordis.patch.web.yml     → ~/.dsh/profiles/web/cordis.patch.yml
#   deploy/dsh/cordis.patch.tui.yml     → ~/.dsh/profiles/dsh-tui/cordis.patch.yml
#   deploy/dsh/cordis.patch.headless.yml → ~/.dsh/profiles/headless/cordis.patch.yml
# 注意：三个模板里 contextWindow 均为 245760（满血窗口），模型 id 与网关 slug 对应
```

## 阶段 6：启动与冒烟测试

```bash
ninfer-mode full        # 自动：bili 窗口→245760、停日常、起满血引擎（约100秒）
# 预期最后一行："满血引擎 已就绪"

# 冒烟 1：引擎直连
curl -s http://127.0.0.1:8092/v1/models | grep -q qwen3.8-27b-full && echo OK1
# 冒烟 2：网关改写（对外 slug → 引擎实名）
curl -s -X POST http://127.0.0.1:18003/v1/chat/completions -H "Content-Type: application/json" \
  -H "Authorization: Bearer $(cat ~/.codex/gateway.token)" \
  -d '{"model":"qwen3.8-27b-ninfer","messages":[{"role":"user","content":"回复OK"}],"max_tokens":4}' | grep -q '"content":"OK"' && echo OK2
# 冒烟 3：bili 双模型名
for m in qwen3.8-27b-ninfer qwen3.8-27b-full; do
  curl -s -X POST http://127.0.0.1:8787/v1/chat/completions -H "Content-Type: application/json" \
    -d "{\"model\":\"$m\",\"messages\":[{\"role\":\"user\",\"content\":\"回复OK\"}],\"max_tokens\":4}" | grep -q '"content":"OK"' && echo "OK3:$m"
done
# 全部通过 → 核心链路就绪
```

## 阶段 7：dsh-web + 桌面入口

```bash
# dsh-web 作为 systemd 用户服务（带正确环境变量；勿裸命令启动）
mkdir -p ~/.config/systemd/user/dsh-web.service.d
cat > ~/.config/systemd/user/dsh-web.service.d/override.conf <<EOF
[Service]
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=NINFER_API_KEY=dummy
Environment=HTTP_PROXY=http://127.0.0.1:7897
Environment=HTTPS_PROXY=http://127.0.0.1:7897
Environment=NO_PROXY=127.0.0.1,localhost
ExecStart=
ExecStart=$HOME/.local/bin/dsh web --port 13080 --no-open --host ${LAN_IP} --trusted-host ${LAN_IP}:13080
EOF
systemctl --user daemon-reload && systemctl --user restart dsh-web
# 注意：HTTP(S)_PROXY 指向本机 Clash；没有代理就把这两行改成实际代理地址或删除

# 桌面图标
cp deploy/desktop/dsh.desktop ~/Desktop/ 2>/dev/null || cp deploy/desktop/dsh.desktop ~/桌面/ 2>/dev/null || true
sed -i "s|/home/ub|$HOME|g" ~/Desktop/dsh.desktop 2>/dev/null
# 启动器里的 IP：
sed -i "s/192.168.1.100/${LAN_IP}/" ~/.local/bin/dsh-launch.sh
bash ~/.local/bin/dsh-launch.sh   # 会等引擎、取 token、打开浏览器、写地址文件
```

## 阶段 8：验收（对照基准）

```bash
ninfer-ctl snap   # 性能快照
```
| 指标 | 合格线 |
|---|---|
| 引擎 active，显存 ~23.2GB | 是 |
| curl 冒烟 OK1/OK2/OK3 全过 | 是 |
| `dsh-web地址.txt` 生成，浏览器可打开 | 是 |
| decode（发一轮长回答） | 65–320 tok/s |
| 二次相同请求 TTFT | <2s（缓存命中） |

## 开机自启清单（部署完成时确认）

```bash
systemctl --user enable ninfer-serve-full codex-model-gateway billion-context dsh-web
# 日常引擎 ninfer-serve 保持 disable（显存互斥）
```

## 排查速查

| 现象 | 查 |
|---|---|
| 引擎起不来 | `journalctl --user -u ninfer-serve-full -n 50`（常见：rootfs 路径错/显存被占） |
| 502 agw-proxy-failure | dsh-web 是否带 NINFER_API_KEY 环境启动（勿裸命令） |
| 网关 401 | LAN 请求带 `Authorization: Bearer $(cat ~/.codex/gateway.token)` |
| bili model_not_found | 重跑 `ninfer-mode full` 对齐窗口与注册表 |
| 监控显示 inactive 但 GPU 在忙 | 旧 ninfer-mon 实例，关掉重开 `ninfer-ctl mon` |

## 边界

- 不要修改 deploy/ 之外的系统配置（防火墙、全局代理等）除非用户要求
- 显存互斥：任何时候只跑一台引擎；切引擎用 `ninfer-mode full|daily`
- 模型权重与上游源码的获取遵循各自许可，本手册不协助绕过
