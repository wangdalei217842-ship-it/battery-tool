# -*- coding: utf-8 -*-
"""battery-tracker 命令行入口 —— 供 AI 助手在对话中直接代录数据

与网页共用同一个 SQLite 数据库，录完刷新网页即可看到。

用法示例：
  python cli.py status
  python cli.py channels
  python cli.py add-channel C01 --name 通道1
  python cli.py bulk-channel C01-C16
  python cli.py add-battery B001 --batch A --model 18650 --cap 2.5
  python cli.py swap C05 B001 --result 完成 --at "2026-10-18 09:20"
  python cli.py checkout C05 --result 异常
  python cli.py snap C05 --status 运行中 --set 电压=3.85 --set 容量=2.05 --note "第50循环"
  python cli.py timeline C05
  python cli.py profile B001
  python cli.py fields
"""

import argparse
import json
import sys

import store


def out(*args):
    print(*args)


def resolve_channel(conn, code):
    ch = store.channel_by_code(conn, code)
    if not ch:
        # 允许直接创建
        cid = store.create_channel(conn, code)
        conn.commit()
        ch = store.channel_by_id(conn, cid)
        out("（已自动创建通道 %s）" % code)
    return ch


def resolve_field(conn, token):
    return store.resolve_field(conn, token)


def cmd_status(conn):
    st = store.build_state(conn)
    s = st["stats"]
    out("通道 %d 个，其中 %d 个装有电池；电池 %d 颗；装机记录 %d 条；报送 %d 条"
        % (s["channels"], s["occupied"], s["batteries"], s["placements"], s["snapshots"]))
    out("")
    if not st["channels"]:
        out("（还没有通道，用 add-channel 或 bulk-channel 添加）")
        return
    out("%-8s %-12s %-10s %-8s %s" % ("通道", "电池", "状态", "已装", "最近报送"))
    out("-" * 68)
    for c in st["channels"]:
        cur = c["current"]
        last = c["last"]
        bat = cur["battery"]["code"] if cur else "（空）"
        status = (last or {}).get("status") or "—"
        from store import datetime as _dt  # noqa
        since = ""
        if cur:
            try:
                d = store.datetime.fromisoformat(cur["started_at"])
                since = "%.1f天" % ((store.datetime.now() - d).total_seconds() / 86400)
            except Exception:
                since = ""
        out("%-8s %-12s %-10s %-8s %s"
            % (c["code"], bat, status, since, (last or {}).get("taken_at", "—")))


def cmd_channels(conn):
    chs = store.list_channels(conn)
    if not chs:
        out("（无通道）")
        return
    for c in chs:
        p = store.open_placement(conn, c["id"])
        out("%-8s %-16s %s" % (c["code"], c["name"] or "", ("在装 " + store.battery_by_id(conn, p["battery_id"])["code"]) if p else "空"))


def cmd_add_channel(conn, code, name):
    cid = store.create_channel(conn, code, name or "")
    conn.commit()
    out("已添加通道 %s (id=%d)" % (code, cid))


def cmd_bulk(conn, spec):
    codes = store.expand_range(spec)
    created, skipped = [], []
    for code in codes:
        try:
            store.create_channel(conn, code)
            created.append(code)
        except ValueError:
            skipped.append(code)
    conn.commit()
    out("新增 %d 个：%s" % (len(created), ", ".join(created) or "无"))
    if skipped:
        out("跳过 %d 个已存在：%s" % (len(skipped), ", ".join(skipped)))


def cmd_batteries(conn, q):
    items = store.search_batteries(conn, q or "", 500)
    if not items:
        out("（无电池）")
        return
    for b in items:
        loc = ("在机 " + b["current"]["channel_code"]) if b["current"] else "空闲"
        out("%-12s 批次:%-10s 型号:%-10s %-10s 装机%d次"
            % (b["code"], b["batch"] or "-", b["model"] or "-", loc, b["placement_count"]))


def cmd_add_battery(conn, code, batch, model, cap, note):
    b, existed = store.ensure_battery(conn, code, batch or "", model or "", cap, note or "")
    conn.commit()
    out("%s电池 %s (id=%d)" % ("已存在，更新" if existed else "已创建", b["code"], b["id"]))


