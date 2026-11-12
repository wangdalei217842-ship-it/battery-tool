# -*- coding: utf-8 -*-
"""battery-tracker 后端服务（纯标准库 http.server，无第三方依赖）"""

import json
import os
import re
import sys
import time
import threading
import mimetypes
import posixpath
import hashlib
import html as html_lib
import socketserver
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote, quote

import store
import accounts
import methods
import expenses
import lab
import tests
import ndax

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(ROOT, "static")

# 默认只允许本机；设 BT_HOST=0.0.0.0 可让同一 WiFi 下的手机访问
HOST = (os.environ.get("BT_HOST") or "127.0.0.1").strip()
PORT = int(os.environ.get("BT_PORT", "8787"))
# 多用户登录：账号密码见 accounts.py；旧的单一访问密码（pin.txt）已停用
PIN = ""            # 兼容旧代码的占位，不再参与鉴权
PIN_TOKEN = ""
PROBE_HOST = "127.0.0.1"

# 部署在 HTTPS 反向代理后面时设 BT_SECURE=1，登录 cookie 才会带 Secure 标记
COOKIE_SECURE = (os.environ.get("BT_SECURE") or "").strip() in ("1", "true", "yes", "on")

COOKIE_NAME = "bt_sid"

# ndax 解析的并发闸门：2 核机器上同时跑 2 个就到顶了
_PARSE_GATE = threading.Semaphore(2)

# 登录失败限速：同一个 IP+用户名 在窗口期内失败太多次就暂时锁掉，防止公网暴力猜密码
LOGIN_MAX_FAILS = int(os.environ.get("BT_LOGIN_MAX_FAILS", "10"))
LOGIN_WINDOW = int(os.environ.get("BT_LOGIN_WINDOW", "600"))   # 秒
_login_fails = {}
_login_lock = threading.Lock()


def _login_rate_ok(ip):
    now = time.time()
    with _login_lock:
        arr = [t for t in _login_fails.get(ip, []) if now - t < LOGIN_WINDOW]
        _login_fails[ip] = arr
        return len(arr) < LOGIN_MAX_FAILS


def _login_fail(ip):
    with _login_lock:
        _login_fails.setdefault(ip, []).append(time.time())
        # 记录表别无限长：超过 2000 个 IP 就丢掉最早的一半
        if len(_login_fails) > 2000:
            for k in list(_login_fails)[:1000]:
                _login_fails.pop(k, None)


def _login_reset(ip):
    with _login_lock:
        _login_fails.pop(ip, None)

# PWA 需要正确的 manifest MIME 类型，标准库的映射表里没有 .webmanifest
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("image/png", ".png")
mimetypes.add_type("text/javascript", ".js")

ROUTES = []

# 已经初始化过表结构的用户库，避免每个请求都跑一次建表脚本
_ready_dbs = set()


def route(method, pattern):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn))
        return fn
    return deco


class ApiError(Exception):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.message = message
        self.code = code


def need(data, key, label=None):
    v = (data or {}).get(key)
    if v is None or str(v).strip() == "":
        raise ApiError("缺少必填项：%s" % (label or key))
    return str(v).strip()


# ------------------------------------------------------------------ routes

@route("GET", r"/api/ping")
def api_ping(h, m, body):
    return {"ok": True, "time": store.now_str()}


@route("GET", r"/api/state")
def api_state(h, m, body):
    conn = h.conn
    return store.build_state(conn)


@route("POST", r"/api/channels")
def api_channel_create(h, m, body):
    conn = h.conn
    code = need(body, "code", "通道编号")
    gid = body.get("group_id")
    cid = store.create_channel(conn, code, body.get("name", ""), body.get("note", ""),
                               group_id=int(gid) if gid else None)
    return {"id": cid, "channel": store.channel_by_id(conn, cid)}


@route("POST", r"/api/channels/create-batch")
def api_channel_create_batch(h, m, body):
    conn = h.conn
    codes = body.get("codes")
    if not isinstance(codes, list):
        raise ApiError("codes 必须是数组")
    gid = body.get("group_id")
    gid = int(gid) if gid else None
    created = store.create_channels_bulk(conn, codes, gid)
    return {"created": created}


@route("POST", r"/api/channels/bulk")
def api_channel_bulk(h, m, body):
    conn = h.conn
    spec = need(body, "spec", "通道编号")
    gid = body.get("group_id")
    gid = int(gid) if gid else None
    codes = store.expand_range(spec)
    if not codes:
        raise ApiError("没有解析出任何通道编号")
    created, skipped = [], []
    for code in codes:
        try:
            store.create_channel(conn, code, body.get("name", ""), group_id=gid)
            created.append(code)
        except ValueError:
            skipped.append(code)
    return {"created": created, "skipped": skipped}


@route("PUT", r"/api/channels/(\d+)")
def api_channel_update(h, m, body):
    conn = h.conn
    cid = int(m.group(1))
    ch = store.channel_by_id(conn, cid)
    if not ch:
        raise ApiError("通道不存在", 404)
    code = (body.get("code") or ch["code"]).strip()
    if code != ch["code"] and store.channel_by_code(conn, code):
        raise ApiError("通道编号 %s 已存在" % code)
    gid = body.get("group_id", ch["group_id"]) if "group_id" in body else ch["group_id"]
    conn.execute(
        "UPDATE channels SET code=?, name=?, note=?, group_id=?, sort_order=?, archived=? WHERE id=?",
        (
            code,
            body.get("name", ch["name"]),
            body.get("note", ch["note"]),
            int(gid) if gid else None,
            body.get("sort_order", ch["sort_order"]),
            int(body.get("archived", ch["archived"]) or 0),
            cid,
        ),
    )
    return {"channel": store.channel_by_id(conn, cid)}


@route("POST", r"/api/groups/reorder")
def api_group_reorder(h, m, body):
    ids = body.get("ids") or []
    store.reorder_groups(h.conn, ids)
    return {"ok": True, "count": len(ids)}


@route("POST", r"/api/channels/reorder")
def api_channel_reorder(h, m, body):
    blocks = body.get("groups") or []
    if not isinstance(blocks, list):
        raise ApiError("groups 必须是数组")
    n = store.reorder_channels(h.conn, blocks)
    return {"ok": True, "updated": n}


@route("POST", r"/api/groups")
def api_group_create(h, m, body):
    name = need(body, "name", "分组名称")
    gid = store.create_group(h.conn, name)
    return {"group": store.group_by_id(h.conn, gid)}


@route("PUT", r"/api/groups/(\d+)")
def api_group_update(h, m, body):
    gid = int(m.group(1))
    store.update_group(h.conn, gid, name=body.get("name"), sort_order=body.get("sort_order"))
    return {"group": store.group_by_id(h.conn, gid)}


