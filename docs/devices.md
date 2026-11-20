---
title: Devices and configuration
description: Check USB modem discovery and capabilities, set runtime options, and manage the SIM PIN and modem storage.
nav_order: 3
---

# Devices and configuration

| Setting | Meaning |
|---|---|
| MODEMBOX_TOKEN | Operator bearer token, 16 to 256 ASCII characters. Required by default |
| MODEMBOX_DISABLE_AUTH | Set to true to allow clients without a token. Defaults to false |
| MODEMBOX_DEVICES | Optional JSON array of id, control, and optional audio paths. An empty array uses supported USB discovery |
| MODEMBOX_ALLOW_RAW_AT | false by default. Allows additional single AT commands when enabled |
| MODEMBOX_DATA_DIR | SQLite, private SIM PIN key and rotating logs. Docker uses /data |
| MODEMBOX_WEB_DIR | Static frontend directory. Docker uses /app/ui |

For a Huawei E173, verify the AT port with identification commands. The tested layout used USB interface 02 for control and interface 01 for audio. Match the physical USB device and interface during configuration. An unknown device can still be configured with a control port and use supported 3GPP commands. Its voice capability can remain unknown if the modem has no safe profile-specific probe. Call status support alone does not prove that it exposes a host audio device.

The PIN is saved only after successful unlock and encrypted with a local key. It is tied to the SIM's ICCID, so switching a SIM or changing its number cannot select the wrong saved PIN. Automatic unlock requires the ICCID to be readable while locked and a verified remaining retry count of at least two. Some devices need manual unlocking because they do not expose that information. Protect and back up the SQLite database and key together.

Outbound SMS currently fits one message of up to 70 UTF-16 units. The inbox scans supported modem storage plus new message notifications. It displays multipart fragments separately. ModemBox leaves messages on the modem, so modem storage can fill up. Message status and feature tags distinguish submission and hardware capability from carrier confirmation.

WAV soundboard entries live in the browser tab. Caller audio is buffered briefly in RAM during the call. A browser can listen without a microphone. Headphones help prevent acoustic echo when talking through the browser. The gateway does not configure the host's cellular internet, alter network selection, or expose a public phone line by itself.
