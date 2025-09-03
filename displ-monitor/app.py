#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import smtplib
import sqlite3
from contextlib import closing
from email.message import EmailMessage
from datetime import datetime, timezone, timedelta

from flask import Flask, request, redirect, render_template_string, url_for, flash, Response
from apscheduler.schedulers.background import BackgroundScheduler

# ====== DISPL API ===============================================================
try:
    # Debes reemplazar displ_api.py por tu wrapper real con get_devices()
    from displ_api import get_devices
except ModuleNotFoundError:
    raise SystemExit(
        "No se encontró 'displ_api'. Coloca displ_api.py junto a este archivo "
        "o instala tu paquete para poder importar get_devices()."
    )

# ====== CONFIG (ENV) ============================================================
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "tu_correo@gmail.com")
SMTP_PASS = os.getenv("SMTP_PASS", "APP_PASSWORD_AQUI")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER)
SMTP_TO   = [e.strip() for e in os.getenv("SMTP_TO", "tu_correo@gmail.com").split(",") if e.strip()]
SUBJECT_PREFIX = os.getenv("SUBJECT_PREFIX", "[DISPL]")

DB_PATH = os.getenv("DB_PATH", "/data/monitor.db")
# Valores por defecto (se pueden cambiar en la UI)
DEFAULT_IDLE_THRESHOLD_MIN = int(os.getenv("IDLE_THRESHOLD_MIN", "10"))
DEFAULT_INTERVAL_SEC       = int(os.getenv("INTERVAL_SEC", "10"))
DEFAULT_NOTIFY_FIRST       = os.getenv("NOTIFY_FIRST", "true").lower() == "true"
DEFAULT_NO_RECOVERY        = os.getenv("NO_RECOVERY", "false").lower() == "true"
VERBOSE = os.getenv("VERBOSE", "true").lower() == "true"

# ====== FLASK ===================================================================
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "secret-key-para-demo")

# ====== AUTH BÁSICA (opcional) =================================================
ADMIN_USER = os.getenv("ADMIN_USER")
ADMIN_PASS = os.getenv("ADMIN_PASS")

@app.before_request
def require_basic_auth():
    # health sin auth
    if request.path == "/healthz":
        return
    # si no configuraste credenciales, no exige auth
    if not ADMIN_USER or not ADMIN_PASS:
        return
    auth = request.authorization
    if not auth or not (auth.username == ADMIN_USER and auth.password == ADMIN_PASS):
        return Response("Auth required", 401, {"WWW-Authenticate": 'Basic realm="Login Required"'})

