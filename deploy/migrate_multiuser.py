# -*- coding: utf-8 -*-
"""把旧的单库 data/battery.db 迁移到多用户结构的「管理员」库里。

在服务器上执行（服务停止时）：
    cd /opt/battery-tracker && python3 deploy/migrate_multiuser.py

安全策略：
  - 旧库 data/battery.db 只读，不删除、不修改
  - 目标库（data/users/u1.db）已有数据时跳过，不会覆盖
  - 用 SQLite 官方 backup API，能正确处理 WAL
"""

import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import accounts  # noqa: E402
import store     # noqa: E402


def count_channels(path):
    try:
        c = sqlite3.connect(path)
        try:
            return c.execute("SELECT COUNT(*) FROM channels").fetchone()[0]
        finally:
            c.close()
    except Exception:
        return None


def main():
    accounts.init_db()
    created = accounts.ensure_admin()
    if created:
        print("已创建管理员：%s / %s" % created)

    admin = accounts.get_user_by_name("admin")
    if not admin:
        print("找不到 admin 账号，中止")
        return 1

    old = os.path.join(ROOT, "data", "battery.db")
    new = accounts.user_db(admin["id"])

    if not os.path.exists(old):
        print("没有旧库 %s，无需迁移" % old)
        return 0

    old_count = count_channels(old)
    print("旧库：%s（%s 个通道）" % (old, old_count))

    # 先把目标库的表结构建好
    store.use_db(new)
    store.init_db()

    new_count = count_channels(new) or 0
    print("目标库：%s（当前 %d 个通道）" % (new, new_count))

    if new_count > 0:
        print("目标库里已经有数据，跳过迁移（不会覆盖）")
        return 0

    if not old_count:
        print("旧库没有业务数据，跳过")
        return 0

    src = sqlite3.connect(old)
    dst = sqlite3.connect(new)
    try:
        with dst:
            src.backup(dst)
        # 迁移后重放一遍建表逻辑，补齐可能缺的列
        store.use_db(new)
        store.init_db()
    finally:
        dst.close()
        src.close()

    print("迁移完成：%s 个通道" % count_channels(new))
    print("旧库 %s 原样保留，可作备份" % old)
    return 0


if __name__ == "__main__":
    sys.exit(main())
