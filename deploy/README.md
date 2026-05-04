# Deploying scrumai-sync

This deploys the FastAPI sync API to a Linux box (target: `amd.syd.oracle`,
domain `scrumai.oldcai.com`). The Forge plugin in `scrumai-forge` calls
`POST /sync/tick/{jira_key}` on this server to trigger an immediate sync
after user actions; a background polling task keeps things in step
otherwise.

This is a demo deploy. Single VM, single uvicorn worker, plain nginx +
certbot. No HA, no ops, no backup. If you want HA, this isn't the doc.

## 1. Prerequisites on the target box

- Ubuntu 22.04+ (or any distro with systemd + apt)
- nginx already installed and running (`systemctl status nginx`)
- Outbound network to atlassian.net + the VK backend
- `sudo` available

Install Python 3.12 and `uv` (the rest of the project assumes both):

```bash
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv git nginx certbot python3-certbot-nginx
curl -LsSf https://astral.sh/uv/install.sh | sudo sh
sudo install -m 0755 ~/.local/bin/uv /usr/local/bin/uv  # if uv landed in user dir
```

## 2. Service user + code

```bash
sudo useradd -r -s /usr/sbin/nologin -d /opt/scrumai scrumai
sudo install -d -m 0755 -o scrumai -g scrumai /opt/scrumai

sudo -u scrumai git clone https://github.com/<you>/scrumai-prompts.git \
    /opt/scrumai/scrumai-prompts

cd /opt/scrumai/scrumai-prompts
sudo -u scrumai uv sync
```

`uv sync` creates `/opt/scrumai/scrumai-prompts/.venv`. The systemd unit
runs `uv run python main.py serve` from that directory, so the venv
resolves automatically.

## 3. Environment file

```bash
sudo install -d -m 0750 -o root -g scrumai /etc/scrumai
sudo install -m 0640 -o root -g scrumai \
    /opt/scrumai/scrumai-prompts/deploy/sync.env.example \
    /etc/scrumai/sync.env
sudoedit /etc/scrumai/sync.env
```

Set `JIRA_*`, `VIBE_BACKEND_URL`, and a real `SCRUMAI_API_KEY`. The API key
is what the Forge plugin sends as `X-API-Key`; treat it as a secret.
Generate one with `openssl rand -hex 32`.

## 4. systemd unit

```bash
sudo install -m 0644 \
    /opt/scrumai/scrumai-prompts/deploy/scrumai-sync.service \
    /etc/systemd/system/scrumai-sync.service
sudo systemctl daemon-reload
sudo systemctl enable --now scrumai-sync
journalctl -u scrumai-sync -f
```

You should see `scrumai-sync starting: host=127.0.0.1 port=8000 ...`. The
service binds to loopback only — public access goes through nginx.

Local sanity:

```bash
curl http://127.0.0.1:8000/health   # -> {"status":"ok"}
curl -X POST http://127.0.0.1:8000/sync/tick \
     -H "X-API-Key: $(sudo grep SCRUMAI_API_KEY /etc/scrumai/sync.env | cut -d= -f2)"
```

## 5. nginx vhost + TLS cert

### 5a. Install vhost first (HTTP-only, for the ACME challenge)

The shipped vhost has both 80 + 443 server blocks. Comment out the 443
block initially since the certs don't exist yet:

```bash
sudo cp /opt/scrumai/scrumai-prompts/deploy/scrumai.oldcai.com.nginx.conf \
    /etc/nginx/sites-available/scrumai.oldcai.com
sudoedit /etc/nginx/sites-available/scrumai.oldcai.com
# (comment out the `server { listen 443 ssl; ... }` block)

sudo install -d -m 0755 /var/www/certbot
sudo ln -sf /etc/nginx/sites-available/scrumai.oldcai.com \
    /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

### 5b. Get the cert

**Standard path** (port 80 free, --nginx plugin):

```bash
sudo certbot --nginx -d scrumai.oldcai.com \
    --agree-tos -m [email protected] --no-eff-email
```

**Port 80 occupied by something else** (this is the case on `amd.syd.oracle`
where `apiproxy` listens on 80): use the webroot challenge — nginx
already serves `/.well-known/acme-challenge/` from `/var/www/certbot/`
via the vhost, so certbot can drop the token without touching port 80.

```bash
sudo certbot certonly --webroot -w /var/www/certbot \
    -d scrumai.oldcai.com \
    --agree-tos -m [email protected] --no-eff-email
```

This works because nginx already accepts traffic on port 80 for
`scrumai.oldcai.com` via that vhost — `apiproxy` and nginx coexist on
different IPs/SNI/etc, or `apiproxy` is the *only* listener and you'll
need to free port 80 first. Diagnose with `sudo ss -tlnp 'sport = :80'`.

If you can't free port 80 at all, fall back to **DNS-01**:

```bash
sudo certbot certonly --manual --preferred-challenges dns \
    -d scrumai.oldcai.com
# then add the TXT record certbot prints to your DNS provider
```

### 5c. Re-enable the 443 block

```bash
sudoedit /etc/nginx/sites-available/scrumai.oldcai.com
# uncomment the listen 443 ssl block
sudo nginx -t && sudo systemctl reload nginx
```

Auto-renewal: `certbot.timer` (installed with the package) renews on its
own. Verify with `sudo systemctl list-timers | grep certbot`.

## 6. Smoke test from outside

```bash
curl https://scrumai.oldcai.com/health
# -> {"status":"ok"}

curl -X POST https://scrumai.oldcai.com/sync/tick \
     -H "X-API-Key: $YOUR_KEY"
# -> JSON TickReport

curl -X POST https://scrumai.oldcai.com/sync/tick/SCRUM-123 \
     -H "X-API-Key: $YOUR_KEY"
```

A wrong / missing key returns 401. `/health` is intentionally
unauthenticated for uptime monitors.

## 7. Wire up the Forge plugin

In `scrumai-forge`, point the plugin at the new endpoint and inject the
key. The plugin should `POST /sync/tick/{key}` after any user action that
mutates Jira (status change, brainstorm save, etc.) — the server
deduplicates against its own writes via `MirrorLedger` so back-to-back
calls from polling + Forge don't double-write.

## 8. Operations cheat-sheet

```bash
# tail logs
journalctl -u scrumai-sync -f

# restart after config change
sudo systemctl restart scrumai-sync

# rotate the API key
sudoedit /etc/scrumai/sync.env  # change SCRUMAI_API_KEY=...
sudo systemctl restart scrumai-sync
# then update the Forge plugin's stored secret

# nginx access log (Forge calls)
sudo tail -f /var/log/nginx/scrumai.access.log

# pull updates
cd /opt/scrumai/scrumai-prompts
sudo -u scrumai git pull
sudo -u scrumai uv sync
sudo systemctl restart scrumai-sync
```

## 9. Why these defaults

- **Single uvicorn worker** — the engine's `MirrorLedger` and adaptive
  polling state live in process memory. Multi-worker would split that
  state and produce duplicate writes / wrong intervals.
- **Loopback-only bind + nginx in front** — TLS termination at nginx
  keeps the FastAPI app simple, and the loopback bind is a defense
  against accidentally exposing the unauthenticated `/health` when the
  vhost is broken.
- **Adaptive polling (30s hot / 300s cold)** — Forge calls drive
  immediate sync; polling is a safety net so a missed webhook doesn't
  lose state. Hot window is 1h after the last write.
- **EnvironmentFile (not `--env-file`)** — keeps secrets out of the unit
  file, lets `sudoedit` rotate them without `systemctl daemon-reload`.
