import hashlib
import io
import json
import os
import random
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, g, jsonify, request, send_file, send_from_directory
from flask_cors import CORS
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("SABOOT_DB_PATH") or (
    "/tmp/saboot.db" if os.environ.get("VERCEL") else os.path.join(BASE_DIR, "saboot.db")
)

app = Flask(__name__, static_folder=os.path.join(BASE_DIR, "static"), static_url_path="")
CORS(app)


def get_db():
    if "db" not in g:
        if not os.path.exists(DB_PATH):
            init_db()
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def now_ms():
    row = get_db().execute("SELECT value FROM meta WHERE key='time_offset_ms'").fetchone()
    offset = int(row["value"]) if row else 0
    return int(time.time() * 1000) + offset


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_terms(t: dict) -> str:
    payload = [
        t["id"],
        t["work"],
        int(t["wage"]),
        t["per"],
        sorted(t["days"]),
        t["lang"],
        t["ts"],
        int(t["pd"]),
        t.get("empName", ""),
        t.get("wrkName", ""),
        t.get("empPhone", ""),
        t.get("wrkPhone", ""),
    ]
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS users (
            phone TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('employer','worker')),
            name TEXT NOT NULL,
            wallet_balance INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            PRIMARY KEY (phone, role)
        );
        CREATE TABLE IF NOT EXISTS otps (
            phone TEXT NOT NULL,
            role TEXT NOT NULL,
            otp TEXT NOT NULL,
            expires_at INTEGER NOT NULL,
            PRIMARY KEY (phone, role)
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            phone TEXT NOT NULL,
            role TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS contracts (
            id TEXT PRIMARY KEY,
            emp_phone TEXT NOT NULL,
            wrk_phone TEXT NOT NULL,
            emp_name TEXT NOT NULL,
            wrk_name TEXT NOT NULL,
            work TEXT NOT NULL,
            wage INTEGER NOT NULL,
            per TEXT NOT NULL,
            days_json TEXT NOT NULL,
            lang TEXT NOT NULL,
            pd INTEGER NOT NULL,
            ts TEXT NOT NULL,
            contract_hash TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('offer','active','withdrawn')),
            cancel_until INTEGER NOT NULL,
            emp_sig TEXT NOT NULL,
            wrk_sig TEXT,
            escrow_lock INTEGER NOT NULL DEFAULT 0,
            voice_clip TEXT
        );
        CREATE TABLE IF NOT EXISTS escrow_days (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_id TEXT NOT NULL,
            day_key TEXT NOT NULL,
            amt INTEGER NOT NULL,
            note TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','paid','auto','disputed','refunded')),
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            ok INTEGER NOT NULL,
            amt INTEGER NOT NULL,
            ts TEXT NOT NULL,
            event_hash TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS wallet_tx (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL,
            role TEXT NOT NULL,
            kind TEXT NOT NULL,
            amt INTEGER NOT NULL,
            note TEXT NOT NULL,
            ts TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS trust_shares (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wrk_phone TEXT NOT NULL,
            tier TEXT NOT NULL,
            ts TEXT NOT NULL
        );
        """
    )

    if not conn.execute("SELECT 1 FROM meta WHERE key='time_offset_ms'").fetchone():
        conn.execute("INSERT INTO meta(key,value) VALUES('time_offset_ms','0')")

    if not conn.execute("SELECT 1 FROM users").fetchone():
        now = int(time.time() * 1000)
        now_iso = datetime.now(timezone.utc).isoformat()
        conn.executemany(
            "INSERT INTO users(phone,role,name,wallet_balance,created_at) VALUES(?,?,?,?,?)",
            [
                ("9988776655", "employer", "Kalinga Builders", 8500, now_iso),
                ("9001122334", "employer", "Meera Sharma", 6000, now_iso),
                ("9876543210", "worker", "Ramesh Nayak", 550, now_iso),
                ("9123456789", "worker", "Sunita Devi", 0, now_iso),
            ],
        )

        t1 = {
            "id": "SB4829A",
            "work": "con",
            "wage": 550,
            "per": "day",
            "days": [0, 1, 2, 3, 4, 5],
            "lang": "od",
            "pd": 1,
            "ts": datetime.fromtimestamp((now - 86400000 * 4) / 1000, tz=timezone.utc).isoformat(),
            "empName": "Kalinga Builders",
            "wrkName": "Ramesh Nayak",
            "empPhone": "9988776655",
            "wrkPhone": "9876543210",
        }
        h1 = sha256_hex(canonical_terms(t1))
        conn.execute(
            """INSERT INTO contracts(id,emp_phone,wrk_phone,emp_name,wrk_name,work,wage,per,days_json,lang,pd,ts,contract_hash,status,cancel_until,emp_sig,wrk_sig,escrow_lock)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                t1["id"], t1["empPhone"], t1["wrkPhone"], t1["empName"], t1["wrkName"],
                t1["work"], t1["wage"], t1["per"], json.dumps(t1["days"]), t1["lang"],
                t1["pd"], t1["ts"], h1, "active", now - 86400000 * 3,
                sha256_hex("EMP:" + h1), sha256_hex("WRK:" + h1), 2750,
            ),
        )
        ev1_ts = datetime.fromtimestamp((now - 86400000 * 2) / 1000, tz=timezone.utc).isoformat()
        ev1_h = sha256_hex(f"{h1}p1550{ev1_ts}")
        ev2_ts = datetime.fromtimestamp((now - 86400000 * 2 + 3600000) / 1000, tz=timezone.utc).isoformat()
        ev2_h = sha256_hex(f"{ev1_h}a10{ev2_ts}")
        conn.executemany(
            "INSERT INTO events(contract_id,kind,ok,amt,ts,event_hash) VALUES(?,?,?,?,?,?)",
            [
                ("SB4829A", "p", 1, 550, ev1_ts, ev1_h),
                ("SB4829A", "a", 1, 0, ev2_ts, ev2_h),
            ],
        )
        conn.executemany(
            "INSERT INTO escrow_days(contract_id,day_key,amt,note,status,created_at) VALUES(?,?,?,?,?,?)",
            [
                ("SB4829A", datetime.fromtimestamp((now - 86400000 * 2) / 1000, tz=timezone.utc).strftime("%Y-%m-%d"), 550, "Daily shift", "paid", now - 86400000 * 2),
                ("SB4829A", datetime.now(timezone.utc).strftime("%Y-%m-%d"), 550, "Daily shift", "pending", now - 3600000 * 3),
            ],
        )

        t2 = {
            "id": "SB9104K",
            "work": "dom",
            "wage": 6500,
            "per": "month",
            "days": [0, 1, 2, 3, 4, 5, 6],
            "lang": "hi",
            "pd": 2,
            "ts": datetime.fromtimestamp((now - 7200000) / 1000, tz=timezone.utc).isoformat(),
            "empName": "Meera Sharma",
            "wrkName": "Sunita Devi",
            "empPhone": "9001122334",
            "wrkPhone": "9123456789",
        }
        h2 = sha256_hex(canonical_terms(t2))
        conn.execute(
            """INSERT INTO contracts(id,emp_phone,wrk_phone,emp_name,wrk_name,work,wage,per,days_json,lang,pd,ts,contract_hash,status,cancel_until,emp_sig,wrk_sig,escrow_lock)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                t2["id"], t2["empPhone"], t2["wrkPhone"], t2["empName"], t2["wrkName"],
                t2["work"], t2["wage"], t2["per"], json.dumps(t2["days"]), t2["lang"],
                t2["pd"], t2["ts"], h2, "offer", now + 86400000,
                sha256_hex("EMP:" + h2), None, 0,
            ),
        )
        conn.executemany(
            "INSERT INTO wallet_tx(phone,role,kind,amt,note,ts) VALUES(?,?,?,?,?,?)",
            [
                ("9988776655", "employer", "topup", 11800, "UPI Deposit", t1["ts"]),
                ("9988776655", "employer", "fund", 3300, "SB4829A · 6 days", t1["ts"]),
                ("9876543210", "worker", "pay", 550, "SB4829A · Approved", ev2_ts),
            ],
        )
    conn.commit()
    conn.close()


def require_auth(role=None):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            auth = request.headers.get("Authorization", "")
            token = auth.replace("Bearer ", "").strip()
            if not token:
                return jsonify({"error": "Authentication required"}), 401
            db = get_db()
            sess = db.execute("SELECT phone, role FROM sessions WHERE token=?", (token,)).fetchone()
            if not sess:
                return jsonify({"error": "Invalid or expired session"}), 401
            if role and sess["role"] != role:
                return jsonify({"error": f"Only {role} accounts can perform this action"}), 403
            user = db.execute("SELECT * FROM users WHERE phone=? AND role=?", (sess["phone"], sess["role"])).fetchone()
            g.user = dict(user)
            g.token = token
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def sweep_auto_release(db):
    cur_now = now_ms()
    due = db.execute("SELECT * FROM escrow_days WHERE status='pending' AND ? - created_at >= 86400000", (cur_now,)).fetchall()
    for d in due:
        c = db.execute("SELECT * FROM contracts WHERE id=?", (d["contract_id"],)).fetchone()
        if not c or c["status"] != "active" or c["escrow_lock"] < d["amt"]:
            continue
        db.execute("UPDATE contracts SET escrow_lock = escrow_lock - ? WHERE id=?", (d["amt"], c["id"]))
        db.execute("UPDATE escrow_days SET status='auto' WHERE id=?", (d["id"],))
        db.execute("UPDATE users SET wallet_balance = wallet_balance + ? WHERE phone=? AND role='worker'", (d["amt"], c["wrk_phone"]))
        ts = datetime.fromtimestamp(cur_now / 1000, tz=timezone.utc).isoformat()
        db.execute("INSERT INTO wallet_tx(phone,role,kind,amt,note,ts) VALUES(?,?,?,?,?,?)", (c["wrk_phone"], "worker", "pay", d["amt"], f"{c['id']} · 24h Auto-Release", ts))
        append_event(db, c["id"], c["contract_hash"], "p", 1, d["amt"], ts)
        ev_idx = db.execute("SELECT COUNT(*) AS n FROM events WHERE contract_id=?", (c["id"],)).fetchone()["n"] - 1
        append_event(db, c["id"], c["contract_hash"], "a", 1, ev_idx, ts)
    db.commit()


def append_event(db, contract_id, base_hash, kind, ok, amt, ts):
    last = db.execute("SELECT event_hash FROM events WHERE contract_id=? ORDER BY id DESC LIMIT 1", (contract_id,)).fetchone()
    prev = last["event_hash"] if last else base_hash
    eh = sha256_hex(f"{prev}{kind}{int(bool(ok))}{amt}{ts}")
    db.execute("INSERT INTO events(contract_id,kind,ok,amt,ts,event_hash) VALUES(?,?,?,?,?,?)", (contract_id, kind, int(bool(ok)), amt, ts, eh))


def serialize_contract(db, c):
    evs = [dict(e) for e in db.execute("SELECT kind as k, ok, amt, ts, event_hash as hash FROM events WHERE contract_id=? ORDER BY id ASC", (c["id"],)).fetchall()]
    days = [dict(d) for d in db.execute("SELECT id, day_key as d, amt, note, status as st, created_at as at FROM escrow_days WHERE contract_id=? ORDER BY id ASC", (c["id"],)).fetchall()]
    return {
        "terms": {
            "id": c["id"],
            "work": c["work"],
            "wage": c["wage"],
            "per": c["per"],
            "days": json.loads(c["days_json"]),
            "lang": c["lang"],
            "pd": c["pd"],
            "ts": c["ts"],
            "empName": c["emp_name"],
            "wrkName": c["wrk_name"],
            "empPhone": c["emp_phone"],
            "wrkPhone": c["wrk_phone"],
        },
        "hash": c["contract_hash"],
        "until": c["cancel_until"],
        "status": c["status"],
        "ev": [{"k": e["k"], "ok": bool(e["ok"]), "amt": e["amt"], "ts": e["ts"], "hash": e["hash"]} for e in evs],
        "esc": {"lock": c["escrow_lock"], "days": days},
        "sig": {"e": c["emp_sig"], "w": c["wrk_sig"]},
        "voiceClip": c["voice_clip"],
    }


def verify_integrity(db, cid):
    c = db.execute("SELECT * FROM contracts WHERE id=?", (cid,)).fetchone()
    if not c:
        return None
    t = {
        "id": c["id"], "work": c["work"], "wage": c["wage"], "per": c["per"],
        "days": json.loads(c["days_json"]), "lang": c["lang"], "pd": c["pd"],
        "ts": c["ts"], "empName": c["emp_name"], "wrkName": c["wrk_name"],
        "empPhone": c["emp_phone"], "wrkPhone": c["wrk_phone"],
    }
    terms_ok = sha256_hex(canonical_terms(t)) == c["contract_hash"]
    prev = c["contract_hash"]
    chain_ok = True
    for e in db.execute("SELECT * FROM events WHERE contract_id=? ORDER BY id ASC", (cid,)).fetchall():
        if sha256_hex(f"{prev}{e['kind']}{e['ok']}{e['amt']}{e['ts']}") != e["event_hash"]:
            chain_ok = False
            break
        prev = e["event_hash"]
    sig_ok = bool(c["emp_sig"]) and (c["status"] == "offer" or bool(c["wrk_sig"]))
    return {"authentic": terms_ok and chain_ok and sig_ok, "terms_ok": terms_ok, "chain_ok": chain_ok, "sig_ok": sig_ok, "contract": serialize_contract(db, c)}


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "service": "saboot-flask", "time_ms": now_ms()})


@app.route("/api/auth/request-otp", methods=["POST"])
def request_otp():
    data = request.get_json(force=True)
    phone = "".join(ch for ch in str(data.get("phone", "")) if ch.isdigit())[-10:]
    role = data.get("role", "worker")
    if len(phone) < 10 or role not in ("employer", "worker"):
        return jsonify({"error": "Enter a valid 10-digit phone number and role"}), 400
    otp = str(random.randint(1000, 9999))
    db = get_db()
    db.execute("INSERT OR REPLACE INTO otps(phone,role,otp,expires_at) VALUES(?,?,?,?)", (phone, role, otp, int(time.time()) + 300))
    db.commit()
    return jsonify({"ok": True, "phone": phone, "role": role, "demo_otp": otp})


@app.route("/api/auth/verify-otp", methods=["POST"])
def verify_otp():
    data = request.get_json(force=True)
    phone = "".join(ch for ch in str(data.get("phone", "")) if ch.isdigit())[-10:]
    role = data.get("role", "worker")
    otp = str(data.get("otp", "")).strip()
    name = str(data.get("name", "")).strip()
    db = get_db()
    row = db.execute("SELECT * FROM otps WHERE phone=? AND role=?", (phone, role)).fetchone()
    if not row or row["otp"] != otp or row["expires_at"] < int(time.time()):
        return jsonify({"error": "Invalid or expired OTP"}), 400
    db.execute("DELETE FROM otps WHERE phone=? AND role=?", (phone, role))
    user = db.execute("SELECT * FROM users WHERE phone=? AND role=?", (phone, role)).fetchone()
    if not user:
        default_name = name or ("Employer " + phone[-4:] if role == "employer" else "Worker " + phone[-4:])
        bal = 5000 if role == "employer" else 0
        db.execute("INSERT INTO users(phone,role,name,wallet_balance,created_at) VALUES(?,?,?,?,?)", (phone, role, default_name, bal, datetime.now(timezone.utc).isoformat()))
        user = db.execute("SELECT * FROM users WHERE phone=? AND role=?", (phone, role)).fetchone()
    elif name and name != user["name"]:
        db.execute("UPDATE users SET name=? WHERE phone=? AND role=?", (name, phone, role))
        user = db.execute("SELECT * FROM users WHERE phone=? AND role=?", (phone, role)).fetchone()
    token = secrets.token_hex(24)
    db.execute("INSERT INTO sessions(token,phone,role,created_at) VALUES(?,?,?,?)", (token, phone, role, int(time.time())))
    db.commit()
    return jsonify({"token": token, "user": dict(user)})


@app.route("/api/auth/me")
@require_auth()
def auth_me():
    db = get_db()
    sweep_auto_release(db)
    txs = [dict(x) for x in db.execute("SELECT kind as k, amt, note, ts FROM wallet_tx WHERE phone=? AND role=? ORDER BY id DESC LIMIT 30", (g.user["phone"], g.user["role"])).fetchall()]
    return jsonify({"user": g.user, "tx": txs, "time_ms": now_ms()})


@app.route("/api/auth/logout", methods=["POST"])
@require_auth()
def auth_logout():
    db = get_db()
    db.execute("DELETE FROM sessions WHERE token=?", (g.token,))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/contracts", methods=["GET"])
@require_auth()
def list_contracts():
    db = get_db()
    sweep_auto_release(db)
    if g.user["role"] == "employer":
        rows = db.execute("SELECT * FROM contracts WHERE emp_phone=? ORDER BY ts DESC", (g.user["phone"],)).fetchall()
    else:
        rows = db.execute("SELECT * FROM contracts WHERE wrk_phone=? OR wrk_phone='' ORDER BY ts DESC", (g.user["phone"],)).fetchall()
    return jsonify({"contracts": [serialize_contract(db, r) for r in rows]})


@app.route("/api/contracts", methods=["POST"])
@require_auth("employer")
def create_contract():
    data = request.get_json(force=True)
    wage = int(data.get("wage", 0))
    days = sorted({int(d) for d in data.get("days", []) if 0 <= int(d) <= 6})
    if wage <= 0 or not days:
        return jsonify({"error": "Valid wage and working days are required"}), 400
    cid = "SB" + secrets.token_hex(3).upper()
    wrk_phone = "".join(ch for ch in str(data.get("wrkPhone", "")) if ch.isdigit())[-10:]
    ts = datetime.fromtimestamp(now_ms() / 1000, tz=timezone.utc).isoformat()
    t = {
        "id": cid,
        "work": data.get("work", "con"),
        "wage": wage,
        "per": data.get("per", "day"),
        "days": days,
        "lang": data.get("lang", "en"),
        "pd": int(data.get("pd", 0)),
        "ts": ts,
        "empName": data.get("empName") or g.user["name"],
        "wrkName": data.get("wrkName") or "Worker",
        "empPhone": g.user["phone"],
        "wrkPhone": wrk_phone,
    }
    chash = sha256_hex(canonical_terms(t))
    db = get_db()
    db.execute(
        """INSERT INTO contracts(id,emp_phone,wrk_phone,emp_name,wrk_name,work,wage,per,days_json,lang,pd,ts,contract_hash,status,cancel_until,emp_sig,wrk_sig,escrow_lock,voice_clip)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            cid, t["empPhone"], t["wrkPhone"], t["empName"], t["wrkName"],
            t["work"], t["wage"], t["per"], json.dumps(days), t["lang"], t["pd"],
            ts, chash, "offer", now_ms() + 86400000, sha256_hex("EMP:" + chash), None, 0, data.get("voiceClip"),
        ),
    )
    db.commit()
    row = db.execute("SELECT * FROM contracts WHERE id=?", (cid,)).fetchone()
    return jsonify({"contract": serialize_contract(db, row)}), 201


