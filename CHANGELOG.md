# Changelog

## 0.2.2

- Keep the same modem tab when a USB modem is unplugged and reconnected.

## 0.2.1

- Show the browser console with demo modem and SMS data in the README and documentation.
- Document SIM unlock, SMS and calls, webhook setup and dry runs, and manual modem port mapping.
- Clarify where to find per-segment status for multipart SMS.

## 0.2.0

- Add rules that match incoming SMS or calls, then forward or reply by SMS, send HTTP webhooks, or decline the ringing call.
- Manage rules and webhook destinations through the web console, REST API or MCP. Preview rules without sending anything, and view call history and delivery results.
- Send long SMS in multiple parts and record each part's submission status.
- Create the new SQLite tables at startup. Existing inboxes and saved SIM PINs work without a manual migration.

## 0.1.1

- Add the Docker image label the MCP Registry requires to verify package ownership.

## 0.1.0

- Join incoming multipart SMS and recover old split messages when their original segments are still on the modem.
- Keep auto-discovered modem IDs stable across USB ports by deriving them from the modem's IMEI. Browse older port-keyed messages with the all-modems filter.
- Added the initial modem gateway, SQLite SMS inbox/outbox and SIM PIN vault.
- Added direct AT, Huawei serial voice, shared live browser state, and browser call audio.
- Added FastMCP 2, agent packages and tag-triggered registry workflows.
- Added Docker-backed browser tests and a physical modem probe.
- Added an opt-in physical SMS reply test with an invitation and a five-minute timeout.
- Added a Compose installer with USB modem discovery, image selection, and local data storage.
- Added an installed command for managing user and system installations.
- Added installation via `curl | bash` and optional Tailscale HTTPS access through the installed command.
- Added an explicit opt-out for operator token authentication.
- Added the documentation site, funding links, and Glama maintainer metadata.
- Avoided manual serial control line changes during port opening for USB modems that reject them.
- Recognized Huawei's unsupported command response so SIM discovery can try the next query.
