---
title: Devices and configuration
description: Check USB modem discovery and capabilities, set runtime options, and manage the SIM PIN and modem storage.
nav_order: 3
---

# Devices and configuration

## Hardware compatibility

| Device | ModemBox support | Physical coverage |
|---|---|---|
| Huawei E173 | USB discovery on the layout below, SMS, voice calls, and serial PCM audio | Tested on Linux with firmware `11.126.83.00.264` and USB ID `12d1:1001`. Verified SMS reception, an outgoing call, WAV playback into the call, and capture of caller audio. |
| Huawei E169 and E169G | ModemBox selects the Huawei serial PCM profile and probes for voice and audio | Verify on your device. Firmware and USB layout can vary. |
| Other USB serial modems | Map the control port manually. ModemBox probes standard AT capabilities. | SMS and call controls depend on the device's AT support. The generic profile has no audio setup. |

The E173 is a 2G/3G modem. Check the model suffix printed on the stick when comparing hardware or firmware. The physical test used USB interface `02` for AT commands and `01` for audio. Auto-discovery matches USB ID `12d1:1001` and requires interface `02`. Automated tests send microphone PCM over WebSocket to a simulated modem.

- [Huawei E173 USB Stick manual (PDF)](https://gzhls.at/blob/ldb/f/0/0/a/67e162a9d1d0ce56ed690613b12cf92f0448.pdf)
- [Huawei E173u-1 FCC filing](https://fccid.io/QISE173U-1) and [Huawei E173s-1 FCC filing](https://fccid.io/QISE173S-1)

## Runtime settings

| Setting | Meaning |
|---|---|
| MODEMBOX_TOKEN | Operator bearer token, 16 to 256 ASCII characters. Required by default |
| MODEMBOX_DISABLE_AUTH | Set to true to allow clients without a token. Defaults to false |
| MODEMBOX_DEVICES | Optional JSON array of id, control, and optional audio paths. An empty array uses supported USB discovery |
| MODEMBOX_ALLOW_RAW_AT | false by default. Allows additional single AT commands when enabled |
| MODEMBOX_DATA_DIR | SQLite, private SIM PIN key and rotating logs. Docker uses /data |
| MODEMBOX_WEB_DIR | Static frontend directory. Docker uses /app/ui |

Call status and audio are separate capabilities. A modem can report call state even when ModemBox has no driver for its audio interface.

### Map a control port manually

If discovery misses a modem, check that the host sees its serial ports with `ls -l /dev/serial/by-id/`. Check which group owns the control port and close any host modem software using it. Use the device path that the container can see. For an installer-managed deployment, set it in `compose.override.yaml` next to `compose.yaml`, then run `modembox start`.

```yaml
services:
  modembox:
    environment:
      MODEMBOX_DEVICES: '[{"id":"lab-modem","control":"/dev/ttyUSB2"}]'
```

Replace the example port with your modem's AT port. With direct Docker, put the same JSON value in the `MODEMBOX_DEVICES` entry in the file passed to `--env-file`. Once the modem appears, check its capability tags in the [browser console](console.md).

### Device identity and SIM PINs

An auto-discovered modem gets a UUID derived from its IMEI. Moving the same stick to another USB port or changing its SIM keeps that UUID. A modem that cannot report a usable IMEI gets a port-local ID. An explicit `MODEMBOX_DEVICES` mapping uses the ID you supplied.

Messages saved by an older version under a USB port ID keep that ID. Open **All modems** in Messages to see them alongside messages from the current device.

The PIN is saved only after successful unlock and encrypted with a local key. It is tied to the SIM's ICCID, so switching a SIM or changing its number cannot select the wrong saved PIN. Automatic unlock requires the ICCID to be readable while locked and a verified remaining retry count of at least two. Some devices need manual unlocking because they do not expose that information. Protect and back up the SQLite database and key together.

Outbound SMS longer than 70 UTF-16 units uses multipart submission. The inbox scans supported modem storage plus new message notifications. It saves incoming multipart SMS as one message once every part arrives. Incomplete parts are saved across restarts. When all original parts of an older split message are still on the modem, the next scan replaces the fragment rows in the inbox with the joined text. ModemBox leaves messages on the modem, so modem storage can fill up. Message status and feature tags distinguish submission and hardware capability from carrier confirmation. See [call and SMS hooks](hooks.md) for caller ID and decline behavior.

WAV soundboard entries live in the browser tab. Caller audio is buffered briefly in RAM during the call. A browser can listen without a microphone. Headphones help prevent acoustic echo when talking through the browser. The gateway does not configure the host's cellular internet, alter network selection, or expose a public phone line by itself.
