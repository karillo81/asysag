# Deploying AutoSys Agent to an Ubuntu server

Step-by-step for Ubuntu 22.04 LTS or 24.04 LTS. Two paths:

- **Path A — Docker Compose** (recommended): fewer moving parts, matches the dev image.
- **Path B — Native systemd + nginx**: more granular control; useful when you can't run Docker.

End state: app reachable at `https://agent.yourdomain.com`, login-gated (`root` + password from `.env`), agent talks to your AutoSys instance over the REST API and your chosen LLM over the public internet.

---

## 0. Prerequisites

| What | Why |
|---|---|
| Ubuntu 22.04 or 24.04 LTS, sudo access | The app supports Linux; both LTS lines work. |
| Public IP **or** a hostname pointing at the box | Required if you want HTTPS via Let's Encrypt. |
| Outbound internet from the box | To reach the LLM API (Anthropic / Gemini / OpenAI / Azure). |
| Network reach to your AutoSys host on TCP **9443** | The agent calls AEWS over HTTPS. Punch a hole if the AutoSys box is firewalled. |
| One LLM API key | `ANTHROPIC_API_KEY` (or one of the provider keys matching your `LITELLM_MODEL`). |

> **Picking a host size**: 2 vCPU / 4 GB RAM is comfortable. ChromaDB and the LangGraph runtime account for most of the memory; the rest is uvicorn + nginx. 10 GB disk is plenty (state grows slowly).

---

## 1. Base system prep (do this once, both paths)

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git ufw curl ca-certificates
sudo timedatectl set-timezone UTC      # keeps log timestamps unambiguous
```

Create a non-root user that will own the app (don't run as root):

```bash
sudo adduser --gecos "" autosys           # prompts for a password — set one, you'll need it for sudo
sudo usermod -aG sudo autosys             # let this account install packages in the next sections
sudo su - autosys                         # switch to it
```

> **Important**: do **not** pass `--disabled-password`. An account with no password can't run `sudo` (there's nothing to authenticate). If you already created the user that way, fix it before continuing: `exit` back to your sudo user, then `sudo passwd autosys` to set a password, then `sudo su - autosys` again.
>
> When `sudo` later prompts for a password, type the **`autosys` user's password** — not root's.

The rest of the instructions assume you're logged in as `autosys` in `/home/autosys`.

---

## 2. Clone the repo

```bash
git clone https://github.com/karillo81/asysag.git
cd asysag
```

If you cloned over HTTPS but want to push later, add your SSH key to GitHub and switch the remote:

```bash
git remote set-url origin git@github.com:karillo81/asysag.git
```

---

## 3. Configure `.env` (do this once, both paths)

```bash
cp .env.example .env
nano .env
```

Required to set, in order of importance:

```ini
# LLM
LITELLM_MODEL=anthropic/claude-sonnet-4-5
ANTHROPIC_API_KEY=sk-ant-...                 # replace with your key

# Login gate — CHANGE THE PASSWORD
AUTH_USERNAME=root
AUTH_PASSWORD=<a-strong-password>            # do not leave as `changeme`
SESSION_SECRET=<32+ random chars>            # generate with: openssl rand -base64 32

# AutoSys (live mode)
AUTOSYS_MODE=live
AUTOSYS_BASE_URL=https://<autosys-host>:9443/AEWS/
AUTOSYS_USER=ejmcommander
AUTOSYS_PASS=<password>
AUTOSYS_VERIFY_TLS=false                     # set to true if AutoSys has a real CA cert
```

Optional but worth setting in production:

```ini
SESSION_TTL_SECONDS=604800                   # 7 days; lower for tighter security
AUTOSYS_AUTOREP_HISTORY_STRATEGY=walk-runs   # default; days-flag if your autorep supports -d
AUTOSYS_LOG_MOUNT_ROOT=                      # only if you mount agent logs locally
```

`.env` is gitignored — make sure it stays that way (`grep -F '.env' .gitignore`).

---

## 4. Path A — Docker Compose (recommended)

### 4.1 Install Docker Engine + Compose plugin

```bash
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker autosys
exit && sudo su - autosys     # re-login so the group takes effect
cd asysag
```

Verify: `docker run hello-world` should succeed without `sudo`.

### 4.2 Fix the backend Dockerfile (one-time)

The shipped `backend/Dockerfile` installs only the core FastAPI deps — it skips the `[agent,rag]` extras, so the agent and RAG paths break. Patch it:

```bash
nano backend/Dockerfile
```

Replace the file with:

```dockerfile
FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir \
      "fastapi>=0.115" "uvicorn[standard]>=0.30" "python-dotenv>=1.0" \
      "pydantic>=2.0" "itsdangerous>=2.2" \
      "litellm>=1.50" "langgraph>=0.2" \
      "chromadb>=0.5"

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

