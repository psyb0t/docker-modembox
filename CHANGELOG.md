# Changelog

## Unreleased

- Added the initial modem gateway, SQLite SMS inbox/outbox and SIM PIN vault.
- Added direct AT, Huawei serial voice, shared live browser state, and browser call audio.
- Added FastMCP 2, agent packages and tag-triggered registry workflows.
- Added Docker-backed browser tests and a physical modem probe.
- Added an opt-in physical SMS reply test with an invitation and a five-minute timeout.
- Added a Compose installer with USB modem discovery, image selection, and local data storage.
- Added an installed command for managing user and system installations.
- Added an explicit opt-out for operator token authentication.
- Added the documentation site, funding links, and Glama maintainer metadata.
- Avoided manual serial control line changes during port opening for USB modems that reject them.
- Recognized Huawei's unsupported command response so SIM discovery can try the next query.
