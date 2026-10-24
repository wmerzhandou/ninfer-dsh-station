# AGENTS.md — 部署智能体完整工作手册 v2

> 你的任务：把 **ninfer-dsh-station** 部署到这台机器，使它成为"开机自启、浏览器即用"的
> 本地长上下文智能体工作站（满血 Qwen3.8-27B，245K 上下文 + 视觉 + 秒回缓存）。
>
> 执行纪律：
> 1. **严格按阶段顺序执行**，每步先跑【验证】，通过再进下一步；
> 2. 任何【验证】失败 → 先跑该步【排错】，仍失败 → **停止，把失败步骤编号和完整报错输出贴给用户**，不要硬闯；
> 3. 所有命令以普通用户运行，需要 sudo 的地方已明确标出；
> 4. 全程约 30–60 分钟（不含模型下载）。

---

## 阶段 0：环境前置（全部通过才能继续）

```bash
# 0.1 GPU 显存 ≥24GB（硬性门槛）
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
# 【验证】输出含 3090/4090 且 24576 MiB（或 16384 显示为 4090 24G 卡）
# 【排错】无输出 → 驱动没装：sudo ubuntu-drivers install && sudo reboot 后重来
# 【排错】显存 <24G → 硬件不满足，停止并告知用户看 docs/硬件方案.md

# 0.2 驱动 ≥550
nvidia-smi -q | grep -m1 "Driver Version" | awk '{print $3}'
# 【验证】≥ 550
# 【排错】低于 550 → sudo ubuntu-drivers install && sudo reboot

# 0.3 内存 ≥32G、磁盘剩余 ≥60G
free -g | awk 'NR==2{print $2}'; df -g "$HOME" | awk 'NR==2{print $4}'
# 【验证】内存数字 ≥31，磁盘数字 ≥60

# 0.4 基础工具
git --version; curl --version | head -1; zstd --version
# 【排错】缺哪个装哪个（需要 sudo）：
#   sudo apt update && sudo apt install -y git curl zstd

# 0.5 Node.js ≥20（dsh 和 bili 都依赖）
node --version
# 【验证】v20.x / v22.x / v24.x 均可
# 【排错】没有或 <20，用 nvm 装（国内把镜像也配好）：
#   curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
#   export NVM_NODEJS_ORG_MIRROR=https://npmmirror.com/mirrors/node   # 国内加速，必须
#   source ~/.bashrc && nvm install 20
# 【排错】raw.githubusercontent.com 打不开（无代理环境常见）→ 让用户配置代理后重试，或：
#   curl -o- https://ghproxy.com/https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash

# 0.6 npm 全局目录指向 ~/.local（systemd 单元按此路径找命令）
npm config set prefix "$HOME/.local"
grep -q 'HOME/.local/bin' ~/.bashrc || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
export PATH="$HOME/.local/bin:$PATH"
which npm && npm config get prefix
# 【验证】prefix 输出 /home/<用户名>/.local

# 0.7 读取部署配置
cat config.env
# 【验证】用户已填 LAN_IP 和 MODEL_PATH；MODEL_PATH 指向的文件存在且约 18GB
# 【排错】模型文件不存在 → 跳到本文末尾【附录A：模型文件获取】，拿到后回来
```

---

## 阶段 1：安装脚本与 systemd 单元

```bash
cp deploy/bin/ninfer-mode deploy/bin/ninfer-ctl deploy/bin/ninfer-mon deploy/bin/dsh-launch.sh ~/.local/bin/
chmod +x ~/.local/bin/ninfer-mode ~/.local/bin/ninfer-ctl ~/.local/bin/ninfer-mon ~/.local/bin/dsh-launch.sh deploy/engine/run-full.sh
cp deploy/systemd/*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user list-unit-files | grep -E "ninfer|dsh-web|gateway|billion"
```
【验证】看到 4 个单元（ninfer-serve-full / dsh-web / codex-model-gateway / billion-context）。
【排错】`~/.config/systemd/user` 不存在 → 先 `mkdir -p ~/.config/systemd/user`。

---

## 阶段 2：引擎二进制（下载优先，约 5–10 分钟）

