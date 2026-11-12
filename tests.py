# -*- coding: utf-8 -*-
"""充放电测试数据（新威 ndax 解析结果）的存储与查询。

数据写在**用户自己的库**里（跟通道台账同一个 db），因为测试数据属于个人实验记录。
曲线按圈降采样后入库，10 万级采样点也不会拖慢页面。
"""

import json
import os
from datetime import datetime

import ndax

MAX_POINTS_PER_CYCLE = 120   # 每圈保留的曲线点数
MAX_CURVE_CYCLES = 400       # 最多存多少圈的曲线


SCHEMA = """
CREATE TABLE IF NOT EXISTS tests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_code TEXT DEFAULT '',
    channel_id INTEGER,
    filename TEXT DEFAULT '',
    ndc_version INTEGER DEFAULT 0,
    device TEXT DEFAULT '',
    unit TEXT DEFAULT '',
    ch TEXT DEFAULT '',
    nominal_mAh REAL DEFAULT 0,
    mass_mg REAL DEFAULT 0,
    setpoints TEXT DEFAULT '',
    start_at TEXT DEFAULT '',
    end_at TEXT DEFAULT '',
    rows INTEGER DEFAULT 0,
    cycles INTEGER DEFAULT 0,
    vmin REAL DEFAULT 0,
    vmax REAL DEFAULT 0,
    imin REAL DEFAULT 0,
    imax REAL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS test_cycles (
    test_id INTEGER NOT NULL,
    cycle INTEGER NOT NULL,
    charge_mAh REAL DEFAULT 0,
    discharge_mAh REAL DEFAULT 0,
    charge_mWh REAL DEFAULT 0,
    discharge_mWh REAL DEFAULT 0,
    ce_pct REAL,
    retention_pct REAL
);
CREATE INDEX IF NOT EXISTS ix_test_cycles ON test_cycles(test_id, cycle);
CREATE TABLE IF NOT EXISTS test_points (
    test_id INTEGER NOT NULL,
    cycle INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    q REAL,
    v REAL,
    cur REAL
);
CREATE INDEX IF NOT EXISTS ix_test_points ON test_points(test_id, cycle, seq);
"""


def ensure_schema(conn):
    """只建一次表：每次请求都 executescript 一遍会白白多花几十毫秒。"""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tests'").fetchone()
    if not row:
        conn.executescript(SCHEMA)
        conn.commit()
        return
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(tests)")}
    if "mass_mg" not in cols:
        conn.execute("ALTER TABLE tests ADD COLUMN mass_mg REAL DEFAULT 0")
        conn.commit()


def set_mass(conn, tid, mass_mg):
    ensure_schema(conn)
    conn.execute("UPDATE tests SET mass_mg=? WHERE id=?", (float(mass_mg or 0), tid))
    conn.commit()


def get_dqdv(conn, tid, cycle=None):
    """dQ/dV 曲线：对相邻点做差分，按电流正负拆成充电/放电两支。"""
    curve = get_curve(conn, tid, cycle)
    pts = curve["points"]
    if not pts:
        return {"cycle": curve["cycle"], "charge": [], "discharge": []}

    def branch(seq):
        out = []
        prev = None
        for p in seq:
            if prev is not None:
                dv = p["v"] - prev["v"]
                dq = p["q"] - prev["q"]
                # 电压变化太小时数值会被噪声放大，直接跳过
                if abs(dv) >= 0.002:
                    out.append([round(p["v"], 4), round(dq / dv, 4)])
            prev = p
        return out

    charge = [p for p in pts if (p["cur"] or 0) > 0]
    discharge = [p for p in pts if (p["cur"] or 0) < 0]
    return {"cycle": curve["cycle"],
            "charge": _thin(branch(charge), 600),
            "discharge": _thin(branch(discharge), 600)}


def _thin(points, limit):
    if len(points) <= limit:
        return points
    step = len(points) / float(limit)
    return [points[int(i * step)] for i in range(limit)]


def now_str():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def channel_code_of(meta):
    parts = [str(meta.get("device") or ""), str(meta.get("unit") or ""), str(meta.get("channel") or "")]
    parts = [p for p in parts if p]
    return "-".join(parts)


def _downsample(items, limit):
    n = len(items)
    if n <= limit:
        return list(range(n))
    step = (n - 1) / float(limit - 1)
    return [int(round(i * step)) for i in range(limit)]


def match_channel(conn, meta):
    """按编号猜通道。

    新威文件里是 device-unit-channel（如 50-8-7），而台账里的通道编号
    可能只写 unit-channel（8-7）或纯通道号（7），所以依次尝试几种组合。
    """
    parts = [str(meta.get(k) or "") for k in ("device", "unit", "channel")]
    parts = [p for p in parts if p]
    cands = []
    if parts:
        cands.append("-".join(parts))
        if len(parts) >= 2:
            cands.append("-".join(parts[-2:]))
        cands.append(parts[-1])
    seen = set()
    for c in cands:
        if not c or c in seen:
            continue
        seen.add(c)
        hit = conn.execute("SELECT id FROM channels WHERE code=? ORDER BY id LIMIT 1", (c,)).fetchone()
        if hit:
            return hit["id"], c
    return None, ""