@route("DELETE", r"/api/groups/(\d+)")
def api_group_delete(h, m, body):
    conn = h.conn
    gid = int(m.group(1))
    g = store.group_by_id(conn, gid)
    if not g:
        raise ApiError("分组不存在", 404)
    st = store.delete_group(conn, gid)
    parts = []
    if st["archived"]:
        parts.append("%d 个归档" % st["archived"])
    if st["deleted"]:
        parts.append("%d 个彻底删除" % st["deleted"])
    msg = "分组「%s」已删除" % g["name"]
    msg += ("，组内通道：" + "、".join(parts)) if parts else "（组里没有通道）"
    if st["checked_out"]:
        msg += "；%s 已自动下机" % "、".join(st["checked_out"])
    return {"deleted": gid, "channels": st, "message": msg}


@route("DELETE", r"/api/channels/(\d+)")
def api_channel_delete(h, m, body):
    conn = h.conn
    cid = int(m.group(1))
    ch = store.channel_by_id(conn, cid)
    if not ch:
        raise ApiError("通道不存在", 404)
    res = store.delete_channel(conn, cid)
    tail = ("，%s 已自动下机" % res["checked_out"]) if res["checked_out"] else ""
    if res["archived"]:
        return {"archived": True, "checked_out": res["checked_out"],
                "message": "通道 %s 已归档%s（历史 %d 条装机 / %d 条报送保留，可在设置里恢复）"
                           % (ch["code"], tail, res["placements"], res["snapshots"])}
    return {"archived": False, "checked_out": res["checked_out"],
            "message": "通道 %s 已彻底删除%s" % (ch["code"], tail)}


@route("POST", r"/api/channels/(\d+)/swap")
def api_swap(h, m, body):
    conn = h.conn
    cid = int(m.group(1))
    if not store.channel_by_id(conn, cid):
        raise ApiError("通道不存在", 404)
    code = need(body, "battery_code", "电池编号")
    res = store.swap_battery(
        conn, cid, code,
        batch=body.get("batch", ""),
        model=body.get("model", ""),
        rated_capacity=body.get("rated_capacity") or None,
        battery_note=body.get("battery_note", ""),
        started_at=body.get("started_at"),
        prev_ended_at=body.get("prev_ended_at"),
        result=body.get("result", ""),
        note=body.get("note", ""),
        source=body.get("source", "web"),
    )
    return res


@route("POST", r"/api/channels/(\d+)/checkout")
def api_checkout(h, m, body):
    conn = h.conn
    cid = int(m.group(1))
    res = store.checkout_channel(conn, cid, body.get("result", ""), body.get("note", ""), body.get("ended_at"))
    return res


@route("GET", r"/api/channels/(\d+)/timeline")
def api_timeline(h, m, body):
    return store.channel_timeline(h.conn, int(m.group(1)))


@route("POST", r"/api/snapshots")
def api_snapshot_create(h, m, body):
    conn = h.conn
    cid = body.get("channel_id")
    if not cid and body.get("channel"):
        ch = store.channel_by_code(conn, body["channel"])
        if not ch:
            raise ApiError("通道 %s 不存在" % body["channel"])
        cid = ch["id"]
    if not cid:
        raise ApiError("缺少通道")
    r = store.add_snapshot(
        conn, int(cid),
        values=body.get("values") or {},
        status=body.get("status", ""),
        note=body.get("note", ""),
        taken_at=body.get("taken_at"),
        source=body.get("source", "web"),
    )
    return {"id": r["id"], "skipped_fields": r["skipped"]}


@route("DELETE", r"/api/snapshots/(\d+)")
def api_snapshot_delete(h, m, body):
    conn = h.conn
    sid = int(m.group(1))
    conn.execute("DELETE FROM snapshot_values WHERE snapshot_id=?", (sid,))
    conn.execute("DELETE FROM snapshots WHERE id=?", (sid,))
    return {"deleted": sid}


@route("GET", r"/api/snapshots")
def api_snapshot_list(h, m, body):
    q = h.query
    conn = h.conn
    rows = store.query_snapshots(
        conn,
        channel_id=int(q["channel_id"]) if q.get("channel_id") else None,
        battery_id=int(q["battery_id"]) if q.get("battery_id") else None,
        batch=q.get("batch") or None,
        date_from=q.get("from") or None,
        date_to=q.get("to") or None,
        status=q.get("status") or None,
        limit=int(q.get("limit", 300)),
    )
    return {"items": rows}


@route("GET", r"/api/batteries")
def api_battery_list(h, m, body):
    return {"items": store.search_batteries(h.conn, h.query.get("q", ""), int(h.query.get("limit", 200)))}


@route("POST", r"/api/batteries")
def api_battery_create(h, m, body):
    conn = h.conn
    code = need(body, "code", "电池编号")
    b, existed = store.ensure_battery(
        conn, code,
        batch=body.get("batch", ""),
        model=body.get("model", ""),
        rated_capacity=body.get("rated_capacity") or None,
        note=body.get("note", ""),
    )
    return {"battery": b, "existed": existed}


@route("GET", r"/api/batteries/(\d+)")
def api_battery_profile(h, m, body):
    return store.battery_profile(h.conn, int(m.group(1)))


@route("GET", r"/api/compare")
def api_compare(h, m, body):
    return {"items": store.batch_compare(h.conn)}


@route("POST", r"/api/fields")
def api_field_create(h, m, body):
    conn = h.conn
    label = need(body, "label", "字段名称")
    f = store.create_field(
        conn,
        body.get("key", ""),
        label,
        body.get("unit", ""),
        body.get("kind", "number"),
        body.get("options", ""),
        int(body.get("required", 0) or 0),
    )
    return {"field": f}


@route("PUT", r"/api/fields/(\d+)")
def api_field_update(h, m, body):
    conn = h.conn
    fid = int(m.group(1))
    store.update_field(
        conn, fid,
        label=body.get("label"),
        unit=body.get("unit"),
        kind=body.get("kind"),
        options=body.get("options"),
        required=body.get("required"),
        sort_order=body.get("sort_order"),
        active=body.get("active"),
    )
    return {"ok": True}


@route("DELETE", r"/api/fields/(\d+)")
def api_field_delete(h, m, body):
    store.delete_field(h.conn, int(m.group(1)))
    return {"ok": True}


@route("PUT", r"/api/settings")
def api_settings(h, m, body):
    conn = h.conn
    if "app_name" in body:
        store.set_setting(conn, "app_name", str(body["app_name"]))
    if "status_options" in body:
        opts = body["status_options"]
        if isinstance(opts, str):
            opts = [x.strip() for x in re.split(r"[,，\n]", opts) if x.strip()]
        store.set_setting(conn, "status_options", json.dumps(opts, ensure_ascii=False))
    return {"settings": store.get_settings(conn)}


@route("POST", r"/api/reset")
def api_reset(h, m, body):
    conn = h.conn
    confirm = str(body.get("confirm", "")).strip()
    if confirm not in ("清空", "RESET", "reset"):
        raise ApiError("清空操作需要确认：请提交 confirm=清空")
    for t in ("snapshot_values", "snapshots", "placements", "batteries", "channels"):
        conn.execute("DELETE FROM %s" % t)
    try:
        conn.execute(
            "DELETE FROM sqlite_sequence WHERE name IN"
            " ('snapshot_values','snapshots','placements','batteries','channels')"
        )
    except Exception:
        pass
    return {"ok": True, "message": "已清空全部通道、电池、装机与报送记录（字段和设置保留）"}


