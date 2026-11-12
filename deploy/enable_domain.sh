#!/usr/bin/env bash
# 把电池台账从「公网 IP + HTTP」切成「域名 + HTTPS」
# 用法：
#   sudo bash deploy/enable_domain.sh 域名 邮箱 [DNSPod_ID DNSPod_Token]
#
# 例（有 DNSPod API Token，走 DNS-01，不用等备案、不依赖 80 端口）：
#   sudo bash deploy/enable_domain.sh <YOUR_DOMAIN> me@qq.com 123456 abcdef1234567890abcdef1234567890
#
# 例（域名已备案，80 端口可用）：
#   sudo bash deploy/enable_domain.sh <YOUR_DOMAIN> me@qq.com
set -euo pipefail

DOMAIN="${1:-}"
EMAIL="${2:-}"
DP_ID="${3:-}"
DP_KEY="${4:-}"
APP_DIR="${APP_DIR:-/opt/battery-tracker}"
CERT_DIR="/etc/letsencrypt/live/$DOMAIN"
SITE=/etc/nginx/sites-available/battery-tracker

red()  { printf "\033[31m%s\033[0m\n" "$*"; }
grn()  { printf "\033[32m%s\033[0m\n" "$*"; }
blu()  { printf "\033[36m%s\033[0m\n" "$*"; }

if [ -z "$DOMAIN" ] || [ -z "$EMAIL" ]; then
  red "用法: sudo bash deploy/enable_domain.sh 域名 邮箱 [DNSPod_ID DNSPod_Token]"
  exit 1
fi

# ---------- 1. 预检 ----------
blu "① 检查域名解析"
IP_NOW=$(getent ahostsv4 "$DOMAIN" | awk 'NR==1{print $1}')
IP_PUB=$(curl -s --max-time 8 https://api.ipify.org || echo "")
echo "   域名解析到： ${IP_NOW:-（没有记录）}"
echo "   本机公网 IP：${IP_PUB:-取不到}"
if [ -z "$IP_NOW" ]; then
  red "   域名还没有 A 记录。先去域名后台加一条：$DOMAIN → A → $IP_PUB，等 1~10 分钟再跑。"
  exit 1
fi

blu "② 装依赖"
apt-get update -qq
apt-get install -y -qq nginx curl ca-certificates socat cron >/dev/null
mkdir -p /var/www/certbot

# ---------- 2. 先立一个只服务证书验证的 80 站点 ----------
blu "③ 准备证书验证通道（80 只放 ACME 目录）"
cat > "$SITE" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 301 https://\$host\$request_uri; }
}
EOF
ln -sf "$SITE" /etc/nginx/sites-enabled/battery-tracker
nginx -t
systemctl reload nginx

# ---------- 3. 证书 ----------
if [ -f "$CERT_DIR/fullchain.pem" ]; then
  grn "④ 证书已存在，跳过签发"
else
  blu "④ 申请证书"
  if [ -n "$DP_ID" ] && [ -n "$DP_KEY" ]; then
    echo "   方式：DNS-01（DNSPod），不依赖 80 端口，未备案也能签"
    if [ ! -d "$HOME/.acme.sh" ]; then
      curl -s https://get.acme.sh | sh -s email="$EMAIL" >/dev/null
    fi
    export DP_Id="$DP_ID"
    export DP_Key="$DP_KEY"
    "$HOME/.acme.sh/acme.sh" --set-default-ca --server letsencrypt >/dev/null
    "$HOME/.acme.sh/acme.sh" --issue --dns dns_dp -d "$DOMAIN" --keylength ec-256 --force
    "$HOME/.acme.sh/acme.sh" --install-cert -d "$DOMAIN" --ecc \
      --key-file       "$CERT_DIR/privkey.pem" \
      --fullchain-file "$CERT_DIR/fullchain.pem" \
      --reloadcmd      "systemctl reload nginx"
  else
    echo "   方式：HTTP-01 webroot（要求 80 端口可达 + 域名已备案）"
    apt-get install -y -qq certbot >/dev/null
    certbot certonly --webroot -w /var/www/certbot -d "$DOMAIN" \
      --agree-tos -m "$EMAIL" --non-interactive --keep-until-expiring
  fi
fi

if [ ! -f "$CERT_DIR/fullchain.pem" ]; then
  red "证书没签下来，先别继续。看一下上面的报错（最常见：解析没生效 / 安全组没放行 443 或 80）。"
  exit 1
fi
grn "   证书就绪：$CERT_DIR/fullchain.pem"

# ---------- 4. Nginx 站点 ----------
blu "⑤ 写 Nginx 配置（80 跳转 + 443 反代）"
sed -e "s#__DOMAIN__#$DOMAIN#g" -e "s#__CERT_DIR__#$CERT_DIR#g" \
    "$APP_DIR/deploy/nginx-ssl.conf.template" > "$SITE"
ln -sf "$SITE" /etc/nginx/sites-enabled/battery-tracker
[ -e /etc/nginx/sites-enabled/default ] && rm -f /etc/nginx/sites-enabled/default || true

nginx -t
systemctl enable nginx >/dev/null 2>&1 || true
systemctl reload nginx
grn "   Nginx 已重载"

# ---------- 5. 应用只监听本机 ----------
blu "⑥ 让程序只监听 127.0.0.1，并给 Cookie 加 Secure"
install -d /etc/systemd/system/battery-tracker.service.d
cat > /etc/systemd/system/battery-tracker.service.d/domain.conf <<'EOF'
[Service]
Environment=BT_HOST=127.0.0.1
Environment=BT_SECURE=1
EOF
systemctl daemon-reload
systemctl restart battery-tracker
sleep 2

# ---------- 6. 自检 ----------
blu "⑦ 自检"
if curl -fsS --max-time 8 "http://127.0.0.1:8787/api/ping" >/dev/null; then
  grn "   后端 127.0.0.1:8787 正常"
else
  red "   后端没起来：journalctl -u battery-tracker -n 50"
fi
CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "https://$DOMAIN/api/ping" || echo 000)
echo "   https://$DOMAIN/api/ping → $CODE"
if [ "$CODE" = "200" ]; then
  grn "   外网 HTTPS 通了"
else
  red "   外网没通：先确认腾讯云安全组放行了 443（未备案域名走 443 仍可能被拦）"
fi

cat <<EOF

==================== 下一步（手动做两件事） ====================
1) 腾讯云控制台 → 防火墙/安全组：
   - 放行 TCP 443
   - 删掉 8787 的入站规则   ← 这一步做完，公网 IP 就再也连不上台账了
2) 手机浏览器打开 https://$DOMAIN ，登录后「添加到主屏幕」

想回滚（回到 IP:8787）：
   rm /etc/systemd/system/battery-tracker.service.d/domain.conf
   systemctl daemon-reload && systemctl restart battery-tracker
================================================================
EOF