@app.route("/api/contracts/<cid>/sign", methods=["POST"])
@require_auth("worker")
def sign_contract(cid):
    data = request.get_json(force=True) or {}
    if not data.get("heard"):
        return jsonify({"error": "Voice summary must be heard before signing"}), 400
    db = get_db()
    c = db.execute("SELECT * FROM contracts WHERE id=?", (cid,)).fetchone()
    if not c or c["status"] != "offer":
        return jsonify({"error": "Offer not found or already processed"}), 404
    if c["wrk_phone"] and c["wrk_phone"] != g.user["phone"]:
        return jsonify({"error": "This offer is addressed to another worker phone"}), 403
    wrk_phone = c["wrk_phone"] or g.user["phone"]
    wrk_name = c["wrk_name"] if c["wrk_name"] != "Worker" else g.user["name"]
    t = {
        "id": c["id"], "work": c["work"], "wage": c["wage"], "per": c["per"],
        "days": json.loads(c["days_json"]), "lang": c["lang"], "pd": c["pd"],
        "ts": c["ts"], "empName": c["emp_name"], "wrkName": wrk_name,
        "empPhone": c["emp_phone"], "wrkPhone": wrk_phone,
    }
    chash = sha256_hex(canonical_terms(t))
    db.execute(
        "UPDATE contracts SET wrk_phone=?, wrk_name=?, contract_hash=?, emp_sig=?, wrk_sig=?, status='active', cancel_until=? WHERE id=?",
        (wrk_phone, wrk_name, chash, sha256_hex("EMP:" + chash), sha256_hex("WRK:" + chash), now_ms() + 86400000, cid),
    )
    db.commit()
    return jsonify({"contract": serialize_contract(db, db.execute("SELECT * FROM contracts WHERE id=?", (cid,)).fetchone())})


