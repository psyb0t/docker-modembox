# ModemBox

[![CI](https://github.com/psyb0t/docker-modembox/actions/workflows/pipeline.yml/badge.svg?branch=main)](https://github.com/psyb0t/docker-modembox/actions/workflows/pipeline.yml)
[![version](https://raw.githubusercontent.com/psyb0t/docker-modembox/badges/version.svg)](https://github.com/psyb0t/docker-modembox/releases)
[![license](https://raw.githubusercontent.com/psyb0t/docker-modembox/badges/license.svg)](LICENSE)
[![Docker Pulls](https://img.shields.io/docker/pulls/psyb0t/modembox?style=flat-square)](https://hub.docker.com/r/psyb0t/modembox)

ModemBox turns an attached USB cellular modem into a shared browser console, REST API and MCP server. Read and send SMS, inspect the SIM and radio, place or answer calls, and stream audio with supported devices.

One operator token controls the instance by default. Each browser receives a snapshot on connect and live updates from other browsers, HTTP clients and MCP tools. FastAPI serves the static SvelteKit build.

## Contents

- [Run on Linux](#run-on-linux)
- [Hardware](#hardware)
- [Agent integrations](#agent-integrations)
- [Documentation](#documentation)
- [Development](#development)
- [Sponsor](#sponsor)
- [License](#license)

## Run on Linux

Install ModemBox from this checkout, then start it:

```bash
bash install.sh
modembox start
```

Open http://localhost:8000 and enter the token from the installed `.env`. The installer selects `psyb0t/modembox:latest` on first install and configures supported USB modem discovery. SQLite and the PIN key live in `data/` beside the Compose file.

The installer puts the command in `~/.local/bin`. Use `modembox upgrade` to refresh the selected image, `modembox upgrade --version vX.Y.Z` to select a published tag, and `modembox uninstall` to remove the stack and command while keeping the data. For a system install, use `sudo bash install.sh --system`.

Prefer direct Docker? Run `psyb0t/modembox:latest` with your own `.env`, a persistent `/data` mount, and access to the USB serial devices. See the [installation guide](https://psyb0t.github.io/docker-modembox/install/) for the full command and advanced options. Microphone access needs localhost or HTTPS.

## Hardware

ModemBox reads manufacturer, model and firmware before selecting a profile. The Huawei E173 profile uses a separate serial PCM port. Other Huawei devices fall back to generic call and SMS control unless a known audio profile matches. Explicitly map a control port to use other vendors with the generic profile. Discoverable Huawei layouts can also be detected by Linux USB interface identity when the process can see the stable device paths and sysfs.

Each serial port needs one owner. Configure host modem software to leave ModemBox's control and audio ports alone. If Docker loses a passed device after USB re-enumeration, recreate the container with the updated node. Do not assume two dongles assign the same tty numbers or support the same voice commands.

See [device setup and limitations](https://psyb0t.github.io/docker-modembox/devices/).

## Agent integrations

Connect a Streamable HTTP MCP client to http://localhost:8000/mcp with the same operator bearer token when auth is enabled. The tools act on the live modem shared by the web UI. List devices first to learn their current capabilities.

Once released, install the skill and optional stdio bridge through the shared catalogs:

```bash
claude plugin marketplace add psyb0t/agents
claude plugin install modembox@psyb0t

codex plugin marketplace add psyb0t/agents
codex plugin add modembox@psyb0t

openclaw skills install @psyb0t/docker-modembox
openclaw plugins install clawhub:@psyb0t/modembox
```

For the stdio bridge, set `MODEMBOX_URL` to the base URL and `MODEMBOX_TOKEN` to the bearer token when auth is enabled. The bridge appends `/mcp`. See [MCP and agent integrations](https://psyb0t.github.io/docker-modembox/mcp/).

## Documentation

[Read the ModemBox documentation](https://psyb0t.github.io/docker-modembox/) for installation, supported devices, and the [REST and WebSocket API](https://psyb0t.github.io/docker-modembox/api/).

## Development

```bash
make lint
make test
make build
```

Tests run in Docker. `make test-unit` covers codecs and discovery. `make test-integration` uses a simulated modem over Linux pseudo terminals with the real HTTP, MCP, WebSocket, and SQLite stack. `make test-ui` opens the console in a disposable Stealthy Auto Browse container, exercises messages and calls against a simulated modem, then removes the test containers. It starts the browser on the fixture's isolated network.

`make test-real MODEMBOX_TEST_DEVICE=/dev/ttyUSB2` reads identity, SIM status, registration, and voice capability from a selected physical modem. It does not send SMS, place calls, or submit a PIN. Run it with the selected control port free.

For a physical SMS reply check, set `MODEMBOX_TEST_NUMBER` to the test phone's international number in a private `.env.test` file. Set `MODEMBOX_TEST_PIN` if the modem's SIM needs unlocking. Run `make test-real-sms-receive MODEMBOX_TEST_DEVICE=/dev/ttyUSB2` with the control port free. The modem texts the test phone and asks for a `TEST1` reply. Set `MODEMBOX_TEST_SMS_TEXT` to use another reply. The test passes when a new SMS from the test phone matches the expected text exactly. It fails if the modem rejects the invitation or if no match arrives within five minutes of submission.

The image builds the frontend in a separate stage and packages only its static output. This service uses one process so serial ports, call audio, and browser snapshots share one owner.

Released images target Linux amd64 and arm64. See [AGENTS.md](AGENTS.md) for repository conventions.

A push to `main` runs checks and publishes `:latest` and `:sha-<commit>`. A version tag waits for the successful `main` run for its commit, promotes that image to the version tag, and publishes the GitHub release, ClawHub packages, and MCP registry metadata.

## Sponsor

[Sponsor on GitHub](https://github.com/sponsors/psyb0t)

<a href="https://www.buymeacoffee.com/psyb0t"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-blue.png" alt="Buy Me a Coffee" height="60" width="217"></a>

## License

[AGPL-3.0-only](LICENSE).
