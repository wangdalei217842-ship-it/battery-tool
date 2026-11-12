# -*- coding: utf-8 -*-
"""实验方法库（共享数据，不跟随个人库）

为什么单独一个库：方法文档是团队共用的知识，师弟师妹登录后应该看到同一份，
不能像通道台账那样一人一份。

存储：
  data/shared.db          方法正文与附件元数据
  data/uploads/<id>/      附件实体文件
"""

import os
import re
import shutil
import sqlite3
import uuid
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("BT_DATA_DIR") or os.path.join(ROOT, "data")
DB_PATH = os.environ.get("BT_SHARED_DB") or os.path.join(DATA_DIR, "shared.db")
UPLOAD_DIR = os.environ.get("BT_UPLOAD_DIR") or os.path.join(DATA_DIR, "uploads")

MAX_FILE_BYTES = int(os.environ.get("BT_UPLOAD_MAX", str(30 * 1024 * 1024)))  # 单文件 30M

DEFAULT_CATEGORIES = ["设备操作", "样品制备", "组装装配", "表征测试", "数据处理", "安全规范", "其他"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS methods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    category TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    content TEXT DEFAULT '',
    tags TEXT DEFAULT '',
    author TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS method_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    method_id INTEGER NOT NULL,
    filename TEXT NOT NULL,
    stored TEXT NOT NULL,
    size INTEGER DEFAULT 0,
    mime TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_method_files ON method_files(method_id);
CREATE TABLE IF NOT EXISTS method_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    sort INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);
"""

# 允许预览的图片类型，其它一律走下载
INLINE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf"}


def now_str():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("PRAGMA journal_mode = WAL")
    return c


def init_db():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    c = connect()
    try:
        c.executescript(SCHEMA)
        _migrate_groups(c)
        c.commit()
    finally:
        c.close()


def _migrate_groups(c):
    """给 methods 补 group_id 列，并把历史 category 转成分组。"""
    cols = {r["name"] for r in c.execute("PRAGMA table_info(methods)")}
    if "group_id" not in cols:
        c.execute("ALTER TABLE methods ADD COLUMN group_id INTEGER")
    cats = [r["category"] for r in c.execute(
        "SELECT DISTINCT category FROM methods WHERE category<>'' AND group_id IS NULL")]
    for cat in cats:
        row = c.execute("SELECT id FROM method_groups WHERE name=?", (cat,)).fetchone()
        if row:
            gid = row["id"]
        else:
            cur = c.execute("INSERT INTO method_groups(name, sort, created_at) VALUES(?,?,?)",
                            (cat, 0, now_str()))
            gid = cur.lastrowid
        c.execute("UPDATE methods SET group_id=? WHERE category=? AND group_id IS NULL", (gid, cat))
    if not c.execute("SELECT 1 FROM method_groups LIMIT 1").fetchone():
        c.execute("INSERT INTO method_groups(name, sort, created_at) VALUES(?,?,?)",
                  ("未分组", 0, now_str()))


# ---------------------------------------------------------------- 分组
def list_groups():
    c = connect()
    try:
        rows = [dict(r) for r in c.execute(
            "SELECT g.id, g.name, g.sort,"
            " (SELECT COUNT(*) FROM methods m WHERE m.group_id=g.id) AS files"
            " FROM method_groups g ORDER BY g.sort, g.id")]
        return rows
    finally:
        c.close()


def create_group(name):
    name = (name or "").strip()
    if not name:
        raise ValueError("分组名不能为空")
    c = connect()
    try:
        cur = c.execute("INSERT INTO method_groups(name, sort, created_at) VALUES(?,?,?)",
                        (name, 100, now_str()))
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def rename_group(gid, name=None, sort=None):
    c = connect()
    try:
        if name is not None:
            name = name.strip()
            if not name:
                raise ValueError("分组名不能为空")
            c.execute("UPDATE method_groups SET name=? WHERE id=?", (name, gid))
        if sort is not None:
            c.execute("UPDATE method_groups SET sort=? WHERE id=?", (sort, gid))
        c.commit()
    finally:
        c.close()


def delete_group(gid):
    """删分组：里面的方法移到「未分组」，不删内容。"""
    c = connect()
    try:
        row = c.execute("SELECT id FROM method_groups WHERE name=? LIMIT 1", ("未分组",)).fetchone()
        if row:
            fallback = row["id"]
        else:
            fallback = c.execute("INSERT INTO method_groups(name, sort, created_at) VALUES(?,?,?)",
                                 ("未分组", 0, now_str())).lastrowid
        c.execute("UPDATE methods SET group_id=? WHERE group_id=?", (fallback, gid))
        c.execute("DELETE FROM method_groups WHERE id=?", (gid,))
        c.commit()
    finally:
        c.close()


# ---------------------------------------------------------------- 方法
def list_methods(keyword="", category=""):
    c = connect()
    try:
        sql = ("SELECT m.*, (SELECT COUNT(*) FROM method_files f WHERE f.method_id=m.id) AS files "
               "FROM methods m WHERE 1=1")
        args = []
        if keyword:
            sql += " AND (m.title LIKE ? OR m.summary LIKE ? OR m.content LIKE ? OR m.tags LIKE ?)"
            like = "%" + keyword + "%"
            args += [like, like, like, like]
        if category:
            sql += " AND m.category = ?"
            args.append(category)
        sql += " ORDER BY m.updated_at DESC, m.id DESC"
        return [dict(r) for r in c.execute(sql, args)]
    finally:
        c.close()


def get_method(mid):
    c = connect()
    try:
        row = c.execute("SELECT * FROM methods WHERE id=?", (mid,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["attachments"] = [dict(r) for r in c.execute(
            "SELECT id, filename, size, mime, created_at FROM method_files"
            " WHERE method_id=? ORDER BY id", (mid,))]
        return d
    finally:
        c.close()


def create_method(title, category="", summary="", content="", tags="", author="", group_id=None):
    title = (title or "").strip()
    if not title:
        raise ValueError("标题不能为空")
    if len(title) > 120:
        raise ValueError("标题太长（最多 120 字）")
    ts = now_str()
    gid = None if group_id in (None, "", 0, "0") else int(group_id)
    c = connect()
    try:
        cur = c.execute(
            "INSERT INTO methods(title, category, summary, content, tags, author, group_id, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (title, (category or "").strip(), (summary or "").strip(),
             content or "", (tags or "").strip(), (author or "").strip(), gid, ts, ts))
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def update_method(mid, title=None, category=None, summary=None, content=None, tags=None, group_id=None):
    c = connect()
    try:
        if not c.execute("SELECT 1 FROM methods WHERE id=?", (mid,)).fetchone():
            raise ValueError("方法不存在")
        sets, args = [], []
        for key, val in (("title", title), ("category", category), ("summary", summary),
                         ("content", content), ("tags", tags)):
            if val is not None:
                sets.append("%s=?" % key)
                args.append(val.strip() if key != "content" else val)
        if group_id is not None:
            sets.append("group_id=?")
            args.append(None if group_id in ("", 0, "0") else int(group_id))
        if sets:
            sets.append("updated_at=?")
            args.append(now_str())
            args.append(mid)
            c.execute("UPDATE methods SET %s WHERE id=?" % ",".join(sets), args)
            c.commit()
    finally:
        c.close()


def delete_method(mid):
    c = connect()
    try:
        files = [dict(r) for r in c.execute(
            "SELECT stored FROM method_files WHERE method_id=?", (mid,))]
        c.execute("DELETE FROM method_files WHERE method_id=?", (mid,))
        c.execute("DELETE FROM methods WHERE id=?", (mid,))
        c.commit()
    finally:
        c.close()
    for f in files:
        try:
            os.remove(os.path.join(UPLOAD_DIR, f["stored"]))
        except OSError:
            pass
    folder = os.path.join(UPLOAD_DIR, str(mid))
    shutil.rmtree(folder, ignore_errors=True)
    return len(files)


# ---------------------------------------------------------------- 附件
_SAFE = re.compile(r"[^\w\.\-\u4e00-\u9fa5]+")


def safe_name(name):
    base = os.path.basename(name or "file")
    base = _SAFE.sub("_", base).strip("._") or "file"
    return base[:80]


def save_file(mid, filename, data, mime=""):
    if not data:
        raise ValueError("文件是空的")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("文件太大（最多 %d MB）" % (MAX_FILE_BYTES // 1024 // 1024))
    c = connect()
    try:
        if not c.execute("SELECT 1 FROM methods WHERE id=?", (mid,)).fetchone():
            raise ValueError("方法不存在")
    finally:
        c.close()

    folder = os.path.join(UPLOAD_DIR, str(mid))
    os.makedirs(folder, exist_ok=True)
    clean = safe_name(filename)
    stored = "%s_%s" % (uuid.uuid4().hex[:12], clean)
    with open(os.path.join(folder, stored), "wb") as f:
        f.write(data)

    c = connect()
    try:
        cur = c.execute(
            "INSERT INTO method_files(method_id, filename, stored, size, mime, created_at)"
            " VALUES(?,?,?,?,?,?)",
            (mid, clean, "%d/%s" % (mid, stored), len(data), mime or "", now_str()))
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def get_file(fid):
    c = connect()
    try:
        row = c.execute("SELECT * FROM method_files WHERE id=?", (fid,)).fetchone()
        return dict(row) if row else None
    finally:
        c.close()


def file_path(rec):
    return os.path.join(UPLOAD_DIR, rec["stored"])


def delete_file(fid):
    rec = get_file(fid)
    if not rec:
        return False
    c = connect()
    try:
        c.execute("DELETE FROM method_files WHERE id=?", (fid,))
        c.commit()
    finally:
        c.close()
    try:
        os.remove(file_path(rec))
    except OSError:
        pass
    return True