# ====== DB UTIL =================================================================
def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with closing(db()) as conn:
        c = conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS devices (id INTEGER PRIMARY KEY)")
        c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS states (
            dev_id TEXT PRIMARY KEY,
            offline INTEGER,
            last_motivo TEXT,
            last_notified_at TEXT,
            name TEXT
        )""")
        # defaults
        defaults = {
            "idle_threshold_min": str(DEFAULT_IDLE_THRESHOLD_MIN),
            "interval_sec": str(DEFAULT_INTERVAL_SEC),
            "notify_first": "1" if DEFAULT_NOTIFY_FIRST else "0",
            "no_recovery": "1" if DEFAULT_NO_RECOVERY else "0"
        }
        for k, v in defaults.items():
            c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
        conn.commit()

def get_setting(key, as_int=False, as_bool=False):
    with closing(db()) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        val = row["value"]
        if as_int:
            return int(val)
        if as_bool:
            return val == "1"
        return val

def set_setting(key, value):
    with closing(db()) as conn:
        conn.execute("INSERT INTO settings(key,value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
        conn.commit()

# ====== EMAIL ===================================================================
def send_email(subject: str, html_body: str, to_addrs: list[str], text_body: str | None = None):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = ", ".join(to_addrs)
    if not text_body:
        text_body = "Ver contenido en HTML."
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    if VERBOSE:
        print(f"[EMAIL] Enviando → {to_addrs} | Asunto: {subject}")

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.ehlo()
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)

def render_html(title: str, lines: list[str]) -> str:
    items = "".join(f"<li>{ln}</li>" for ln in lines)
    return f"""
    <html><body style="font-family:Arial,Helvetica,sans-serif;line-height:1.5; color:#111">
      <h2 style="margin:0 0 8px 0">{title}</h2>
      <ul>{items}</ul>
      <p style="color:#666;font-size:12px">Enviado {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    </body></html>
    """

# ====== FECHAS / ONLINE-OFFLINE =================================================
def parse_dt(val):
    if val in (None, ""):
        return None
    if isinstance(val, (int, float)) or (isinstance(val, str) and val.isdigit()):
        try:
            return datetime.fromtimestamp(float(val), tz=timezone.utc)
        except Exception:
            pass
    for fmt in ("%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S.%fZ",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(val, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None

def is_offline(device: dict, idle_threshold_min: int) -> (bool, str):
    status = str(device.get("status", "")).lower()
    state  = str(device.get("state", "")).lower()
    online_flag    = device.get("online", None)
    is_online_flag = device.get("is_online", None)

    last_seen = (device.get("last_seen") or device.get("lastSeen") or
                 device.get("last_online_at") or device.get("lastHeartbeatAt") or
                 device.get("last_heartbeat_at"))
    last_seen_dt = parse_dt(last_seen) if isinstance(last_seen, (str, int, float)) else None

    if status in {"offline", "inactive", "disconnected"}:
        return True, f"status={status}"
    if state in {"offline", "inactive", "disconnected"}:
        return True, f"state={state}"
    if isinstance(online_flag, bool) and not online_flag:
        return True, "online=False"
    if isinstance(is_online_flag, bool) and not is_online_flag:
        return True, "is_online=False"

    if last_seen_dt is not None:
        now = datetime.now(timezone.utc)
        delta = now - last_seen_dt
        if delta > timedelta(minutes=idle_threshold_min):
            return True, f"last_seen {int(delta.total_seconds()/60)} min (> {idle_threshold_min} min)"
    return False, "online"

def format_device_line(d: dict, motivo: str) -> str:
    dev_id   = d.get("id", "N/A")
    name     = d.get("name") or d.get("device_name") or f"Device {dev_id}"
    location = d.get("location") or d.get("site") or d.get("place") or "—"
    last_seen = (d.get("last_seen") or d.get("lastSeen") or d.get("last_online_at") or
                 d.get("lastHeartbeatAt") or d.get("last_heartbeat_at") or "N/D")
    return (f"<b>{name}</b> (ID: <code>{dev_id}</code>, ubicación: {location}) "
            f"→ <b>OFFLINE</b> ({motivo}). Último visto: <code>{last_seen}</code>")

# ====== MONITOR JOB ==============================================================
COOLDOWN_MIN = int(os.getenv("COOLDOWN_MIN", "10"))  # minutos mínimo entre alertas por dispositivo

def allowed_to_notify(dev_id: str) -> bool:
    with closing(db()) as conn:
        row = conn.execute("SELECT last_notified_at FROM states WHERE dev_id=?", (dev_id,)).fetchone()
        if not row or not row["last_notified_at"]:
            return True
        try:
            last_dt = datetime.fromisoformat(row["last_notified_at"])
        except Exception:
            return True
        return (datetime.now(timezone.utc) - last_dt) >= timedelta(minutes=COOLDOWN_MIN)

def set_notified(dev: dict):
    dev_id = str(dev.get("id", ""))
    with closing(db()) as conn:
        conn.execute("UPDATE states SET last_notified_at=? WHERE dev_id=?", (datetime.now(timezone.utc).isoformat(), dev_id))
        conn.commit()

def monitor_tick():
    idle_threshold_min = get_setting("idle_threshold_min", as_int=True)
    no_recovery        = get_setting("no_recovery", as_bool=True)
    notify_first       = get_setting("notify_first", as_bool=True)

    with closing(db()) as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM devices ORDER BY id").fetchall()]

    if VERBOSE:
        print(f"[INFO] Tick → IDS={ids} | idle={idle_threshold_min} min")

    with closing(db()) as conn:
        prev_state = {r["dev_id"]: dict(r) for r in conn.execute("SELECT * FROM states").fetchall()}

    res = get_devices(ids if ids else None)
    devices = res.get("payload", []) if isinstance(res, dict) else []

    new_state = {}
    offline_changed, online_changed, offline_initial = [], [], []

    for d in devices:
        offline, motivo = is_offline(d, idle_threshold_min)
        dev_id = str(d.get("id", "")) or (d.get("uuid", "") or d.get("serial", "unknown"))
        new_state[dev_id] = {
            "offline": 1 if offline else 0,
            "last_motivo": motivo,
            "name": d.get("name") or d.get("device_name") or f"Device {dev_id}",
        }
        prev = prev_state.get(dev_id)
        prev_offline = prev["offline"] if prev and "offline" in prev else None

        if VERBOSE:
            print(f"[CHK] {new_state[dev_id]['name']} (ID {dev_id}) → {'OFFLINE' if offline else 'ONLINE'} ({motivo}) | prev={prev_offline}")

        if prev_offline is None:
            if offline:
                offline_initial.append((d, motivo))
        else:
            if prev_offline and not offline:
                online_changed.append(d)
            elif (prev_offline == 0) and offline:
                offline_changed.append((d, motivo))

    with closing(db()) as conn:
        for dev_id, st in new_state.items():
            conn.execute("""
                INSERT INTO states(dev_id, offline, last_motivo, name)
                VALUES(?,?,?,?)
                ON CONFLICT(dev_id) DO UPDATE SET offline=excluded.offline, last_motivo=excluded.last_motivo, name=excluded.name
            """, (dev_id, st["offline"], st["last_motivo"], st["name"]))
        conn.commit()

    # OFFLINE inicial
    if notify_first and offline_initial:
        lines = [format_device_line(d, m) for d, m in offline_initial if allowed_to_notify(str(d.get("id","")))]
        if lines:
            html = render_html("⚠️ DISPL: Dispositivos OFFLINE detectados (inicio)", lines)
            send_email(f"{SUBJECT_PREFIX} OFFLINE inicial: {len(lines)} dispositivo(s)", html, SMTP_TO)
            for d,_ in offline_initial:
                set_notified(d)

    # Cambios OFFLINE
    if offline_changed:
        filt = [(d,m) for (d,m) in offline_changed if allowed_to_notify(str(d.get("id","")))]
        if filt:
            lines = [format_device_line(d, m) for d, m in filt]
            html = render_html("⚠️ DISPL: Dispositivos OFFLINE detectados", lines)
            send_email(f"{SUBJECT_PREFIX} OFFLINE: {len(lines)} dispositivo(s)", html, SMTP_TO)
            for d,_ in filt:
                set_notified(d)

    # Recuperados ONLINE
    if online_changed and not no_recovery:
        lines = []
        for d in online_changed:
            dev_id = d.get("id", "N/A")
            name = d.get("name") or d.get("device_name") or f"Device {dev_id}"
            location = d.get("location") or d.get("site") or d.get("place") or "—"
            lines.append(f"<b>{name}</b> (ID: <code>{dev_id}</code>, ubicación: {location}) volvió <b>ONLINE</b> ✅")
        if lines:
            html = render_html("✅ DISPL: Dispositivos recuperados", lines)
            send_email(f"{SUBJECT_PREFIX} ONLINE: {len(lines)} dispositivo(s)", html, SMTP_TO)

# ====== SCHEDULER ===============================================================
scheduler = BackgroundScheduler(daemon=True)

def start_scheduler_now():
    init_db()
    interval_sec = get_setting("interval_sec", as_int=True)
    if not scheduler.get_jobs():
        scheduler.add_job(monitor_tick, "interval", seconds=interval_sec, id="monitor")
        scheduler.start()
        if VERBOSE:
            print(f"[SCHED] Monitor corriendo cada {interval_sec}s")

@app.before_first_request
def _start_on_first_request():
    start_scheduler_now()

if os.getenv("START_SCHEDULER_ON_BOOT", "true").lower() == "true":
    with app.app_context():
        start_scheduler_now()

# ====== UI MINIMAL ==============================================================
TPL = """
<!doctype html>
<title>DISPL Monitor</title>
<link rel="stylesheet" href="https://unpkg.com/mvp.css">
<main>
  <header>
    <h1>DISPL Monitor</h1>
    <p>Emails a: {{ smtp_to }}</p>
  </header>

  <section>
    <h3>Dispositivos vigilados</h3>
    <form method="post" action="{{ url_for('add_device') }}">
      <label>Nuevo ID</label>
      <input type="number" name="dev_id" required>
      <button>Agregar</button>
    </form>
    <table>
      <thead><tr><th>ID</th><th>Acciones</th></tr></thead>
      <tbody>
      {% for d in devices %}
        <tr>
          <td>{{ d['id'] }}</td>
          <td>
            <form method="post" action="{{ url_for('delete_device') }}" style="display:inline">
              <input type="hidden" name="dev_id" value="{{ d['id'] }}">
              <button type="submit">Eliminar</button>
            </form>
          </td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
  </section>

  <section>
    <h3>Parámetros</h3>
    <form method="post" action="{{ url_for('save_settings') }}">
      <label>Umbral inactividad (min)</label>
      <input type="number" name="idle_threshold_min" value="{{ idle_threshold_min }}" min="1" required>
      <label>Intervalo de chequeo (seg)</label>
      <input type="number" name="interval_sec" value="{{ interval_sec }}" min="5" required>
      <label>Notificar OFFLINE en primera pasada</label>
      <input type="checkbox" name="notify_first" {% if notify_first %}checked{% endif %}>
      <label>No enviar recuperación (ONLINE)</label>
      <input type="checkbox" name="no_recovery" {% if no_recovery %}checked{% endif %}>
      <button>Guardar</button>
    </form>
  </section>

  <section>
    <h3>Estado actual</h3>
    <table>
      <thead><tr><th>ID</th><th>Nombre</th><th>Offline</th><th>Motivo</th><th>Última notificación</th></tr></thead>
      <tbody>
      {% for s in states %}
        <tr>
          <td>{{ s['dev_id'] }}</td>
          <td>{{ s['name'] }}</td>
          <td>{{ 'Sí' if s['offline'] else 'No' }}</td>
          <td>{{ s['last_motivo'] }}</td>
          <td>{{ s['last_notified_at'] or '—' }}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
  </section>
</main>
"""

@app.get("/")
def index():
    with closing(db()) as conn:
        devices = conn.execute("SELECT id FROM devices ORDER BY id").fetchall()
        states  = conn.execute("SELECT * FROM states ORDER BY dev_id").fetchall()
    return render_template_string(
        TPL,
        devices=devices,
        states=states,
        smtp_to=", ".join(SMTP_TO),
        idle_threshold_min=get_setting("idle_threshold_min", as_int=True),
        interval_sec=get_setting("interval_sec", as_int=True),
        notify_first=get_setting("notify_first", as_bool=True),
        no_recovery=get_setting("no_recovery", as_bool=True),
    )

@app.post("/add")
def add_device():
    dev_id = request.form.get("dev_id", "").strip()
    if not dev_id.isdigit():
        flash("ID inválido")
        return redirect(url_for("index"))
    with closing(db()) as conn:
        conn.execute("INSERT OR IGNORE INTO devices(id) VALUES(?)", (int(dev_id),))
        conn.commit()
    flash(f"Agregado ID {dev_id}")
    return redirect(url_for("index"))

@app.post("/delete")
def delete_device():
    dev_id = request.form.get("dev_id", "").strip()
    with closing(db()) as conn:
        conn.execute("DELETE FROM devices WHERE id=?", (dev_id,))
        conn.execute("DELETE FROM states WHERE dev_id=?", (dev_id,))
        conn.commit()
    flash(f"Eliminado ID {dev_id}")
    return redirect(url_for("index"))

@app.post("/settings")
def save_settings():
    idle = int(request.form.get("idle_threshold_min", "10"))
    interval = int(request.form.get("interval_sec", "10"))
    notify_first = 1 if request.form.get("notify_first") == "on" else 0
    no_recovery  = 1 if request.form.get("no_recovery") == "on" else 0
    set_setting("idle_threshold_min", idle)
    set_setting("interval_sec", interval)
    set_setting("notify_first", notify_first)
    set_setting("no_recovery", no_recovery)

    job = scheduler.get_job("monitor")
    if job:
        job.reschedule(trigger="interval", seconds=interval)
    flash("Parámetros guardados")
    return redirect(url_for("index"))

@app.get("/healthz")
def healthz():
    return "ok", 200

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
