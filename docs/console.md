---
title: Browser console
description: Unlock a SIM, send and receive SMS, operate calls, and check live activity in the browser.
---

# Browser console

Open the address from the [installation guide](install.md). Once connected, look for **Live** in the header. Select a modem to see its identity, SIM state, network registration, and capabilities.

## Unlock the SIM

If the device panel shows **SIM PIN**, enter the PIN there and select **Unlock**. Select **Remember this SIM** to save the PIN after a successful unlock. **Forget saved PIN** removes the saved copy for the selected SIM. If the modem refuses the unlock, verify the PIN and check the SIM before attempting it again. See [device setup](devices.md) for the conditions for automatic unlock.

## Send and receive SMS

In **Messages**, enter the recipient number and message, then select **Send SMS**. The outbox shows whether the modem accepted the message. Check the outbox before retrying a message marked **unknown** or **partial**. The [message detail API](api.md) shows the status of each segment for a long outgoing SMS.

Use the message direction menu to switch between the inbox and outbox. **All modems** includes history from other devices and older messages that were saved under a USB port ID. Incoming multipart messages appear after all segments arrive.

## Calls and audio

Enter the number in **Voice** and select **Dial**, or select **Answer** when the modem shows an incoming call. Select **Hang up** to end the call.

If the device exposes call audio, select **Listen** when the call is active. **Enable mic** sends audio from the browser microphone after you grant permission. For remote use, open the console over HTTPS. To play a clip into the call, add it to **Soundboard** and select **Play**. Use a mono 8 kHz signed PCM16 WAV, up to 30 seconds.

The device's `callStatus` and `audio` capabilities are separate. If a call connects but the audio controls are unavailable, check the modem profile and audio port in [device setup](devices.md).

## Live activity

Open the console in another browser tab to watch state changes from either tab or an API or MCP client. Use [Hooks](hooks.md) for incoming call history and automation delivery results.