```bash
sudo apt install -y zstd    # 若 0.4 已装跳过
mkdir -p deploy/engine/dist build
cd build
curl -L -o engine.tar.zst https://github.com/wmerzhandou/ninfer-dsh-station/releases/download/v1.0.0-engine/ninfer-serve-full-rtx3090.tar.zst
curl -L -o SHA256SUMS https://github.com/wmerzhandou/ninfer-dsh-station/releases/download/v1.0.0-engine/SHA256SUMS
sha256sum -c SHA256SUMS
```
【验证】`engine.tar.zst: OK`。
【排错】GitHub 下载极慢/失败 → 给用户三个选项：配代理重试 / 挂 github 加速镜像（如 `https://ghproxy.com/https://github.com/...` 前缀）/ 源码构建（附录B）。

```bash
tar -I zstd -xf engine.tar.zst
mv dist/ninfer-serve-full ../deploy/engine/dist/
mv rootfs ../build/rootfs   # 若提示已存在先 rm -rf ../build/rootfs
cd .. && deploy/engine/dist/ninfer-serve-full --version
```
【验证】打印版本号。
【排错】`error while loading shared libraries` —— 正常现象：该二进制必须用 rootfs 内的加载器运行，
版本检查请改用： `"$PWD/build/rootfs/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2" --library-path "$PWD/build/rootfs/usr/lib/x86_64-linux-gnu:$PWD/build/rootfs/usr/local/cuda-libs" deploy/engine/dist/ninfer-serve-full --version`

---

## 阶段 3：网关（模型路由 + 局域网访问）

```bash
cp deploy/gateway/codex-model-gateway.py ~/.local/bin/codex-model-gateway
chmod +x ~/.local/bin/codex-model-gateway
mkdir -p ~/.codex
python3 -c "import secrets; print(secrets.token_hex(32))" > ~/.codex/gateway.token
head -c 8 ~/.codex/gateway.token; echo " ...（已生成，勿外泄）"
```
【验证】`~/.codex/gateway.token` 存在且 64 位十六进制。
说明：网关对外端口 18003，监听 0.0.0.0；本机访问免鉴权，局域网访问必须带
`Authorization: Bearer <该token>`。

---

## 阶段 4：billion-context 长上下文压缩层

```bash
npm i -g billion-context
bili --version    # 预期 0.1.20x
mkdir -p ~/.config/billion-context
cp deploy/bili/billion-context.json.example ~/.config/billion-context/billion-context.json
```
【验证】`bili --version` 有输出。
【排错】npm 慢/失败 → `npm config set registry https://registry.npmmirror.com` 后重试。

---

## 阶段 5：dsh 安装与配置

```bash
npm i -g @deepseek-ai/dsh
dsh --version
# 【验证】有版本输出。【排错】无 → 检查 0.5/0.6 步

dsh --version >/dev/null    # 触发 ~/.dsh 目录生成
mkdir -p ~/.dsh/profiles/web ~/.dsh/profiles/dsh-tui ~/.dsh/profiles/headless
cp deploy/dsh/cordis.patch.web.yml     ~/.dsh/profiles/web/cordis.patch.yml
cp deploy/dsh/cordis.patch.tui.yml     ~/.dsh/profiles/dsh-tui/cordis.patch.yml
cp deploy/dsh/cordis.patch.headless.yml ~/.dsh/profiles/headless/cordis.patch.yml
```
【验证】三个文件就位：`ls ~/.dsh/profiles/*/cordis.patch.yml`。

---

## 阶段 6：启动与冒烟测试（核心验收）

