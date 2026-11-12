# -*- coding: utf-8 -*-
"""组内报账（共享库）

一条报账 = 开票日期 + 物品 + 金额 + 一张 PDF（发票）。
PDF 显示名按「20260929-称量纸-15.60元.pdf」，实际存储用随机名，避免重名覆盖。
"""
import csv
import io
import zipfile
import os
import re
import secrets
import sqlite3
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
DB_PATH = os.environ.get("BT_SHARED_DB") or os.path.join(DATA_DIR, "shared.db")
UPLOAD_DIR = os.environ.get("BT_UPLOAD_DIR") or os.path.join(DATA_DIR, "uploads")

MAX_PDF = 20 * 1024 * 1024

SCHEMA = """
CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_date TEXT NOT NULL,
    item TEXT NOT NULL,
    amount REAL NOT NULL DEFAULT 0,
    note TEXT DEFAULT '',
    author TEXT DEFAULT '',
    reimbursed INTEGER NOT NULL DEFAULT 0,
    reimbursed_at TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_expenses_date ON expenses(invoice_date);
CREATE TABLE IF NOT EXISTS expense_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    expense_id INTEGER NOT NULL,
    filename TEXT NOT NULL,
    stored TEXT NOT NULL,
    size INTEGER DEFAULT 0,
    mime TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_expense_files ON expense_files(expense_id);
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
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    c = connect()
    try:
        c.executescript(SCHEMA)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(expenses)")}
        if "reimbursed" not in cols:
            c.execute("ALTER TABLE expenses ADD COLUMN reimbursed INTEGER NOT NULL DEFAULT 0")
        if "reimbursed_at" not in cols:
            c.execute("ALTER TABLE expenses ADD COLUMN reimbursed_at TEXT DEFAULT ''")
        c.commit()
    finally:
        c.close()


def safe_name(text, limit=40):
    text = re.sub(r'[\\/:*?"<>|\r\n\t]', "", (text or "").strip())
    return text[:limit] or "未命名"


def display_pdf_name(invoice_date, item, amount):
    """2026-09-29 → 20260929-称量纸-15.60元.pdf"""
    d = (invoice_date or "").replace("-", "")
    return "%s-%s-%.2f元.pdf" % (d, safe_name(item), float(amount or 0))


def _attach_files(c, rows):
    if not rows:
        return rows
    ids = [r["id"] for r in rows]
    q = ",".join("?" * len(ids))
    files = {}
    for f in c.execute("SELECT * FROM expense_files WHERE expense_id IN (%s) ORDER BY id" % q, ids):
        files.setdefault(f["expense_id"], []).append({
            "id": f["id"], "filename": f["filename"], "size": f["size"], "mime": f["mime"],
        })
    for r in rows:
        r["files"] = files.get(r["id"], [])
    return rows


def list_expenses(keyword="", month="", limit=500, only_pending=False):
    c = connect()
    try:
        sql = "SELECT * FROM expenses WHERE 1=1"
        args = []
        if only_pending:
            sql += " AND reimbursed=0"
        if keyword:
            sql += " AND (item LIKE ? OR note LIKE ? OR author LIKE ?)"
            like = "%" + keyword + "%"
            args += [like, like, like]
        if month:
            sql += " AND substr(invoice_date,1,7)=?"
            args.append(month)
        sql += " ORDER BY invoice_date DESC, id DESC LIMIT ?"
        args.append(limit)
        rows = [dict(r) for r in c.execute(sql, args)]
        _attach_files(c, rows)
        total = sum(r["amount"] or 0 for r in rows)
        return {"items": rows, "total": round(total, 2), "count": len(rows)}
    finally:
        c.close()


def get_expense(eid):
    c = connect()
    try:
        row = c.execute("SELECT * FROM expenses WHERE id=?", (eid,)).fetchone()
        if not row:
            return None
        d = dict(row)
        _attach_files(c, [d])
        return d
    finally:
        c.close()


def create_expense(invoice_date, item, amount, note="", author=""):
    invoice_date = (invoice_date or "").strip()
    item = (item or "").strip()
    if not invoice_date:
        raise ValueError("请填开票日期")
    if not item:
        raise ValueError("请填物品名称")
    try:
        amount = round(float(amount), 2)
    except (TypeError, ValueError):
        raise ValueError("金额要填数字")
    if amount <= 0:
        raise ValueError("金额要大于 0")
    ts = now_str()
    c = connect()
    try:
        cur = c.execute(
            "INSERT INTO expenses(invoice_date,item,amount,note,author,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (invoice_date, item, amount, (note or "").strip(), (author or "").strip(), ts, ts))
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def csv_from_items(items):
    """三列：开票日期、物品、金额。带 BOM，Excel/WPS 双击打开不乱码。"""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["开票日期", "物品", "金额"])
    for it in items:
        w.writerow([it["invoice_date"], it["item"], "%.2f" % (it["amount"] or 0)])
    w.writerow([])
    w.writerow(["合计", "", "%.2f" % sum(it["amount"] or 0 for it in items)])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def export_csv(keyword="", month="", only_pending=False):
    data = list_expenses(keyword, month, limit=100000, only_pending=only_pending)
    return csv_from_items(data["items"])


def export_zip(keyword="", month="", only_pending=False, ids=None):
    """打包：一张汇总 CSV + 发票 PDF。传 ids 就只打包选中的那几条。"""
    if ids:
        items = [x for x in (get_expense(i) for i in ids) if x]
    else:
        items = list_expenses(keyword, month, limit=100000, only_pending=only_pending)["items"]
    buf = io.BytesIO()
    used = {}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("报账汇总.csv", csv_from_items(items))
        c = connect()
        try:
            for it in items:
                rows = c.execute("SELECT * FROM expense_files WHERE expense_id=? ORDER BY id", (it["id"],)).fetchall()
                for f in rows:
                    full = os.path.join(UPLOAD_DIR, f["stored"])
                    if not os.path.isfile(full):
                        continue
                    name = f["filename"]
                    # 同名发票加序号，避免覆盖
                    if name in used:
                        used[name] += 1
                        base, ext = os.path.splitext(name)
                        name = "%s(%d)%s" % (base, used[name], ext)
                    else:
                        used[name] = 1
                    with open(full, "rb") as fh:
                        z.writestr("发票/" + name, fh.read())
        finally:
            c.close()
    buf.seek(0)
    return buf.getvalue()


def set_reimbursed(eid, done=True):
    """报销完打个勾，避免重复报。"""
    c = connect()
    try:
        if not c.execute("SELECT 1 FROM expenses WHERE id=?", (eid,)).fetchone():
            raise ValueError("报账记录不存在")
        c.execute("UPDATE expenses SET reimbursed=?, reimbursed_at=?, updated_at=? WHERE id=?",
                  (1 if done else 0, now_str() if done else "", now_str(), eid))
        c.commit()
    finally:
        c.close()


def sync_filenames(c, eid):
    """修改日期/物品/金额后，把已上传 PDF 的显示名一起更新。"""
    row = c.execute("SELECT invoice_date, item, amount FROM expenses WHERE id=?", (eid,)).fetchone()
    if not row:
        return
    base = display_pdf_name(row["invoice_date"], row["item"], row["amount"])
    files = [dict(r) for r in c.execute(
        "SELECT id FROM expense_files WHERE expense_id=? ORDER BY id", (eid,))]
    for i, f in enumerate(files):
        name = base if i == 0 else base[:-4] + "(%d).pdf" % (i + 1)
        c.execute("UPDATE expense_files SET filename=? WHERE id=?", (name, f["id"]))


def update_expense(eid, invoice_date=None, item=None, amount=None, note=None):
    c = connect()
    try:
        if not c.execute("SELECT 1 FROM expenses WHERE id=?", (eid,)).fetchone():
            raise ValueError("报账记录不存在")
        sets, args = [], []
        if invoice_date is not None:
            sets.append("invoice_date=?")
            args.append(invoice_date.strip())
        if item is not None:
            sets.append("item=?")
            args.append(item.strip())
        if amount is not None:
            sets.append("amount=?")
            args.append(round(float(amount), 2))
        if note is not None:
            sets.append("note=?")
            args.append((note or "").strip())
        if sets:
            sets.append("updated_at=?")
            args.append(now_str())
            args.append(eid)
            c.execute("UPDATE expenses SET %s WHERE id=?" % ",".join(sets), args)
            sync_filenames(c, eid)
            c.commit()
    finally:
        c.close()


def delete_expense(eid):
    c = connect()
    try:
        files = [dict(r) for r in c.execute("SELECT * FROM expense_files WHERE expense_id=?", (eid,))]
        c.execute("DELETE FROM expense_files WHERE expense_id=?", (eid,))
        c.execute("DELETE FROM expenses WHERE id=?", (eid,))
        c.commit()
    finally:
        c.close()
    for f in files:
        try:
            os.remove(os.path.join(UPLOAD_DIR, f["stored"]))
        except OSError:
            pass
    folder = os.path.join(UPLOAD_DIR, "expenses", str(eid))
    try:
        if os.path.isdir(folder) and not os.listdir(folder):
            os.rmdir(folder)
    except OSError:
        pass
    return len(files)


def save_pdf(eid, filename, data, mime=""):
    if len(data) > MAX_PDF:
        raise ValueError("PDF 太大了（最多 20MB）")
    if not data.startswith(b"%PDF"):
        raise ValueError("只能上传 PDF 文件")
    rec = get_expense(eid)
    if not rec:
        raise ValueError("报账记录不存在")
    folder = os.path.join(UPLOAD_DIR, "expenses", str(eid))
    os.makedirs(folder, exist_ok=True)
    stored = os.path.join("expenses", str(eid), "%s.pdf" % secrets.token_hex(10))
    with open(os.path.join(UPLOAD_DIR, stored), "wb") as fh:
        fh.write(data)
    c = connect()
    try:
        cur = c.execute(
            "INSERT INTO expense_files(expense_id,filename,stored,size,mime,created_at)"
            " VALUES(?,?,?,?,?,?)",
            (eid, (filename or "invoice.pdf")[:120], stored, len(data), mime or "application/pdf", now_str()))
        c.commit()
        return {"id": cur.lastrowid, "filename": filename, "size": len(data)}
    finally:
        c.close()


def get_file(fid):
    c = connect()
    try:
        row = c.execute("SELECT * FROM expense_files WHERE id=?", (fid,)).fetchone()
        return dict(row) if row else None
    finally:
        c.close()


def delete_file(fid):
    rec = get_file(fid)
    if not rec:
        return None
    c = connect()
    try:
        c.execute("DELETE FROM expense_files WHERE id=?", (fid,))
        c.commit()
    finally:
        c.close()
    try:
        os.remove(os.path.join(UPLOAD_DIR, rec["stored"]))
    except OSError:
        pass
    return rec
