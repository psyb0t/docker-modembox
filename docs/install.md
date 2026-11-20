---
title: Install ModemBox
description: Install ModemBox with Docker, manage image versions and upgrades, and preserve the SQLite data and SIM PIN key.
nav_order: 2
---

# Install ModemBox

## Docker Compose

Run the installer as your normal user on the Linux Docker host. You need Docker Compose, curl, and OpenSSL. Plug in your modem first so the installer can read its serial port group. With no serial device present, it uses the host's `dialout` group when available.

```bash
curl -fsSL https://raw.githubusercontent.com/psyb0t/docker-modembox/main/install.sh | bash
modembox start
```

The user install puts the command in `~/.local/bin` and creates `compose.yaml`, a private `.env` with the operator token, and `data/` under `${XDG_CONFIG_HOME:-$HOME/.config}/modembox`. If `~/.local/bin` is outside your PATH, run `~/.local/bin/modembox start` or add that directory to your PATH. Keep the same `XDG_CONFIG_HOME` when you run the command.

For a system install, run the following. It puts the command in `/usr/local/bin` and the configuration and data in `/etc/modembox`.

```bash
curl -fsSL https://raw.githubusercontent.com/psyb0t/docker-modembox/main/install.sh |
  sudo bash -s -- --system
sudo modembox start
```

The installer generates a random `MODEMBOX_TOKEN` when the installed `.env` does not exist. If the file exists, the installer keeps its contents. Open http://localhost:8000 and paste the value of `MODEMBOX_TOKEN` into the **Access token** field. For a system install, the token is in `/etc/modembox/.env`.

From a source checkout, you can also run `bash install.sh` or `sudo bash install.sh --system`.

`data/` contains `modembox.sqlite3`, the PIN encryption key, and service logs. Back up the directory while the service is stopped.

### Manage the installation

| Command | What it does |
|---|---|
| `modembox start` | Starts the configured stack |
| `modembox stop` | Stops the stack |
| `modembox status` | Shows container status |
| `modembox logs -f` | Follows the logs |
| `modembox upgrade` | Pulls the current tag and starts the stack with it |
| `modembox upgrade --version vX.Y.Z` | Pulls a published tag, saves the pin, and starts the stack |
| `modembox upgrade --version latest` | Switches to the rolling image |
| `modembox uninstall` | Removes the stack and command, retaining the token and data |
| `modembox uninstall --purge-data` | Also deletes the token, inbox, PIN key, logs, and local Tailscale state |

A fresh install selects `psyb0t/modembox:latest`. To pin a published tag from the start, pass `--version` to the installer. A failed pull keeps the current pin. For local builds, run `make build` and `bash install.sh --version local`, then start the stack. After rebuilding, run `modembox upgrade` to use the new local image.

The installed command works from any directory. You can also use native `docker compose` commands from the configuration directory. Put local Compose changes in `compose.override.yaml` so reinstalling can regenerate the base file.

### Tailscale access

Enable MagicDNS and HTTPS certificates in your tailnet. In the installed `.env`, add the following and keep your ModemBox token in place. For an existing installation, rerun the installer first to add the sidecar to Compose.

```dotenv
MODEMBOX_TAILSCALE_ENABLED=true
MODEMBOX_TAILSCALE_HOSTNAME=modembox
# MODEMBOX_TAILSCALE_AUTHKEY=your-tailscale-auth-key
```

Run `modembox start`. On first start, use an auth key or follow the login URL from `modembox logs -f tailscale`. Open the node's full HTTPS name from the Tailscale Machines page. Tailscale Serve makes the web console, API, WebSockets, and MCP available to clients allowed by your tailnet policy. HTTPS also lets the browser use its microphone remotely.

Set `MODEMBOX_TAILSCALE_ENABLED=false` and run `modembox start` to remove the sidecar. Its login persists across disables, upgrades, and standard uninstalls. To delete that local login, use `modembox uninstall --purge-data`. To remove the node from the tailnet, delete it from the Tailscale Machines page.

## Direct Docker

Use a private `.env` with `MODEMBOX_TOKEN` set, or use the auth override below. Set the serial group from the host modem. The example uses `ttyUSB2` to read that group. ModemBox picks the actual control and audio ports at runtime.

```bash
serial_gid="$(stat -c %g /dev/ttyUSB2)"
docker run --rm --init --name modembox \
  --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --cap-drop ALL --security-opt no-new-privileges \
  --memory 512m --cpus 1 --pids-limit 256 \
  --log-opt max-size=10m --log-opt max-file=5 \
  -p 127.0.0.1:8000:8000 \
  --env-file .env --group-add "$serial_gid" \
  --mount type=bind,src=/dev,dst=/dev,readonly \
  --device-cgroup-rule 'c 188:* rw' \
  --mount type=volume,source=modembox-data,target=/data \
  psyb0t/modembox:latest
```

Both methods give ModemBox a read-only view of host `/dev` so it can see hotplugged devices. The device rule grants read/write access to USB serial nodes, subject to their Unix permissions. Only known USB layouts are picked automatically. See [device setup](devices.md) for explicit port mappings and modem support.

## Authentication

ModemBox requires a valid `MODEMBOX_TOKEN` at startup by default. Set `MODEMBOX_DISABLE_AUTH=true` in `.env` to let browser, REST, WebSocket, and MCP clients connect without a token. Recreate the container after editing `.env`. Keep a tokenless instance bound to loopback or behind a proxy that authenticates users. For remote browser microphone access, use HTTPS and forward WebSocket upgrades.