@app.route("/api/contracts/<cid>/withdraw", methods=["POST"])
@require_auth("worker")
def withdraw_contract(cid):
    db = get_db()
    c = db.execute("SELECT * FROM contracts WHERE id=? AND wrk_phone=?", (cid, g.user["phone"])).fetchone()
    if not c or c["status"] != "active":
        return jsonify({"error": "Active contract not found"}), 404
    if now_ms() >= c["cancel_until"]:
        return jsonify({"error": "24-hour withdrawal window has expired"}), 400
    db.execute("UPDATE contracts SET status='withdrawn' WHERE id=?", (cid,))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/wallet/topup", methods=["POST"])
@require_auth("employer")
def wallet_topup():
    amt = int((request.get_json(force=True) or {}).get("amt", 0))
    if amt <= 0:
        return jsonify({"error": "Invalid amount"}), 400
    db = get_db()
    db.execute("UPDATE users SET wallet_balance = wallet_balance + ? WHERE phone=? AND role='employer'", (amt, g.user["phone"]))
    ts = datetime.fromtimestamp(now_ms() / 1000, tz=timezone.utc).isoformat()
    db.execute("INSERT INTO wallet_tx(phone,role,kind,amt,note,ts) VALUES(?,?,?,?,?,?)", (g.user["phone"], "employer", "topup", amt, "UPI Deposit", ts))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/wallet/cashout", methods=["POST"])
