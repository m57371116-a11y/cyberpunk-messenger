#!/usr/bin/env python3
"""
سایبر پانک — سرور پیامرسان (نسخه پیشرفته)
اجرا:  python3 server.py
سپس مرورگر:  http://127.0.0.1:5000

قابلیت‌ها:
- چت خصوصی و گروهی
- ارسال عکس / ویدیو / فایل
- وضعیت آنلاین
- مدیریت گروه (ادمین، اعضا، خروج)
"""

import json
import os
import re
import sqlite3
import hashlib
import secrets
import time
import base64
import mimetypes
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote
from socketserver import ThreadingMixIn

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", 5000))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)

# محدودیت حجم فایل (بایت)
MAX_FILE_SIZE = 15 * 1024 * 1024  # 15 MB
ONLINE_THRESHOLD = 90  # ثانیه


def _resolve_db_path():
    if os.environ.get("CYBERPUNK_DB"):
        return os.environ["CYBERPUNK_DB"]
    local = os.path.join(BASE_DIR, "cyberpunk.db")
    try:
        os.makedirs(BASE_DIR, exist_ok=True)
        test = local + ".write_test"
        with open(test, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(test)
        return local
    except OSError:
        pass
    import tempfile
    return os.path.join(tempfile.gettempdir(), "cyberpunk_messenger.db")


DB_PATH = _resolve_db_path()


def get_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        name TEXT NOT NULL,
        bio TEXT DEFAULT '',
        created_at REAL NOT NULL,
        last_seen REAL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        created_at REAL NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS chats (
        id TEXT PRIMARY KEY,
        type TEXT NOT NULL,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
        creator_id TEXT NOT NULL,
        created_at REAL NOT NULL,
        last_message_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS chat_members (
        chat_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        deleted INTEGER DEFAULT 0,
        last_read_at REAL DEFAULT 0,
        role TEXT DEFAULT 'member',
        PRIMARY KEY (chat_id, user_id),
        FOREIGN KEY (chat_id) REFERENCES chats(id),
        FOREIGN KEY (user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY,
        chat_id TEXT NOT NULL,
        sender_id TEXT NOT NULL,
        text TEXT NOT NULL,
        ts REAL NOT NULL,
        edited INTEGER DEFAULT 0,
        deleted_for_all INTEGER DEFAULT 0,
        msg_type TEXT DEFAULT 'text',
        file_name TEXT DEFAULT '',
        file_size INTEGER DEFAULT 0,
        mime_type TEXT DEFAULT '',
        FOREIGN KEY (chat_id) REFERENCES chats(id),
        FOREIGN KEY (sender_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS message_deleted_for (
        message_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        PRIMARY KEY (message_id, user_id)
    );
    """)
    conn.commit()

    # مهاجرت ستون‌ها
    migrations = [
        ("users", "last_seen", "REAL DEFAULT 0"),
        ("chat_members", "last_read_at", "REAL DEFAULT 0"),
        ("chat_members", "role", "TEXT DEFAULT 'member'"),
        ("messages", "msg_type", "TEXT DEFAULT 'text'"),
        ("messages", "file_name", "TEXT DEFAULT ''"),
        ("messages", "file_size", "INTEGER DEFAULT 0"),
        ("messages", "mime_type", "TEXT DEFAULT ''"),
    ]
    for table, col, coltype in migrations:
        try:
            cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()]
            if col not in cols:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
                conn.commit()
        except Exception:
            pass

    # کاربران پیش‌فرض
    defaults = [
        ("u1", "demo", "1234", "کاربر دمو", "حساب آزمایشی"),
        ("u2", "alice", "1234", "آلیس", "علاقه‌مند به امنیت"),
        ("u3", "bob", "1234", "باب", "توسعه‌دهنده"),
        ("u4", "charlie", "1234", "چارلی", ""),
    ]
    for uid_, uname, pwd, name, bio in defaults:
        exists = c.execute("SELECT 1 FROM users WHERE username=?", (uname,)).fetchone()
        if not exists:
            c.execute(
                "INSERT INTO users (id, username, password, name, bio, created_at, last_seen) VALUES (?,?,?,?,?,?,?)",
                (uid_, uname, hash_pw(pwd), name, bio, time.time() - 86400 * 5, 0)
            )
    conn.commit()

    # چت‌های نمونه
    if not c.execute("SELECT 1 FROM chats LIMIT 1").fetchone():
        now = time.time()
        c.execute(
            "INSERT INTO chats (id, type, name, description, creator_id, created_at, last_message_at) VALUES (?,?,?,?,?,?,?)",
            ("c1", "private", "آلیس", "", "u1", now - 3600, now - 1000)
        )
        for mid in ("u1", "u2"):
            c.execute("INSERT INTO chat_members (chat_id, user_id, role) VALUES (?,?,?)",
                      ("c1", mid, "member"))
        c.execute(
            "INSERT INTO messages (id, chat_id, sender_id, text, ts) VALUES (?,?,?,?,?)",
            ("m1", "c1", "u2", "سلام! سرور پایتون آماده است.", now - 1200)
        )
        c.execute(
            "INSERT INTO messages (id, chat_id, sender_id, text, ts) VALUES (?,?,?,?,?)",
            ("m2", "c1", "u1", "عالی، چند نفره کار می‌کنه؟", now - 1000)
        )

        c.execute(
            "INSERT INTO chats (id, type, name, description, creator_id, created_at, last_message_at) VALUES (?,?,?,?,?,?,?)",
            ("c2", "group", "گروه امنیت", "بحث امنیت و هک اخلاقی", "u1", now - 7200, now - 2000)
        )
        for mid, role in [("u1", "admin"), ("u2", "member"), ("u3", "member"), ("u4", "member")]:
            c.execute("INSERT INTO chat_members (chat_id, user_id, role) VALUES (?,?,?)",
                      ("c2", mid, role))
        c.execute(
            "INSERT INTO messages (id, chat_id, sender_id, text, ts) VALUES (?,?,?,?,?)",
            ("m3", "c2", "u3", "سرور با SQLite و پایتون خالص راه افتاد.", now - 2000)
        )
        conn.commit()
    conn.close()
    print("[DB] آماده شد →", DB_PATH)


def hash_pw(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def uid() -> str:
    return secrets.token_hex(8)


def row_to_dict(row):
    if row is None:
        return None
    return dict(row)


def is_online(last_seen):
    if not last_seen:
        return False
    return (time.time() - float(last_seen)) < ONLINE_THRESHOLD


def user_public(u, include_online=True):
    if not u:
        return None
    d = {
        "id": u["id"],
        "username": u["username"],
        "name": u["name"],
        "bio": u.get("bio") or "",
        "created_at": u.get("created_at"),
    }
    if include_online:
        d["online"] = is_online(u.get("last_seen"))
        d["last_seen"] = u.get("last_seen") or 0
    return d


def get_user_from_token(token: str):
    if not token:
        return None
    conn = get_db()
    row = conn.execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token=?",
        (token,)
    ).fetchone()
    conn.close()
    return row_to_dict(row)


def safe_filename(name: str) -> str:
    name = os.path.basename(name or "file")
    name = re.sub(r"[^\w\.\-\u0600-\u06FF]", "_", name)
    return name[:120] or "file"


def save_base64_file(b64_data: str, original_name: str, msg_id: str) -> tuple:
    """ذخیره فایل از base64. برمی‌گرداند (relative_path, size, mime)"""
    if "," in b64_data:
        header, b64_data = b64_data.split(",", 1)
        mime = "application/octet-stream"
        m = re.search(r"data:([^;]+)", header)
        if m:
            mime = m.group(1)
    else:
        mime = mimetypes.guess_type(original_name)[0] or "application/octet-stream"

    try:
        raw = base64.b64decode(b64_data)
    except Exception:
        raise ValueError("داده فایل نامعتبر است")

    if len(raw) > MAX_FILE_SIZE:
        raise ValueError(f"حجم فایل بیشتر از {MAX_FILE_SIZE // (1024*1024)} مگابایت است")

    fname = safe_filename(original_name)
    stored = f"{msg_id}_{fname}"
    path = os.path.join(UPLOADS_DIR, stored)
    with open(path, "wb") as f:
        f.write(raw)
    return f"/uploads/{stored}", len(raw), mime


# ---------- API ----------

def api_register(data):
    username = (data.get("username") or "").strip().lower()
    name = (data.get("name") or "").strip()
    password = data.get("password") or ""
    if len(username) < 3:
        return 400, {"error": "نام کاربری حداقل ۳ کاراکتر"}
    if len(password) < 4:
        return 400, {"error": "رمز حداقل ۴ کاراکتر"}
    if not name:
        return 400, {"error": "نام نمایشی الزامی است"}
    conn = get_db()
    if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        conn.close()
        return 400, {"error": "این نام کاربری قبلاً گرفته شده"}
    user_id = uid()
    now = time.time()
    conn.execute(
        "INSERT INTO users (id, username, password, name, bio, created_at, last_seen) VALUES (?,?,?,?,?,?,?)",
        (user_id, username, hash_pw(password), name, "", now, now)
    )
    token = secrets.token_hex(24)
    conn.execute(
        "INSERT INTO sessions (token, user_id, created_at) VALUES (?,?,?)",
        (token, user_id, now)
    )
    conn.commit()
    user = row_to_dict(conn.execute(
        "SELECT id, username, name, bio, created_at, last_seen FROM users WHERE id=?", (user_id,)
    ).fetchone())
    conn.close()
    return 200, {"token": token, "user": user_public(user)}


def api_login(data):
    username = (data.get("username") or "").strip().lower()
    password = data.get("password") or ""
    conn = get_db()
    user = conn.execute(
        "SELECT * FROM users WHERE username=? AND password=?",
        (username, hash_pw(password))
    ).fetchone()
    if not user:
        conn.close()
        return 401, {"error": "نام کاربری یا رمز اشتباه است"}
    token = secrets.token_hex(24)
    now = time.time()
    conn.execute(
        "INSERT INTO sessions (token, user_id, created_at) VALUES (?,?,?)",
        (token, user["id"], now)
    )
    conn.execute("UPDATE users SET last_seen=? WHERE id=?", (now, user["id"]))
    conn.commit()
    user_d = user_public(row_to_dict(user))
    conn.close()
    return 200, {"token": token, "user": user_d}


def api_me(user):
    return 200, {"user": user_public(user)}


def api_heartbeat(user):
    conn = get_db()
    now = time.time()
    conn.execute("UPDATE users SET last_seen=? WHERE id=?", (now, user["id"]))
    conn.commit()
    conn.close()
    return 200, {"ok": True, "ts": now}


def api_users(user):
    conn = get_db()
    rows = conn.execute(
        "SELECT id, username, name, bio, created_at, last_seen FROM users ORDER BY name"
    ).fetchall()
    conn.close()
    return 200, {"users": [user_public(row_to_dict(r)) for r in rows]}


def api_update_profile(user, data):
    name = (data.get("name") or "").strip()
    bio = (data.get("bio") or "").strip()
    if not name:
        return 400, {"error": "نام الزامی است"}
    conn = get_db()
    conn.execute("UPDATE users SET name=?, bio=? WHERE id=?", (name, bio, user["id"]))
    conn.commit()
    u = row_to_dict(conn.execute(
        "SELECT id, username, name, bio, created_at, last_seen FROM users WHERE id=?", (user["id"],)
    ).fetchone())
    conn.close()
    return 200, {"user": user_public(u)}


def api_chats(user):
    conn = get_db()
    rows = conn.execute("""
        SELECT c.*, cm.last_read_at, cm.role
        FROM chats c
        JOIN chat_members cm ON cm.chat_id = c.id
        WHERE cm.user_id=? AND cm.deleted=0
        ORDER BY c.last_message_at DESC
    """, (user["id"],)).fetchall()
    result = []
    for r in rows:
        chat = row_to_dict(r)
        last_read = chat.pop("last_read_at", None) or 0
        my_role = chat.pop("role", "member")

        members_rows = conn.execute("""
            SELECT u.id, u.username, u.name, u.last_seen, cm.role
            FROM chat_members cm
            JOIN users u ON u.id = cm.user_id
            WHERE cm.chat_id=?
        """, (chat["id"],)).fetchall()
        chat["members"] = [m["id"] for m in members_rows]
        chat["members_detail"] = [
            {
                "id": m["id"],
                "username": m["username"],
                "name": m["name"],
                "online": is_online(m["last_seen"]),
                "role": m["role"] or "member",
            }
            for m in members_rows
        ]
        chat["my_role"] = my_role

        # برای چت خصوصی، نام و وضعیت طرف مقابل
        if chat["type"] == "private":
            other = next((m for m in members_rows if m["id"] != user["id"]), None)
            if other:
                chat["name"] = other["name"]
                chat["other_online"] = is_online(other["last_seen"])
                chat["other_id"] = other["id"]

        # پیش‌نمایش آخرین پیام
        msgs = conn.execute(
            "SELECT * FROM messages WHERE chat_id=? ORDER BY ts DESC LIMIT 40",
            (chat["id"],)
        ).fetchall()
        preview = ""
        last_ts = chat["last_message_at"]
        for m in msgs:
            if m["deleted_for_all"]:
                preview = "این پیام حذف شده"
                last_ts = m["ts"]
                break
            del_for = conn.execute(
                "SELECT 1 FROM message_deleted_for WHERE message_id=? AND user_id=?",
                (m["id"], user["id"])
            ).fetchone()
            if del_for:
                continue
            mt = m["msg_type"] or "text"
            if mt == "image":
                preview = "📷 تصویر"
            elif mt == "video":
                preview = "🎬 ویدیو"
            elif mt == "file":
                preview = "📎 " + (m["file_name"] or "فایل")
            else:
                preview = m["text"] or ""
            last_ts = m["ts"]
            break
        chat["preview"] = preview
        chat["last_message_at"] = last_ts

        unread = conn.execute("""
            SELECT COUNT(*) AS n FROM messages m
            WHERE m.chat_id=?
              AND m.sender_id != ?
              AND m.ts > ?
              AND m.deleted_for_all=0
              AND NOT EXISTS (
                SELECT 1 FROM message_deleted_for d
                WHERE d.message_id=m.id AND d.user_id=?
              )
        """, (chat["id"], user["id"], last_read, user["id"])).fetchone()
        chat["unread"] = unread["n"] if unread else 0
        result.append(chat)
    conn.close()
    return 200, {"chats": result}


def api_create_chat(user, data):
    chat_type = data.get("type") or "private"
    name = (data.get("name") or "").strip()
    description = (data.get("description") or "").strip()
    member_usernames = data.get("members") or []

    conn = get_db()
    member_ids = [user["id"]]

    if chat_type == "private":
        target = (data.get("username") or name or "").strip().lower()
        other = conn.execute("SELECT * FROM users WHERE username=?", (target,)).fetchone()
        if not other:
            conn.close()
            return 404, {"error": "کاربر یافت نشد"}
        if other["id"] == user["id"]:
            conn.close()
            return 400, {"error": "نمی‌توانید با خودتان چت کنید"}
        existing = conn.execute("""
            SELECT c.id FROM chats c
            JOIN chat_members a ON a.chat_id=c.id AND a.user_id=?
            JOIN chat_members b ON b.chat_id=c.id AND b.user_id=?
            WHERE c.type='private'
        """, (user["id"], other["id"])).fetchone()
        if existing:
            conn.execute(
                "UPDATE chat_members SET deleted=0 WHERE chat_id=? AND user_id=?",
                (existing["id"], user["id"])
            )
            conn.commit()
            chat = row_to_dict(conn.execute("SELECT * FROM chats WHERE id=?", (existing["id"],)).fetchone())
            members = conn.execute("SELECT user_id FROM chat_members WHERE chat_id=?", (existing["id"],)).fetchall()
            chat["members"] = [m["user_id"] for m in members]
            conn.close()
            return 200, {"chat": chat}
        name = other["name"]
        member_ids = [user["id"], other["id"]]
    else:
        if not name:
            conn.close()
            return 400, {"error": "نام گروه الزامی است"}
        for un in member_usernames:
            un = un.strip().lower()
            if not un:
                continue
            u = conn.execute("SELECT id FROM users WHERE username=?", (un,)).fetchone()
            if u and u["id"] not in member_ids:
                member_ids.append(u["id"])

    chat_id = uid()
    now = time.time()
    conn.execute(
        "INSERT INTO chats (id, type, name, description, creator_id, created_at, last_message_at) VALUES (?,?,?,?,?,?,?)",
        (chat_id, chat_type, name, description, user["id"], now, now)
    )
    for mid in member_ids:
        role = "admin" if mid == user["id"] and chat_type == "group" else "member"
        conn.execute(
            "INSERT INTO chat_members (chat_id, user_id, deleted, role) VALUES (?,?,0,?)",
            (chat_id, mid, role)
        )
    conn.commit()
    chat = row_to_dict(conn.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone())
    chat["members"] = member_ids
    conn.close()
    return 200, {"chat": chat}


def api_delete_chat(user, chat_id):
    """حذف برای خودم (leave یا hide)"""
    conn = get_db()
    m = conn.execute(
        "SELECT 1 FROM chat_members WHERE chat_id=? AND user_id=?",
        (chat_id, user["id"])
    ).fetchone()
    if not m:
        conn.close()
        return 404, {"error": "چت یافت نشد"}
    conn.execute(
        "UPDATE chat_members SET deleted=1 WHERE chat_id=? AND user_id=?",
        (chat_id, user["id"])
    )
    conn.commit()
    conn.close()
    return 200, {"ok": True}


def api_chat_info(user, chat_id):
    conn = get_db()
    m = conn.execute(
        "SELECT role FROM chat_members WHERE chat_id=? AND user_id=? AND deleted=0",
        (chat_id, user["id"])
    ).fetchone()
    if not m:
        conn.close()
        return 404, {"error": "چت یافت نشد"}
    chat = row_to_dict(conn.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone())
    members = conn.execute("""
        SELECT u.id, u.username, u.name, u.bio, u.last_seen, cm.role
        FROM chat_members cm
        JOIN users u ON u.id = cm.user_id
        WHERE cm.chat_id=?
        ORDER BY CASE cm.role WHEN 'admin' THEN 0 ELSE 1 END, u.name
    """, (chat_id,)).fetchall()
    chat["members_detail"] = [
        {
            "id": x["id"],
            "username": x["username"],
            "name": x["name"],
            "bio": x["bio"] or "",
            "online": is_online(x["last_seen"]),
            "last_seen": x["last_seen"] or 0,
            "role": x["role"] or "member",
        }
        for x in members
    ]
    chat["my_role"] = m["role"] or "member"
    conn.close()
    return 200, {"chat": chat}


def api_update_chat(user, chat_id, data):
    conn = get_db()
    m = conn.execute(
        "SELECT role FROM chat_members WHERE chat_id=? AND user_id=? AND deleted=0",
        (chat_id, user["id"])
    ).fetchone()
    if not m or m["role"] != "admin":
        conn.close()
        return 403, {"error": "فقط ادمین می‌تواند گروه را ویرایش کند"}
    chat = conn.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    if not chat or chat["type"] != "group":
        conn.close()
        return 400, {"error": "فقط گروه‌ها قابل ویرایش هستند"}
    name = (data.get("name") or chat["name"]).strip()
    description = (data.get("description") if "description" in data else chat["description"]) or ""
    if not name:
        conn.close()
        return 400, {"error": "نام نمی‌تواند خالی باشد"}
    conn.execute("UPDATE chats SET name=?, description=? WHERE id=?", (name, description, chat_id))
    conn.commit()
    chat = row_to_dict(conn.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone())
    conn.close()
    return 200, {"chat": chat}


def api_add_member(user, chat_id, data):
    conn = get_db()
    m = conn.execute(
        "SELECT role FROM chat_members WHERE chat_id=? AND user_id=? AND deleted=0",
        (chat_id, user["id"])
    ).fetchone()
    if not m or m["role"] != "admin":
        conn.close()
        return 403, {"error": "فقط ادمین می‌تواند عضو اضافه کند"}
    chat = conn.execute("SELECT type FROM chats WHERE id=?", (chat_id,)).fetchone()
    if not chat or chat["type"] != "group":
        conn.close()
        return 400, {"error": "فقط در گروه"}
    username = (data.get("username") or "").strip().lower()
    target = conn.execute("SELECT id, name FROM users WHERE username=?", (username,)).fetchone()
    if not target:
        conn.close()
        return 404, {"error": "کاربر یافت نشد"}
    exists = conn.execute(
        "SELECT deleted FROM chat_members WHERE chat_id=? AND user_id=?",
        (chat_id, target["id"])
    ).fetchone()
    if exists:
        if exists["deleted"]:
            conn.execute(
                "UPDATE chat_members SET deleted=0, role='member' WHERE chat_id=? AND user_id=?",
                (chat_id, target["id"])
            )
        else:
            conn.close()
            return 400, {"error": "این کاربر قبلاً عضو است"}
    else:
        conn.execute(
            "INSERT INTO chat_members (chat_id, user_id, deleted, role) VALUES (?,?,0,'member')",
            (chat_id, target["id"])
        )
    conn.commit()
    conn.close()
    return 200, {"ok": True, "user_id": target["id"], "name": target["name"]}


def api_remove_member(user, chat_id, target_user_id):
    conn = get_db()
    my = conn.execute(
        "SELECT role FROM chat_members WHERE chat_id=? AND user_id=? AND deleted=0",
        (chat_id, user["id"])
    ).fetchone()
    if not my:
        conn.close()
        return 404, {"error": "چت یافت نشد"}

    # خروج خودم همیشه مجاز است
    if target_user_id == user["id"]:
        conn.execute(
            "UPDATE chat_members SET deleted=1 WHERE chat_id=? AND user_id=?",
            (chat_id, user["id"])
        )
        conn.commit()
        conn.close()
        return 200, {"ok": True, "left": True}

    # حذف دیگران فقط توسط ادمین
    if my["role"] != "admin":
        conn.close()
        return 403, {"error": "فقط ادمین می‌تواند عضو را حذف کند"}

    chat = conn.execute("SELECT type, creator_id FROM chats WHERE id=?", (chat_id,)).fetchone()
    if not chat or chat["type"] != "group":
        conn.close()
        return 400, {"error": "فقط در گروه"}
    if target_user_id == chat["creator_id"]:
        conn.close()
        return 400, {"error": "نمی‌توانید سازنده گروه را حذف کنید"}

    conn.execute(
        "UPDATE chat_members SET deleted=1 WHERE chat_id=? AND user_id=?",
        (chat_id, target_user_id)
    )
    conn.commit()
    conn.close()
    return 200, {"ok": True}


def api_messages(user, chat_id, since=0):
    conn = get_db()
    m = conn.execute(
        "SELECT 1 FROM chat_members WHERE chat_id=? AND user_id=? AND deleted=0",
        (chat_id, user["id"])
    ).fetchone()
    if not m:
        conn.close()
        return 404, {"error": "چت یافت نشد"}
    since_f = float(since or 0)
    rows = conn.execute(
        "SELECT * FROM messages WHERE chat_id=? AND ts>? ORDER BY ts ASC",
        (chat_id, since_f)
    ).fetchall()
    result = []
    for r in rows:
        msg = row_to_dict(r)
        if msg["deleted_for_all"]:
            msg["text"] = ""
            msg["msg_type"] = "text"
            result.append(msg)
            continue
        del_for = conn.execute(
            "SELECT 1 FROM message_deleted_for WHERE message_id=? AND user_id=?",
            (msg["id"], user["id"])
        ).fetchone()
        if del_for:
            continue
        # ساخت URL فایل برای پیام‌های رسانه‌ای
        mt = msg.get("msg_type") or "text"
        if mt in ("image", "video", "file") and msg.get("file_name"):
            safe = safe_filename(msg["file_name"])
            candidate = f"{msg['id']}_{safe}"
            if os.path.isfile(os.path.join(UPLOADS_DIR, candidate)):
                msg["file_url"] = f"/uploads/{candidate}"
            else:
                try:
                    for fn in os.listdir(UPLOADS_DIR):
                        if fn.startswith(msg["id"] + "_"):
                            msg["file_url"] = f"/uploads/{fn}"
                            break
                except OSError:
                    pass
        result.append(msg)

    conn.execute(
        "UPDATE chat_members SET last_read_at=? WHERE chat_id=? AND user_id=?",
        (time.time(), chat_id, user["id"])
    )
    conn.commit()
    conn.close()
    return 200, {"messages": result}


def api_send_message(user, chat_id, data):
    text = (data.get("text") or "").strip()
    msg_type = data.get("msg_type") or "text"
    file_b64 = data.get("file_data")  # base64
    file_name = data.get("file_name") or ""
    mime_type = data.get("mime_type") or ""

    if msg_type == "text" and not text:
        return 400, {"error": "متن خالی"}
    if msg_type in ("image", "video", "file") and not file_b64:
        return 400, {"error": "فایل ارسال نشده"}

    conn = get_db()
    chat = conn.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    if not chat:
        conn.close()
        return 404, {"error": "چت یافت نشد"}
    m = conn.execute(
        "SELECT 1 FROM chat_members WHERE chat_id=? AND user_id=? AND deleted=0",
        (chat_id, user["id"])
    ).fetchone()
    if not m:
        conn.close()
        return 403, {"error": "دسترسی ندارید"}

    msg_id = uid()
    now = time.time()
    file_path = ""
    file_size = 0
    stored_mime = mime_type

    if msg_type in ("image", "video", "file") and file_b64:
        try:
            file_path, file_size, stored_mime = save_base64_file(file_b64, file_name, msg_id)
        except ValueError as e:
            conn.close()
            return 400, {"error": str(e)}
        except Exception:
            conn.close()
            return 500, {"error": "خطا در ذخیره فایل"}

        # اگر caption نداشت، نام فایل را به عنوان متن بگذار
        if not text:
            text = file_name or ("تصویر" if msg_type == "image" else "ویدیو" if msg_type == "video" else "فایل")

    # در text فیلد، برای فایل‌ها مسیر نسبی را نگه می‌داریم تا فرانت بتواند نمایش دهد
    # text = caption ، file_name جدا
    display_text = text
    if file_path:
        # ذخیره مسیر در text برای سازگاری ساده (یا جداگانه)
        pass

    conn.execute(
        """INSERT INTO messages
           (id, chat_id, sender_id, text, ts, edited, deleted_for_all, msg_type, file_name, file_size, mime_type)
           VALUES (?,?,?,?,?,0,0,?,?,?,?)""",
        (msg_id, chat_id, user["id"], display_text, now, msg_type, file_name, file_size, stored_mime or "")
    )
    # مسیر فایل را در یک فیلد مجازی برمی‌گردانیم (از طریق file_name و id ساخته می‌شود)
    # در پاسخ، file_url را اضافه می‌کنیم
    conn.execute("UPDATE chats SET last_message_at=? WHERE id=?", (now, chat_id))
    conn.execute("UPDATE chat_members SET deleted=0 WHERE chat_id=?", (chat_id,))
    conn.commit()
    msg = row_to_dict(conn.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone())
    if file_path:
        msg["file_url"] = file_path
    conn.close()
    return 200, {"message": msg}


def api_edit_message(user, msg_id, data):
    text = (data.get("text") or "").strip()
    if not text:
        return 400, {"error": "متن خالی"}
    conn = get_db()
    msg = conn.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
    if not msg:
        conn.close()
        return 404, {"error": "پیام یافت نشد"}
    if msg["sender_id"] != user["id"]:
        conn.close()
        return 403, {"error": "فقط پیام خودتان"}
    if msg["deleted_for_all"]:
        conn.close()
        return 400, {"error": "پیام حذف شده"}
    if (msg["msg_type"] or "text") != "text":
        conn.close()
        return 400, {"error": "فقط پیام متنی قابل ویرایش است"}
    conn.execute("UPDATE messages SET text=?, edited=1 WHERE id=?", (text, msg_id))
    conn.commit()
    msg = row_to_dict(conn.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone())
    conn.close()
    return 200, {"message": msg}


def api_delete_message(user, msg_id, data):
    mode = data.get("mode") or "me"
    conn = get_db()
    msg = conn.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
    if not msg:
        conn.close()
        return 404, {"error": "پیام یافت نشد"}
    if mode == "all":
        if msg["sender_id"] != user["id"]:
            conn.close()
            return 403, {"error": "فقط پیام خودتان را برای همه حذف کنید"}
        conn.execute("UPDATE messages SET deleted_for_all=1, text='' WHERE id=?", (msg_id,))
    else:
        conn.execute(
            "INSERT OR IGNORE INTO message_deleted_for (message_id, user_id) VALUES (?,?)",
            (msg_id, user["id"])
        )
    conn.commit()
    conn.close()
    return 200, {"ok": True}


def api_bulk_delete(user, data):
    ids = data.get("ids") or []
    mode = data.get("mode") or "me"
    if not ids:
        return 400, {"error": "لیست خالی"}
    conn = get_db()
    count = 0
    for mid in ids:
        msg = conn.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
        if not msg:
            continue
        if mode == "all":
            if msg["sender_id"] != user["id"]:
                continue
            conn.execute("UPDATE messages SET deleted_for_all=1, text='' WHERE id=?", (mid,))
            count += 1
        else:
            conn.execute(
                "INSERT OR IGNORE INTO message_deleted_for (message_id, user_id) VALUES (?,?)",
                (mid, user["id"])
            )
            count += 1
    conn.commit()
    conn.close()
    return 200, {"ok": True, "count": count}


# ---------- HTTP Handler ----------

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".ogg": "video/ogg",
    ".pdf": "application/pdf",
    ".zip": "application/zip",
    ".txt": "text/plain; charset=utf-8",
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {args[0]}")

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length == 0:
            return {}
        # اجازه تا حدود ۲۰ مگابایت برای آپلود base64
        if length > 22 * 1024 * 1024:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _token(self):
        auth = self.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return ""

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path.startswith("/api/"):
            self._handle_api("GET", path, qs, None)
            return

        # سرو فایل‌های آپلود شده
        if path.startswith("/uploads/"):
            rel = unquote(path[len("/uploads/"):])
            rel = os.path.basename(rel)  # جلوگیری از path traversal
            fpath = os.path.join(UPLOADS_DIR, rel)
            if not os.path.isfile(fpath):
                self.send_error(404)
                return
            ext = os.path.splitext(fpath)[1].lower()
            ctype = MIME.get(ext, "application/octet-stream")
            with open(fpath, "rb") as f:
                data = f.read()
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(data)
            return

        if path == "/" or path == "":
            path = "/index.html"
        rel = path.lstrip("/").replace("..", "")
        fpath = os.path.join(BASE_DIR, rel)
        if not os.path.isfile(fpath):
            self.send_error(404, "Not Found")
            return
        ext = os.path.splitext(fpath)[1].lower()
        ctype = MIME.get(ext, "application/octet-stream")
        with open(fpath, "rb") as f:
            data = f.read()
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        data = self._read_json()
        if path.startswith("/api/"):
            self._handle_api("POST", path, {}, data)
            return
        self._json(404, {"error": "Not found"})

    def do_PUT(self):
        parsed = urlparse(self.path)
        path = parsed.path
        data = self._read_json()
        if path.startswith("/api/"):
            self._handle_api("PUT", path, {}, data)
            return
        self._json(404, {"error": "Not found"})

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path
        data = self._read_json()
        if path.startswith("/api/"):
            self._handle_api("DELETE", path, {}, data)
            return
        self._json(404, {"error": "Not found"})

    def _handle_api(self, method, path, qs, data):
        data = data or {}

        if method == "POST" and path == "/api/register":
            return self._json(*api_register(data))
        if method == "POST" and path == "/api/login":
            return self._json(*api_login(data))

        user = get_user_from_token(self._token())
        if not user:
            return self._json(401, {"error": "لطفاً وارد شوید"})

        if method == "GET" and path == "/api/me":
            return self._json(*api_me(user))
        if method == "POST" and path == "/api/heartbeat":
            return self._json(*api_heartbeat(user))
        if method == "GET" and path == "/api/users":
            return self._json(*api_users(user))
        if method == "PUT" and path == "/api/profile":
            return self._json(*api_update_profile(user, data))
        if method == "GET" and path == "/api/chats":
            return self._json(*api_chats(user))
        if method == "POST" and path == "/api/chats":
            return self._json(*api_create_chat(user, data))

        m = re.match(r"^/api/chats/([^/]+)/messages$", path)
        if m:
            chat_id = m.group(1)
            if method == "GET":
                since = float((qs.get("since") or ["0"])[0])
                return self._json(*api_messages(user, chat_id, since))
            if method == "POST":
                return self._json(*api_send_message(user, chat_id, data))

        m = re.match(r"^/api/chats/([^/]+)/info$", path)
        if m and method == "GET":
            return self._json(*api_chat_info(user, m.group(1)))

        m = re.match(r"^/api/chats/([^/]+)/members$", path)
        if m and method == "POST":
            return self._json(*api_add_member(user, m.group(1), data))

        m = re.match(r"^/api/chats/([^/]+)/members/([^/]+)$", path)
        if m and method == "DELETE":
            return self._json(*api_remove_member(user, m.group(1), m.group(2)))

        m = re.match(r"^/api/chats/([^/]+)$", path)
        if m:
            chat_id = m.group(1)
            if method == "PUT":
                return self._json(*api_update_chat(user, chat_id, data))
            if method == "DELETE":
                return self._json(*api_delete_chat(user, chat_id))

        m = re.match(r"^/api/messages/([^/]+)$", path)
        if m:
            msg_id = m.group(1)
            if method == "PUT":
                return self._json(*api_edit_message(user, msg_id, data))
            if method == "DELETE":
                return self._json(*api_delete_message(user, msg_id, data))

        if method == "POST" and path == "/api/messages/bulk-delete":
            return self._json(*api_bulk_delete(user, data))

        self._json(404, {"error": "API not found"})


class ThreadedServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def main():
    init_db()
    server = ThreadedServer((HOST, PORT), Handler)
    print("=" * 55)
    print("  سایبر پانک — پیامرسان پیشرفته")
    print(f"  آدرس:   http://127.0.0.1:{PORT}")
    print(f"  دیتابیس: {DB_PATH}")
    print(f"  آپلودها: {UPLOADS_DIR}")
    print("  توقف:   Ctrl+C")
    print("=" * 55)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nسرور متوقف شد.")
        server.server_close()


if __name__ == "__main__":
    main()