@route("GET", r"/api/export/snapshots\.csv")
def api_export_snapshots(h, m, body):
    conn = h.conn
    q = h.query
    rows = store.query_snapshots(
        conn,
        channel_id=int(q["channel_id"]) if q.get("channel_id") else None,
        battery_id=int(q["battery_id"]) if q.get("battery_id") else None,
        batch=q.get("batch") or None,
        date_from=q.get("from") or None,
        date_to=q.get("to") or None,
        limit=int(q.get("limit", 100000)),
    )
    fields = store.list_fields(conn)
    head = ["时间", "通道", "电池编号", "批次", "状态", "备注", "来源"] + [
        f["label"] + (("(" + f["unit"] + ")") if f["unit"] else "") for f in fields
    ]
    lines = [",".join(_csv(x) for x in head)]
    for r in rows:
        vmap = {v["key"]: v["value"] for v in r["values"]}
        line = [
            r["taken_at"], r["channel_code"], r["battery_code"] or "", r["battery_batch"] or "",
            r["status"] or "", r["note"] or "", r["source"] or "",
        ] + ["" if vmap.get(f["key"]) is None else vmap.get(f["key"]) for f in fields]
        lines.append(",".join(_csv(x) for x in line))
    return CsvResponse("\n".join(lines), "snapshots.csv")


@route("GET", r"/api/export/placements\.csv")
def api_export_placements(h, m, body):
    conn = h.conn
    rows = conn.execute(
        "SELECT p.*, c.code channel_code, b.code battery_code, b.batch, b.model"
        " FROM placements p JOIN channels c ON c.id=p.channel_id JOIN batteries b ON b.id=p.battery_id"
        " ORDER BY p.started_at DESC"
    ).fetchall()
    head = ["上机时间", "下机时间", "通道", "电池编号", "批次", "型号", "结果", "备注"]
    lines = [",".join(_csv(x) for x in head)]
    for r in rows:
        lines.append(",".join(_csv(x) for x in [
            r["started_at"], r["ended_at"] or "", r["channel_code"], r["battery_code"],
            r["batch"] or "", r["model"] or "", r["result"] or "", (r["note"] or "").replace("\n", " "),
        ]))
    return CsvResponse("\n".join(lines), "placements.csv")


def _csv(v):
    s = "" if v is None else str(v)
    if re.search(r'[",\n]', s):
        return '"' + s.replace('"', '""') + '"'
    return s