```bash
ninfer-mode full
# 【验证】最后一行"满血引擎 已就绪"（约 100 秒：权重加载 17.2GiB + 图捕获）
# 【排错】超时 → journalctl --user -u ninfer-serve-full -n 50
#   常见1: model missing → NINFER_MODEL 没设：systemctl --user edit ninfer-serve-full
#          加 [Service] 段 Environment=NINFER_MODEL=<你的模型路径>，再 restart
#   常见2: rootfs missing → ln -s "$PWD/build/rootfs" ~/ninfer-dsh-station/build/rootfs

# 冒烟1：引擎直连
curl -s http://127.0.0.1:8092/v1/models | grep -q qwen3.8-27b-full && echo "OK1 引擎"
# 冒烟2：网关 slug 改写
curl -s -X POST http://127.0.0.1:18003/v1/chat/completions -H "Content-Type: application/json" \
  -d '{"model":"qwen3.8-27b-ninfer","messages":[{"role":"user","content":"回复OK"}],"max_tokens":4}' \
  | grep -q '"content":"OK"' && echo "OK2 网关"
# 冒烟3：bili 两个模型名
for m in qwen3.8-27b-ninfer qwen3.8-27b-full; do
  curl -s -X POST http://127.0.0.1:8787/v1/chat/completions -H "Content-Type: application/json" \
    -d "{\"model\":\"$m\",\"messages\":[{\"role\":\"user\",\"content\":\"回复OK\"}],\"max_tokens\":4}" \
    | grep -q '"content":"OK"' && echo "OK3:$m"
done
```
【验证】OK1、OK2、OK3:qwen3.8-27b-ninfer、OK3:qwen3.8-27b-full 全部出现。
说明：日常引擎（180K 版）未随本方案发布，`ninfer-mode daily` 会报单元不存在，属预期——满血版就是默认。

---

## 阶段 7：dsh-web（浏览器界面）+ 桌面入口

```bash
# 7.1 用 LAN_IP 生成绑定局域网地址的覆盖配置
mkdir -p ~/.config/systemd/user/dsh-web.service.d
LAN_IP=$(grep LAN_IP config.env | cut -d= -f2)
cat > ~/.config/systemd/user/dsh-web.service.d/override.conf <<EOF
[Service]
ExecStart=
ExecStart=%h/.local/bin/dsh web --port 13080 --no-open --host ${LAN_IP} --trusted-host ${LAN_IP}:13080
EOF
# 【说明】仓库自带的 dsh-web.service 已含 NINFER_API_KEY 与 PATH；
#   需要代理访问外网时才追加 Environment=HTTP_PROXY=...（多数内网部署不需要）
systemctl --user daemon-reload && systemctl --user restart dsh-web

# 7.2 取访问地址（token 每次启动都变）
sleep 6 && journalctl --user -u dsh-web -n 5 --no-pager -o cat | grep -oE "http://[^ ]*token=\S+" | tail -1
```
【验证】输出 `http://<LAN_IP>:13080/?token=...`。浏览器打开能见到 dsh 界面。
【排错】401 → token 过期，重新取；打不开 → `systemctl --user status dsh-web` + `ss -tlnp | grep 13080`。

```bash
# 7.3 桌面一键图标（GNOME 桌面）
DESK=~/Desktop; [ -d ~/桌面 ] && DESK=~/桌面
cp deploy/desktop/dsh.desktop "$DESK/" && chmod +x "$DESK/dsh.desktop"
sed -i "s|/home/ub|$HOME|; s|192.168.1.100|${LAN_IP}|" "$DESK/dsh.desktop"
gio set "$DESK/dsh.desktop" metadata::trusted true 2>/dev/null || true
bash ~/.local/bin/dsh-launch.sh    # 会刷新 ~/Desktop/dsh-web地址.txt 并打开浏览器
```
【验证】桌面出现图标；双击能打开浏览器进入 dsh。

---

## 阶段 8：性能验收（对照基准，不达标要报告）

发一条"写一段300字散文"的指令，然后：

```bash
ninfer-ctl snap
```
| 指标 | 合格线 | 不达标含义 |
|---|---|---|
| 服务 active，显存 ~23.2GB | 必须 | 引擎没跑满/没跑 |
| decode | 65–320 tok/s | 低于 65 → 检查是否有其它进程抢 GPU：`nvidia-smi` |
| 同一问题再问一次 TTFT | <2s | 缓存没命中 → 检查 `--no-prefix-reuse` 是否被误加 |

---

## 阶段 9：开机自启

```bash
systemctl --user enable ninfer-serve-full codex-model-gateway billion-context dsh-web
systemctl --user is-enabled ninfer-serve-full codex-model-gateway billion-context dsh-web
```
【验证】四行全是 enabled。重启机器后 3 分钟，全套服务自动恢复（这是最终验收，强烈建议做一次真重启测试）。

---

## 附录A：模型文件获取（唯一需要用户自己准备的东西）

