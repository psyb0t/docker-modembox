---
title: API
description: Look up ModemBox REST endpoints, authentication, live modem events, and WebSocket call audio frames.
nav_order: 4
---

# API

Protected REST and MCP calls require an Authorization bearer header by default. In that mode, each WebSocket expects the operator token as its first text frame. With `MODEMBOX_DISABLE_AUTH=true`, omit the header and the first token frame. Tokenless endpoints reject a browser's cross-origin requests. All clients share one instance.

| Method and path | Payload or result |
|---|---|
| GET /healthz | Public application liveness |
| GET /auth-mode | Public token requirement as `{"tokenRequired": true}` or `{"tokenRequired": false}` |
| GET /v1/state | Device, recent message and activity snapshot |
| GET /v1/modems | Device details, capabilities, SIM and network state, call state |
| GET /v1/messages | Use modem, before and limit query parameters. Returns items and next cursor |
| GET /v1/messages/{id} | One message |
| POST /v1/modems/{id}/sms | number and text |
| POST /v1/modems/{id}/pin | pin and optional remember |
| DELETE /v1/modems/{id}/pin | Forget the saved PIN for the current SIM |
| POST /v1/modems/{id}/calls | number |
| POST /v1/modems/{id}/calls/answer | Empty JSON object |
| DELETE /v1/modems/{id}/calls/current | Hang up |
| GET /v1/modems/{id}/audio | Recent caller audio as base64 WAV. Optional seconds from 1 to 10 |
| POST /v1/modems/{id}/audio | wav, a base64 mono 8 kHz PCM16 WAV up to 30 seconds |
| DELETE /v1/modems/{id}/audio | Stop WAV playback |
| POST /v1/modems/{id}/at | command, a single allowed AT command |
| POST /mcp | MCP Streamable HTTP |
| GET /v1/hooks/{resource} | List rules, destinations, runs, or calls with `limit` and `offset`. Returns `items`, `limit`, `offset`, and `hasMore` |
| POST /v1/hooks/rules | Create a rule with name, trigger, modem, match, conditions and actions |
| PUT /v1/hooks/rules/{id} | Replace a rule, including its enabled state |
| POST /v1/hooks/destinations | Create an HTTP destination |
| PUT /v1/hooks/destinations/{id} | Replace an HTTP destination |
| DELETE /v1/hooks/{resource}/{id} | Delete a rule or destination and cancel queued actions |
| POST /v1/hooks/preview | Preview a draft `rule` against one `messageId` or `callId` |

See [MCP and agent integrations](mcp.md) for the tool list. Read-only AT commands are allowlisted. Raw writes require an operator opt-in and are unavailable during a call.

Use the ID returned by `GET /v1/modems` for modem actions and SMS filtering. Auto-discovered modems with a usable IMEI use a hardware UUID. Older messages retain their original modem ID in unfiltered message history. See [device identity](devices.md).

REST failures return a stable code and message. Validate the SMS result before retrying. Submitted means the modem accepted the SMS. Unknown means the send may have completed before transport loss. Neither is a delivery receipt.

## WebSockets

/v1/events sends a snapshot with modems, messages and activity before sending modem, message and activity events. Sequence numbers increase in one running service. Reconnect for a fresh snapshot. Slow clients can receive a replacement snapshot.

A `message` event can include `replaces`, the IDs of old fragment rows to remove from the inbox when a multipart SMS is joined.

The snapshot includes `hooks` with `rules`, `destinations`, `runs`, and `calls`. Live events of type `hook.rules`, `hook.destinations`, `hook.runs`, and `hook.calls` replace the row with the same ID. Rule and destination deletions carry `deleted: true`. See the [hooks guide](hooks.md) for event payloads and delivery behavior.

/v1/modems/{id}/audio/ws sends binary 20 ms PCM frames during a call. Listening clients get caller audio. One browser at a time can request the microphone with mic:on and release it with mic:off. Send PCM16 mono 8 kHz in 320-byte frames. WAV playback takes precedence over queued microphone frames. Caller audio stays in a short in-memory buffer for capture.