LOGIN_PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>需要密码 · battery技术</title>
<style>
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f5f6f8;
font-family:"Segoe UI","Microsoft YaHei",system-ui,sans-serif}
.box{background:#fff;padding:28px 26px;border-radius:12px;width:min(340px,88vw);text-align:center;
border:1px solid #e4e7ec;box-shadow:0 16px 48px rgba(16,24,40,.10)}
h1{font-size:16px;margin:0 0 6px;color:#101828;letter-spacing:-.2px}
p{font-size:13px;color:#667085;margin:0 0 18px}
input{width:100%;box-sizing:border-box;padding:11px 13px;font-size:15px;border:1px solid #e4e7ec;
border-radius:8px;outline:none;margin-bottom:9px;font-family:inherit}
input:focus{border-color:#2c4a7c;box-shadow:0 0 0 3px rgba(44,74,124,.12)}
button{margin-top:13px;width:100%;padding:12px;font-size:15px;border:0;border-radius:8px;
background:#2c4a7c;color:#fff;cursor:pointer;font-family:inherit}
.err{color:#dc2626;font-size:12.5px;margin-top:10px;min-height:17px}
</style></head><body>
<form class="box" method="POST" action="/api/login-form">
  <h1>battery技术</h1>
  <p>请登录你的账号</p>
  <input name="username" placeholder="用户名" autofocus autocomplete="username" required>
  <input name="password" type="password" placeholder="密码" autocomplete="current-password" required>
  <button type="submit">登录</button>
  <div class="err">__ERR__</div>
</form>
</body></html>"""


class CsvResponse:
    def __init__(self, text, filename):
        self.body = ("\ufeff" + text).encode("utf-8")
        self.filename = filename


# ------------------------------------------------------------------ handler

class Handler(BaseHTTPRequestHandler):
    server_version = "BatteryTracker/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if os.environ.get("BT_VERBOSE"):
            sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None, cache="no-store"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        for k, v in (extra or {}).items():
            if isinstance(v, (list, tuple)):
                for item in v:
                    self.send_header(k, item)
            else:
                self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str))

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            raise ApiError("请求体不是合法 JSON")

    def _client_ip(self):
        """真实客户端 IP。经反向代理时只信任来自本机的 X-Real-IP，防止伪造绕过限速。"""
        ip = self.client_address[0]
        if ip in ("127.0.0.1", "::1"):
            fwd = (self.headers.get("X-Real-IP") or "").strip()
            if fwd:
                return fwd
        return ip

    def _cookie(self, name):
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v.strip()
        return ""

    def _session(self):
        """当前会话（含登录用户和「以谁的身份查看」），未登录返回 None。"""
        if not hasattr(self, "_sess_cache"):
            self._sess_cache = accounts.get_session(self._cookie(COOKIE_NAME))
        return self._sess_cache

    @staticmethod
    def _session_cookie(token):
        return {
            "Set-Cookie": "%s=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Lax%s"
                          % (COOKIE_NAME, token, accounts.SESSION_DAYS * 86400,
                             "; Secure" if COOKIE_SECURE else "")
        }

    def _open_user_db(self, uid):
        """把当前请求绑定到某个用户的业务库（首次访问时自动建表）。"""
        path = accounts.user_db(uid)
        store.use_db(path)
        if path not in _ready_dbs:
            store.init_db()
            _ready_dbs.add(path)
        self.conn = store.connect()

    def _handle(self, method):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        self.query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        self.conn = None
        try:
            # ---------- 公开接口 ----------
            if path == "/api/ping":
                self._json(200, {"ok": True, "time": store.now_str()})
                return
            if path == "/api/login" and method == "POST":
                self._api_login()
                return
            if path == "/api/logout" and method in ("POST", "GET"):
                self._api_logout()
                return
            if path == "/api/login-form" and method == "POST":
                self._login_form()
                return

            sess = self._session()
            if not sess:
                if path.startswith("/api/"):
                    self._json(401, {"error": "请先登录"})
                    return
                if path in ("/", "", "/index.html"):
                    err = html_lib.escape(self.query.get("err", ""))
                    self._send(200, LOGIN_PAGE.replace("__ERR__", err), "text/html; charset=utf-8")
                    return
                # 静态资源放行，保证登录页图标和 PWA 的 manifest / sw.js 能拿到
                if method == "GET":
                    self._static(path)
                    return
                self._json(401, {"error": "请先登录"})
                return

            # ---------- 账号相关（不碰业务库）----------
            if path == "/api/me" and method == "GET":
                self._api_me(sess)
                return
            if path == "/api/me/password" and method == "POST":
                self._api_change_password(sess)
                return
            if path.startswith("/api/admin/"):
                self._api_admin(sess, path, method)
                return

            # ---------- 实验方法库（团队共享，不跟个人库走）----------
            if path == "/api/method-categories" or path.startswith("/api/method-groups") \
                    or path.startswith("/api/methods"):
                self._api_methods(sess, path, method)
                return
            if path.startswith("/files/"):
                self._api_file(sess, path, method)
                return
            if path.startswith("/api/expenses") or path.startswith("/expense-files/"):
                self._api_expenses(sess, path, method)
                return
            if path.startswith("/api/drugs"):
                self._api_drugs(sess, path, method)
                return

            # ---------- 业务：绑定「当前正在看的那个人」的库 ----------
            self._open_user_db(self._acting_uid(sess) or sess["user_id"])

            # ---------- 充放电测试数据（存在用户自己的库里）----------
            if path.startswith("/api/tests"):
                self._api_tests(sess, path, method)
                return

            if path.startswith("/api/"):
                body = self._body() if method in ("POST", "PUT", "PATCH", "DELETE") else {}
                for m_method, rx, fn in ROUTES:
                    if m_method != method:
                        continue
                    mo = rx.match(path)
                    if mo:
                        result = fn(self, mo, body)
                        self.conn.commit()
                        if isinstance(result, CsvResponse):
                            self._send(200, result.body, "text/csv; charset=utf-8",
                                       {"Content-Disposition": 'attachment; filename="%s"' % result.filename})
                        else:
                            self._json(200, result)
                        return
                raise ApiError("接口不存在：%s %s" % (method, path), 404)
            if method == "GET":
                self._static(path)
            else:
                raise ApiError("方法不支持", 405)
        except ApiError as e:
            self._json(e.code, {"error": e.message})
        except ValueError as e:
            if self.conn is not None:
                self.conn.rollback()
            self._json(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            if self.conn is not None:
                self.conn.rollback()
            self._json(500, {"error": "服务器内部错误：%s" % e})
        finally:
            if self.conn is not None:
                self.conn.close()

    # ---------------------------------------------------------- 认证接口
    def _clear_cookies(self):
        """同时清除 host-only 与 Domain 两种同名 cookie。

        浏览器里可能因为历史原因并存两份 bt_sid，只清一份的话，
        另一份会在下次请求里把旧身份带回来，表现就是「登录了 A 还是 B」。
        """
        host = (self.headers.get("Host") or "").split(":")[0]
        dead = "Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT; HttpOnly; SameSite=Lax"
        out = [
            "%s=; Path=/; %s" % (COOKIE_NAME, dead),
            "bt_as=; Path=/; %s" % dead,
        ]
        if host:
            out.append("%s=; Path=/; Domain=%s; %s" % (COOKIE_NAME, host, dead))
        return out

    def _login_form(self):
        """表单登录：返回 302。

        浏览器在跟随重定向前就会把 Set-Cookie 写进 cookie jar，
        不像 fetch 那样存在「响应头已到、cookie 还没落地」的竞态。
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(min(length, 8192)).decode("utf-8", "ignore")
        data = {k: v[0] for k, v in parse_qs(raw).items()}
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""

        if not username or not password:
            self._redirect("/?err=" + quote("请输入用户名和密码"))
            return
        key = "%s|%s" % (self._client_ip(), username.lower())
        if not _login_rate_ok(key):
            self._redirect("/?err=" + quote("尝试次数过多，请 10 分钟后再试"))
            return
        user = accounts.verify(username, password)
        if not user:
            _login_fail(key)
            self._redirect("/?err=" + quote("用户名或密码不对"))
            return
        _login_reset(key)
        old = self._cookie(COOKIE_NAME)
        if old:
            accounts.delete_session(old)
        token = accounts.create_session(user["id"])

        # 微信内置浏览器（X5 内核）对「302 + Set-Cookie」不可靠：有时不落 cookie 就跟随跳转，
        # 导致跳过去还是登录页。改成返回极简页面，让浏览器先处理 Set-Cookie，再用 JS 跳转。
        body = ('<!doctype html><meta charset="utf-8"><title>\u767b\u5f55\u6210\u529f</title>'
                '<script>location.replace("/")</script>'
                '<p style="font:16px system-ui,-apple-system,sans-serif;padding:48px 24px;'
                'text-align:center;color:#475467">\u767b\u5f55\u6210\u529f\uff0c\u6b63\u5728\u8fdb\u5165\u2026</p>'
                ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for item in self._clear_cookies():
            self.send_header("Set-Cookie", item)
        self.send_header("Set-Cookie", self._session_cookie(token)["Set-Cookie"])
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _api_login(self):
        ip = self._client_ip()
        data = self._body()
        username = str(data.get("username", "")).strip()
        password = str(data.get("password", ""))
        if not username or not password:
            raise ApiError("请输入用户名和密码", 400)
        key = "%s|%s" % (ip, username.lower())
        if not _login_rate_ok(key):
            raise ApiError("尝试次数过多，请 10 分钟后再试", 429)
        user = accounts.verify(username, password)
        if not user:
            _login_fail(key)
            raise ApiError("用户名或密码不对", 401)
        _login_reset(key)
        # 关键：先作废这个浏览器带过来的旧会话。
        # 否则一旦新 Cookie 没被浏览器接受，页面会继续以「上一个账号」的身份运行，
        # 表现就是「登录了 A，进去还是 B」。
        old_token = self._cookie(COOKIE_NAME)
        if old_token:
            accounts.delete_session(old_token)
        token = accounts.create_session(user["id"])
        self._send(200, json.dumps({"ok": True, "user": user}, ensure_ascii=False),
                   "application/json; charset=utf-8", self._session_cookie(token))

    def _api_logout(self):
        token = self._cookie(COOKIE_NAME)
        accounts.delete_session(token)
        self._send(200, json.dumps({"ok": True, "had_cookie": bool(token)}, ensure_ascii=False),
                   "application/json; charset=utf-8",
                   {"Set-Cookie": self._clear_cookies()})

    def _acting_uid(self, sess):
        """管理员「查看他人数据」的当前对象。

        状态放在 cookie（bt_as）而不是 session 表里：写库再读会有时序问题，
        表现为「点完切换没反应」。这个 cookie 只有管理员会话才被认，伪造无效。
        """
        if sess["user"]["role"] != accounts.ROLE_ADMIN:
            return None
        raw = (self._cookie("bt_as") or "").strip()
        if not raw.isdigit():
            return None
        uid = int(raw)
        if uid == sess["user_id"]:
            return None
        return uid if accounts.get_user(uid) else None

    @staticmethod
    def _acting_cookie(uid):
        if uid is None:
            return {"Set-Cookie": "bt_as=; Path=/; Max-Age=0; "
                                  "Expires=Thu, 01 Jan 1970 00:00:00 GMT; HttpOnly; SameSite=Lax"}
        return {"Set-Cookie": "bt_as=%d; Path=/; Max-Age=%d; HttpOnly; SameSite=Lax%s"
                              % (uid, accounts.SESSION_DAYS * 86400,
                                 "; Secure" if COOKIE_SECURE else "")}

    def _api_me(self, sess):
        me = sess["user"]
        acting_uid = self._acting_uid(sess)
        acting = accounts.get_user(acting_uid) if acting_uid else None
        self._json(200, {
            "user": me,
            "is_admin": me["role"] == accounts.ROLE_ADMIN,
            "acting_as": acting,
            "db_user_id": acting_uid or sess["user_id"],
        })

    def _api_change_password(self, sess):
        data = self._body()
        me = sess["user"]
        old = str(data.get("old", ""))
        new = str(data.get("new", ""))
        if not accounts.verify(me["username"], old):
            raise ApiError("原密码不对", 400)
        accounts.change_password(me["id"], new)   # 注意：会清掉所有会话
        token = accounts.create_session(me["id"])
        self._send(200, json.dumps({"ok": True}), "application/json; charset=utf-8",
                   self._session_cookie(token))

    # ---------------------------------------------------------- 充放电测试数据

    def _sync_test_report(self, conn, tid):
        """把某份 ndax 的末圈数据写成该通道的报送。重复调用会替换掉旧的自动报送。"""
        rec = tests.get_test(conn, tid) or {}
        cid = rec.get("channel_id")
        if not cid:
            return None
        cyc = [c for c in (rec.get("cycle_rows") or []) if (c.get("discharge_mAh") or 0) > 0]
        if not cyc:
            return None
        last = cyc[-1]
        mass = rec.get("mass_mg") or 0
        vals = {"cycle": last["cycle"]}
        if mass > 0:
            vals["specific_capacity"] = round(last["discharge_mAh"] / (mass / 1000.0), 2)
        if last.get("ce_pct") is not None:
            vals["coulombic_efficiency"] = round(last["ce_pct"], 2)
        # 先清掉这条通道之前的自动报送，避免每点一次堆一条
        for old in conn.execute(
                "SELECT id FROM snapshots WHERE channel_id=? AND source='ndax'", (cid,)).fetchall():
            conn.execute("DELETE FROM snapshot_values WHERE snapshot_id=?", (old["id"],))
            conn.execute("DELETE FROM snapshots WHERE id=?", (old["id"],))
        conn.commit()
        store.add_snapshot(conn, cid, values=vals, status="运行中",
                           note="ndax 同步（第 %d 圈）" % last["cycle"], source="ndax")
        conn.commit()
        return {"channel_id": cid, "cycle": last["cycle"], "values": vals}

    def _api_tests(self, sess, path, method):
        conn = self.conn

        if path == "/api/tests" and method == "GET":
            cid = self.query.get("channel_id")
            rows = tests.list_tests(conn, int(cid) if cid and cid.isdigit() else None)
            self._json(200, {"tests": rows})
            return

        if path == "/api/tests/upload" and method == "POST":
            parts = self._parse_multipart(max_bytes=200 * 1024 * 1024)
            f = next((p for p in parts if p["filename"]), None)
            if not f:
                raise ApiError("没有收到文件", 400)
            name = f["filename"]
            if not name.lower().endswith(".ndax"):
                raise ApiError("只支持新威的 .ndax 文件", 400)

            os.makedirs(methods.UPLOAD_DIR, exist_ok=True)
            tmp = os.path.join(methods.UPLOAD_DIR, "_tmp_%d.ndax" % int(time.time() * 1000))
            with open(tmp, "wb") as fh:
                fh.write(f["data"])
            # 解析是 CPU 密集的（8 万点约 1.3 秒），并发多了会把 2 核占满，
            # 这里限制同时最多 2 个，其余的排队等，超时就明确告知。
            got = _PARSE_GATE.acquire(timeout=30)
            if not got:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise ApiError("服务器正在处理其他文件，请稍后再试", 503)
            try:
                meta, rows = ndax.read_ndax(tmp)
            except Exception as exc:
                raise ApiError("解析失败：%s" % exc, 400)
            finally:
                _PARSE_GATE.release()
                try:
                    os.remove(tmp)
                except OSError:
                    pass

            tid, info = tests.save_test(conn, meta, rows, name)
            want_cid = self.query.get("channel_id")
            removed = 0
            synced = None
            # 没有显式传通道时，用 ndax 里自动匹配到的那条
            if not want_cid:
                rec0 = tests.get_test(conn, tid) or {}
                want_cid = rec0.get("channel_id")
            if want_cid:
                try:
                    cid = int(want_cid)
                    tests.link_test(conn, tid, cid)
                    # 一个通道只保留最新一份，旧的连同点位一起清掉，省空间
                    for row in conn.execute(
                            "SELECT id FROM tests WHERE channel_id=? AND id<>?", (cid, tid)).fetchall():
                        tests.delete_test(conn, row["id"])
                        removed += 1
                    synced = self._sync_test_report(conn, tid)
                except Exception as exc:
                    sys.stderr.write("[ndax] 绑定/同步失败: %r\n" % (exc,))
                    import traceback
                    traceback.print_exc()
            self._json(200, {
                "ok": True, "id": tid, "channel_code": info["channel_code"],
                "cycles": info["cycles"], "points": info["points"],
                "rows": meta.get("rows"), "start": meta.get("start"), "end": meta.get("end"),
                "nominal_mAh": meta.get("nominal_mAh"),
                "setpoints_A": meta.get("setpoints_A"),
                "ndc_version": meta.get("ndc_version"),
                "removed_old": removed, "synced": synced,
            })
            return

        mo = re.match(r"^/api/tests/(\d+)$", path)
        if mo:
            tid = int(mo.group(1))
            if method == "GET":
                rec = tests.get_test(conn, tid)
                if not rec:
                    raise ApiError("测试记录不存在", 404)
                self._json(200, {"test": rec})
                return
            if method == "DELETE":
                tests.delete_test(conn, tid)
                self._json(200, {"ok": True})
                return

        mo = re.match(r"^/api/tests/(\d+)/dqdv$", path)
        if mo and method == "GET":
            cyc = self.query.get("cycle")
            self._json(200, tests.get_dqdv(conn, int(mo.group(1)), int(cyc) if cyc else None))
            return

        mo = re.match(r"^/api/tests/(\d+)/sync$", path)
        if mo and method == "POST":
            got = self._sync_test_report(conn, int(mo.group(1)))
            if not got:
                raise ApiError("这份数据还没关联通道，或没有有效循环", 400)
            self._json(200, {"ok": True, "synced": got})
            return

        mo = re.match(r"^/api/tests/(\d+)/mass$", path)
        if mo and method == "PUT":
            tests.set_mass(conn, int(mo.group(1)), self._body().get("mass_mg"))
            self._json(200, {"ok": True})
            return

        mo = re.match(r"^/api/tests/(\d+)/curve$", path)
        if mo and method == "GET":
            c = self.query.get("cycle")
            self._json(200, tests.get_curve(conn, int(mo.group(1)),
                                            int(c) if c and c.isdigit() else None))
            return

        mo = re.match(r"^/api/tests/(\d+)/channel$", path)
        if mo and method == "PUT":
            body = self._body()
            cid = body.get("channel_id")
            tests.link_test(conn, int(mo.group(1)), int(cid) if cid else None)
            self._json(200, {"ok": True})
            return

        raise ApiError("接口不存在：%s %s" % (method, path), 404)

    # ---------------------------------------------------------- 实验方法库
    def _api_methods(self, sess, path, method):
        me = sess["user"]
        author = me.get("display") or me.get("username") or ""

        if path == "/api/method-categories" and method == "GET":
            self._json(200, {"categories": methods.DEFAULT_CATEGORIES})
            return

        if path == "/api/method-groups" and method == "GET":
            self._json(200, {"groups": methods.list_groups()})
            return

        if path == "/api/method-groups" and method == "POST":
            gid = methods.create_group(self._body().get("name"))
            self._json(200, {"ok": True, "id": gid, "groups": methods.list_groups()})
            return

        mg = re.match(r"^/api/method-groups/(\d+)$", path)
        if mg:
            gid = int(mg.group(1))
            if method == "PUT":
                data = self._body()
                methods.rename_group(gid, data.get("name"), data.get("sort"))
                self._json(200, {"ok": True, "groups": methods.list_groups()})
                return
            if method == "DELETE":
                methods.delete_group(gid)
                self._json(200, {"ok": True, "groups": methods.list_groups()})
                return

        if path == "/api/methods" and method == "GET":
            rows = methods.list_methods(self.query.get("q", ""), self.query.get("category", ""))
            self._json(200, {"methods": rows})
            return

        if path == "/api/methods" and method == "POST":
            data = self._body()
            mid = methods.create_method(
                data.get("title"), data.get("category"), data.get("summary"),
                data.get("content"), data.get("tags"), author, data.get("group_id"))
            self._json(200, {"ok": True, "id": mid, "method": methods.get_method(mid)})
            return

        mo = re.match(r"^/api/methods/(\d+)$", path)
        if mo:
            mid = int(mo.group(1))
            if method == "GET":
                rec = methods.get_method(mid)
                if not rec:
                    raise ApiError("方法不存在", 404)
                self._json(200, {"method": rec})
                return
            if method == "PUT":
                data = self._body()
                methods.update_method(mid, data.get("title"), data.get("category"),
                                      data.get("summary"), data.get("content"), data.get("tags"),
                                      data.get("group_id"))
                self._json(200, {"ok": True, "method": methods.get_method(mid)})
                return
            if method == "DELETE":
                n = methods.delete_method(mid)
                self._json(200, {"ok": True, "deleted_files": n})
                return

        mo = re.match(r"^/api/methods/(\d+)/files$", path)
        if mo and method == "POST":
            mid = int(mo.group(1))
            saved = []
            for p in self._parse_multipart():
                if not p["filename"]:
                    continue
                saved.append(methods.save_file(mid, p["filename"], p["data"], p["mime"]))
            if not saved:
                raise ApiError("没有收到文件", 400)
            self._json(200, {"ok": True, "saved": saved, "method": methods.get_method(mid)})
            return

        mo = re.match(r"^/api/methods/files/(\d+)$", path)
        if mo and method == "DELETE":
            self._json(200, {"ok": methods.delete_file(int(mo.group(1)))})
            return

        raise ApiError("接口不存在：%s %s" % (method, path), 404)



    def _api_drugs(self, sess, path, method):
        """药品台账：同为内测/管理员可见。"""
        me = sess["user"]
        if me.get("role") != "admin" and not me.get("beta"):
            raise ApiError("药品管理只对内测用户开放", 403)

        if path == "/api/drugs" and method == "GET":
            self._json(200, lab.list_drugs(self.query.get("q", ""), self.query.get("category", "")))
            return

        if path == "/api/drugs" and method == "POST":
            data = self._body()
            same = lab.find_same_name(data.get("name"))
            did = lab.create_drug(data, me.get("display") or me.get("username") or "")
            self._json(200, {"ok": True, "id": did, "drug": lab.get_drug(did),
                             "same_name": same})
            return

        if path == "/api/drugs/check" and method == "GET":
            self._json(200, {"items": lab.find_same_name(self.query.get("name", ""))})
            return

        if path == "/api/drugs/stats" and method == "GET":
            self._json(200, {"stats": lab.stats(), "categories": lab.CATEGORIES})
            return

        mo = re.match(r"^/api/drugs/(\d+)$", path)
        if mo:
            did = int(mo.group(1))
            if method == "GET":
                rec = lab.get_drug(did)
                if not rec:
                    raise ApiError("药品不存在", 404)
                self._json(200, {"drug": rec})
                return
            if method == "PUT":
                lab.update_drug(did, self._body())
                self._json(200, {"ok": True, "drug": lab.get_drug(did)})
                return
            if method == "DELETE":
                lab.delete_drug(did)
                self._json(200, {"ok": True})
                return

        raise ApiError("接口不存在：%s %s" % (method, path), 404)

    def _api_expenses(self, sess, path, method):
        """组内报账：只有管理员和内测用户能用。"""
        me = sess["user"]
        if me.get("role") != "admin" and not me.get("beta"):
            raise ApiError("报账功能只对内测用户开放", 403)

        if path == "/api/expenses/export-zip" and method == "GET":
            only_pending = self.query.get("scope", "pending") == "pending"
            raw_ids = self.query.get("ids", "")
            ids = [int(x) for x in raw_ids.split(",") if x.strip().isdigit()] if raw_ids else None
            body = expenses.export_zip(self.query.get("q", ""), self.query.get("month", ""),
                                      only_pending, ids)
            mm = (self.query.get("month") or "").strip() or datetime.now().strftime("%Y-%m")
            name = "\u62a5\u8d26\u6253\u5305-%s.zip" % mm
            self._send(200, body, "application/zip", {
                "Content-Disposition": "attachment; filename*=UTF-8''%s" % quote(name),
            })
            return

        if path == "/api/expenses/export" and method == "GET":
            only_pending = self.query.get("scope", "pending") == "pending"
            body = expenses.export_csv(self.query.get("q", ""), self.query.get("month", ""), only_pending)
            name = "\u62a5\u8d26\u660e\u7ec6-%s.csv" % datetime.now().strftime("%Y%m%d")
            self._send(200, body, "text/csv; charset=utf-8", {
                "Content-Disposition": "attachment; filename*=UTF-8''%s" % quote(name),
            })
            return

        if path == "/api/expenses" and method == "GET":
            only_pending = self.query.get("scope", "all") == "pending"
            self._json(200, expenses.list_expenses(
                self.query.get("q", ""), self.query.get("month", ""), only_pending=only_pending))
            return

        if path == "/api/expenses" and method == "POST":
            data = self._body()
            eid = expenses.create_expense(
                data.get("invoice_date"), data.get("item"), data.get("amount"),
                data.get("note", ""), me.get("display") or me.get("username") or "")
            self._json(200, {"ok": True, "id": eid, "expense": expenses.get_expense(eid)})
            return

        mo = re.match(r"^/api/expenses/(\d+)$", path)
        if mo:
            eid = int(mo.group(1))
            if method == "GET":
                rec = expenses.get_expense(eid)
                if not rec:
                    raise ApiError("报账记录不存在", 404)
                self._json(200, {"expense": rec})
                return
            if method == "PUT":
                data = self._body()
                expenses.update_expense(eid, data.get("invoice_date"), data.get("item"),
                                        data.get("amount"), data.get("note"))
                self._json(200, {"ok": True, "expense": expenses.get_expense(eid)})
                return
            if method == "DELETE":
                if me.get("role") != "admin":
                    raise ApiError("只有管理员可以删除报账记录", 403)
                n = expenses.delete_expense(eid)
                self._json(200, {"ok": True, "deleted_files": n})
                return

        mo = re.match(r"^/api/expenses/(\d+)/reimburse$", path)
        if mo and method == "POST":
            if me.get("role") != "admin":
                raise ApiError("只有管理员可以确认报销状态", 403)
            eid = int(mo.group(1))
            expenses.set_reimbursed(eid, bool(self._body().get("done", True)))
            self._json(200, {"ok": True, "expense": expenses.get_expense(eid)})
            return

        mo = re.match(r"^/api/expenses/(\d+)/files$", path)
        if mo and method == "POST":
            eid = int(mo.group(1))
            rec = expenses.get_expense(eid)
            if not rec:
                raise ApiError("报账记录不存在", 404)
            parts = self._parse_multipart(expenses.MAX_PDF)
            if not parts:
                raise ApiError("没有收到文件", 400)
            part = parts[0]
            # 系统按「开票日期-物品-金额元.pdf」自动命名，不用用户自己改
            name = expenses.display_pdf_name(rec["invoice_date"], rec["item"], rec["amount"])
            saved = expenses.save_pdf(eid, name, part["data"], part["mime"])
            self._json(200, {"ok": True, "file": saved, "expense": expenses.get_expense(eid)})
            return

        mo = re.match(r"^/api/expenses/files/(\d+)$", path)
        if mo and method == "DELETE":
            expenses.delete_file(int(mo.group(1)))
            self._json(200, {"ok": True})
            return

        mo = re.match(r"^/expense-files/(\d+)$", path)
        if mo and method == "GET":
            rec = expenses.get_file(int(mo.group(1)))
            if not rec:
                raise ApiError("文件不存在", 404)
            full = os.path.join(expenses.UPLOAD_DIR, rec["stored"])
            if not os.path.isfile(full):
                raise ApiError("文件已丢失", 404)
            with open(full, "rb") as fh:
                data = fh.read()
            self._send(200, data, "application/pdf", {
                "Content-Disposition": "inline; filename*=UTF-8''%s" % quote(rec["filename"]),
            }, cache="private, max-age=600")
            return

        raise ApiError("接口不存在：%s %s" % (method, path), 404)

    def _parse_multipart(self, max_bytes=None):
        """极简 multipart/form-data 解析，够收附件用。"""
        limit = max_bytes or methods.MAX_FILE_BYTES
        ctype = self.headers.get("Content-Type", "")
        mo = re.search(r'boundary="?([^";]+)"?', ctype)
        if not mo:
            raise ApiError("不是 multipart 请求", 400)
        boundary = mo.group(1).strip().encode("utf-8")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            raise ApiError("没有收到文件", 400)
        if length > limit + 2 * 1024 * 1024:
            raise ApiError("文件太大（最多 %d MB）" % (limit // 1024 // 1024), 413)

        body = self.rfile.read(length)
        parts = []
        for chunk in body.split(b"--" + boundary):
            chunk = chunk.strip(b"\r\n")
            if not chunk or chunk == b"--":
                continue
            head, sep, data = chunk.partition(b"\r\n\r\n")
            if not sep:
                continue
            head_text = head.decode("utf-8", "ignore")
            fn = re.search(r'filename="([^"]*)"', head_text)
            ct = re.search(r"Content-Type:\s*([^\r\n]+)", head_text)
            parts.append({
                "filename": (fn.group(1) if fn else "").strip(),
                "mime": (ct.group(1).strip() if ct else ""),
                "data": data,
            })
        return parts

    def _api_file(self, sess, path, method):
        mo = re.match(r"^/files/(\d+)$", path)
        if not mo or method != "GET":
            raise ApiError("接口不存在：%s %s" % (method, path), 404)
        rec = methods.get_file(int(mo.group(1)))
        if not rec:
            raise ApiError("文件不存在", 404)
        fp = methods.file_path(rec)
        if not os.path.isfile(fp):
            raise ApiError("文件已丢失，可能被手动清理过", 404)
        with open(fp, "rb") as f:
            data = f.read()
        inline = rec["mime"] in methods.INLINE_TYPES
        # HTTP 头只能放 ASCII：中文名走 RFC 5987 的 filename*，filename 只留 ASCII 兜底，
        # 否则 send_header 会因 latin-1 编码失败，响应在中途被截断。
        ascii_name = "".join(ch for ch in rec["filename"] if ord(ch) < 128).strip() or "file"
        self._send(200, data, rec["mime"] or "application/octet-stream",
                   {"Content-Disposition":
                    '%s; filename="%s"; filename*=UTF-8\'\'%s'
                    % ("inline" if inline else "attachment", ascii_name, quote(rec["filename"]))},
                   cache="private, max-age=600")

    def _api_admin(self, sess, path, method):
        me = sess["user"]
        if me["role"] != accounts.ROLE_ADMIN:
            raise ApiError("需要管理员权限", 403)

        if path == "/api/admin/users" and method == "GET":
            rows = accounts.list_users()
            for r in rows:
                r["db_exists"] = os.path.exists(accounts.user_db(r["id"]))
            self._json(200, {"users": rows, "me": me["id"], "acting_as": self._acting_uid(sess)})
            return

        if path == "/api/admin/users" and method == "POST":
            data = self._body()
            uid = accounts.create_user(data.get("username", ""), data.get("password", ""),
                                       data.get("display", ""), accounts.ROLE_USER)
            self._open_user_db(uid)
            self.conn.close()
            self.conn = None
            self._json(200, {"ok": True, "id": uid})
            return

        m = re.match(r"^/api/admin/users/(\d+)/beta$", path)
        if m:
            accounts.set_beta(int(m.group(1)), bool(self._body().get("beta")))
            self._json(200, {"ok": True})
            return

        m = re.match(r"^/api/admin/users/(\d+)/(password|active|delete)$", path)
        if m:
            uid, action = int(m.group(1)), m.group(2)
            if uid == me["id"] and action in ("active", "delete"):
                raise ApiError("不能对自己执行这个操作", 400)
            if action == "password":
                accounts.change_password(uid, self._body().get("password", ""))
                self._json(200, {"ok": True})
                return
            if action == "active":
                accounts.set_active(uid, bool(self._body().get("active")))
                self._json(200, {"ok": True})
                return
            if action == "delete":
                path = accounts.user_db(uid)
                if os.path.isfile(path):
                    # 先留一份到备份目录再删，避免误删账号后数据找不回来
                    bak_dir = os.path.join(os.path.dirname(path), "..", "backups", "deleted-users")
                    os.makedirs(bak_dir, exist_ok=True)
                    stamp = time.strftime("%Y%m%d-%H%M%S")
                    try:
                        with open(path, "rb") as src, \
                                open(os.path.join(bak_dir, "u%d-%s.db" % (uid, stamp)), "wb") as dst:
                            dst.write(src.read())
                    except OSError:
                        pass
                    for suffix in ("", "-wal", "-shm"):
                        try:
                            os.remove(path + suffix)
                        except OSError:
                            pass
                accounts.delete_user(uid)
                self._json(200, {"ok": True, "data_removed": True})
                return

        if path == "/api/admin/act-as" and method == "POST":
            uid = self._body().get("user_id")
            if uid in (None, "", 0, "0"):
                accounts.set_acting(sess["token"], None)
                self._send(200, json.dumps({"ok": True, "acting_as": None}, ensure_ascii=False),
                           "application/json; charset=utf-8", self._acting_cookie(None))
                return
            uid = int(uid)
            target = accounts.get_user(uid)
            if not target:
                raise ApiError("用户不存在", 404)
            accounts.set_acting(sess["token"], uid)
            self._send(200,
                       json.dumps({"ok": True, "acting_as": uid,
                                   "name": target.get("display") or target.get("username")},
                                  ensure_ascii=False),
                       "application/json; charset=utf-8", self._acting_cookie(uid))
            return

        raise ApiError("接口不存在：%s %s" % (method, path), 404)

    def _static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        rel = posixpath.normpath(path).lstrip("/")
        full = os.path.abspath(os.path.join(STATIC_DIR, rel))
        if not full.startswith(STATIC_DIR + os.sep) and full != STATIC_DIR:
            self._send(403, "forbidden", "text/plain; charset=utf-8")
            return
        if not os.path.isfile(full):
            # 单页应用回退
            full = os.path.join(STATIC_DIR, "index.html")
            if not os.path.isfile(full):
                self._send(404, "not found", "text/plain; charset=utf-8")
                return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json",
                                                  "application/manifest+json"):
            ctype += "; charset=utf-8"
        name = os.path.basename(full).lower()
        # 入口页和 Service Worker 每次回源验证，保证发版后能立刻更新；其余静态资源可缓存
        cache = "no-cache" if name in ("index.html", "sw.js") else "public, max-age=3600"
        with open(full, "rb") as f:
            data = f.read()
        self._send(200, data, ctype, cache=cache)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")


class Server(ThreadingHTTPServer):
    # Windows 下默认允许两个 socket 绑同一端口（0.0.0.0 与 127.0.0.1 可以共存，
    # 谁收到连接不确定），必须显式独占，才能保证「端口被占用」能被正确检测。
    allow_reuse_address = os.name != "nt"
    daemon_threads = True

    def server_bind(self):
        if os.name == "nt":
            try:
                import socket
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except Exception:
                pass
        ThreadingHTTPServer.server_bind(self)


def _already_running(port):
    """端口被占用时，判断是不是本程序已经开着了。"""
    import urllib.request
    try:
        with urllib.request.urlopen("http://%s:%d/api/ping" % (PROBE_HOST, port), timeout=0.7) as r:
            return b'"ok"' in r.read()
    except Exception:
        return False


def local_ips():
    """本机局域网 IPv4 地址，默认出口网卡的排最前（最可能是手机该用的那个）。"""
    import socket
    primary = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))   # 不会真的发包，只是让系统选出口网卡
            primary = s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        pass

    found = []
    if primary and not primary.startswith("127."):
        found.append(primary)
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith("127.") or ip.startswith("169.254.") or ip in found:
                continue
            found.append(ip)
    except Exception:
        pass
    return found


def main():
    accounts.init_db()
    methods.init_db()
    expenses.init_db()
    lab.init_db()
    created = accounts.ensure_admin()
    if created:
        uname, upwd = created
        print("=" * 62)
        print("  首次启动，已创建管理员账号")
        print("  用户名: %s" % uname)
        print("  密码:   %s" % upwd)
        print("  密码也写进了 data/admin-password.txt，登录后请尽快改掉")
        print("=" * 62)
    # 现有账号的业务库先建好表
    for u in accounts.list_users():
        store.use_db(accounts.user_db(u["id"]))
        store.init_db()
        _ready_dbs.add(accounts.user_db(u["id"]))
    store.use_db(None)
    lan = HOST not in ("127.0.0.1", "localhost")
    port = PORT
    for attempt in range(20):
        try:
            httpd = Server((HOST, port), Handler)
            break
        except OSError:
            if _already_running(port):
                url = "http://%s:%d/" % (PROBE_HOST, port)
                print("已在运行：%s （无需重复启动，已在浏览器打开）" % url)
                if os.environ.get("BT_OPEN"):
                    import webbrowser
                    webbrowser.open(url)
                return
            port += 1
    else:
        print("找不到可用端口", file=sys.stderr)
        sys.exit(1)

    local_url = "http://127.0.0.1:%d/" % port
    print("=" * 62)
    print("  battery技术 已启动")
    print("  本机访问:  %s" % local_url)
    if lan:
        ips = local_ips()
        if ips:
            print("  手机访问:  http://%s:%d/    ← 手机连同一个 WiFi 打开" % (ips[0], port))
            if len(ips) > 1:
                print("  （其它网卡: %s，一般用上面那个）" % "、".join(ips[1:]))
        else:
            print("  手机访问:  没检测到局域网 IP，请确认电脑连了 WiFi/网线")
        if PIN:
            print("  访问密码:  已开启（手机首次打开需输入）")
        else:
            print("  登录方式:  用户名 + 密码（每人一个账号，数据各自独立）")
        print("  手机打不开？多半是 Windows 防火墙拦了，见 README「用手机打开」")
    print("  账号库:    %s" % accounts.ACCOUNTS_DB)
    print("  用户数据:  %s" % accounts.USERS_DIR)
    print("  按 Ctrl+C 停止")
    print("=" * 62, flush=True)
    if os.environ.get("BT_OPEN"):
        import threading
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(local_url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
