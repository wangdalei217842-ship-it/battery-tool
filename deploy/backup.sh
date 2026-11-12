#!/usr/bin/env bash
# 多用户版在线备份：账号库 + 每个用户各自的业务库
#
# 每次运行在 /var/backups/battery-tracker/db-<时间戳>/ 下生成一个完整快照：
#   accounts.db   账号（用户名、密码哈希、角色）
#   u1.db u2.db…  每个用户自己的业务数据
#
# 恢复某个用户：
#   systemctl stop battery-tracker
#   cp db-20261018-031000/u2.db /opt/battery-tracker/data/users/u2.db
#   chown battery:battery /opt/battery-tracker/data/users/u2.db
#   systemctl start battery-tracker
set -euo pipefail

APP_DIR="${BT_APP_DIR:-/opt/battery-tracker}"
DATA="$APP_DIR/data"
DEST="${BT_BACKUP_DIR:-/var/backups/battery-tracker}"
KEEP_DAYS="${BT_BACKUP_KEEP:-30}"
STAMP="$(date +%Y%m%d-%H%M%S)"
TARGET="$DEST/db-$STAMP"

mkdir -p "$TARGET"

# 用 SQLite 官方 backup API，业务正在写入时也能拿到一致快照
backup_one() {
  python3 - "$1" "$2" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1], timeout=30)
dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
dst.close()
src.close()
PY
}

COUNT=0

if [ -f "$DATA/accounts.db" ]; then
  backup_one "$DATA/accounts.db" "$TARGET/accounts.db"
  COUNT=$((COUNT + 1))
fi

if [ -d "$DATA/users" ]; then
  for f in "$DATA"/users/*.db; do
    [ -e "$f" ] || continue
    backup_one "$f" "$TARGET/$(basename "$f")"
    COUNT=$((COUNT + 1))
  done
fi

# 测试方法库（团队共享）与附件
if [ -f "$DATA/shared.db" ]; then
  backup_one "$DATA/shared.db" "$TARGET/shared.db"
  COUNT=$((COUNT + 1))
fi
if [ -d "$DATA/uploads" ] && [ -n "$(ls -A "$DATA/uploads" 2>/dev/null)" ]; then
  tar czf "$TARGET/uploads.tar.gz" -C "$DATA" uploads
  COUNT=$((COUNT + 1))
fi

# 兼容还没迁移的单库部署
if [ -f "$DATA/battery.db" ] && [ ! -d "$DATA/users" ]; then
  backup_one "$DATA/battery.db" "$TARGET/battery.db"
  COUNT=$((COUNT + 1))
fi

if [ "$COUNT" -eq 0 ]; then
  rmdir "$TARGET" 2>/dev/null || true
  echo "$(date '+%F %T')  没有找到可备份的数据库" >&2
  exit 1
fi

echo "$(date '+%F %T')  已备份 $COUNT 个库到 $TARGET（$(du -sh "$TARGET" | cut -f1)）"

# 清理过期快照
find "$DEST" -maxdepth 1 -type d -name 'db-*' -mtime "+$KEEP_DAYS" -print -exec rm -rf {} + 2>/dev/null || true
