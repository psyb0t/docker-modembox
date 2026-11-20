---
title: MCP and agent integrations
description: Connect an MCP client to ModemBox, configure the operator token, and look up every tool for SMS, calls, audio, and AT commands.
nav_order: 5
---

# MCP and agent integrations

Connect a Streamable HTTP client to `http://localhost:8000/mcp`. Send `Authorization: Bearer <operator-token>` unless the instance explicitly disables authentication. Keep the endpoint on a trusted host and use HTTPS outside localhost.

Start with `list_modems` to find the device ID and its available capabilities. The same modem state and activity appear in the browser when an MCP client acts.

| Tool | What it does |
|---|---|
| `list_modems` | List modems, capabilities, SIM state, and active calls |
| `list_sms` | List recent messages. Use the oldest returned ID as `before` to page back |
| `get_sms` | Fetch a single message by ID |
| `send_sms` | Submit an SMS and return the modem's submission status |
| `unlock_sim` | Submit a SIM PIN, with an option to remember it after a successful unlock |
| `forget_pin` | Remove the saved PIN for the current SIM |
| `dial` | Place a call |
| `answer` | Answer an incoming call |
| `hangup` | End the current call |
| `play_audio` | Play a base64-encoded mono 8 kHz signed PCM16 WAV, up to 30 seconds |
| `stop_audio` | Stop clip playback |
| `capture_audio` | Read 1 to 10 seconds of recent caller audio as a base64 WAV |
| `modem_at` | Issue a single AT command permitted by the server |
| `list_hooks` | List rules, HTTP destinations, call history, or action runs |
| `save_hook_rule` | Create or replace a hook rule |
| `save_hook_destination` | Create or replace an HTTP destination with write-only credentials |
| `delete_hook` | Delete a rule or destination and cancel its queued actions |
| `preview_hook` | Dry-run a rule against a saved SMS or call |

For call audio, wait until the call is active and check the modem's audio capability before playing or capturing. If an SMS send returns an unknown result, check the outbox before retrying. See the [API reference](api.md) for audio frames and authentication.

## Agent packages

The [repository README](https://github.com/psyb0t/docker-modembox#agent-integrations) lists the Claude Code, Codex, and OpenClaw install commands. The Claude Code plugin prompts for the server base URL and token. The OpenClaw bridge reads `MODEMBOX_URL` and, when needed, `MODEMBOX_TOKEN` from its environment and appends `/mcp` to the URL. It sends tool inputs and the bearer token to that endpoint.

The [MCP Registry manifest](https://github.com/psyb0t/docker-modembox/blob/main/server.json) describes the Docker package. The [Glama maintainer file](https://github.com/psyb0t/docker-modembox/blob/main/glama.json) identifies the maintainer for a Glama listing.
