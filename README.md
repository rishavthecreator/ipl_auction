# IPL Auction — Public Website

Multiplayer IPL-style cricket auction you can host on the open internet.

**Play modes**
- Multiplayer (locally) — same screen / party
- Create a room code — host shares a code; friends join on their devices
- Join a room — enter a host’s code

Single player vs CPU is **Developing** and disabled.

---

## 1. Run locally (dev)

```bash
cd ipl_auction_public
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export FLASK_ENV=development
export SECRET_KEY=dev-only-not-for-prod
python3 app.py
```

Open http://127.0.0.1:5000

Health check: http://127.0.0.1:5000/health

---

## 2. Put the code on GitHub

1. Create a **new empty GitHub repository** (public or private).
2. From this folder:

```bash
cd ipl_auction_public
git init
git add .
git commit --trailer "Co-authored-by: Cursor <cursoragent@cursor.com>" -m "Initial IPL Auction public website"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```

Do **not** commit `.env`, `data/rooms/*.json`, or `data/auction_state.json` (already gitignored).

---

## 3. Deploy on Render (easiest free/public URL)

1. Sign up at https://render.com with GitHub.
2. **New → Web Service** → connect your repo.
3. Settings:

| Field | Value |
|-------|--------|
| Language / Runtime | Python 3 |
| Root Directory | leave blank if repo root is this app |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `gunicorn -b 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 120 app:app` |

4. **Environment** → Add:

| Key | Value |
|-----|--------|
| `SECRET_KEY` | long random string (e.g. `python3 -c "import secrets; print(secrets.token_hex(32))"`) |
| `FLASK_ENV` | `production` |
| `SESSION_COOKIE_SECURE` | `1` |

5. Click **Create Web Service** / Deploy.
6. When live, open the `https://….onrender.com` URL and share it.

**Important:** Keep **1 instance**. Room codes are stored as files on disk; multiple instances will not share rooms unless you add shared storage later.

Free Render services can sleep after idle time — first load may be slow.

---

## 4. Deploy on Railway (alternative)

1. https://railway.app → New Project → Deploy from GitHub.
2. Add variables: `SECRET_KEY`, `FLASK_ENV=production`.
3. Railway detects `Procfile` / runs gunicorn.
4. Generate a public domain under Settings → Networking.

Same rule: **one replica** for room multiplayer.

---

## 5. Deploy with Docker (VPS / Fly.io / any host)

```bash
docker build -t ipl-auction .
docker run -p 5000:5000 -e SECRET_KEY=your-secret -e FLASK_ENV=production ipl-auction
```

For a VPS with a domain:

1. Point DNS A record to the server.
2. Run the container (or gunicorn directly).
3. Put **nginx** or **Caddy** in front for HTTPS.
4. Example Caddyfile:

```
auction.yourdomain.com {
    reverse_proxy localhost:5000
}
```

---

## 6. Custom domain (optional)

On Render: Settings → Custom Domains → add `auction.yourdomain.com` and follow DNS instructions.  
HTTPS is provisioned automatically.

---

## Ops notes

| Topic | Guidance |
|-------|----------|
| Workers | Use **1** gunicorn worker (file-backed rooms + in-process locks) |
| Persistence | Free tiers wipe disk on restart — rooms are short-lived anyway |
| CSRF | Built-in; browsers must allow cookies on your domain |
| Solo mode | Disabled (`SOLO_MODE_ENABLED = False` in `config.py`) |

---

## Project layout

```
app.py              # Flask entry (gunicorn: app:app)
config.py
blueprints/         # pages + JSON API
services/           # auction + rooms
templates/          # UI
data/players.json   # roster (committed)
data/all_stars.json
data/rooms/         # runtime only (gitignored)
```
