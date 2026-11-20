---
title: Install ModemBox
description: Install ModemBox with Docker, manage image versions and upgrades, and preserve the SQLite data and SIM PIN key.
nav_order: 2
---

# Install ModemBox

## Docker Compose

Run [install.sh](https://github.com/psyb0t/docker-modembox/blob/main/install.sh) from a source checkout as your normal user on the Linux Docker host. It needs Docker Compose and OpenSSL. Plug in your modem before installing so the installer can read its serial port group. With no serial device present, it uses the host's `dialout` group when available.

```bash
bash install.sh
modembox start
```

The user install puts the command in `~/.local/bin` and creates `compose.yaml`, a private `.env` with the operator token, and `data/` under `${XDG_CONFIG_HOME:-$HOME/.config}/modembox`. If `~/.local/bin` is outside your PATH, run `~/.local/bin/modembox start` or add that directory to your PATH. Keep the same `XDG_CONFIG_HOME` when you run the command.

For a system install, run `sudo bash install.sh --system`. It puts the command in `/usr/local/bin` and the configuration and data in `/etc/modembox`. Use `sudo modembox` to manage it. Both install types publish http://localhost:8000. Open the console and enter the token from the installed `.env`.

`data/` contains `modembox.sqlite3`, the PIN encryption key, and service logs. Back up the directory while the service is stopped.

### Manage the installation

| Command | What it does |
|---|---|
| `modembox start` | Starts the configured stack |
| `modembox stop` | Stops the service |
| `modembox status` | Shows the container status |
| `modembox logs -f` | Follows the logs |
| `modembox upgrade` | Pulls the current tag and starts the stack with it |
| `modembox upgrade --version vX.Y.Z` | Pulls a published tag, saves the pin, and starts the stack |
| `modembox upgrade --version latest` | Switches to the rolling image |
| `modembox uninstall` | Removes the stack and command, retaining the token and data |
| `modembox uninstall --purge-data` | Also deletes the token, inbox, PIN key, and service logs |

A fresh install selects `psyb0t/modembox:latest`. To pin a published tag from the start, pass `--version` to the installer. A failed pull keeps the current pin. For local builds, run `make build` and `bash install.sh --version local`, then start the stack. After rebuilding, run `modembox upgrade` to use the new local image.

The installed command works from any directory. You can also use native `docker compose` commands from the configuration directory. Put local Compose changes in `compose.override.yaml` so reinstalling can regenerate the base file.

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
