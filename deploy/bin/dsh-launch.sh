#!/usr/bin/env bash
# dsh-launch.sh — 小白一键启动器：等服务就绪 → 取最新 token → 打开浏览器
# 双击桌面"启动dsh"图标即可。同事局域网使用：把桌面上的 dsh-web地址.txt 发给他。
set -uo pipefail
IP="${DSH_LAN_IP:-192.168.1.100}"
PORT="${DSH_WEB_PORT:-13080}"

echo "正在检查服务状态…"
# 等引擎（最多 5 分钟，开机后首次较慢）
for i in $(seq 1 60); do
    systemctl --user is-active --quiet ninfer-serve-full && break
    sleep 5
done
if ! systemctl --user is-active --quiet ninfer-serve-full; then
    echo "⚠ 引擎还没起来，尝试自动启动…"
    systemctl --user start ninfer-serve-full
    sleep 20
fi
# 等 web
for i in $(seq 1 20); do
    systemctl --user is-active --quiet dsh-web && break
    sleep 3
done

# 取最新 token（每次重启都会变，所以每次双击都现取）
TOKEN=$(journalctl --user -u dsh-web -n 10 --no-pager -o cat 2>/dev/null | grep -oE "token=[A-Za-z0-9_\-]+" | tail -1 | cut -d= -f2)
if [[ -z "$TOKEN" ]]; then
    echo "⚠ 没取到登录口令，5 秒后重试一次…"; sleep 5
    TOKEN=$(journalctl --user -u dsh-web -n 10 --no-pager -o cat 2>/dev/null | grep -oE "token=[A-Za-z0-9_\-]+" | tail -1 | cut -d= -f2)
fi
URL="http://$IP:$PORT/?token=$TOKEN"

# 把当前地址写进桌面文件（同事照这个地址在自己电脑上打开即可）
mkdir -p ~/Desktop
cat > ~/Desktop/dsh-web地址.txt << EOF
本机使用：双击桌面"启动dsh"图标

同事在局域网其它电脑使用（浏览器直接打开）：
$URL

注意：服务器每次重启后口令都会变，用之前先看这个文件里的最新地址。
生成时间：$(date '+%Y-%m-%d %H:%M:%S')
EOF

echo "✔ 一切就绪，正在打开浏览器…"
xdg-open "$URL" >/dev/null 2>&1 &
echo "地址已同步到桌面 dsh-web地址.txt"
