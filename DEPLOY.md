# Running unattended for months (while your devices are off)

Your laptop can't do this — a loop stops the moment the machine sleeps or shuts
down. To trade "while my phone's off, for months," the loop has to live on a
host that is **always on**. Two realistic options:

| Host | Cost | Good for |
|---|---|---|
| A small cloud VM (Hetzner, DigitalOcean, Linode, AWS Lightsail) | ~$4–6/month | Set-and-forget, always online, easy to reach |
| A Raspberry Pi you leave plugged in at home | one-time ~$50 | No monthly fee; depends on your home power/internet |

The auth model is built for this: you log in **once on a device with a browser**,
which produces a refreshable token file; you copy that file to the server, and
the loop refreshes its own access tokens from then on — no browser needed again.

---

## Step 1 — Log in once (on your laptop, has a browser)

```bash
trading-agent login
```

A browser opens for Robinhood consent. On success it writes a token file (default
`~/.trading-agent/robinhood_oauth.json`) containing a **refresh token**. Treat
that file like a password.

## Step 2 — Get a small always-on Linux server

Any provider works. You want the cheapest Ubuntu VM they offer (1 shared vCPU,
1 GB RAM is plenty). Note its IP and your SSH login.

## Step 3 — Put the project + your files on the server

```bash
# on the server
git clone https://github.com/obinnasoki0/trading-agent && cd trading-agent
mkdir -p data
cp config.robinhood.example.yaml data/config.yaml    # edit as you like
```

Copy your token file from your laptop into `data/` on the server:

```bash
# from your laptop (Windows PowerShell: use scp or WinSCP)
scp ~/.trading-agent/robinhood_oauth.json  you@SERVER_IP:~/trading-agent/data/robinhood_oauth.json
```

## Step 4 — Run it, with auto-restart

With Docker (simplest — survives reboots):

```bash
# on the server, in the repo folder
docker compose up -d --build
docker compose logs -f            # watch the [DRY-RUN]/[LIVE] lines
```

No Docker? Use systemd instead:

```ini
# /etc/systemd/system/trading-agent.service
[Unit]
Description=trading-agent loop
After=network-online.target

[Service]
WorkingDirectory=/home/YOU/trading-agent
Environment=ROBINHOOD_TOKEN_PATH=/home/YOU/trading-agent/data/robinhood_oauth.json
ExecStart=/home/YOU/trading-agent/.venv/bin/trading-agent loop --config /home/YOU/trading-agent/data/config.yaml
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now trading-agent
journalctl -u trading-agent -f     # watch it
```

## Step 5 — Stay in dry-run until you trust it

`data/config.yaml` ships with `allow_live: false`. Let it run for days in
dry-run and read the logs. Only when the decisions look sane do you set
`allow_live: true` and add `--i-understand-the-risks` (edit the compose `command`
or the systemd `ExecStart`). Real money starts there, deliberately.

---

## Honest limits for "months unattended"

- **Refresh tokens may not live forever.** If Robinhood caps their lifetime or
  requires periodic re-consent, the loop will eventually fail to refresh and
  **stop trading** (it won't do anything unsafe — it just halts). When that
  happens you re-run `trading-agent login` on your laptop and recopy the file.
  How long that is depends on Robinhood, not this code. Check on it periodically.
- **$15 (or any tiny balance) can't "maximize returns."** Position sizes scale
  with equity; at a small balance orders are sub-dollar and may not fill. Fund
  only what you can afford to lose, and understand the shipped strategies have no
  proven edge — they enforce discipline, they don't guarantee profit.
- **Watch it, especially at first.** Unattended ≠ unmonitored. Check the logs and
  your Robinhood account regularly, particularly in the first weeks and after any
  market turbulence. The risk manager caps and kill-switch limit damage; they
  don't eliminate it.
- **Keep the token file secret.** Anyone with it can trade your Agentic account.
  It's chmod 600 by default; don't commit it or share it.