def save_test(conn, meta, rows, filename):
    """把解析结果落库，返回 (test_id, 概要)。"""
    ensure_schema(conn)
    code = channel_code_of(meta)
    ts = now_str()

    # 先算循环汇总，用它作为循环数（ndax 的 meta 里没有这个字段）
    summary = ndax.summarize_cycles(rows)

    cur = conn.execute(
        "INSERT INTO tests(channel_code, filename, ndc_version, device, unit, ch, nominal_mAh,"
        " setpoints, start_at, end_at, rows, cycles, vmin, vmax, imin, imax, created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (code, filename, int(meta.get("ndc_version") or 0),
         str(meta.get("device") or ""), str(meta.get("unit") or ""), str(meta.get("channel") or ""),
         float(meta.get("nominal_mAh") or 0),
         json.dumps(meta.get("setpoints_A") or [], ensure_ascii=False),
         str(meta.get("start") or ""), str(meta.get("end") or ""),
         int(meta.get("rows") or 0), len(summary),
         float(meta.get("voltage_min_V") or 0), float(meta.get("voltage_max_V") or 0),
         float(meta.get("current_min_A") or 0), float(meta.get("current_max_A") or 0),
         ts))
    tid = cur.lastrowid

    # 通道自动匹配：先精确，再退到 unit-channel / 纯通道号
    hit_id, _ = match_channel(conn, meta)
    if hit_id:
        conn.execute("UPDATE tests SET channel_id=? WHERE id=?", (hit_id, tid))

    # 循环汇总
    first_discharge = None
    cycle_rows = []
    for item in summary:
        c = int(item["cycle"])
        ch_mAh = float(item.get("charge_mAh") or 0)
        dis_mAh = float(item.get("discharge_mAh") or 0)
        ce = round(dis_mAh / ch_mAh * 100, 2) if ch_mAh > 0 else None
        if first_discharge is None and dis_mAh > 0:
            first_discharge = dis_mAh
        ret = round(dis_mAh / first_discharge * 100, 2) if first_discharge else None
        cycle_rows.append((tid, c, ch_mAh, dis_mAh,
                           float(item.get("charge_mWh") or 0), float(item.get("discharge_mWh") or 0),
                           ce, ret))
    if cycle_rows:
        conn.executemany(
            "INSERT INTO test_cycles(test_id, cycle, charge_mAh, discharge_mAh, charge_mWh,"
            " discharge_mWh, ce_pct, retention_pct) VALUES(?,?,?,?,?,?,?,?)", cycle_rows)

    # 每圈降采样曲线
    by_cycle = {}
    for row in rows:
        c = int(row.get("cycle") or 0)
        if c not in by_cycle:
            if len(by_cycle) >= MAX_CURVE_CYCLES:
                continue
            by_cycle[c] = []
        by_cycle[c].append(row)

    point_rows = []
    for c, items in by_cycle.items():
        # 容量是整份文件的累计值，画单圈曲线要减去该圈起点的累计量，
        # 否则会得到横跨几十圈的巨大负数。
        c0 = float(items[0].get("charge_capacity_mAh") or 0)
        d0 = float(items[0].get("discharge_capacity_mAh") or 0)
        for seq, idx in enumerate(_downsample(items, MAX_POINTS_PER_CYCLE)):
            r = items[idx]
            ch = float(r.get("charge_capacity_mAh") or 0) - c0
            dis = float(r.get("discharge_capacity_mAh") or 0) - d0
            point_rows.append((tid, c, seq, ch - dis, float(r.get("voltage_V") or 0),
                               float(r.get("current_A") or 0)))
    if point_rows:
        conn.executemany(
            "INSERT INTO test_points(test_id, cycle, seq, q, v, cur) VALUES(?,?,?,?,?,?)", point_rows)

    conn.commit()
    return tid, {"cycles": len(cycle_rows), "points": len(point_rows), "channel_code": code}


def list_tests(conn, channel_id=None):
    ensure_schema(conn)
    sql = ("SELECT id, channel_code, channel_id, filename, start_at, end_at, rows, cycles,"
           " nominal_mAh, vmin, vmax, created_at FROM tests")
    args = []
    if channel_id is not None:
        sql += " WHERE channel_id=?"
        args.append(channel_id)
    sql += " ORDER BY start_at DESC, id DESC"
    return [dict(r) for r in conn.execute(sql, args)]


def get_test(conn, tid):
    ensure_schema(conn)
    row = conn.execute("SELECT * FROM tests WHERE id=?", (tid,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["cycle_rows"] = [dict(r) for r in conn.execute(
        "SELECT cycle, charge_mAh, discharge_mAh, charge_mWh, discharge_mWh, ce_pct, retention_pct"
        " FROM test_cycles WHERE test_id=? ORDER BY cycle", (tid,))]
    return d


def get_curve(conn, tid, cycle=None):
    ensure_schema(conn)
    if cycle is None:
        row = conn.execute(
            "SELECT cycle FROM test_cycles WHERE test_id=? AND discharge_mAh>0"
            " ORDER BY cycle DESC LIMIT 1", (tid,)).fetchone()
        cycle = row["cycle"] if row else 1
    pts = [dict(r) for r in conn.execute(
        "SELECT seq, q, v, cur FROM test_points WHERE test_id=? AND cycle=? ORDER BY seq",
        (tid, cycle))]
    return {"cycle": cycle, "points": pts}


def delete_test(conn, tid):
    ensure_schema(conn)
    for table in ("test_points", "test_cycles", "tests"):
        conn.execute("DELETE FROM %s WHERE %s=?" % (table, "id" if table == "tests" else "test_id"),
                     (tid,))
    conn.commit()


def link_test(conn, tid, channel_id):
    """手动把一次测试挂到某个通道上（channel_id=None 表示取消关联）。"""
    ensure_schema(conn)
    conn.execute("UPDATE tests SET channel_id=? WHERE id=?", (channel_id, tid))
    conn.commit()
