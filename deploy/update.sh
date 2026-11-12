#!/usr/bin/env bash
# 只更新代码、不动数据的升级脚本（install.sh 的重跑会做同样的事，这个更轻）
#
# 用法（项目根目录）：
#   sudo bash deploy/update.sh
set -euo pipefail

APP_DIR=/opt/battery-tracker
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

[[ $EUID -eq 0 ]] || { echo "请用 root 执行：sudo bash deploy/update.sh"; exit 1; }
[[ -f "$SRC/server.py" ]] || { echo "找不到 server.py，请在项目根目录执行"; exit 1; }

echo "==> 先备份数据库"
BT_DB="$APP_DIR/data/battery.db" "$APP_DIR/deploy/backup.sh" || echo "（备份跳过，继续）"

echo "==> 同步代码（data/ 不动）"
rsync -a --delete \
  --exclude 'data/' --exclude '__pycache__/' --exclude '*.pyc' --exclude 'docs/icon-src/' \
  "$SRC"/ "$APP_DIR"/
chown -R battery:battery "$APP_DIR"
chmod 750 "$APP_DIR/data"
chmod 640 "$APP_DIR/pin.txt" 2>/dev/null || true

echo "==> 重启服务"
systemctl restart battery-tracker
sleep 2
systemctl --no-pager --lines=0 status battery-tracker | head -4

echo
echo "完成。手机/桌面上已安装的 App 下次打开会自动更新（Service Worker 会在后台换新版本）。"
echo "如果界面没变，把 static/sw.js 里的 VERSION 改一下再发布。"
