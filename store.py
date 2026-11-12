# -*- coding: utf-8 -*-
"""battery-tracker 数据层（纯标准库，无第三方依赖）

核心概念：
  channels   通道       —— 测试柜上的一个通道位
  batteries  电池       —— 一颗被测电芯/电池包
  placements 装机记录   —— 某颗电池在某个通道上从装到拆的一段经历
  fields     字段定义   —— 用户自定义的状态字段（可增删改）
  snapshots  状态报送   —— 某时刻某个通道上记录的一组状态值
"""

import json
import os
import re
import sqlite3
import threading
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
# 默认数据库在 data/battery.db；设置环境变量 BT_DB 可以指向别的库（做演示/测试时用）
DB_PATH = os.path.abspath(os.environ.get("BT_DB") or os.path.join(ROOT, "data", "battery.db"))
DATA_DIR = os.path.dirname(DB_PATH)

# 多用户：每个请求通过 use_db() 指向当前登录用户自己的库。
# 用 threading.local 而不是全局变量，否则并发请求会互相覆盖（A 的请求写到 B 的库）。
_tls = threading.local()


def use_db(path):
    """把当前线程的数据库切到指定文件；传 None 恢复默认。"""
    _tls.db_path = os.path.abspath(path) if path else None


def current_db():
    return getattr(_tls, "db_path", None) or DB_PATH

DEFAULT_STATUS_OPTIONS = ["运行中", "静置", "异常", "停机", "待检"]