(Splitting the deps from the source copy keeps the layer cache useful between code changes.)

### 4.3 Build and start

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f --tail 50          # Ctrl-C when both are "Application startup complete"
```

Smoke test:

```bash
curl -sf http://localhost:8000/health     # backend direct
curl -sf -o /dev/null -w "%{http_code}\n" http://localhost:5173       # frontend (nginx) — expect 200
curl -sf -o /dev/null -w "%{http_code}\n" http://localhost:5173/api/jobs   # expect 401 (login gate working)
```

Skip to **§6 — Reverse proxy + TLS** below to put HTTPS in front.

---

## 5. Path B — Native systemd + nginx

### 5.1 Install runtimes

```bash
sudo apt install -y python3.11 python3.11-venv python3-pip build-essential
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
node --version && python3.11 --version
```

### 5.2 Backend venv + install

```bash
cd /home/autosys/asysag/backend
python3.11 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e ".[agent,rag]"
```

Smoke-test manually before turning it into a service:

```bash
.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
# in another shell:
curl -sf http://127.0.0.1:8000/health
```

Stop the manual run with Ctrl-C.

### 5.3 Frontend build (static assets)

```bash
cd /home/autosys/asysag/frontend
npm ci
npm run build
# Output is in ./dist — nginx will serve this directly.
```

### 5.4 Backend systemd service

```bash
sudo tee /etc/systemd/system/autosys-agent.service >/dev/null <<'EOF'
[Unit]
Description=AutoSys Agent backend (FastAPI)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=autosys
Group=autosys
WorkingDirectory=/home/autosys/asysag/backend
EnvironmentFile=/home/autosys/asysag/.env
ExecStart=/home/autosys/asysag/backend/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5
# Sandbox: read-only filesystem except the state dir
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=/home/autosys/asysag/backend/state

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now autosys-agent.service
sudo systemctl status autosys-agent.service --no-pager
journalctl -u autosys-agent.service -f                      # follow logs; Ctrl-C to exit
```

### 5.5 nginx site config (terminates plain HTTP for now)

```bash
sudo apt install -y nginx
sudo tee /etc/nginx/sites-available/autosys-agent >/dev/null <<'EOF'
server {
    listen 80;
    server_name agent.yourdomain.com;

    root /home/autosys/asysag/frontend/dist;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;                    # SSE chat stream needs this
        proxy_read_timeout 600s;
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/autosys-agent /etc/nginx/sites-enabled/autosys-agent
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

Smoke-test over port 80 from a browser before adding TLS.

---

## 6. Reverse proxy + TLS (both paths)

Point your DNS A record at the server's public IP, wait for propagation (`dig +short agent.yourdomain.com` should return your IP), then:

```bash
sudo apt install -y certbot python3-certbot-nginx

# Path A (Docker): use the standalone challenge — Docker has port 80 free? Check first.
#   Easier: terminate TLS in a host nginx that proxies to docker on 5173, same shape as Path B.

# Path B (native): the nginx site is already there. certbot updates it in place.
sudo certbot --nginx -d agent.yourdomain.com \
  --redirect --agree-tos -m you@yourdomain.com --no-eff-email
```

Certbot writes a `listen 443 ssl;` block, drops a 301 redirect from :80 to :443, and installs a systemd timer that auto-renews. Verify:

```bash
sudo certbot renew --dry-run
curl -I https://agent.yourdomain.com         # expect HTTP/2 200
```

**For Path A**, swap the nginx site config above to proxy `/` to `http://127.0.0.1:5173` (the dockerised frontend) instead of serving local `dist/` files. Everything else stays the same.

---

## 7. Firewall (UFW)

Lock everything down except SSH + HTTP/HTTPS:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw enable
sudo ufw status verbose
```

Path A also: do **not** publish ports 5173/8000 externally — the Docker Compose `ports:` block binds them to all interfaces by default. Either:

- Edit `docker-compose.yml` and change `"5173:80"` / `"8000:8000"` to `"127.0.0.1:5173:80"` / `"127.0.0.1:8000:8000"`, then `docker compose up -d --force-recreate`, or
- Trust UFW to block them — but the bind-to-loopback change is safer.

---

## 8. Production hardening checklist

- [ ] `AUTH_PASSWORD` is not `changeme`.
- [ ] `SESSION_SECRET` is set (otherwise every backend restart logs everyone out and warns in journal).
- [ ] `AUTOSYS_VERIFY_TLS=true` if your AutoSys cert is signed by a real CA — flip back to `false` only with intent.
- [ ] `ANTHROPIC_API_KEY` (or chosen provider key) is in `.env`, not on the command line and not in git.
- [ ] The `state/` dir is in your backup rotation. It contains `agent.sqlite` (memory/incidents) and `chromadb/` (RAG vectors). Rebuilding ChromaDB is cheap; losing memory loses past incidents.
- [ ] HTTPS is terminated, plain :80 redirects to :443.
- [ ] UFW is enabled, status shows ports 22/80/443 only.
- [ ] You've changed the `autosys` user's password or disabled password login entirely (`sudo passwd -l autosys` if it's SSH-key only).

---

## 9. Operating it

### Logs

```bash
# Path A
docker compose logs -f --tail 200 agent-backend
docker compose logs -f --tail 200 agent-frontend

# Path B
journalctl -u autosys-agent.service -f --since "1 hour ago"
sudo tail -f /var/log/nginx/access.log /var/log/nginx/error.log
```

### Updates

```bash
cd ~/asysag
git pull
# Path A
docker compose up -d --build
# Path B
cd backend && .venv/bin/pip install -e ".[agent,rag]"
cd ../frontend && npm ci && npm run build
sudo systemctl restart autosys-agent.service
sudo systemctl reload nginx
```

### Health check from outside

```bash
curl -sf https://agent.yourdomain.com/api/health
# {"status":"ok","mode":"live","model":"anthropic/claude-sonnet-4-5"}
```

### Common failure modes

| Symptom | Likely cause | Where to look |
|---|---|---|
| Login screen loads, login returns 500 | `SESSION_SECRET` malformed (contains a stray quote, etc.) | `journalctl -u autosys-agent.service` |
| Agent answers "no AutoSys connection" | `AUTOSYS_BASE_URL` wrong / `:9443` blocked | `curl -vk -u $AUTOSYS_USER:$AUTOSYS_PASS $AUTOSYS_BASE_URL/job` from the box |
| 502 from nginx on `/api/*` | Backend not running or bound to a different port | `systemctl status autosys-agent` or `docker compose ps` |
| Chat stream cuts off after ~60s | `proxy_buffering` on or `proxy_read_timeout` too low | Re-check the nginx site config above |
| `mode: mock` when you expected live | `.env` not loaded — check working directory of the service; `EnvironmentFile=` must be an absolute path | `systemctl cat autosys-agent.service` |

---

## 10. Optional: SSH-mounted AutoSys logs

If you want real log content (not just the JIL path), mount the AutoSys agent's `job_logs/` directory on the deploy box via sshfs:

```bash
sudo apt install -y sshfs
mkdir -p /home/autosys/autosys-logs
sshfs autosys@<autosys-host>:/opt/CA/WorkloadAutomationAE/SystemAgent/WA_AGENT/job_logs \
      /home/autosys/autosys-logs -o IdentityFile=/home/autosys/.ssh/id_ed25519_new,allow_other,reconnect
```

Add `AUTOSYS_LOG_MOUNT_ROOT=/home/autosys/autosys-logs` to `.env`, restart the backend, and `get_job_log` will return actual log content instead of a path-only message.

Make the mount persistent across reboots with an `/etc/fstab` entry — see the sshfs man page for the exact line.
