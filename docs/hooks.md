---
title: SMS and call hooks
description: Forward or reply to SMS, send webhooks, and decline incoming calls with rules.
nav_order: 6
---

# SMS and call hooks

Open **Hooks** in the browser to create rules, manage HTTP destinations, and see call and delivery history. Every enabled rule that matches a new event runs its configured actions.

| When | If | Do |
| --- | --- | --- |
| SMS received | From a configured number | Forward the original text to a fixed number |
| SMS received | Text starts with ALERT | POST the event to an HTTP destination |
| Incoming call | Caller starts with +40 | Decline the ringing call and POST the result |
| Call ended | Outcome equals missed | Reply with a short SMS |

A rule can restrict the receiving modem and match all or any of its conditions. Phone conditions support exact matches, prefixes, and lists. ModemBox strips common number formatting and keeps the international prefix. Caller ID is reported as present, withheld, or unavailable. A dry run shows the matches and rendered actions for a saved SMS or call without sending anything.

## Send matching SMS to a webhook

1. Open **Hooks**, then **HTTP destinations**. Select **New destination** and enter your webhook URL. Use **POST** for a webhook receiver such as n8n.
2. Open **Rules** and select **New rule**. Choose **SMS received** and add a **Message text** condition with **Starts with** and the value `ALERT:`.
3. Choose **Make HTTP request** and pick the destination. Keep **Standard event** to include the original message and sender.
4. Pick a matching inbox message in **Preview record** and select **Dry run**. Check the rendered request, then save the rule.
5. Send a new matching SMS to the modem and check **Delivery history** for its result and HTTP status.

## SMS and calls

Leave the SMS recipient blank to reply to the sender, or enter a fixed number to forward. Use `{% raw %}{{text}}{% endraw %}` as the SMS template to forward the original body. Templates can also insert `{% raw %}{{peer}}{% endraw %}`, `{% raw %}{{modem}}{% endraw %}`, `{% raw %}{{outcome}}{% endraw %}`, and other event fields. See [SMS submission status](api.md#sms-submission-status) for multipart and uncertain sends.

Incoming SMS can trigger hooks after all parts arrive. Duplicate scans, legacy repairs, and texts dated before the rule was enabled stay in the inbox without firing. An SMS whose age cannot be determined is also kept without firing. Hook SMS waits while its modem is busy and has a per-instance limit of six automated send attempts per minute. Waiting actions expire after one hour.

A call gets one history entry. ModemBox waits briefly for caller ID and tracks the final outcome. Decline takes priority over other actions for the incoming event. ModemBox checks the current call just before sending the modem command. Decline is available only when the modem can confirm one ringing incoming call and no other call, so a concurrent active or held call is left alone. Call outcomes include answered, missed, declined, and interrupted.

## HTTP destinations

Create a destination with a fixed URL, HTTP method, and optional headers. Use the header editor for a service's credentials. Header values and the signing key are encrypted in SQLite and are write-only in the API. Store the local encryption key with your database backup.

Default POST requests carry a JSON event with version, ID, type, modem, timestamp, and event fields. IDs, times, and types are available as id, time, and type. Time is UTC Unix seconds. The peer field holds the sender or caller. Choose selected fields, a JSON object with string templates, or a plain text template to change the body. GET and HEAD send no body. An external service can use the [send SMS API](api.md) to reply.

Each request carries an X-ModemBox-Delivery header. Deduplicate requests by that value. With a signing key configured, X-ModemBox-Signature has the form t=timestamp,v1=hex. Verify the hex HMAC-SHA256 over the ASCII timestamp, a period, and the exact body bytes. Check timestamp freshness and compare signatures in constant time.

Requests do not follow redirects. For a private LAN, Docker, or Tailscale destination, enter its approved IP in the destination editor. Loopback and link-local addresses are blocked. HTTP transport errors, selected rate-limit or timeout responses, and server errors can retry twice. Disabling or deleting a rule or destination cancels queued actions. Editing a destination cancels its outstanding queue so pending requests cannot be redirected to the new URL.

## Storage

ModemBox creates the hook tables at startup in the existing database. Existing messages and PINs stay in their current tables. Rules and action documents can gain optional fields without rewriting older rows. Keep the database and its local encryption key together when you back up or restore ModemBox.