# 默认字段：新装环境里的初始字段，用户可在「设置」里增删改
DEFAULT_FIELDS = [
    ("cycle", "循环圈数", "圈", "number", "", 0, 10),
    ("specific_capacity", "比容量", "mAh/g", "number", "", 0, 20),
    ("coulombic_efficiency", "库伦效率", "%", "number", "", 0, 30),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    sort_order INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    name TEXT DEFAULT '',
    note TEXT DEFAULT '',
    group_id INTEGER,
    sort_order INTEGER DEFAULT 0,
    archived INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_channel_group_code
    ON channels(COALESCE(group_id,0), code);

CREATE TABLE IF NOT EXISTS batteries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    batch TEXT DEFAULT '',
    model TEXT DEFAULT '',
    rated_capacity REAL,
    note TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS placements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id INTEGER NOT NULL REFERENCES channels(id),
    battery_id INTEGER NOT NULL REFERENCES batteries(id),
    started_at TEXT NOT NULL,
    ended_at TEXT,
    result TEXT DEFAULT '',
    note TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_open_placement ON placements(channel_id) WHERE ended_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_placement_battery ON placements(battery_id);

CREATE TABLE IF NOT EXISTS fields (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    unit TEXT DEFAULT '',
    kind TEXT DEFAULT 'number',
    options TEXT DEFAULT '',
    required INTEGER DEFAULT 0,
    sort_order INTEGER DEFAULT 0,
    active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id INTEGER NOT NULL REFERENCES channels(id),
    battery_id INTEGER REFERENCES batteries(id),
    placement_id INTEGER REFERENCES placements(id),
    taken_at TEXT NOT NULL,
    status TEXT DEFAULT '',
    note TEXT DEFAULT '',
    source TEXT DEFAULT 'web',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_snapshot_channel ON snapshots(channel_id, taken_at);

CREATE TABLE IF NOT EXISTS snapshot_values (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    field_id INTEGER NOT NULL REFERENCES fields(id),
    value_text TEXT,
    value_num REAL
);
CREATE INDEX IF NOT EXISTS ix_sv_snapshot ON snapshot_values(snapshot_id);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def now_str():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def connect():
    path = current_db()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db():
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        # 老库升级：补上 group_id 列
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(channels)")}
        if "group_id" not in cols:
            conn.execute("ALTER TABLE channels ADD COLUMN group_id INTEGER")
        cur = conn.cursor()
        if cur.execute("SELECT COUNT(*) c FROM fields").fetchone()["c"] == 0:
            for key, label, unit, kind, options, required, order in DEFAULT_FIELDS:
                cur.execute(
                    "INSERT INTO fields(key,label,unit,kind,options,required,sort_order,active)"
                    " VALUES(?,?,?,?,?,?,?,1)",
                    (key, label, unit, kind, options, required, order),
                )
        if not get_setting(conn, "status_options"):
            set_setting(conn, "status_options", json.dumps(DEFAULT_STATUS_OPTIONS, ensure_ascii=False))
        if not get_setting(conn, "app_name"):
            set_setting(conn, "app_name", "battery技术")
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- settings

def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def get_settings(conn):
    out = {
        "app_name": get_setting(conn, "app_name", "battery技术"),
        "status_options": json.loads(get_setting(conn, "status_options", "[]") or "[]"),
    }
    return out


# ---------------------------------------------------------------- fields

def list_fields(conn, only_active=False):
    sql = "SELECT * FROM fields"
    if only_active:
        sql += " WHERE active=1"
    sql += " ORDER BY sort_order, id"
    return [dict(r) for r in conn.execute(sql)]


def field_by_key(conn, key):
    row = conn.execute("SELECT * FROM fields WHERE key=?", (key,)).fetchone()
    return dict(row) if row else None


def create_field(conn, key, label, unit="", kind="number", options="", required=0):
    label = (label or "").strip()
    if not label:
        raise ValueError("字段名称不能为空")
    if conn.execute("SELECT 1 FROM fields WHERE label=?", (label,)).fetchone():
        raise ValueError("字段「%s」已存在" % label)
    key = (key or "").strip()
    if not key:
        key = "f%s" % int(datetime.now().timestamp() * 1000)
    if conn.execute("SELECT 1 FROM fields WHERE key=?", (key,)).fetchone():
        raise ValueError("字段标识 %s 已存在" % key)
    order = conn.execute("SELECT COALESCE(MAX(sort_order),0)+10 m FROM fields").fetchone()["m"]
    conn.execute(
        "INSERT INTO fields(key,label,unit,kind,options,required,sort_order,active) VALUES(?,?,?,?,?,?,?,1)",
        (key, label, unit, kind, options, required, order),
    )
    return field_by_key(conn, key)


def update_field(conn, fid, **kw):
    allowed = ["label", "unit", "kind", "options", "required", "sort_order", "active", "key"]
    if kw.get("label"):
        dup = conn.execute("SELECT id FROM fields WHERE label=? AND id<>?", (kw["label"].strip(), fid)).fetchone()
        if dup:
            raise ValueError("字段「%s」已存在" % kw["label"])
        kw["label"] = kw["label"].strip()
    sets, vals = [], []
    for k, v in kw.items():
        if k in allowed and v is not None:
            sets.append("%s=?" % k)
            vals.append(v)
    if sets:
        vals.append(fid)
        conn.execute("UPDATE fields SET %s WHERE id=?" % ",".join(sets), vals)


def delete_field(conn, fid):
    conn.execute("DELETE FROM snapshot_values WHERE field_id=?", (fid,))
    conn.execute("DELETE FROM fields WHERE id=?", (fid,))


# ---------------------------------------------------------------- groups

def list_groups(conn, with_counts=False):
    rows = [dict(r) for r in conn.execute("SELECT * FROM groups ORDER BY sort_order, id")]
    if with_counts:
        for g in rows:
            g["count"] = conn.execute(
                "SELECT COUNT(*) c FROM channels WHERE group_id=? AND archived=0", (g["id"],)
            ).fetchone()["c"]
    return rows


def group_by_id(conn, gid):
    row = conn.execute("SELECT * FROM groups WHERE id=?", (gid,)).fetchone()
    return dict(row) if row else None


def create_group(conn, name):
    name = (name or "").strip()
    if not name:
        raise ValueError("分组名称不能为空")
    if conn.execute("SELECT 1 FROM groups WHERE name=?", (name,)).fetchone():
        raise ValueError("分组「%s」已存在" % name)
    order = conn.execute("SELECT COALESCE(MAX(sort_order),0)+10 m FROM groups").fetchone()["m"]
    cur = conn.execute(
        "INSERT INTO groups(name,sort_order,created_at) VALUES(?,?,?)",
        (name, order, now_str()),
    )
    return cur.lastrowid


def update_group(conn, gid, name=None, sort_order=None):
    g = group_by_id(conn, gid)
    if not g:
        raise ValueError("分组不存在")
    if name is not None:
        name = name.strip()
        if not name:
            raise ValueError("分组名称不能为空")
        if conn.execute("SELECT 1 FROM groups WHERE name=? AND id<>?", (name, gid)).fetchone():
            raise ValueError("分组「%s」已存在" % name)
        conn.execute("UPDATE groups SET name=? WHERE id=?", (name, gid))
    if sort_order is not None:
        conn.execute("UPDATE groups SET sort_order=? WHERE id=?", (int(sort_order), gid))


def delete_channel(conn, cid):
    """删通道：上面还装着电池就先自动下机；有装机/报送记录就归档，没有就彻底删。"""
    checked_out = None
    if open_placement(conn, cid):
        r = checkout_channel(conn, cid, result="通道删除", note="删除通道时自动下机")
        checked_out = r["battery"]["code"]

    n = conn.execute("SELECT COUNT(*) c FROM placements WHERE channel_id=?", (cid,)).fetchone()["c"]
    s = conn.execute("SELECT COUNT(*) c FROM snapshots WHERE channel_id=?", (cid,)).fetchone()["c"]
    if n or s:
        conn.execute("UPDATE channels SET archived=1 WHERE id=?", (cid,))
        return {"archived": True, "checked_out": checked_out, "placements": n, "snapshots": s}
    conn.execute("DELETE FROM channels WHERE id=?", (cid,))
    return {"archived": False, "checked_out": checked_out, "placements": 0, "snapshots": 0}


def delete_group(conn, gid, delete_channels=True):
    """删分组。

    delete_channels=True（默认）：组里的通道一并删除——有历史记录的归档，
    没有记录的彻底删，和单独删一个通道的行为一致。
    """
    rows = conn.execute("SELECT id FROM channels WHERE group_id=?", (gid,)).fetchall()
    stats = {"total": len(rows), "archived": 0, "deleted": 0, "checked_out": []}
    if delete_channels:
        for r in rows:
            res = delete_channel(conn, r["id"])
            if res["archived"]:
                stats["archived"] += 1
            else:
                stats["deleted"] += 1
            if res["checked_out"]:
                stats["checked_out"].append(res["checked_out"])
    else:
        conn.execute("UPDATE channels SET group_id=NULL WHERE group_id=?", (gid,))
    conn.execute("DELETE FROM groups WHERE id=?", (gid,))
    return stats


def reorder_groups(conn, ids):
    """按传入顺序重排分组（常用分组拖到前面）。"""
    for idx, gid in enumerate(ids or []):
        try:
            conn.execute("UPDATE groups SET sort_order=? WHERE id=?", (idx, int(gid)))
        except (TypeError, ValueError):
            continue
    conn.commit()


def reorder_channels(conn, payload):
    """payload: [{"group_id": 3, "ids": [12, 13, ...]}, ...]，按顺序写入组和排序。"""
    updated = 0
    for block in payload or []:
        gid = int(block.get("group_id") or 0) or None
        for idx, cid in enumerate(block.get("ids") or []):
            conn.execute(
                "UPDATE channels SET group_id=?, sort_order=? WHERE id=?",
                (gid, (idx + 1) * 10, int(cid)),
            )
            updated += 1
    return updated


# ---------------------------------------------------------------- channels

def list_channels(conn, include_archived=False):
    sql = "SELECT * FROM channels"
    if not include_archived:
        sql += " WHERE archived=0"
    sql += " ORDER BY sort_order, id"
    return [dict(r) for r in conn.execute(sql)]


def channel_by_id(conn, cid):
    row = conn.execute("SELECT * FROM channels WHERE id=?", (cid,)).fetchone()
    return dict(row) if row else None


def channel_by_code(conn, code, group_id=None):
    """编号只在同一个分组内唯一，不同分组可以都用 2-1。"""
    if group_id:
        row = conn.execute(
            "SELECT * FROM channels WHERE code=? AND group_id=?", (code.strip(), int(group_id))).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM channels WHERE code=? AND COALESCE(group_id,0)=0", (code.strip(),)).fetchone()
    return dict(row) if row else None


def create_channel(conn, code, name="", note="", sort_order=None, group_id=None):
    code = (code or "").strip()
    if not code:
        raise ValueError("通道编号不能为空")
    if channel_by_code(conn, code):
        raise ValueError("通道编号 %s 已存在" % code)
    if sort_order is None:
        sort_order = conn.execute("SELECT COALESCE(MAX(sort_order),0)+10 m FROM channels").fetchone()["m"]
    cur = conn.execute(
        "INSERT INTO channels(code,name,note,group_id,sort_order,created_at) VALUES(?,?,?,?,?,?)",
        (code, name or "", note or "", int(group_id) if group_id else None, sort_order, now_str()),
    )
    return cur.lastrowid


def expand_range(spec):
    """把 'C01-C04, C09' 展开成列表。支持 C01-C04 / 1-4 / A01~A03"""
    out = []
    for chunk in str(spec).replace("，", ",").replace("~", "-").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            head, _, tail = chunk.partition("-")
            head, tail = head.strip(), tail.strip()
            m1 = _split_alpha_num(head)
            m2 = _split_alpha_num(tail)
            if m1[0] == m2[0] and m1[1].isdigit() and m2[1].isdigit():
                start, end = int(m1[1]), int(m2[1])
                width = max(len(m1[1]), len(m2[1]))
                step = 1 if end >= start else -1
                for i in range(start, end + step, step):
                    out.append(m1[0] + str(i).zfill(width))
                continue
        out.append(chunk)
    return out


def _split_alpha_num(s):
    i = 0
    while i < len(s) and not s[i].isdigit():
        i += 1
    return s[:i], s[i:]


def next_row_number(conn):
    """现有编号如果是「排号-序号」的形式（4-1 / 4-2 …），返回下一个排号。"""
    rows = []
    for r in conn.execute("SELECT code FROM channels"):
        m = re.match(r"^(\d+)-\d+$", r["code"] or "")
        if m:
            rows.append(int(m.group(1)))
    return (max(rows) + 1) if rows else 1


def create_channels_bulk(conn, codes, group_id=None):
    """按给定的编号批量建通道；任何一个有问题就整批不建。"""
    clean = [str(c).strip() for c in (codes or [])]
    if not clean:
        raise ValueError("没有要创建的通道")
    if len(clean) > 200:
        raise ValueError("一次最多创建 200 个通道")

    seen, problems = set(), []
    for i, c in enumerate(clean):
        if not c:
            problems.append("第 %d 个编号为空" % (i + 1))
            continue
        if c in seen:
            problems.append("「%s」重复" % c)
        if channel_by_code(conn, c, group_id):
            problems.append("「%s」在本组已存在" % c)
        seen.add(c)
    if problems:
        raise ValueError("；".join(problems[:5]) + ("…" if len(problems) > 5 else ""))

    base = conn.execute("SELECT COALESCE(MAX(sort_order),0) m FROM channels").fetchone()["m"]
    gid = int(group_id) if group_id else None
    created = []
    for i, c in enumerate(clean):
        conn.execute(
            "INSERT INTO channels(code,name,note,group_id,sort_order,created_at) VALUES(?,?,?,?,?,?)",
            (c, "", "", gid, base + (i + 1) * 10, now_str()),
        )
        created.append(c)
    return created


# ---------------------------------------------------------------- batteries

def battery_by_code(conn, code):
    row = conn.execute("SELECT * FROM batteries WHERE code=?", ((code or "").strip(),)).fetchone()
    return dict(row) if row else None


def battery_by_id(conn, bid):
    row = conn.execute("SELECT * FROM batteries WHERE id=?", (bid,)).fetchone()
    return dict(row) if row else None


def ensure_battery(conn, code, batch="", model="", rated_capacity=None, note=""):
    code = (code or "").strip()
    if not code:
        raise ValueError("电池编号不能为空")
    exist = battery_by_code(conn, code)
    if exist:
        # 只补空字段，不覆盖已有信息
        updates = {}
        for k, v in (("batch", batch), ("model", model), ("rated_capacity", rated_capacity), ("note", note)):
            if v not in (None, "") and not exist.get(k):
                updates[k] = v
        if updates:
            sets = ",".join("%s=?" % k for k in updates)
            conn.execute("UPDATE batteries SET %s WHERE id=?" % sets, list(updates.values()) + [exist["id"]])
            exist = battery_by_id(conn, exist["id"])
        return exist, True
    cur = conn.execute(
        "INSERT INTO batteries(code,batch,model,rated_capacity,note,created_at) VALUES(?,?,?,?,?,?)",
        (code, batch or "", model or "", rated_capacity, note or "", now_str()),
    )
    return battery_by_id(conn, cur.lastrowid), False


def search_batteries(conn, q="", limit=100):
    q = (q or "").strip()
    if q:
        like = "%" + q + "%"
        rows = conn.execute(
            "SELECT * FROM batteries WHERE code LIKE ? OR batch LIKE ? OR model LIKE ? OR note LIKE ?"
            " ORDER BY id DESC LIMIT ?",
            (like, like, like, like, limit),
        )
    else:
        rows = conn.execute("SELECT * FROM batteries ORDER BY id DESC LIMIT ?", (limit,))
    out = []
    for r in rows:
        d = dict(r)
        d["placement_count"] = conn.execute(
            "SELECT COUNT(*) c FROM placements WHERE battery_id=?", (d["id"],)
        ).fetchone()["c"]
        open_row = conn.execute(
            "SELECT p.*, c.code channel_code FROM placements p JOIN channels c ON c.id=p.channel_id"
            " WHERE p.battery_id=? AND p.ended_at IS NULL",
            (d["id"],),
        ).fetchone()
        d["current"] = dict(open_row) if open_row else None
        out.append(d)
    return out


# ---------------------------------------------------------------- placements

def open_placement(conn, channel_id):
    row = conn.execute(
        "SELECT * FROM placements WHERE channel_id=? AND ended_at IS NULL", (channel_id,)
    ).fetchone()
    return dict(row) if row else None


def _iso(v):
    if not v:
        return None
    v = str(v).strip().replace(" ", "T")
    if len(v) == 16:
        v += ":00"
    return v


def swap_battery(conn, channel_id, battery_code, batch="", model="", rated_capacity=None,
                 battery_note="", started_at=None, prev_ended_at=None, result="", note="",
                 source="web"):
    """换电池：结束通道上旧的装机记录，装入新电池。"""
    started_at = _iso(started_at) or now_str()
    battery, existed = ensure_battery(
        conn, battery_code, batch=batch, model=model, rated_capacity=rated_capacity, note=battery_note
    )
    prev = open_placement(conn, channel_id)
    closed = None
    if prev:
        ended = _iso(prev_ended_at) or started_at
        conn.execute(
            "UPDATE placements SET ended_at=?, result=? WHERE id=?",
            (ended, result or "", prev["id"]),
        )
        closed = prev["id"]
    cur = conn.execute(
        "INSERT INTO placements(channel_id,battery_id,started_at,note,created_at) VALUES(?,?,?,?,?)",
        (channel_id, battery["id"], started_at, note or "", now_str()),
    )
    result = {
        "placement_id": cur.lastrowid,
        "battery": battery,
        "battery_created": not existed,
        "closed_placement_id": closed,
        "previous_battery": battery_by_id(conn, prev["battery_id"]) if prev else None,
    }
    # 装了电池就是运行中，不用再手动报状态
    add_snapshot(conn, channel_id, status="运行中",
                 note="装入 " + battery_code, source="swap")
    return result


def checkout_channel(conn, channel_id, result="", note="", ended_at=None):
    prev = open_placement(conn, channel_id)
    if not prev:
        raise ValueError("该通道当前没有在装的电池")
    ended_at = _iso(ended_at) or now_str()
    conn.execute(
        "UPDATE placements SET ended_at=?, result=?, note=TRIM(COALESCE(note,'') || ?) WHERE id=?",
        (ended_at, result or "", ("\n" + note) if note else "", prev["id"]),
    )
    bat = battery_by_id(conn, prev["battery_id"])
    add_snapshot(conn, channel_id, status="停机",
                 note="卸下 " + (bat["code"] if bat else ""), source="swap")
    return {"closed_placement_id": prev["id"], "battery": bat}


# ---------------------------------------------------------------- snapshots

def _norm_value(field, raw):
    if raw is None:
        return None, None
    s = str(raw).strip()
    if s == "":
        return None, None
    if field["kind"] == "number":
        cleaned = s.replace(",", "").replace("＋", "+").strip()
        try:
            return s, float(cleaned)
        except ValueError:
            # 允许「3.85V」「35 mΩ」这种带单位的写法
            m = re.match(r"^([+-]?\d+(?:\.\d+)?)", cleaned)
            if m:
                return s, float(m.group(1))
            return s, None
    return s, None


def resolve_field(conn, token, cache=None):
    """按 key 或 label 找字段，找不到返回 None。"""
    token = (token or "").strip()
    if not token:
        return None
    fields = list_fields(conn)
    for f in fields:
        if f["key"] == token:
            return f
    for f in fields:
        if f["label"] == token:
            return f
    for f in fields:
        if f["label"] + "(" + (f["unit"] or "") + ")" == token:
            return f
    return None


def add_snapshot(conn, channel_id, values=None, status="", note="", taken_at=None, source="web"):
    channel = channel_by_id(conn, channel_id)
    if not channel:
        raise ValueError("通道不存在")
    taken_at = _iso(taken_at) or now_str()
    placement = open_placement(conn, channel_id)
    cur = conn.execute(
        "INSERT INTO snapshots(channel_id,battery_id,placement_id,taken_at,status,note,source,created_at)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (
            channel_id,
            placement["battery_id"] if placement else None,
            placement["id"] if placement else None,
            taken_at,
            status or "",
            note or "",
            source or "web",
            now_str(),
        ),
    )
    sid = cur.lastrowid
    skipped = []
    for key, raw in (values or {}).items():
        f = resolve_field(conn, key)
        if not f:
            skipped.append(key)
            continue
        vt, vn = _norm_value(f, raw)
        if vt is None:
            continue
        conn.execute(
            "INSERT INTO snapshot_values(snapshot_id,field_id,value_text,value_num) VALUES(?,?,?,?)",
            (sid, f["id"], vt, vn),
        )
    return {"id": sid, "skipped": skipped}


def snapshot_values_map(conn, snapshot_ids):
    if not snapshot_ids:
        return {}
    qmarks = ",".join("?" * len(snapshot_ids))
    rows = conn.execute(
        "SELECT sv.snapshot_id, f.key, f.label, f.unit, f.kind, sv.value_text, sv.value_num"
        " FROM snapshot_values sv JOIN fields f ON f.id=sv.field_id"
        " WHERE sv.snapshot_id IN (%s) ORDER BY f.sort_order, f.id" % qmarks,
        list(snapshot_ids),
    )
    out = {}
    for r in rows:
        out.setdefault(r["snapshot_id"], []).append({
            "key": r["key"],
            "label": r["label"],
            "unit": r["unit"],
            "kind": r["kind"],
            "value": r["value_num"] if r["value_num"] is not None else r["value_text"],
        })
    return out


def snapshot_full(conn, row):
    d = dict(row)
    d["values"] = snapshot_values_map(conn, [d["id"]]).get(d["id"], [])
    return d


def latest_snapshot(conn, channel_id):
    row = conn.execute(
        "SELECT * FROM snapshots WHERE channel_id=? ORDER BY taken_at DESC, id DESC LIMIT 1",
        (channel_id,),
    ).fetchone()
    return snapshot_full(conn, row) if row else None


def query_snapshots(conn, channel_id=None, battery_id=None, batch=None, date_from=None,
                    date_to=None, status=None, limit=500, offset=0):
    where, args = [], []
    if channel_id:
        where.append("s.channel_id=?")
        args.append(channel_id)
    if battery_id:
        where.append("s.battery_id=?")
        args.append(battery_id)
    if batch:
        where.append("b.batch=?")
        args.append(batch)
    if status:
        where.append("s.status=?")
        args.append(status)
    if date_from:
        where.append("s.taken_at>=?")
        args.append(_iso(date_from))
    if date_to:
        where.append("s.taken_at<=?")
        args.append(_iso(date_to))
    sql = (
        "SELECT s.*, c.code channel_code, b.code battery_code, b.batch battery_batch"
        " FROM snapshots s JOIN channels c ON c.id=s.channel_id"
        " LEFT JOIN batteries b ON b.id=s.battery_id"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY s.taken_at DESC, s.id DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    rows = [dict(r) for r in conn.execute(sql, args)]
    vmap = snapshot_values_map(conn, [r["id"] for r in rows])
    for r in rows:
        r["values"] = vmap.get(r["id"], [])
    return rows


# ---------------------------------------------------------------- views

def channel_state(conn, ch):
    d = dict(ch)
    placement = open_placement(conn, d["id"])
    if placement:
        d["current"] = {
            "placement_id": placement["id"],
            "started_at": placement["started_at"],
            "note": placement["note"],
            "battery": battery_by_id(conn, placement["battery_id"]),
        }
    else:
        d["current"] = None
    d["last"] = latest_snapshot(conn, d["id"])
    d["counts"] = {
        "snapshots": conn.execute("SELECT COUNT(*) c FROM snapshots WHERE channel_id=?", (d["id"],)).fetchone()["c"],
        "placements": conn.execute("SELECT COUNT(*) c FROM placements WHERE channel_id=?", (d["id"],)).fetchone()["c"],
    }
    return d


def build_state(conn):
    channels = [channel_state(conn, ch) for ch in list_channels(conn)]
    groups = list_groups(conn, with_counts=True)
    archived = []
    for r in conn.execute("SELECT * FROM channels WHERE archived=1 ORDER BY sort_order, id"):
        d = dict(r)
        d["snapshots"] = conn.execute(
            "SELECT COUNT(*) c FROM snapshots WHERE channel_id=?", (d["id"],)).fetchone()["c"]
        d["placements"] = conn.execute(
            "SELECT COUNT(*) c FROM placements WHERE channel_id=?", (d["id"],)).fetchone()["c"]
        archived.append(d)
    stats = {
        "channels": len(channels),
        "occupied": sum(1 for c in channels if c["current"]),
        "groups": len(groups),
        "archived": len(archived),
        "batteries": conn.execute("SELECT COUNT(*) c FROM batteries").fetchone()["c"],
        "placements": conn.execute("SELECT COUNT(*) c FROM placements").fetchone()["c"],
        "snapshots": conn.execute("SELECT COUNT(*) c FROM snapshots").fetchone()["c"],
    }
    return {
        "settings": get_settings(conn),
        "fields": list_fields(conn),
        "groups": groups,
        "channels": channels,
        "archived_channels": archived,
        "stats": stats,
        "server_time": now_str(),
    }


def channel_timeline(conn, channel_id, limit=200):
    ch = channel_by_id(conn, channel_id)
    if not ch:
        raise ValueError("通道不存在")
    events = []
    rows = conn.execute(
        "SELECT p.*, b.code battery_code, b.batch, b.model FROM placements p"
        " JOIN batteries b ON b.id=p.battery_id WHERE p.channel_id=? ORDER BY p.started_at DESC",
        (channel_id,),
    )
    for r in rows:
        r = dict(r)
        events.append({
            "type": "placement",
            "at": r["started_at"],
            "end": r["ended_at"],
            "battery_code": r["battery_code"],
            "batch": r["batch"],
            "model": r["model"],
            "result": r["result"],
            "note": r["note"],
        })
    snaps = query_snapshots(conn, channel_id=channel_id, limit=limit)
    for s in snaps:
        events.append({
            "type": "snapshot",
            "at": s["taken_at"],
            "status": s["status"],
            "note": s["note"],
            "battery_code": s["battery_code"],
            "values": s["values"],
            "source": s["source"],
        })
    events.sort(key=lambda e: e["at"] or "", reverse=True)
    return {"channel": ch, "events": events[:limit]}


def battery_profile(conn, battery_id):
    b = battery_by_id(conn, battery_id)
    if not b:
        raise ValueError("电池不存在")
    placements = []
    for r in conn.execute(
        "SELECT p.*, c.code channel_code FROM placements p JOIN channels c ON c.id=p.channel_id"
        " WHERE p.battery_id=? ORDER BY p.started_at DESC",
        (battery_id,),
    ):
        placements.append(dict(r))
    snaps = query_snapshots(conn, battery_id=battery_id, limit=200)
    b["placements"] = placements
    b["snapshots"] = snaps
    b["channels_used"] = sorted({p["channel_code"] for p in placements})
    total_hours = 0.0
    for p in placements:
        try:
            start = datetime.fromisoformat(p["started_at"])
            end = datetime.fromisoformat(p["ended_at"]) if p["ended_at"] else datetime.now()
            total_hours += max(0.0, (end - start).total_seconds() / 3600.0)
        except Exception:
            pass
    b["total_hours"] = round(total_hours, 1)
    return b


def batch_compare(conn):
    """同批次 / 各通道横向对比：每颗电池的最新一次报送数据。"""
    rows = conn.execute(
        "SELECT b.id, b.code, b.batch, b.model, c.code channel_code, p.started_at, p.ended_at"
        " FROM batteries b"
        " LEFT JOIN placements p ON p.battery_id=b.id AND p.ended_at IS NULL"
        " LEFT JOIN channels c ON c.id=p.channel_id ORDER BY b.batch, b.code"
    )
    out = []
    for r in rows:
        d = dict(r)
        last = conn.execute(
            "SELECT * FROM snapshots WHERE battery_id=? ORDER BY taken_at DESC, id DESC LIMIT 1", (d["id"],)
        ).fetchone()
        d["last"] = snapshot_full(conn, last) if last else None
        out.append(d)
    return out
