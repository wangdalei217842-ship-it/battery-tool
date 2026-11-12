# -*- coding: utf-8 -*-
"""账号与会话（纯标准库，无第三方依赖）

设计：
  - 账号库 data/accounts.db 与业务库完全分开，业务的每个用户一个独立库
    data/users/u{id}.db —— 物理隔离，不可能串数据
  - 密码用 scrypt 加盐哈希，明文不落盘
  - 会话 token 存 sessions 表，默认 30 天；管理员可借用他人身份查看（acting_as）
"""

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
ACCOUNTS_DB = os.environ.get("BT_ACCOUNTS_DB") or os.path.join(DATA_DIR, "accounts.db")
USERS_DIR = os.environ.get("BT_USERS_DIR") or os.path.join(DATA_DIR, "users")

SESSION_DAYS = 30
ROLE_ADMIN = "admin"
ROLE_USER = "user"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    display TEXT DEFAULT '',
    role TEXT NOT NULL DEFAULT 'user',
    pass_hash TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    beta INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    last_login TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    acting_as INTEGER,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_session_user ON sessions(user_id);
"""


def now_str():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _conn():
    os.makedirs(os.path.dirname(ACCOUNTS_DB), exist_ok=True)
    c = sqlite3.connect(ACCOUNTS_DB, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode = WAL")
    return c


def init_db():
    os.makedirs(USERS_DIR, exist_ok=True)
    c = _conn()
    try:
        c.executescript(SCHEMA)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(users)")}
        if "beta" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN beta INTEGER NOT NULL DEFAULT 0")
        c.commit()
    finally:
        c.close()


def set_beta(uid, beta):
    """内测标记：能看新功能（如报账）。"""
    c = _conn()
    try:
        c.execute("UPDATE users SET beta=? WHERE id=?", (1 if beta else 0, uid))
        c.commit()
    finally:
        c.close()


# ---------------------------------------------------------------- 密码
def _hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt),
                        n=16384, r=8, p=1, dklen=32)
    return "%s$%s" % (salt, dk.hex())


def _verify_hash(password, stored):
    try:
        salt = stored.split("$", 1)[0]
    except Exception:
        return False
    return hmac.compare_digest(_hash(password, salt), stored)


def check_password_strength(password):
    if len(password or "") < 6:
        raise ValueError("密码至少 6 位")
    if len(password) > 128:
        raise ValueError("密码太长")
    return True


# ---------------------------------------------------------------- 用户
def _row_to_user(row):
    if not row:
        return None
    d = dict(row)
    d.pop("pass_hash", None)
    return d


def user_db(uid):
    """某个用户的业务库路径。"""
    return os.path.join(USERS_DIR, "u%d.db" % int(uid))


def create_user(username, password, display="", role=ROLE_USER):
    username = (username or "").strip()
    if not (1 <= len(username) <= 24):
        raise ValueError("用户名长度需在 1-24 个字符之间")
    if role not in (ROLE_ADMIN, ROLE_USER):
        raise ValueError("角色不合法")
    check_password_strength(password)
    c = _conn()
    try:
        cur = c.execute(
            "INSERT INTO users(username, display, role, pass_hash, active, created_at)"
            " VALUES(?,?,?,?,1,?)",
            (username, (display or "").strip(), role, _hash(password), now_str()),
        )
        c.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        raise ValueError("用户名已存在：%s" % username)
    finally:
        c.close()


def list_users():
    c = _conn()
    try:
        rows = c.execute(
            "SELECT id, username, display, role, active, beta, created_at, last_login"
            " FROM users ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def get_user(uid):
    c = _conn()
    try:
        return _row_to_user(c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())
    finally:
        c.close()


def get_user_by_name(username):
    c = _conn()
    try:
        return _row_to_user(
            c.execute("SELECT * FROM users WHERE username=?", ((username or "").strip(),)).fetchone()
        )
    finally:
        c.close()


def verify(username, password):
    """校验用户名密码，成功返回用户信息（不含哈希），失败返回 None。"""
    c = _conn()
    try:
        row = c.execute("SELECT * FROM users WHERE username=?",
                        ((username or "").strip(),)).fetchone()
        if not row:
            return None
        if not row["active"]:
            return None
        if not _verify_hash(password or "", row["pass_hash"]):
            return None
        c.execute("UPDATE users SET last_login=? WHERE id=?", (now_str(), row["id"]))
        c.commit()
        return _row_to_user(row)
    finally:
        c.close()


def change_password(uid, new_password):
    check_password_strength(new_password)
    c = _conn()
    try:
        c.execute("UPDATE users SET pass_hash=? WHERE id=?", (_hash(new_password), uid))
        c.commit()
        # 改密码后踢掉所有旧会话
        c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        c.commit()
    finally:
        c.close()


def set_active(uid, active):
    c = _conn()
    try:
        c.execute("UPDATE users SET active=? WHERE id=?", (1 if active else 0, uid))
        if not active:
            c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        c.commit()
    finally:
        c.close()


def delete_user(uid):
    """删账号。

    会话一并清掉；数据文件由调用方（server 的删除接口）负责移走，
    这里不碰磁盘，避免误删。
    """
    c = _conn()
    try:
        c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        c.execute("DELETE FROM users WHERE id=?", (uid,))
        c.commit()
    finally:
        c.close()


def count_admins():
    c = _conn()
    try:
        return c.execute("SELECT COUNT(*) c FROM users WHERE role=? AND active=1",
                         (ROLE_ADMIN,)).fetchone()["c"]
    finally:
        c.close()


def count_users():
    c = _conn()
    try:
        return c.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    finally:
        c.close()


def ensure_admin():
    """没有任何账号时创建一个管理员，返回 (用户名, 初始密码)；已有账号则返回 None。"""
    if count_users() > 0:
        return None
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    password = "".join(secrets.choice(alphabet) for _ in range(10))
    create_user("admin", password, display="管理员", role=ROLE_ADMIN)
    # 除了控制台输出，再落一份到文件，方便第一次找密码
    try:
        with open(os.path.join(os.path.dirname(ACCOUNTS_DB), "admin-password.txt"),
                  "w", encoding="utf-8") as f:
            f.write("username: admin\npassword: %s\n" % password)
    except Exception:
        pass
    return "admin", password


# ---------------------------------------------------------------- 会话
def create_session(uid, acting_as=None):
    token = secrets.token_urlsafe(32)
    expires = (datetime.now() + timedelta(days=SESSION_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    c = _conn()
    try:
        c.execute(
            "INSERT INTO sessions(token, user_id, acting_as, created_at, expires_at)"
            " VALUES(?,?,?,?,?)",
            (token, uid, acting_as, now_str(), expires),
        )
        c.commit()
    finally:
        c.close()
    return token


def get_session(token):
    """返回 {token, user_id, acting_as, user: {...}}，无效或过期返回 None。"""
    if not token:
        return None
    c = _conn()
    try:
        row = c.execute("SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
        if not row:
            return None
        if row["expires_at"] < now_str():
            c.execute("DELETE FROM sessions WHERE token=?", (token,))
            c.commit()
            return None
        urow = c.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
        if not urow or not urow["active"]:
            return None
        return {
            "token": token,
            "user_id": row["user_id"],
            "acting_as": row["acting_as"],
            "user": _row_to_user(urow),
        }
    finally:
        c.close()


def set_acting(token, acting_as):
    c = _conn()
    try:
        c.execute("UPDATE sessions SET acting_as=? WHERE token=?", (acting_as, token))
        c.commit()
    finally:
        c.close()


def delete_session(token):
    if not token:
        return
    c = _conn()
    try:
        c.execute("DELETE FROM sessions WHERE token=?", (token,))
        c.commit()
    finally:
        c.close()


def purge_expired():
    c = _conn()
    try:
        cur = c.execute("DELETE FROM sessions WHERE expires_at < ?", (now_str(),))
        c.commit()
        return cur.rowcount
    finally:
        c.close()
