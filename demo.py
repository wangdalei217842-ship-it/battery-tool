# -*- coding: utf-8 -*-
"""示例数据：python demo.py seed / clear

用来直观体验界面。真实使用前，可在网页「设置 → 危险操作 → 清空全部数据」清掉。
配合新的字段体系：循环圈数(圈) + 比容量(mAh/g)。
"""

import random
import sys
from datetime import datetime, timedelta

import store

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

NOW = datetime.now()


def clear(conn):
    for t in ("snapshot_values", "snapshots", "placements", "batteries", "channels"):
        conn.execute("DELETE FROM %s" % t)
    try:
        conn.execute(
            "DELETE FROM sqlite_sequence WHERE name IN"
            " ('snapshot_values','snapshots','placements','batteries','channels')"
        )
    except Exception:
        pass
    conn.commit()


def seed():
    conn = store.connect()
    clear(conn)
    random.seed(20261018)

    channels = []
    specs = [("1-1", "柜A-1"), ("1-2", "柜A-2"), ("1-3", "柜A-3"), ("1-4", "柜A-4"),
             ("2-1", "柜B-1"), ("2-2", "柜B-2"), ("2-3", "柜B-3"), ("3-1", "")]
    for code, name in specs:
        cid = store.create_channel(conn, code, name)
        channels.append(store.channel_by_id(conn, cid))

    # 两个分组：C01–C04 在「柜A」，C05–C07 在「柜B」，C08 故意留成未分组
    ga = store.create_group(conn, "柜A")
    gb = store.create_group(conn, "柜B")
    for idx, ch in enumerate(channels[:7]):
        gid = ga if idx < 4 else gb
        conn.execute("UPDATE channels SET group_id=?, sort_order=? WHERE id=?",
                     (gid, (idx % 4 + 1) * 10, ch["id"]))
    conn.commit()

    for i in range(1, 7):
        store.ensure_battery(conn, "A-%03d" % i, batch="2026A", model="扣电-2026A",
                             note="来料检合格")
    for i in range(1, 4):
        store.ensure_battery(conn, "B-%03d" % i, batch="2026B", model="扣电-2026B")
    store.ensure_battery(conn, "A-000", batch="2026A", model="扣电-2026A",
                         note="首轮验证，已被替换")
    conn.commit()

    plan = [
        # channel, battery, 上机天数前, 初始比容量, 循环起始
        (channels[0], "A-001", 12, 158.0, 60),
        (channels[1], "A-002", 11, 155.5, 80),
        (channels[2], "A-003", 10, 160.2, 40),
        (channels[3], "A-004", 9, 152.8, 90),
        (channels[4], "A-005", 8, 161.4, 20),
        (channels[5], "A-006", 7, 156.7, 70),
        (channels[6], "B-001", 6, 148.3, 30),
    ]

    # A-000 先在 C01 上跑了一段，后被换掉
    ch0 = channels[0]
    start0 = NOW - timedelta(days=16)
    store.swap_battery(conn, ch0["id"], "A-000",
                       started_at=start0.strftime("%Y-%m-%dT%H:%M:%S"), source="web")
    for k in range(4):
        t = start0 + timedelta(days=k * 1.2)
        store.add_snapshot(conn, ch0["id"], values={
            "循环圈数": 10 + k * 50,
            "比容量": round(159.5 - k * 1.6 - random.uniform(0, 0.8), 2),
        }, status="运行中", taken_at=t.strftime("%Y-%m-%dT%H:%M:%S"), source="web")
    store.checkout_channel(conn, ch0["id"], result="完成", note="比容量衰减至 80%，下机复检",
                           ended_at=(NOW - timedelta(days=12)).strftime("%Y-%m-%dT%H:%M:%S"))
    conn.commit()

    for idx, (ch, code, days, cap0, cyc0) in enumerate(plan):
        start = NOW - timedelta(days=days)
        store.swap_battery(conn, ch["id"], code,
                           started_at=start.strftime("%Y-%m-%dT%H:%M:%S"), source="web")
        steps = 5 + idx % 3
        interval = (NOW - start) / steps
        for k in range(steps + 1):
            t = start + interval * k
            if t > NOW:
                break
            cap = cap0 - k * (0.75 + idx * 0.05) - random.uniform(0, 0.35)
            status = "运行中"
            if idx == 1 and k == steps:          # C02 最后一笔为异常
                status = "异常"
            elif k == 2 and idx % 3 == 0:
                status = "静置"
            store.add_snapshot(conn, ch["id"], values={
                "循环圈数": cyc0 + k * 50,
                "比容量": round(cap, 2),
            }, status=status, taken_at=t.strftime("%Y-%m-%dT%H:%M:%S"),
               note=("比容量衰减加快，已记录" if idx == 1 and k == steps else ""),
               source=("cli" if (idx == 4 and k == steps) else "web"))
    conn.commit()

    st = store.build_state(conn)["stats"]
    print("示例数据已生成：")
    print("  通道 %d 个（1-1~1-4 在柜A，2-x 在柜B，3-1 未分组）" % st["channels"])
    print("  电池 %d 颗（批次 2026A / 2026B）" % st["batteries"])
    print("  装机记录 %d 条 / 报送 %d 条" % (st["placements"], st["snapshots"]))
    print("  打开网页即可看到效果。真实使用前请在「设置」里清空。")
    conn.close()


def main():
    store.init_db()
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "seed").lower()
    if cmd == "clear":
        conn = store.connect()
        clear(conn)
        conn.close()
        print("已清空全部业务数据（字段与设置保留）。")
    else:
        seed()


if __name__ == "__main__":
    main()
