#!/usr/bin/env bash
# 电池通道监管台账 — Ubuntu 一键部署
#
# 用法（在项目根目录）：
#   sudo bash deploy/install.sh 你的域名 你的邮箱
# 例：
#   sudo bash deploy/install.sh battery.example.com me@example.com
#
# 做的事：装依赖 → 建专用用户 → 部署文件 → 配 systemd → 配 Nginx → 申请 HTTPS 证书 → 加每日备份
# 可重复执行：升级代码时再跑一次即可，数据库不会被覆盖。

set -euo pipefail

APP_DIR=/opt/battery-tracker
APP_USER=battery
APP_PORT=8787
DOMAIN="${1:-}"
EMAIL="${2:-}"

c_red()   { printf "\033[31m%s\033[0m\n" "$*"; }
c_green() { printf "\033[32m%s\033[0m\n" "$*"; }
c_blue()  { printf "\n\033[36m==> %s\033[0m\n" "$*"; }
c_dim()   { printf "\033[2m%s\033[0m\n" "$*"; }

# ---------------------------------------------------------------- 前置检查
[[ $EUID -eq 0 ]] || { c_red "请用 root 执行：sudo bash deploy/install.sh 你的域名 你的邮箱"; exit 1; }
if [[ -z "$DOMAIN" ]]; then
  c_red "缺少域名参数。"
  c_dim "例：sudo bash deploy/install.sh battery.example.com me@example.com"
  exit 1
fi
if ! [[ "$DOMAIN" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]]; then
  c_red "域名格式不对：$DOMAIN（只填域名，不要带 http:// 或路径）"
  exit 1
fi

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ -f "$SRC/server.py" ]] || { c_red "找不到 server.py。请在项目根目录执行：sudo bash deploy/install.sh 域名 邮箱"; exit 1; }

if ! command -v apt-get >/dev/null 2>&1; then
  c_red "这个脚本是为 Ubuntu / Debian 写的。当前系统没有 apt-get。"
  exit 1
fi

c_green "域名：$DOMAIN"
c_green "源目录：$SRC"
c_green "部署到：$APP_DIR"

# ---------------------------------------------------------------- 时区
c_blue "设置时区为 Asia/Shanghai（否则报送时间会差 8 小时）"
timedatectl set-timezone Asia/Shanghai 2>/dev/null || true

# ---------------------------------------------------------------- 依赖
c_blue "安装依赖（python3 / nginx / certbot / rsync）"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 nginx rsync certbot python3-certbot-nginx ca-certificates >/dev/null
c_green "依赖就绪：$(python3 -V)"

# ---------------------------------------------------------------- 用户
c_blue "创建运行用户 $APP_USER（不给登录权限）"
if ! id -u "$APP_USER" >/dev/null 2>&1; then
  useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
  c_green "已创建用户 $APP_USER"
else
  c_dim "用户 $APP_USER 已存在，跳过"
fi

# ---------------------------------------------------------------- 部署文件
c_blue "部署程序文件"
mkdir -p "$APP_DIR/data"
# --exclude data/ ：数据库不受 --delete 影响，升级时不会丢数据
rsync -a --delete \
  --exclude 'data/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude 'docs/icon-src/' \
  "$SRC"/ "$APP_DIR"/

# 首次部署才把本地数据库带上来
if [[ ! -f "$APP_DIR/data/battery.db" ]]; then
  if [[ -f "$SRC/data/battery.db" ]]; then
    cp "$SRC/data/battery.db" "$APP_DIR/data/battery.db"
    c_green "已导入现有数据库（含你的历史数据）"
  else
    c_dim "本地没有 data/battery.db，首次启动会自动建空库"
  fi
else
  c_dim "服务器上已有数据库，保留不动"
fi

# ---------------------------------------------------------------- 账号
# 多用户版：首次启动会自动创建管理员账号，密码写进 data/admin-password.txt
c_dim "账号体系：首次启动自动创建管理员 admin，密码见 data/admin-password.txt"