@require_auth("worker")
def wallet_cashout():
    db = get_db()
    bal = g.user["wallet_balance"]
    if bal <= 0:
        return jsonify({"error": "Nothing to cash out"}), 400
    db.execute("UPDATE users SET wallet_balance=0 WHERE phone=? AND role='worker'", (g.user["phone"],))
    ts = datetime.fromtimestamp(now_ms() / 1000, tz=timezone.utc).isoformat()
    db.execute("INSERT INTO wallet_tx(phone,role,kind,amt,note,ts) VALUES(?,?,?,?,?,?)", (g.user["phone"], "worker", "out", bal, "Sent to UPI", ts))
    db.commit()
    return jsonify({"ok": True, "amt": bal})


@app.route("/api/contracts/<cid>/escrow/fund", methods=["POST"])
@require_auth("employer")
def escrow_fund(cid):
    days_n = int((request.get_json(force=True) or {}).get("days", 1))
    db = get_db()
    c = db.execute("SELECT * FROM contracts WHERE id=? AND emp_phone=?", (cid, g.user["phone"])).fetchone()
    if not c:
        return jsonify({"error": "Contract not found"}), 404
    d_cnt = max(1, len(json.loads(c["days_json"])))
    daily = c["wage"] if c["per"] == "day" else (round(c["wage"] * 12 / 52 / d_cnt) if c["per"] == "month" else c["wage"])
    amt = days_n * daily
    if g.user["wallet_balance"] < amt:
        return jsonify({"error": "Insufficient Employer wallet balance"}), 400
    db.execute("UPDATE users SET wallet_balance = wallet_balance - ? WHERE phone=? AND role='employer'", (amt, g.user["phone"]))
    db.execute("UPDATE contracts SET escrow_lock = escrow_lock + ? WHERE id=?", (amt, cid))
    ts = datetime.fromtimestamp(now_ms() / 1000, tz=timezone.utc).isoformat()
    db.execute("INSERT INTO wallet_tx(phone,role,kind,amt,note,ts) VALUES(?,?,?,?,?,?)", (g.user["phone"], "employer", "fund", amt, f"{cid} · {days_n}d escrow", ts))
    db.commit()
    return jsonify({"ok": True, "amt": amt})