def cmd_swap(conn, channel, battery, batch, model, cap, result, note, at):
    ch = resolve_channel(conn, channel)
    r = store.swap_battery(conn, ch["id"], battery, batch=batch or "", model=model or "",
                           rated_capacity=cap, result=result or "", note=note or "",
                           started_at=at, source="cli")
    conn.commit()
    msg = "通道 %s 装入电池 %s" % (ch["code"], r["battery"]["code"])
    if r["battery_created"]:
        msg += "（新建档）"
    if r["previous_battery"]:
        msg += "；上一颗 %s 已下机" % r["previous_battery"]["code"]
    out(msg)


def cmd_checkout(conn, channel, result, note):
    ch = store.channel_by_code(conn, channel)
    if not ch:
        out("通道 %s 不存在" % channel)
        return 1
    r = store.checkout_channel(conn, ch["id"], result or "", note or "")
    conn.commit()
    out("通道 %s 上的电池 %s 已下机%s" % (ch["code"], r["battery"]["code"],
                                      ("（" + result + "）") if result else ""))
    return 0


def cmd_snap(conn, channel, status, note, at, sets):
    ch = resolve_channel(conn, channel)
    values = {}
    unknown = []
    for item in sets or []:
        if "=" not in item:
            out("跳过无法解析的字段：%s（格式应为 字段=值）" % item)
            continue
        k, v = item.split("=", 1)
        f = resolve_field(conn, k.strip())
        if not f:
            unknown.append(k.strip())
            continue
        values[f["key"]] = v.strip()
    if unknown:
        out("警告：找不到这些字段 %s，已忽略。可用字段见 fields 命令。" % ", ".join(unknown))
    if sets and not values:
        out("错误：没有一个字段能识别，未写入。请先运行 fields 查看可用字段。")
        return 1
    sid = store.add_snapshot(conn, ch["id"], values=values, status=status or "",
                             note=note or "", taken_at=at, source="cli")
    conn.commit()
    placed = ""
    p = store.open_placement(conn, ch["id"])
    if p:
        placed = "（电池 %s）" % store.battery_by_id(conn, p["battery_id"])["code"]
    out("已记录通道 %s 的状态%s，记录号 %d" % (ch["code"], placed, sid["id"]))
    if sid["skipped"]:
        out("警告：以下字段不存在，已忽略：%s" % ", ".join(sid["skipped"]))
    out("  值：" + (", ".join("%s=%s" % (k, v) for k, v in values.items()) or "（无）"))
    return 0


def cmd_timeline(conn, channel, limit):
    ch = store.channel_by_code(conn, channel)
    if not ch:
        out("通道 %s 不存在" % channel)
        return 1
    d = store.channel_timeline(conn, ch["id"], limit)
    for e in d["events"]:
        if e["type"] == "placement":
            out("[装机] %s -> %s  电池 %s  %s %s" % (
                e["at"], e["end"] or "至今", e["battery_code"], e["result"] or "", e["note"] or ""))
        else:
            vals = ", ".join("%s=%s%s" % (v["label"], v["value"], v["unit"] or "") for v in e["values"])
            out("[报送] %s  %s  %s  %s" % (e["at"], e["status"] or "-", vals,
                                          ("备注:" + e["note"]) if e["note"] else ""))
    return 0


def cmd_profile(conn, code):
    b = store.battery_by_code(conn, code)
    if not b:
        out("电池 %s 不存在" % code)
        return 1
    d = store.battery_profile(conn, b["id"])
    out("电池 %s  批次:%s  型号:%s  累计在机 %.1f 小时  去过通道:%s" % (
        d["code"], d["batch"] or "-", d["model"] or "-", d["total_hours"],
        ", ".join(d["channels_used"]) or "无"))
    for p in d["placements"]:
        out("  [装机] %s -> %s  通道 %s  %s" % (p["started_at"], p["ended_at"] or "至今",
                                             p["channel_code"], p["result"] or ""))
    for s in d["snapshots"][:20]:
        vals = ", ".join("%s=%s" % (v["label"], v["value"]) for v in s["values"])
        out("  [报送] %s  %s  %s" % (s["taken_at"], s["status"] or "-", vals))
    return 0


def cmd_fields(conn):
    for f in store.list_fields(conn):
        out("%-10s %-12s 单位:%-6s 类型:%-8s %s" % (
            f["key"], f["label"], f["unit"] or "-", f["kind"], "" if f["active"] else "（已停用）"))


def cmd_groups(conn):
    gs = store.list_groups(conn, with_counts=True)
    if not gs:
        out("（还没有分组）")
    for g in gs:
        out("%-14s %d 个通道" % (g["name"], g["count"]))
    loose = conn.execute("SELECT COUNT(*) c FROM channels WHERE group_id IS NULL AND archived=0").fetchone()["c"]
    if loose:
        out("%-14s %d 个通道" % ("（未分组）", loose))