目标文件：`qwen3_8_27b.ninfer`（17.7 GiB）。**两条路，强烈推荐路线 1**（本方案实测机就是路线 1 拿的）。

### 路线 1：下载上游官方转换件（推荐，相当于"官方替你转换好了"）

上游把转换好的工件发布在 HuggingFace（`WarlaxZ/Qwen3.8-27B-NInfer-3090`，配方 `qwen3_8_27b`：
imatrix 加权分组量化、4bit embedding、6bit head，含视觉/MTP/DFlash2 权重），一键脚本下载，
断点续传 + SHA256 校验：

```bash
# 装 aria2 提速（可选但强烈建议：单线程 curl 对这些工件可能只有 ~1MB/s）
sudo apt install -y aria2

git clone --depth 1 https://github.com/Don-Chad/ninfer-3090.git
cd ninfer-3090

# 国内无 HuggingFace 直连：换镜像域名后再跑（二选一）
# sed -i 's|https://huggingface.co|https://hf-mirror.com|' scripts/download-model.sh

./download-model.sh qwen38-27b     # ~18GB，产物在 models/qwen3_8_27b.ninfer
```

【验证】脚本自校验大小+SHA256，成功时打印 OK。然后：
```bash
mkdir -p ~/models && cp models/qwen3_8_27b.ninfer ~/models/
echo "MODEL_PATH=$HOME/models/qwen3_8_27b.ninfer" >> config.env
systemctl --user edit ninfer-serve-full   # [Service] 段加 Environment=NINFER_MODEL=<该路径>
```
【排错】下载反复失败 → 检查代理/换镜像；磁盘不足 → 需要 ~20GB 空闲。

### 路线 2：从官方权重自行转换（需要源码树 + Python3.11/PyTorch + ~60GB 临时磁盘）

```bash
# 在打完本仓库 build/patches 的 Don-Chad/ninfer-3090 源码树根目录：
python3 -m tools.convert \
  --model /path/to/Qwen3.8-27B \
  --recipe qwen3_8_27b \
  --source imatrix=qwen3_8_27b.imatrix.safetensors \
  --source dflash2=/path/to/Qwen3.8-27B-DFlash2 \
  --components text,vision,mtp,dflash2 \
  --proposal \
  --name qwen3.8-27b \
  --out ~/models/qwen3_8_27b.ninfer
```
完整参数与原理见上游 `docs/weight-conversion.md`。可用 `--device cpu`（慢）或 CUDA。
【排错】缺 imatrix/dflash2 源文件 → 上游文档有各 recipe 源文件清单；中断 → 重跑同命令会复用中间产物。

### 社区分享件

若用户从可信渠道拿到转换好的 .ninfer，核对 SHA256 后放 MODEL_PATH 即可。

## 附录B：源码构建引擎（Release 不可用时的兜底）

```bash
git clone https://github.com/Don-Chad/ninfer-3090.git ninfer-src
cd ninfer-src && git checkout d8c2925
git apply <仓库>/build/patches/*.patch
cp <仓库>/build/build-full.sh .
bash build-full.sh            # Docker 构建，30–60 分钟
# 产物按 build/BUILD.md 归位（dist/ 与 rootfs/）
```
【前置】需 Docker 或 CUDA 12.8 工具链；详见 build/BUILD.md。

## 附录C：给用户的最终交付话术（部署完成后照此汇报）

> ✅ 部署完成。浏览器打开：<阶段7拿到的URL>，右下角选 Qwen3.8-27B 即可聊天。
> 桌面有"启动dsh"图标，以后双击即用；同事用这个文件里的地址：~/Desktop/dsh-web地址.txt。
> 开机自启已开启，重启后等 3 分钟即可。看服务状态：ninfer-ctl；实时监控：ninfer-ctl mon。

## 边界（不要越界）

- 不修改防火墙/全局代理/显卡超频等系统级设置，除非用户明确要求；
- 不在任何文件里写入真实密钥（token 一律现场生成）；
- 显存互斥：任何时刻只跑一台引擎；切换用 `ninfer-mode full|daily`（daily 未发布时报错属正常）；
- 模型权重与上游源码遵循各自许可，不协助绕过获取限制。