# ---------------------------------------------------------------- 权限
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 750 "$APP_DIR/data"
chmod +x "$APP_DIR"/deploy/*.sh 2>/dev/null || true

# ---------------------------------------------------------------- systemd
c_blue "配置开机自启服务"
install -m 644 "$APP_DIR/deploy/battery-tracker.service" /etc/systemd/system/battery-tracker.service
systemctl daemon-reload
systemctl enable battery-tracker >/dev/null 2>&1 || true
systemctl restart battery-tracker
sleep 2
if ! systemctl is-active --quiet battery-tracker; then
  c_red "服务启动失败，最近日志："
  journalctl -u battery-tracker -n 25 --no-pager || true
  exit 1
fi
c_green "服务已启动"

# 本机自检
if curl -fsS --noproxy '*' "http://127.0.0.1:$APP_PORT/api/ping" >/dev/null 2>&1; then
  c_green "程序在本机 $APP_PORT 端口响应正常"
else
  c_red "程序没有响应，请看：journalctl -u battery-tracker -n 50 --no-pager"
  exit 1
fi

# ---------------------------------------------------------------- Nginx
c_blue "配置 Nginx 反向代理"
sed "s/__DOMAIN__/$DOMAIN/g" "$APP_DIR/deploy/nginx.conf.template" \
  > /etc/nginx/sites-available/battery-tracker
ln -sf /etc/nginx/sites-available/battery-tracker /etc/nginx/sites-enabled/battery-tracker
rm -f /etc/nginx/sites-enabled/default

if nginx -t 2>/tmp/nginx-test.log; then
  systemctl enable nginx >/dev/null 2>&1 || true
  systemctl reload nginx 2>/dev/null || systemctl restart nginx
  c_green "Nginx 已生效"
else
  c_red "Nginx 配置有误："
  cat /tmp/nginx-test.log
  exit 1
fi

# ---------------------------------------------------------------- 防火墙
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  c_blue "防火墙已启用，放行 80/443（保留 SSH 不动）"
  ufw allow 80/tcp  >/dev/null 2>&1 || true
  ufw allow 443/tcp >/dev/null 2>&1 || true
fi

# ---------------------------------------------------------------- HTTPS
c_blue "申请 HTTPS 证书（Let's Encrypt 免费证书）"
c_dim "前提：域名已解析到这台服务器，且腾讯云安全组已放行 80/443"
CERT_OK=0
CERTBOT_ARGS=(--nginx -d "$DOMAIN" --agree-tos --redirect --keep-until-expiring)
if [[ -n "$EMAIL" ]]; then
  CERTBOT_ARGS+=(--email "$EMAIL")
else
  CERTBOT_ARGS+=(--register-unsafely-without-email)
fi
if certbot "${CERTBOT_ARGS[@]}" 2>&1 | tail -12; then
  CERT_OK=1
fi

# ---------------------------------------------------------------- 备份
c_blue "配置每日自动备份"
install -d -m 750 /var/backups/battery-tracker
cat > /etc/cron.d/battery-tracker-backup <<EOF
# 每天 03:10 备份数据库，保留 30 天
10 3 * * * root $APP_DIR/deploy/backup.sh >/var/log/battery-tracker-backup.log 2>&1
EOF
chmod 644 /etc/cron.d/battery-tracker-backup
c_green "已配置：每天 03:10 备份到 /var/backups/battery-tracker（保留 30 天）"

# ---------------------------------------------------------------- 完成
ADMIN_PW="$(sed -n 's/^password: //p' "$APP_DIR/data/admin-password.txt" 2>/dev/null || echo '')"
printf "\n"
c_green "==================== 部署完成 ===================="
if [[ $CERT_OK -eq 1 ]]; then
  printf "  访问网址：  https://%s\n" "$DOMAIN"
else
  printf "  访问网址（证书未成功，先走 HTTP）：http://%s\n" "$DOMAIN"
  printf "  证书没申请下来，通常是：\n"
  printf "   1) 域名还没解析到本机 IP  →  dig +short %s\n" "$DOMAIN"
  printf "   2) 腾讯云安全组没放行 80/443 → 控制台「防火墙」里加规则\n"
  printf "   3) 域名未备案（国内主机必须备案，否则 80/443 会被拦）\n"
  printf "  修好后补跑证书：sudo certbot --nginx -d %s --redirect\n" "$DOMAIN"
fi
if [[ -n "$ADMIN_PW" ]]; then
  printf "  管理员账号：admin / %s   （登录后请在右上角头像菜单里改密码）\n" "$ADMIN_PW"
else
  printf "  管理员账号：见 %s/data/admin-password.txt\n" "$APP_DIR"
fi
printf "  用户数据：  %s/data/users/（一人一个 .db 文件）\n" "$APP_DIR"
printf "  账号库：    %s/data/accounts.db\n" "$APP_DIR"
printf "  查看日志：  journalctl -u battery-tracker -f\n"
printf "  重启服务：  systemctl restart battery-tracker\n"
printf "  升级代码：  重新上传后 sudo bash deploy/install.sh %s\n" "$DOMAIN"
c_green "=================================================="
printf "\n"
