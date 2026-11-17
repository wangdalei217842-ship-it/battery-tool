# -*- coding: utf-8 -*-
"""通道分配表（共享库）

数据来源：实验室的《通道分配表》Excel，一行一个通道，标注使用人和共享人。
账号是姓名拼音首字母，所以额外维护一张 user_realname 映射，用来把通道推给对应的人。
"""
import os
import sqlite3
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("BT_SHARED_DB") or os.path.join(ROOT, "data", "shared.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS channel_assign (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    machine TEXT DEFAULT '',
    code TEXT NOT NULL,
    status TEXT DEFAULT '',
    owner TEXT DEFAULT '',
    sharer TEXT DEFAULT '',
    note TEXT DEFAULT '',
    source TEXT DEFAULT '',
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_assign_owner ON channel_assign(owner);
CREATE TABLE IF NOT EXISTS user_realname (
    username TEXT PRIMARY KEY,
    realname TEXT NOT NULL
);
"""

# 姓名 → 账号。只填能确定的：账号是姓名首字母，有歧义的先不填，
# 宁可不显示，也不能把别人的通道推给错的人。
SEED_MAP = [
    ("zxq", "曾雪琴"),
    ("ykx", "叶开心"),
    ("dd", "邓迪"),
    ("zc", "邹钞"),
    ("wdl", "王大磊"),
    ("wq", "王清"),
    ("xch", "许春红"),
    ("Izhar", "IZHAR UDDIN"),
    ("wny", "魏乃颖"),
    ("xrs", "薛儒舜"),
    ("hs", "胡顺"),
    ("wzp", "魏梓鹏"),
    ("zyd", "张一栋"),
    ("zjh", "曾静慧"),
    ("qn", "卿妮"),
    ("dzp", "杜展鹏"),
    ("hjh", "何佳俊"),
    ("zyw", "张博"),
]


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
        for username, realname in SEED_MAP:
            c.execute("INSERT OR IGNORE INTO user_realname(username, realname) VALUES(?,?)",
                      (username, realname))
        c.commit()
    finally:
        c.close()


def import_rows(rows):
    """整表替换（Excel 是唯一数据源，重复导入不会产生重复行）。"""
    c = connect()
    try:
        c.execute("DELETE FROM channel_assign")
        ts = now_str()
        c.executemany(
            "INSERT INTO channel_assign(machine,code,status,owner,sharer,note,source,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            [(r.get("machine", ""), r.get("code", ""), r.get("status", ""),
              r.get("owner", ""), r.get("sharer", ""), r.get("note", ""),
              r.get("source", ""), ts) for r in rows])
        c.commit()
        return len(rows)
    finally:
        c.close()


def list_assign(keyword="", owner=""):
    c = connect()
    try:
        sql = "SELECT * FROM channel_assign WHERE 1=1"
        args = []
        if keyword:
            sql += " AND (code LIKE ? OR machine LIKE ? OR owner LIKE ? OR sharer LIKE ?)"
            like = "%" + keyword + "%"
            args += [like] * 4
        if owner:
            sql += " AND (owner=? OR sharer LIKE ?)"
            args += [owner, "%" + owner + "%"]
        sql += " ORDER BY machine, code"
        items = [dict(r) for r in c.execute(sql, args)]
        people = {}
        for it in items:
            if it["owner"]:
                people[it["owner"]] = people.get(it["owner"], 0) + 1
        return {"items": items, "count": len(items), "people": people}
    finally:
        c.close()


def mine(username):
    """当前账号名下的通道：主使用人 + 共享人里提到自己。"""
    c = connect()
    try:
        row = c.execute("SELECT realname FROM user_realname WHERE username=?", (username,)).fetchone()
        if not row:
            return {"matched": False, "realname": None, "items": []}
        name = row["realname"]
        items = [dict(r) for r in c.execute(
            "SELECT * FROM channel_assign WHERE owner=? OR sharer LIKE ?"
            " ORDER BY machine, code", (name, "%" + name + "%"))]
        main = [x for x in items if x["owner"] == name]
        shared = [x for x in items if x["owner"] != name]
        return {"matched": True, "realname": name, "items": items,
                "main_count": len(main), "shared_count": len(shared)}
    finally:
        c.close()


def all_names():
    c = connect()
    try:
        return [(r["username"], r["realname"]) for r in
                c.execute("SELECT username, realname FROM user_realname ORDER BY username")]
    finally:
        c.close()


def stats():
    c = connect()
    try:
        total = c.execute("SELECT COUNT(*) FROM channel_assign").fetchone()[0]
        good = c.execute("SELECT COUNT(*) FROM channel_assign WHERE status LIKE '%良好%'").fetchone()[0]
        named = c.execute("SELECT COUNT(*) FROM channel_assign WHERE owner<>''").fetchone()[0]
        people = c.execute("SELECT COUNT(DISTINCT owner) FROM channel_assign WHERE owner<>''").fetchone()[0]
        return {"total": total, "good": good, "bad": total - good,
                "named": named, "unnamed": total - named, "people": people}
    finally:
        c.close()