@app.route("/api/contracts/<cid>/checkin", methods=["POST"])
@require_auth("worker")
def worker_checkin(cid):
    data = request.get_json(force=True) or {}
    db = get_db()
    c = db.execute("SELECT * FROM contracts WHERE id=? AND wrk_phone=? AND status='active'", (cid, g.user["phone"])).fetchone()
    if not c:
        return jsonify({"error": "Active contract not found"}), 404
    d_cnt = max(1, len(json.loads(c["days_json"])))
    if c["per"] == "piece":
        qty = max(1, int(data.get("qty", 10)))
        amt = qty * c["wage"]
        note = f"{qty} pcs × ₹{c['wage']}"
    else:
        amt = c["wage"] if c["per"] == "day" else round(c["wage"] * 12 / 52 / d_cnt)
        note = "Daily shift"
    dkey = datetime.fromtimestamp(now_ms() / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    if c["per"] != "piece" and db.execute("SELECT 1 FROM escrow_days WHERE contract_id=? AND day_key=? AND status!='refunded'", (cid, dkey)).fetchone():
        return jsonify({"error": "Already checked in today"}), 400
    db.execute("INSERT INTO escrow_days(contract_id,day_key,amt,note,status,created_at) VALUES(?,?,?,?,?,?)", (cid, dkey, amt, note, "pending", now_ms()))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/contracts/<cid>/days/<int:day_id>/approve", methods=["POST"])
@require_auth("employer")
def approve_day(cid, day_id):
    db = get_db()
    c = db.execute("SELECT * FROM contracts WHERE id=? AND emp_phone=?", (cid, g.user["phone"])).fetchone()
    d = db.execute("SELECT * FROM escrow_days WHERE id=? AND contract_id=?", (day_id, cid)).fetchone()
    if not c or not d or d["status"] not in ("pending", "disputed"):
        return jsonify({"error": "Pending day not found"}), 404
    if c["escrow_lock"] < d["amt"]:
        return jsonify({"error": "Lock more funds in escrow first"}), 400
    db.execute("UPDATE contracts SET escrow_lock = escrow_lock - ? WHERE id=?", (d["amt"], cid))
    db.execute("UPDATE escrow_days SET status='paid' WHERE id=?", (day_id,))
    db.execute("UPDATE users SET wallet_balance = wallet_balance + ? WHERE phone=? AND role='worker'", (d["amt"], c["wrk_phone"]))
    ts = datetime.fromtimestamp(now_ms() / 1000, tz=timezone.utc).isoformat()
    db.execute("INSERT INTO wallet_tx(phone,role,kind,amt,note,ts) VALUES(?,?,?,?,?,?)", (c["wrk_phone"], "worker", "pay", d["amt"], f"{cid} · Approved", ts))
    append_event(db, cid, c["contract_hash"], "p", 1, d["amt"], ts)
    ev_idx = db.execute("SELECT COUNT(*) AS n FROM events WHERE contract_id=?", (cid,)).fetchone()["n"] - 1
    append_event(db, cid, c["contract_hash"], "a", 1, ev_idx, ts)
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/contracts/<cid>/days/<int:day_id>/dispute", methods=["POST"])
@require_auth("employer")
def dispute_day(cid, day_id):
    db = get_db()
    c = db.execute("SELECT * FROM contracts WHERE id=? AND emp_phone=?", (cid, g.user["phone"])).fetchone()
    if not c:
        return jsonify({"error": "Contract not found"}), 404
    db.execute("UPDATE escrow_days SET status='disputed' WHERE id=? AND contract_id=?", (day_id, cid))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/contracts/<cid>/days/<int:day_id>/resolve", methods=["POST"])
@require_auth("employer")
def resolve_day(cid, day_id):
    pay_worker = bool((request.get_json(force=True) or {}).get("pay_worker", True))
    db = get_db()
    c = db.execute("SELECT * FROM contracts WHERE id=? AND emp_phone=?", (cid, g.user["phone"])).fetchone()
    d = db.execute("SELECT * FROM escrow_days WHERE id=? AND contract_id=? AND status='disputed'", (day_id, cid)).fetchone()
    if not c or not d:
        return jsonify({"error": "Disputed day not found"}), 404
    ts = datetime.fromtimestamp(now_ms() / 1000, tz=timezone.utc).isoformat()
    if pay_worker:
        if c["escrow_lock"] < d["amt"]:
            return jsonify({"error": "Insufficient escrow locked"}), 400
        db.execute("UPDATE contracts SET escrow_lock = escrow_lock - ? WHERE id=?", (d["amt"], cid))
        db.execute("UPDATE escrow_days SET status='paid' WHERE id=?", (day_id,))
        db.execute("UPDATE users SET wallet_balance = wallet_balance + ? WHERE phone=? AND role='worker'", (d["amt"], c["wrk_phone"]))
        db.execute("INSERT INTO wallet_tx(phone,role,kind,amt,note,ts) VALUES(?,?,?,?,?,?)", (c["wrk_phone"], "worker", "pay", d["amt"], f"{cid} · Dispute settled", ts))
        append_event(db, cid, c["contract_hash"], "p", 1, d["amt"], ts)
    else:
        db.execute("UPDATE contracts SET escrow_lock = escrow_lock - ? WHERE id=?", (d["amt"], cid))
        db.execute("UPDATE escrow_days SET status='refunded' WHERE id=?", (day_id,))
        db.execute("UPDATE users SET wallet_balance = wallet_balance + ? WHERE phone=? AND role='employer'", (d["amt"], c["emp_phone"]))
        db.execute("INSERT INTO wallet_tx(phone,role,kind,amt,note,ts) VALUES(?,?,?,?,?,?)", (c["emp_phone"], "employer", "refund", d["amt"], f"{cid} · Dispute refund", ts))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/contracts/<cid>/escrow/reclaim", methods=["POST"])
@require_auth("employer")
def escrow_reclaim(cid):
    db = get_db()
    c = db.execute("SELECT * FROM contracts WHERE id=? AND emp_phone=?", (cid, g.user["phone"])).fetchone()
    if not c:
        return jsonify({"error": "Contract not found"}), 404
    d_cnt = max(1, len(json.loads(c["days_json"])))
    daily = c["wage"] if c["per"] == "day" else (round(c["wage"] * 12 / 52 / d_cnt) if c["per"] == "month" else c["wage"])
    held = db.execute("SELECT COALESCE(SUM(amt),0) AS s FROM escrow_days WHERE contract_id=? AND status IN ('pending','disputed')", (cid,)).fetchone()["s"]
    reserve = 3 * daily if c["status"] == "active" else 0
    spare = c["escrow_lock"] - held - reserve
    if spare <= 0:
        return jsonify({"error": "3-day worker protection reserve must remain locked"}), 400
    ts = datetime.fromtimestamp(now_ms() / 1000, tz=timezone.utc).isoformat()
    db.execute("UPDATE contracts SET escrow_lock = escrow_lock - ? WHERE id=?", (spare, cid))
    db.execute("UPDATE users SET wallet_balance = wallet_balance + ? WHERE phone=? AND role='employer'", (spare, g.user["phone"]))
    db.execute("INSERT INTO wallet_tx(phone,role,kind,amt,note,ts) VALUES(?,?,?,?,?,?)", (g.user["phone"], "employer", "refund", spare, f"{cid} · Reclaimed", ts))
    db.commit()
    return jsonify({"ok": True, "reclaimed": spare})


@app.route("/api/demo/advance-day", methods=["POST"])
@require_auth()
def advance_day():
    db = get_db()
    db.execute("UPDATE meta SET value = CAST(CAST(value AS INTEGER) + 86400000 AS TEXT) WHERE key='time_offset_ms'")
    db.commit()
    sweep_auto_release(db)
    return jsonify({"ok": True, "time_ms": now_ms()})


@app.route("/api/demo/tamper/<cid>", methods=["POST"])
@require_auth("employer")
def tamper_contract(cid):
    db = get_db()
    db.execute("UPDATE contracts SET wage = wage + 100 WHERE id=? AND emp_phone=?", (cid, g.user["phone"]))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/verify/<cid>")
def api_verify(cid):
    res = verify_integrity(get_db(), cid)
    if not res:
        return jsonify({"error": "Contract not found"}), 404
    return jsonify(res)


@app.route("/verify/<cid>")
def public_verify_page(cid):
    res = verify_integrity(get_db(), cid)
    if not res:
        return f"<h1>Contract {cid} not found</h1>", 404
    c = res["contract"]
    t = c["terms"]
    badge = "AUTHENTIC &amp; UNALTERED" if res["authentic"] else "TAMPER DETECTED"
    color = "#0b7a6b" if res["authentic"] else "#c8341f"
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Verify {cid} — Saboot</title>
    <style>body{{font-family:system-ui,sans-serif;max-width:600px;margin:32px auto;padding:16px;background:#f6f3ea;color:#17153b}}.card{{background:#fff;border:2px solid #17153b;border-radius:16px;padding:20px;box-shadow:4px 4px 0 #17153b}}.pill{{display:inline-block;padding:6px 14px;border-radius:99px;font-weight:800;color:#fff;background:{color}}}</style></head>
    <body><div class="card"><span class="pill">{badge}</span><h1>Saboot Record {t['id']}</h1>
    <p><b>Employer:</b> {t['empName']} ({t['empPhone']})<br><b>Employee:</b> {t['wrkName']} ({t['wrkPhone']})<br><b>Wage:</b> ₹{t['wage']} {t['per']}<br><b>Status:</b> {c['status']}</p>
    <p style="font-family:monospace;font-size:12px;word-break:break-all"><b>SHA-256:</b> {c['hash']}</p>
    <p>Terms check: {'PASS' if res['terms_ok'] else 'FAIL'} · Event chain: {'PASS' if res['chain_ok'] else 'FAIL'} · Dual Signatures: {'PASS' if res['sig_ok'] else 'FAIL'}</p>
    <a href="/api/contracts/{t['id']}/pdf">Download PDF Evidence</a></div></body></html>"""


@app.route("/api/contracts/<cid>/pdf")
def export_pdf(cid):
    res = verify_integrity(get_db(), cid)
    if not res:
        return jsonify({"error": "Contract not found"}), 404
    c = res["contract"]
    t = c["terms"]
    buf = io.BytesIO()
    p = canvas.Canvas(buf, pagesize=A4)
    y = 275 * mm
    p.setFont("Helvetica-Bold", 18)
    p.drawString(20 * mm, y, f"SABOOT VERIFIED MICRO-CONTRACT: {t['id']}")
    y -= 10 * mm
    p.setFont("Helvetica", 11)
    lines = [
        f"Integrity Status: {'AUTHENTIC (SHA-256 + Event Chain Verified)' if res['authentic'] else 'TAMPER DETECTED'}",
        f"Created Timestamp: {t['ts']}",
        f"Employer: {t['empName']} (Phone: {t['empPhone']})",
        f"Employee: {t['wrkName']} (Phone: {t['wrkPhone']})",
        f"Work Category: {t['work']} | Wage: Rs. {t['wage']} ({t['per']})",
        f"Working Days/Week: {len(t['days'])} | Contract Status: {c['status']}",
        f"Escrow Locked: Rs. {c['esc']['lock']}",
        f"SHA-256 Fingerprint: {c['hash']}",
        f"Employer Signature: {c['sig']['e'][:42]}...",
        f"Employee Signature: {(c['sig']['w'] or 'Pending')[:42]}...",
        f"Verified Payment Events: {len([e for e in c['ev'] if e['k'] == 'p'])}",
        "Admissibility Note: Generated under Indian Contract Act 1872 & IEA Sec. 65B principles.",
    ]
    for line in lines:
        p.drawString(20 * mm, y, line)
        y -= 8 * mm
    p.showPage()
    p.save()
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"saboot-{cid}.pdf", mimetype="application/pdf")


@app.route("/api/trust")
@require_auth("worker")
def get_trust():
    db = get_db()
    cur = now_ms()
    rows = db.execute("SELECT * FROM contracts WHERE wrk_phone=? AND status='active' AND ? >= cancel_until", (g.user["phone"], cur)).fetchall()
    ok = 0
    late = 0
    for r in rows:
        evs = db.execute("SELECT * FROM events WHERE contract_id=? ORDER BY id ASC", (r["id"],)).fetchall()
        for e in evs:
            if e["kind"] == "p":
                if e["ok"]:
                    ok += 1
                else:
                    late += 1
    pts = len(rows) * 20 + ok * 10 - late * 5
    tiers = [("New", 0), ("Building", 20), ("Reliable", 60), ("Trusted", 120)]
    tier_idx = 0
    for idx, (_, req) in enumerate(tiers):
        if pts >= req:
            tier_idx = idx
    shares = [dict(s) for s in db.execute("SELECT tier, ts FROM trust_shares WHERE wrk_phone=? ORDER BY id DESC", (g.user["phone"],)).fetchall()]
    return jsonify({"pts": pts, "tier": tiers[tier_idx][0], "tier_idx": tier_idx, "final": len(rows), "on_time": ok, "late": late, "shares": shares})


@app.route("/api/trust/share", methods=["POST"])
@require_auth("worker")
def share_trust():
    tier = (request.get_json(force=True) or {}).get("tier", "New")
    db = get_db()
    ts = datetime.fromtimestamp(now_ms() / 1000, tz=timezone.utc).isoformat()
    db.execute("INSERT INTO trust_shares(wrk_phone,tier,ts) VALUES(?,?,?)", (g.user["phone"], tier, ts))
    db.commit()
    return jsonify({"ok": True, "tier": tier, "ts": ts})


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=os.environ.get("FLASK_DEBUG") == "1")
