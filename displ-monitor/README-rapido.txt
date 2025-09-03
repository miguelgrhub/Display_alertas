# DISPL Monitor (email alerts + web UI)

Monitorea dispositivos DISPL y envía alertas por **correo** cuando pasan a **OFFLINE/ONLINE**.
Incluye **UI web** para agregar/quitar IDs y ajustar parámetros. Listo para **Docker** y **Render.com**.

## Archivos
- `app.py` — Flask + APScheduler + SQLite + UI.
- `displ_api.py` — *Stub demo*. REEMPLÁZALO por tu wrapper real con `get_devices()`.
- `requirements.txt`
- `Dockerfile`
- `.env.example`

## Correr local (opcional)
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
# Copia .env.example a .env y edita tus credenciales
python app.py
# abre http://localhost:8000
```

## Render.com (Docker)
1) Subir este proyecto a GitHub.
2) En Render → New → Web Service → elige el repo (Dockerfile).
3) Variables de entorno (ejemplo):
```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=tu_usuario@gmail.com
SMTP_PASS=tu_app_password
SMTP_FROM=tu_usuario@gmail.com
SMTP_TO=dest1@empresa.com,dest2@empresa.com
SUBJECT_PREFIX=[DISPL]
IDLE_THRESHOLD_MIN=10
INTERVAL_SEC=10
NOTIFY_FIRST=true
NO_RECOVERY=false
COOLDOWN_MIN=10
VERBOSE=true
DB_PATH=/data/monitor.db
PORT=8000
FLASK_SECRET=cadena-secreta
START_SCHEDULER_ON_BOOT=true
ADMIN_USER=admin
ADMIN_PASS=supersecreto
```
4) Disks → Add Disk → Name: `data`, Size: `1 GB`, Mount Path: `/data`.
5) Healthcheck Path: `/healthz`.
6) Deploy. Abre la URL → agrega IDs → listo.

## Importante
- **No subas** contraseñas al repo. Usa *Environment Variables* en Render.
- Si no tienes tu `displ_api.py` real todavía, el stub en modo demo te deja probar emails.
- Para Gmail usa **App Password** (requiere 2FA). Para Outlook/Office365 usa `smtp.office365.com`.