def cmd_add_group(conn, name):
    gid = store.create_group(conn, name)
    conn.commit()
    out("已创建分组 %s (id=%d)" % (name.strip(), gid))


def cmd_set_group(conn, channel, group):
    ch = store.channel_by_code(conn, channel)
    if not ch:
        out("通道 %s 不存在" % channel)
        return 1
    if group in ("-", "", "未分组", "none"):
        conn.execute("UPDATE channels SET group_id=NULL WHERE id=?", (ch["id"],))
        conn.commit()
        out("通道 %s 已移到未分组" % channel)
        return 0
    row = conn.execute("SELECT id FROM groups WHERE name=?", (group.strip(),)).fetchone()
    if not row:
        out("分组 %s 不存在，可用 groups 查看" % group)
        return 1
    conn.execute("UPDATE channels SET group_id=? WHERE id=?", (row["id"], ch["id"]))
    conn.commit()
    out("通道 %s 已归入分组 %s" % (channel, group.strip()))
    return 0


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    store.init_db()

    ap = argparse.ArgumentParser(description="battery技术 命令行")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("status")
    sub.add_parser("fields")
    sub.add_parser("channels")
    sub.add_parser("groups")

    p = sub.add_parser("add-group")
    p.add_argument("name")

    p = sub.add_parser("set-group")
    p.add_argument("channel")
    p.add_argument("group")

    p = sub.add_parser("add-channel")
    p.add_argument("code")
    p.add_argument("--name", default="")

    p = sub.add_parser("bulk-channel")
    p.add_argument("spec")

    p = sub.add_parser("batteries")
    p.add_argument("q", nargs="?", default="")

    p = sub.add_parser("add-battery")
    p.add_argument("code")
    p.add_argument("--batch", default="")
    p.add_argument("--model", default="")
    p.add_argument("--cap", type=float, default=None)
    p.add_argument("--note", default="")

    p = sub.add_parser("swap")
    p.add_argument("channel")
    p.add_argument("battery")
    p.add_argument("--batch", default="")
    p.add_argument("--model", default="")
    p.add_argument("--cap", type=float, default=None)
    p.add_argument("--result", default="")
    p.add_argument("--note", default="")
    p.add_argument("--at", default=None, help="上机时间，如 2026-10-18 09:20")

    p = sub.add_parser("checkout")
    p.add_argument("channel")
    p.add_argument("--result", default="")
    p.add_argument("--note", default="")

    p = sub.add_parser("snap")
    p.add_argument("channel")
    p.add_argument("--status", default="")
    p.add_argument("--note", default="")
    p.add_argument("--at", default=None, help="记录时间，如 2026-10-18 13:05")
    p.add_argument("--set", action="append", default=[], metavar="字段=值")

    p = sub.add_parser("timeline")
    p.add_argument("channel")
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("profile")
    p.add_argument("code")

    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 0

    conn = store.connect()
    try:
        if args.cmd == "status":
            cmd_status(conn)
        elif args.cmd == "fields":
            cmd_fields(conn)
        elif args.cmd == "channels":
            cmd_channels(conn)
        elif args.cmd == "groups":
            cmd_groups(conn)
        elif args.cmd == "add-group":
            cmd_add_group(conn, args.name)
        elif args.cmd == "set-group":
            return cmd_set_group(conn, args.channel, args.group)
        elif args.cmd == "add-channel":
            cmd_add_channel(conn, args.code, args.name)
        elif args.cmd == "bulk-channel":
            cmd_bulk(conn, args.spec)
        elif args.cmd == "batteries":
            cmd_batteries(conn, args.q)
        elif args.cmd == "add-battery":
            cmd_add_battery(conn, args.code, args.batch, args.model, args.cap, args.note)
        elif args.cmd == "swap":
            cmd_swap(conn, args.channel, args.battery, args.batch, args.model, args.cap,
                     args.result, args.note, args.at)
        elif args.cmd == "checkout":
            return cmd_checkout(conn, args.channel, args.result, args.note)
        elif args.cmd == "snap":
            return cmd_snap(conn, args.channel, args.status, args.note, args.at, args.set)
        elif args.cmd == "timeline":
            return cmd_timeline(conn, args.channel, args.limit)
        elif args.cmd == "profile":
            return cmd_profile(conn, args.code)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
