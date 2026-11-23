---
title: API
description: Look up ModemBox REST endpoints, authentication, live modem events, and WebSocket call audio frames.
---

# API

Protected REST and MCP calls require an Authorization bearer header by default. In that mode, each WebSocket expects the operator token as its first text frame. With `MODEMBOX_DISABLE_AUTH=true`, omit the header and the first token frame. Tokenless endpoints reject a browser's cross-origin requests. All clients share one instance.

## REST reference

Expand **API** in the sidebar for each operation's request fields, responses, and curl example. Download the [OpenAPI JSON](https://psyb0t.github.io/docker-modembox/reference/openapi.json) for client generation. A running ModemBox instance also serves the schema at `GET /openapi.json`.

The curl examples use `MODEMBOX_URL` for your server address and `MODEMBOX_TOKEN` for the operator token. Set `MODEMBOX_MODEM_ID` to a modem ID returned by `GET /v1/modems` before running a modem-specific example. Replace the example numbers, PINs, and record IDs with values from your own instance.

See [MCP and agent integrations](mcp.md) for Streamable HTTP at `/mcp` and the tool list. Read-only AT commands are allowlisted. Raw writes require an operator opt-in and are unavailable during a call.

Use the ID returned by `GET /v1/modems` for modem actions and SMS filtering. Auto-discovered modems with a usable IMEI use a hardware UUID. Older messages retain their original modem ID in unfiltered message history. See [device identity](devices.md).

REST failures return a stable code and message.

## SMS submission status

`submitted` means the modem accepted the SMS for sending. Carrier delivery is separate. A `partial` result means the modem accepted some segments before it rejected a later one. An `unknown` result means the send may have completed before transport loss. Check the `parts` array from `GET /v1/messages/{id}` for a multipart message before retrying.

## WebSockets

/v1/events sends a snapshot with modems, messages and activity before sending modem, message and activity events. Sequence numbers increase in one running service. Reconnect for a fresh snapshot. Slow clients can receive a replacement snapshot.

A `message` event can include `replaces`, the IDs of old fragment rows to remove from the inbox when a multipart SMS is joined.

The snapshot includes `hooks` with `rules`, `destinations`, `runs`, and `calls`. Live events of type `hook.rules`, `hook.destinations`, `hook.runs`, and `hook.calls` replace the row with the same ID. Rule and destination deletions carry `deleted: true`. See the [hooks guide](hooks.md) for event payloads and delivery behavior.

/v1/modems/{id}/audio/ws sends binary 20 ms PCM frames during a call. Listening clients get caller audio. One browser at a time can request the microphone with mic:on and release it with mic:off. Send PCM16 mono 8 kHz in 320-byte frames. WAV playback takes precedence over queued microphone frames. Caller audio stays in a short in-memory buffer for capture.
