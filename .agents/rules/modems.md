# Modem ownership

- One worker owns each control port. Serialize AT transactions, including commands shared with background polling.
- Select vendor commands using model identification and capability probes. Generic devices start with 3GPP commands.
- Store a SIM PIN only after successful unlock and bind it to ICCID. Keep private content out of logs.
- Publish inbox updates after SQLite commits. Publish tool operations through the same event stream as browser actions.
- Hardware tests that send messages or place calls require an authorized test recipient.
