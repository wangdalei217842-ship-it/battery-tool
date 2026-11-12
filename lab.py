# -*- coding: utf-8 -*-
"""实验室药品台账（共享库，组内共用）

字段对齐现有腾讯文档表：药品 / 纯度 / 厂家 / 规格，另外补了购买时间和有效期，
用于「防重复购买」和「避免用到过期药品」。
"""
import os
import sqlite3
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
DB_PATH = os.environ.get("BT_SHARED_DB") or os.path.join(DATA_DIR, "shared.db")

CATEGORIES = ["固体", "液体", "手套箱固体", "手套箱液体"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS drugs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL DEFAULT '固体',
    name TEXT NOT NULL,
    purity TEXT DEFAULT '',
    vendor TEXT DEFAULT '',
    spec TEXT DEFAULT '',
    purchase_date TEXT DEFAULT '',
    expire_date TEXT DEFAULT '',
    location TEXT DEFAULT '',
    note TEXT DEFAULT '',
    author TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_drugs_name ON drugs(name);
CREATE INDEX IF NOT EXISTS ix_drugs_cat ON drugs(category);
"""


def now_str():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode = WAL")
    return c


def init_db():
    c = connect()
    try:
        c.executescript(SCHEMA)
        c.commit()
    finally:
        c.close()


def _days_left(expire_date):
    """还有几天过期；没填或格式不对返回 None。"""
    if not expire_date:
        return None
    try:
        d = datetime.strptime(expire_date[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    return (d - datetime.now().date()).days


def list_drugs(keyword="", category="", warn_days=60):
    c = connect()
    try:
        sql = "SELECT * FROM drugs WHERE 1=1"
        args = []
        if keyword:
            sql += " AND (name LIKE ? OR vendor LIKE ? OR purity LIKE ? OR note LIKE ? OR location LIKE ?)"
            like = "%" + keyword + "%"
            args += [like] * 5
        if category:
            sql += " AND category=?"
            args.append(category)
        sql += " ORDER BY category, name, id"
        rows = [dict(r) for r in c.execute(sql, args)]
        for r in rows:
            left = _days_left(r.get("expire_date"))
            r["days_left"] = left
            r["expired"] = 1 if (left is not None and left < 0) else 0
            r["expiring"] = 1 if (left is not None and 0 <= left <= warn_days) else 0
        return {"items": rows, "count": len(rows),
                "expired": sum(r["expired"] for r in rows),
                "expiring": sum(r["expiring"] for r in rows)}
    finally:
        c.close()


def find_same_name(name):
    """同名药品（用于登记时提示可能重复购买）。"""
    c = connect()
    try:
        return [dict(r) for r in c.execute(
            "SELECT id, category, name, purity, vendor, spec, purchase_date, expire_date"
            " FROM drugs WHERE name=? ORDER BY id", ((name or "").strip(),))]
    finally:
        c.close()


def create_drug(data, author=""):
    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("请填药品名称")
    category = (data.get("category") or "固体").strip()
    if category not in CATEGORIES:
        category = "固体"
    ts = now_str()
    c = connect()
    try:
        cur = c.execute(
            "INSERT INTO drugs(category,name,purity,vendor,spec,purchase_date,expire_date,"
            "location,note,author,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (category, name, (data.get("purity") or "").strip(), (data.get("vendor") or "").strip(),
             (data.get("spec") or "").strip(), (data.get("purchase_date") or "").strip(),
             (data.get("expire_date") or "").strip(), (data.get("location") or "").strip(),
             (data.get("note") or "").strip(), (author or "").strip(), ts, ts))
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def update_drug(did, data):
    fields = ("category", "name", "purity", "vendor", "spec",
              "purchase_date", "expire_date", "location", "note")
    sets, args = [], []
    for f in fields:
        if f in data:
            sets.append("%s=?" % f)
            args.append((data.get(f) or "").strip())
    if not sets:
        return
    sets.append("updated_at=?")
    args.append(now_str())
    args.append(did)
    c = connect()
    try:
        c.execute("UPDATE drugs SET %s WHERE id=?" % ",".join(sets), args)
        c.commit()
    finally:
        c.close()


def delete_drug(did):
    c = connect()
    try:
        c.execute("DELETE FROM drugs WHERE id=?", (did,))
        c.commit()
    finally:
        c.close()


def get_drug(did):
    c = connect()
    try:
        row = c.execute("SELECT * FROM drugs WHERE id=?", (did,)).fetchone()
        if not row:
            return None
        d = dict(row)
        left = _days_left(d.get("expire_date"))
        d["days_left"] = left
        d["expired"] = 1 if (left is not None and left < 0) else 0
        d["expiring"] = 1 if (left is not None and 0 <= left <= 60) else 0
        return d
    finally:
        c.close()


def stats():
    c = connect()
    try:
        out = {}
        for cat in CATEGORIES:
            out[cat] = c.execute("SELECT COUNT(*) FROM drugs WHERE category=?", (cat,)).fetchone()[0]
        out["合计"] = c.execute("SELECT COUNT(*) FROM drugs").fetchone()[0]
        return out
    finally:
        c.close()
