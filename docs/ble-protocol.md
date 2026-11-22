# BLE Interface Specification (for Web app developers)

The agreement for operating pico2_aprs (an APRS station built on a Raspberry Pi Pico 2 W) from a smartphone Web app over BLE.

- Audience: developers of the Web app (separate repository)
- Design background on the device side: [ble-control.md](ble-control.md) (written in Japanese; it covers the device's internals. This document is meant to be sufficient on its own for building the Web app)
- Document version: 0.9 (2026-11-22) / protocol version `proto`: 1

## 0. Status markers

Each section heading carries one of these markers.

| Marker | Meaning |
|---|---|
| **[Fixed]** | Implemented on the device and verified: by tests on a PC (with a fake BLE link), and for the BLE layer (§2–§5) also over real BLE. Changes must start with this document |
| **[Provisional]** | The direction is decided, but it is not implemented on the device, or not yet verified over real BLE. Values may change |
| **[Open]** | Not decided yet. The Web app should be built so that it survives a change |

Status as of 2026-11-17: everything is implemented on the device. The BLE layer (advertising, GATT, pairing, line splitting) was verified on real hardware with nRF Connect on Android and with `bleak` on Windows. **It has not been tried from a Web app in Chrome yet**, so the pairing flow (§4) stays provisional. On Windows, see the known issue in §10.

Status as of 2026-11-20 (0.8): APRS messaging and the channel check (§7.6–§7.9, §8.8) are specified here first and are being implemented; they stay [Provisional] until verified on hardware. The channel threshold has not been measured yet.

## 1. Scope

What the app can do (first phase):

- Read the device state (GNSS fix, beacon switch, transmit readiness, receive level, and so on)
- Receive each decoded packet as it arrives (only while connected; the app is not expected to persist them)
- Get the list of stations heard so far, when connecting
- Read, change, and reset settings (changes are saved to the device's flash and survive power cycles)
- Send a position beacon now (`beacon.send`, §7.5), and choose whether the device also sends beacons by itself (`beacon_mode`, §9.1)
- APRS messaging (§7.6–§7.8, §8.8): send a message to another station (the device retries until it is acknowledged), receive messages addressed to this device (the device acknowledges them by itself), and read the messages the device keeps

What the app cannot do: send arbitrary packets, bulletins or other non-message text, serve map data, KISS, or update firmware.

**Transmit safety**: the device emits a radio signal only when the **physical beacon switch on the unit is on** and the device's transmit gate allows it (a callsign is set and `tx_enabled` is true). This also holds for `beacon.send`, for messages and for the acknowledgements the device sends by itself: the app cannot bypass the switch, either by a command or by changing settings. The app should show `status.tx_ready` (§8.1) prominently.

**Channel check** (§7.9): before an automatic beacon, a message or an acknowledgement, the device waits until the channel is clear, judged from the received audio level (the radio's squelch is open while the channel is in use). `beacon.send` does not wait.

## 2. Finding the device [Fixed]

| Item | Value |
|---|---|
| Service UUID in the advertisement | `6E400001-B5A3-F393-E0A9-E50E24DCCA9E` (NUS) |
| Name (scan response) | `PicoAPRS-XXXX` (XXXX is the lower 2 bytes of the MAC address in uppercase hex, such as `PicoAPRS-2574`) |
| Address | Public (the same on every start) |
| Concurrent connections | 1. The device stops advertising while connected, so while another phone or PC is connected it cannot be found |
| Advertising interval | 100 ms |

With Web Bluetooth, filter by service UUID. Show the name to the user to tell units apart, but do not filter by it.

```js
const NUS = "6e400001-b5a3-f393-e0a9-e50e24dcca9e";
const device = await navigator.bluetooth.requestDevice({ filters: [{ services: [NUS] }] });
```

## 3. GATT [Fixed]

The device uses the same UUIDs as the Nordic UART Service (NUS).

| | UUID | Properties | Direction |
|---|---|---|---|
| Service | `6E400001-B5A3-F393-E0A9-E50E24DCCA9E` | | |
| RX | `6E400002-B5A3-F393-E0A9-E50E24DCCA9E` | Write, Write Without Response | app → device |
| TX | `6E400003-B5A3-F393-E0A9-E50E24DCCA9E` | Notify | device → app |

- RX and TX are usable only on an encrypted, authenticated connection (§4)
- Whether to add the standard Device Information Service (0x180A) is **[Open]**. Even if added, the app should not depend on it (model and version are available via `hello`)

## 4. Pairing [Provisional]

- Method: LE Secure Connections, passkey entry with MITM protection (the device is DisplayOnly), with bonding
- Flow:
  1. The app connects and touches RX, or starts notifications on TX
  2. Android starts pairing. The passkey dialog is shown by Android, not by the browser, and it **may appear only as a notification** ("Pairing request ... tap to pair") that the user has to tap (seen in Chrome on Android 11, below)
  3. The device's LCD shows a 6-digit passkey
  4. The user reads it from the LCD and types it into the dialog
- The app should tell the user both things in its hint text: look for the pairing notification and tap it, then type the 6 digits shown on the device's LCD. Tapping the notification takes part of the 30 s passkey limit below
- On later connections the stored bonding key is used and no passkey is shown
- Until the connection is authenticated, the device does not process commands and does not send notifications. RX accepts writes only on an authenticated connection, so a write before authentication is refused by the GATT layer (Insufficient Authentication), which is what makes the phone start pairing. Any bytes that still reach the device before authentication are **silently dropped** (no `err` is returned)
- **How the app learns that authentication completed**: start notifications on TX first (that does not need authentication), then write `hello`. As soon as the connection is authenticated, the device sends a `status` event (§6.2), so the first `status` (or the `hello` response) is the signal. If neither arrives, resend `hello` after a few seconds. Do not tear the connection down before the time limit below while the user may still be typing the passkey
- **Time limits**: the device has no passkey timeout of its own. While the passkey is shown, pairing is limited by the Bluetooth Security Manager timeout (30 s without a pairing message, from the Bluetooth Core Specification; the phone may have its own limit. **Measured: the passkey disappeared from the LCD 30 s after it was shown when none was entered**, Android nRF Connect, 2026-11-18) and by this rule: **if a connection is not authenticated within 60 seconds of connecting, the device disconnects it** and advertises again (measured: 60 s, with a connection from nRF Connect that never started bonding). When pairing fails or the link drops, the passkey disappears from the LCD. A new attempt needs a new connection and shows a new passkey
- While any central is connected, authenticated or not, the device does not advertise (§2), so other phones cannot find it. The 60-second limit keeps an abandoned, unauthenticated connection from hiding the device
- A central that already has a bond reconnects without a passkey (verified on Android and Windows, below)
- The device refuses pairing methods without MITM protection (Just Works) and legacy (non-Secure-Connections) pairing
- The device stores keys for up to 4 peers (oldest removed first). Keys can be erased only over the device's USB. If the app can no longer reconnect after the device erased its keys, ask the user to "forget" the device in the phone's or PC's Bluetooth settings

Verified on real hardware (2026-11-17; time limits 2026-11-18; Chrome 2026-11-19, issue #5):

| Central | Result |
|---|---|
| Android, nRF Connect | The pairing dialog appears when bonding is started. Passkey pairing succeeds. Reconnects without a passkey, also after the device is powered off and on |
| Android, nRF Connect, no bond | Connected without bonding and left idle: the device disconnected after 60 s. Bonding started but no passkey entered: the passkey disappeared from the LCD after 30 s |
| Windows 11, `bleak` with WinRT passkey pairing (`tools/ble_cli.py pair`) | Passkey pairing succeeds. Reconnects without a passkey. See the known issue in §10 |
| Android 11, Chrome 157, Web app (one run, no bond) | The device appears in the chooser (filter by the NUS service UUID). With TX notifications started before any write, pairing started about 90 ms after `startNotifications()` completed. The passkey dialog did not open by itself: Android posted a "Pairing request" notification, and tapping it opened the dialog. `connect()` had already resolved and the link stayed up while the passkey was pending. After the passkey, `hello`, `status`, `settings.schema` and `settings.get` were all answered. After a page reload and choosing the device again, it reconnected with the bond and no passkey |

- The chooser is needed after every page reload in a Chrome without `navigator.bluetooth.getDevices()`
- In the same session one earlier connection attempt failed (`NetworkError`, link lost after about 1.2 s). It coincided with a firmware update being written to the device and is not a property of the pairing flow

**[Open]**: in Chrome, what Web Bluetooth reports for a wrong or dismissed passkey and for a bond removed on one side only, and the behaviour in Chrome on Windows. The Android flow above works without pairing in the OS Bluetooth settings first.

## 5. Framing

### 5.1 Lines [Fixed]

- One message is **one UTF-8 JSON object followed by a newline (`\n`, 0x0A)**. No newline may appear inside the JSON
- The device accepts lines of up to **512 bytes**, excluding the newline. A longer line is discarded and an `err` (`too_long`) is returned
- JSON sent by the device has no guaranteed key order and may contain spaces (`{"t": "status", ...}`)

### 5.2 Splitting across BLE writes and notifications [Fixed]

- **App → device**: a line may be split at any point and sent in any number of writes. The device concatenates until `\n`. Keep each write to **20 bytes or fewer**. This works with the minimum MTU of 23, and Web Bluetooth does not tell the app the negotiated MTU. Write with response (`writeValueWithResponse`) and wait for each write to finish before the next one
- **Device → app**: one line may arrive across several notifications, and one notification may contain a line boundary. The app must concatenate the received bytes, cut at each `\n`, and parse each line as JSON
- A notification carries at most **MTU − 3 bytes, and never more than 244 bytes**. The device accepts an MTU of up to 517. Which MTU is used depends on the central: in the tests, Windows raised it to 517 about 1 second after connecting, and Android stayed at 23 unless the app asked for more. The app does not need to know the MTU
- A notification boundary may fall in the middle of a UTF-8 character. Decode after cutting out a line (or use `TextDecoder` with `stream: true`)

```js
let buf = "";
const dec = new TextDecoder();
tx.addEventListener("characteristicvaluechanged", (ev) => {
  buf += dec.decode(ev.target.value, { stream: true });
  let i;
  while ((i = buf.indexOf("\n")) >= 0) {
    const line = buf.slice(0, i);
    buf = buf.slice(i + 1);
    if (line) onMessage(JSON.parse(line));
  }
});
```

### 5.3 Partial lines and resynchronizing [Fixed]

If a write fails in the middle of a line (GATT error, timeout, a dismissed pairing dialog, a short link loss), the device may already hold the first part of the line.

- The device's receive buffer is cleared **when a central connects, when it disconnects, and when the connection becomes authenticated**. A new connection always starts clean
- The device does **not** drop a partial line after idle time. It keeps it until the next `\n` (or the end of the connection)
- An empty line (a bare `\n`, also `\r\n`) is ignored silently; it produces no response
- **Resync procedure**: after a failed write, send a single `\n` before the next request. If the device held a fragment, it answers with one `bad_json` (no `id`); if it held nothing, nothing comes back. The next line starts clean
- A failed write may still have reached the device. If the fragment happens to be a complete JSON object (the failed write carried only the `\n`), the extra `\n` completes it and the device executes it, so a response with the **old `id`** arrives. Ignore responses whose `id` the app is no longer waiting for

## 6. Message conventions [Fixed]

### 6.1 Requests, responses, events

- Request: `{"op": "<operation>", "id": <integer>, ...arguments}`
  - `id` may be omitted but is recommended. The response carries the same `id`. A non-integer `id` is treated as absent
- Response or event: `{"t": "<type>", ...}`
  - A response to a request carries `id`. An event does not
- Responses come back in request order, but events may be interleaved
- Ignore unknown keys. Adding items does not change `proto`; `proto` is raised only for incompatible changes
- A missing (unknown) value is **omitted as a key**, not sent as `null`. Example: `lat` and `lon` when there is no fix
- Times are UTC ISO 8601 to the second (`"2026-11-16T03:04:05Z"`). They are omitted until the device knows the time from GNSS
- Latitude and longitude are in degrees (north and east positive), rounded to 5 decimal places. Level `lvl_db` is in dBFS, rounded to 1 decimal place
- Floating point on the device is 32-bit. Latitude/longitude precision is about 1 m

### 6.2 Flow after connecting

```
App                                      Device
  | -- connect, pair (§4), start TX notifications --> |
  |                    <-- status (event, immediately) |
  | -- {"op":"hello","id":1} ---------------------> |
  |                    <-- {"t":"hello","id":1,...} - |
  | -- {"op":"settings.schema","id":2} -----------> |
  |                    <-- {"t":"schema","id":2,...}  |
  | -- {"op":"settings.get","id":3} --------------> |
  |                    <-- {"t":"settings","id":3,...}|
  | -- {"op":"stations.list","id":4} -------------> |
  |                    <-- station × n, stations.end  |
  |                    <-- rx / tx / status (anytime) |
```

- If `proto` in `hello` differs from the version the app knows, do not continue; tell the user
- The device sends the first `status` as soon as the connection is authenticated. That can happen before the app has started notifications, so the first `status` may be missed. If the app needs the state right away, send `{"op":"status"}`; otherwise the next periodic `status` arrives within 5 seconds

## 7. Requests [Fixed]

| `op` | Arguments | Response `t` | Description |
|---|---|---|---|
| `hello` | none | `hello` | Protocol version and operating mode |
| `status` | none | `status` | Current state (§8.1) |
| `settings.schema` | none | `schema` | Definition of the settings items (§9.2) |
| `settings.get` | none | `settings` | Current values of all items |
| `settings.set` | `values`: {key: value, …} | `settings` | Change and save. **All or nothing**: if any one value is invalid, nothing changes |
| `settings.reset` | none | `settings` | Delete the saved settings and return to the device's defaults |
| `stations.list` | none | `station` × n, `stations.end` | Stations heard (newest first) |
| `beacon.send` | none | `beacon` | Send a position beacon now (§7.5) |
| `msg.send` | `to`, `text` | `msg` | Queue an APRS message for transmission (§7.6) |
| `msg.list` | none | `msg` × n, `msg.end` | Messages kept by the device, received and sent (§7.7) |
| `msg.cancel` | `no` | `msg` | Stop retrying a message being sent (§7.8) |

### 7.1 `hello`

```json
{"op": "hello", "id": 1}
{"t": "hello", "id": 1, "proto": 1, "name": "pico2_aprs", "mode": "tnc"}
```

- `mode`: `"tnc"` (running as an external TNC). BLE runs only when the mode is `"tnc"`
- Whether to add a firmware version field is **[Open]** (it may be added)

### 7.2 `settings.set`

```json
{"op": "settings.set", "id": 5, "values": {"comment": "Hello", "beacon_still_s": 900}}
{"t": "settings", "id": 5, "values": {"callsign": "Q1TEST", "ssid": 7, "...": "..."}, "changed": ["comment", "beacon_still_s"]}
```

- `values` need not contain every item (only the ones to change). At least one item is required
- `values` in the response is **all items** after the change was applied and saved. `changed` lists the items whose value actually changed (items sent with the same value are not included)
- Changes take effect **immediately**, with no restart. For example, the comment applies from the next beacon, and the transmit permission from the next transmit decision
- Saving is a flash write that takes tens of milliseconds. Reception is expected to pause briefly during it (not yet measured). **Do not send on every keystroke.** Send the changed items together, once, when the user presses "Save"
- A line is limited to 512 bytes (§5.1). Sending every item fits

### 7.3 `settings.reset`

```json
{"op": "settings.reset", "id": 6}
{"t": "settings", "id": 6, "values": {"...": "..."}, "changed": ["ssid"]}
```

"Defaults" are the values written in the device firmware (including the developer's local settings). They are not necessarily factory values.

### 7.4 `stations.list`

```json
{"op": "stations.list", "id": 4}
{"t": "station", "id": 4, "src": "Q1AAA-9", "last_s": 120, "count": 3, "lat": 35.66867, "lon": 139.76117, "lvl_db": -20.1}
{"t": "station", "id": 4, "src": "Q1TEST-7", "last_s": 300, "count": 1, "own": true}
{"t": "stations.end", "id": 4, "n": 2}
```

- Stations are ordered by most recently heard. At most 64 stations
- If a packet is received while the list is being sent, its `rx` event arrives afterwards (the list is a snapshot from the time of the request)
- The fields are described in §8.4

### 7.5 `beacon.send`

Sends one position beacon (Mic-E, the same frame as the automatic beacon) right away. Works in both beacon modes (§9.1).

```json
{"op": "beacon.send", "id": 7}
{"t": "beacon", "id": 7, "dry": false}
{"t": "tx", "tnc2": "Q1TEST-7>SU4PX0,WIDE1-1:`CIXl <0x1C>[/\"4!}Pico2 APRS", "dry": false, "utc": "2026-11-16T03:04:05Z"}
```

- The device checks the conditions below in this order. If one is not met, it returns `err` (§8.6) and sends nothing:

  | Condition | `code` when not met |
  |---|---|
  | The unit's beacon switch is on (`status.sw`) | `switch_off` |
  | The device has a 2D or 3D fix and knows the time (`status.fix` ≥ 2 and `utc` present) | `no_fix` |
  | At least 10 seconds have passed since the last beacon (automatic, sent by command, or dry run) | `too_soon`, with `wait_s` |

- When the conditions are met, the device builds the beacon and transmits it, then replies. The reply comes **after the transmission ends**; see "Time until the reply" below. `dry` is true when the transmit gate refused (`status.tx_ready` was false because the callsign is unset or `tx_enabled` is false): the beacon was recorded but not transmitted, just like an automatic dry run
- The `beacon` response (§8.7) carries only `dry`, always as a boolean. The packet itself follows in the `tx` event (§8.3), right after the response. Every refusal or failure is an `err`; a `beacon` response always means the beacon was built (and transmitted, unless `dry` is true)
- In the `"auto"` beacon mode, the automatic schedule restarts from this beacon (the next automatic beacon comes `beacon_moving_s` or `beacon_still_s` later)
- After an abnormal restart, the device holds back its first automatic beacon for a while. `beacon.send` is not held back
- `frame_too_long`: the beacon does not fit in the device's transmit buffer. With the current limits of the settings (§9.1) this does not happen: the longest beacon (a 40-character comment, 2 path entries, FX.25) takes 1.34 s on the air and the buffer holds 1.5 s. The code is kept for future items. Nothing was sent
- An `err` with `internal` means the transmission failed inside the device. The device then restarts (as it does for a failure of an automatic beacon), so the connection will drop

Time until the reply (measured on the device, 2026-11-19):

| Step | Time |
|---|---|
| Build the waveform | 0.15 s (AX.25) – 0.26 s (FX.25), for the longest beacon |
| Settle the audio output before keying | 0.06 s |
| On the air (including 0.3 s of leading flags) | 0.54–0.93 s (AX.25), 0.91–1.34 s (FX.25), depending on the comment and path |
| **Total** | **about 0.8–1.7 s; at most about 2 s** |

- `beacon.send` does not wait for a clear channel (§7.9) or for anything else before keying. The time does not depend on radio traffic. (Automatic beacons do wait for a clear channel)
- While it waits, the device processes no other requests; they are answered afterwards, in order
- Hard limits: if the transmission does not end within its expected length plus 0.2 s, the device cuts it (`internal`, then a restart). The device's watchdog (3 s) restarts it if the whole step takes longer. **So within about 3 s, either the reply arrives or the device restarts and the connection drops**
- Recommended app timeout: **5 seconds**. If nothing arrives by then and the connection is still up, send `status` and look at `last_tx_s` to see whether the beacon was built

### 7.6 `msg.send` [Provisional]

Queues an APRS message to another station. The device answers at once; the transmissions follow in the device's loop and are reported with `msg` events (§8.8).

```json
{"op": "msg.send", "id": 9, "to": "Q1AAA-9", "text": "Hello"}
{"t": "msg", "id": 9, "dir": "out", "no": "12", "to": "Q1AAA-9", "text": "Hello", "state": "pending", "tries": 0, "utc": "2026-11-16T03:04:05Z"}
```

- `to`: the addressee, 1–9 characters of letters, digits and `-` (callsign with an optional SSID, such as `Q1AAA-9`). Lowercase is converted to uppercase. Own callsign-SSID is rejected
- `text`: 1–67 characters of printable ASCII (0x20–0x7E), without `|`, `~` and `{`. Japanese or other non-ASCII text is rejected. The device does not trim spaces
- `no`: the message number the device assigned (1–5 digits). **Treat it as an opaque string.** The device continues the numbers across restarts when a microSD card is inserted
- The device checks these in order; if one fails it returns `err` (§8.6) and queues nothing:

  | Condition | `code` when not met |
  |---|---|
  | `to` and `text` are valid | `invalid` (`key` is `"to"` or `"text"`) |
  | The unit's beacon switch is on (`status.sw`) | `switch_off` |
  | The transmit gate allows transmitting (callsign set, `tx_enabled`) | `tx_not_ready` (`msg` holds the reason, as in `status.tx_refuse`) |
  | Fewer than 4 messages are `pending` | `queue_full` |

- A GNSS fix is not needed
- **Transmissions**: the first one is made as soon as the switch is on, the gate allows it and the channel is clear (§7.9). Without an acknowledgement, the device sends it again 30, 60, 120 and 240 s after the previous try: **5 tries at most**. When 240 s pass after the 5th try without an acknowledgement, the state becomes `failed`
- While the switch is off, the gate refuses, or the channel is busy, a due try waits (it is not counted). A message stays `pending` until it is sent, acknowledged, or cancelled
- An acknowledgement (`ack`) or rejection (`rej`) from the addressee with the same number ends the message as `acked` or `rejected`, also when it arrives after `failed`. Both the APRS 1.0 form and the APRS 1.1 reply-ack form (`{12}AB`) are recognised
- Messages are never dry runs: when the gate refuses, the device does not transmit and the message waits
- Each try is also reported by a `tx` event (§8.3)

### 7.7 `msg.list` [Provisional]

```json
{"op": "msg.list", "id": 10}
{"t": "msg", "id": 10, "dir": "in", "no": "7", "src": "Q1AAA-9", "text": "Hi there", "utc": "2026-11-16T03:10:00Z"}
{"t": "msg", "id": 10, "dir": "out", "no": "12", "to": "Q1AAA-9", "text": "Hello", "state": "acked", "tries": 1, "utc": "2026-11-16T03:04:05Z"}
{"t": "msg.end", "id": 10, "n": 2}
```

- Received and sent messages together, **newest first**. The device keeps the **16** newest (a `pending` message is never dropped)
- The list survives a restart when a microSD card is inserted (the device saves the messages to it and reads them back once it knows the time). A message that was `pending` when the device stopped comes back as `stopped` and is not sent again
- `msg.list` clears the unread count (`status.msg_unread`, also shown on the LCD)

### 7.8 `msg.cancel` [Provisional]

```json
{"op": "msg.cancel", "id": 11, "no": "12"}
{"t": "msg", "id": 11, "dir": "out", "no": "12", "to": "Q1AAA-9", "text": "Hello", "state": "cancelled", "tries": 2}
```

- Stops a `pending` message. A message that already ended is returned unchanged
- `no` not among the messages the device keeps: `err` with `unknown_msg`

### 7.9 Channel check [Provisional]

The device has no carrier detect. It judges the channel from the received audio: the radio's squelch opens while a signal is on the channel, and the level then rises well above the closed-squelch silence.

- The channel is **busy** while the RMS level of the received audio (`status.lvl_db`, measured every 0.1 s) is at or above a threshold, and stays busy 0.5 s after the level falls below it (squelch tail)
- After the channel becomes clear, the device waits a random 0–1 s before transmitting, so that two stations waiting for the same channel do not key at the same moment
- The device leaves at least 3 s between its own transmissions (messages, acknowledgements, automatic beacons)
- If the channel stays busy for 30 s without a break, the device assumes the squelch is held open and transmits anyway
- The threshold is a device setting made from measurements of the radio (not adjustable from the app yet). `status.ch_busy` shows the current judgement
- Applies to automatic beacons, messages and acknowledgements. `beacon.send` does not wait

## 8. Responses and events [Fixed]

### 8.1 `status`

Sent both as a response to a request and as an event. It is sent as an event:

- right after connecting
- every 5 seconds
- when any of `fix`, `sw`, `tx_ready`, `tx_enabled`, `call`, `beacon_mode`, `msg_unread`, or `msg_pending` changes (at the latest about 20 ms later, in the device's loop)

```json
{"t": "status", "utc": "2026-11-16T03:04:05Z", "fix": 3, "sats": 9, "lat": 35.68124, "lon": 139.76712,
 "sw": true, "tx_enabled": true, "tx_ready": true, "call": "Q1TEST-7", "last_tx_s": 42,
 "next_beacon_s": 48, "rx_count": 17, "tx_count": 3, "lvl_db": -18.5, "sd": "log 0012", "dropped": 0}
```

| Key | Type | Description |
|---|---|---|
| `utc` | str | GNSS time. Omitted if unknown |
| `fix` | int | 0 = no fix, 2 = 2D, 3 = 3D |
| `sats` | int | Number of satellites used in the fix |
| `lat`, `lon` | float | Own position. Only when `fix` is 2 or more |
| `sw` | bool | Whether the unit's physical beacon switch is on |
| `beacon_mode` | str | Value of the `beacon_mode` setting: `"auto"` or `"manual"` (§9.1) |
| `tx_enabled` | bool | Value of the `tx_enabled` setting |
| `tx_ready` | bool | **Whether the next beacon will actually go on the air.** True when `sw` is on and the transmit conditions are met (callsign set, `tx_enabled`). When false, a beacon is a dry run (recorded only, no radio output), or with `sw` off, `beacon.send` is refused. Does not depend on `beacon_mode` |
| `tx_refuse` | str | Why the transmit conditions are not met. Omitted when they are. `"callsign not set"`, `"TX not enabled"`, `"TX clock off failed"`. **A display string; more values may be added** |
| `call` | str | Own callsign-SSID, as in the settings (`callsign`, plus `-ssid` when `ssid` is not 0). **Omitted while the callsign is unset** (the LCD shows a placeholder, which is not sent) |
| `last_tx_s` | int | Seconds since the last beacon was built (automatic or `beacon.send`, dry runs included). Omitted if none since startup |
| `next_beacon_s` | int | Seconds until the next automatic beacon; 0 = now. Omitted when the switch is off, there is no fix, the time is unknown, or `beacon_mode` is `"manual"` (look at `beacon_mode` to tell these apart) |
| `rx_count`, `tx_count` | int | Packets received and transmitted since startup (the transmit count includes dry runs) |
| `lvl_db` | float | RMS level of the received audio (dBFS). Shows the noise level even with no signal |
| `sd` | str | microSD state: `"no card"`, `"wait"` (waiting for time), `"log NNNN"` (file number being logged), `"error"`, `"closed"`. **A display string** |
| `dropped` | int | Number of `rx` events dropped in this connection because they could not be sent in time (§8.5) |
| `msg_unread` | int | Messages received for this device since the last `msg.list` (§7.7) |
| `msg_pending` | int | Messages sent by the app that are still `pending` (§7.6) |
| `ch_busy` | bool | Whether the channel is judged busy now (§7.9). A change does not trigger a `status` event |

### 8.2 `rx` (event)

One per successfully decoded received packet.

```json
{"t": "rx", "tnc2": "Q1AAA-9>APRS,WIDE1-1*:!3540.12N/13945.67E>Hello", "src": "Q1AAA-9",
 "kind": "position", "lat": 35.66867, "lon": 139.76117, "sym": "/>", "lvl_db": -20.1,
 "utc": "2026-11-16T03:04:05Z"}
```

| Key | Type | Description |
|---|---|---|
| `tnc2` | str | The whole packet in TNC2 format (`SRC>DST,PATH:information`). Bytes in the information field that are not printable ASCII (below 0x20 or 0x7F and above) are written as `<0xHH>` (two uppercase hex digits). Japanese text in UTF-8 and the like becomes `<0xE3><0x81>...` |
| `src` | str | Sender (callsign-SSID). Always the station that sent the packet, also for objects and items. For third-party traffic (`}`), the device parses the inner packet, so `src` is the **inner** sender (the outer header is in `tnc2`) |
| `obj` | str | Object or item name. Only when `kind` is `"object"` or `"item"` |
| `kind` | str | Type from the device's parser: `"position"`, `"mice"`, `"object"`, `"item"`, `"message"`, `"status"`, `"other"`. **More may be added.** Treat an unknown value as `"other"` |
| `lat`, `lon` | float | When the packet contains a position. This is the position **in the packet**: for `kind` `"object"` and `"item"` it is the object's position, not the sender's. The device reads positions only from position reports, Mic-E, objects and items (not from a locator in a status text) |
| `sym` | str | Symbol (2 characters: symbol table + symbol), when known. For objects and items, the object's symbol |
| `lvl_db` | float | Receive level of this packet (dBFS, peak of the last 1 second) |
| `clip` | bool | True if the input was saturated. Omitted otherwise |
| `utc` | str | Time received. Omitted if unknown |

`src`, `kind`, `lat`, and so on are omitted for packets the device could not parse (`tnc2` is always present). The app should parse `tnc2` itself to get further detail (comment, speed, and so on).

To keep one table from `stations.list` and later `rx` events the way the device does (§8.4): when `obj` is present, update the object row named `obj`; otherwise update the station row `src`. Keep the two kinds apart, because an object may have the same name as a station.

### 8.3 `tx` (event)

One each time the device builds a packet to send: a beacon (automatic or sent by `beacon.send`, dry runs included), a message try (§7.6), or an acknowledgement the device sends by itself (§8.8).

```json
{"t": "tx", "tnc2": "Q1TEST-7>SU4PX0,WIDE1-1:`CIXl <0x1C>[/\"4!}Pico2 APRS", "dry": false, "utc": "2026-11-16T03:04:05Z"}
```

- `dry`: if true, nothing was transmitted (`tx_ready` was false). Only beacons can be dry runs; messages and acknowledgements are sent only when the gate allows it
- Do not assume a `tx` event is a beacon. Parse `tnc2` if the kind matters

### 8.4 `station`

Each row is **either a station or an object/item**:

- A station row is keyed by the sender's callsign-SSID. It counts the packets that station sent, except objects and items. A station that has only sent objects or items has no row of its own
- An object/item row is keyed by the object or item name and carries `"obj": true`. It counts the object or item reports with that name, from any sender
- An object with the same name as a station is a separate row (one without `obj`, one with `"obj": true`)
- For third-party traffic (`}`), the row is that of the inner sender (or of the inner object). The outer station (for example an IGate) is not counted

| Key | Type | Description |
|---|---|---|
| `src` | str | Station callsign-SSID, or the object/item name when `obj` is true |
| `obj` | bool | True for an object or item. Omitted otherwise |
| `last_s` | int | Seconds since last heard |
| `count` | int | Number of times heard (may include counts from before startup) |
| `lat`, `lon` | float | Last received position. Omitted if none |
| `lvl_db` | float | Receive level of the last packet. Omitted if none |
| `own` | bool | True for own station (own packet that came back via a digipeater). Never set on an object row. Omitted otherwise |

### 8.5 When sending cannot keep up

When BLE cannot send fast enough, items queue up on the device.

- Responses to requests are never dropped (they arrive late)
- The event queue holds at most 8 lines. When it overflows, the oldest `rx` is dropped and counted in `status.dropped` (`dropped` counts from 0 for each connection). Only when no `rx` is waiting is the oldest event of another kind dropped (also counted); a lost `msg` event can be recovered with `msg.list`
- An old `status` is dropped when a newer `status` arrives (only the latest is sent)

### 8.6 `err`

```json
{"t": "err", "id": 5, "code": "invalid", "key": "beacon_still_s", "msg": "integer 60-7200"}
```

| `code` | Meaning | `id` |
|---|---|---|
| `bad_json` | Not readable as JSON, or not an object | absent |
| `too_long` | A line exceeded 512 bytes | absent |
| `busy` | The device could not keep up and dropped one or more lines (too many sent in a short time). Wait a moment and resend (see below) | absent |
| `unknown_op` | Unknown `op` (`msg` holds the first 32 characters of the received `op`) | present |
| `invalid` | A settings value is invalid (`key` is the item, `msg` is the condition). Nothing was changed | present |
| `io` | Could not save to flash. Nothing was changed | present |
| `internal` | An internal device error (`msg` holds the detail). The device keeps running, except after a failed transmission in `beacon.send` (§7.5) | present |
| `switch_off` | `beacon.send`: the unit's beacon switch is off. Nothing was sent | present |
| `no_fix` | `beacon.send`: no 2D/3D fix, or the time is not known yet. Nothing was sent | present |
| `too_soon` | `beacon.send`: less than 10 seconds since the last beacon. `wait_s` (int) is the seconds left. Nothing was sent | present |
| `frame_too_long` | `beacon.send`: the beacon does not fit in the transmit buffer. Nothing was sent | present |
| `tx_not_ready` | `msg.send`: the transmit gate refuses (callsign unset or `tx_enabled` false; `msg` holds the reason). Nothing was queued | present |
| `queue_full` | `msg.send`: 4 messages are already `pending`. Wait or cancel one. Nothing was queued | present |
| `unknown_msg` | `msg.cancel`: no message with that `no` is kept | present |

`switch_off` is also returned by `msg.send`, and `invalid` by `msg.send` (`key` `"to"` or `"text"`) and `msg.cancel` (`key` `"no"`).

`msg` is an English string for display and its wording may change. The app should decide by `code` and `key`.

Guarantees of `busy`:

- The device keeps up to 4 complete lines waiting to be processed (it processes them in its loop, about every 20 ms). A line that completes while 4 are waiting is **discarded without being parsed or executed**: the newest line is dropped, and the lines already waiting are kept
- No response with the dropped line's `id` will follow, so **resending is always safe**, for every `op` (`settings.set` and `settings.reset` included: nothing was changed)
- `busy` comes after the responses to the lines that were already waiting. One `busy` may stand for several dropped lines
- An app that sends one request at a time and waits for its response never gets `busy`

### 8.7 `beacon` (response)

The response to `beacon.send` (§7.5) when the beacon was built. It never comes as an event.

```json
{"t": "beacon", "id": 7, "dry": false}
```

| Key | Type | Description |
|---|---|---|
| `id` | int | The request's `id` (absent if the request had none) |
| `dry` | bool | **Always present.** `false`: the beacon was transmitted. `true`: it was a dry run (built and recorded, no radio output, because `tx_ready` was false) |

- When the device does not send, the response is an `err` (`switch_off`, `no_fix`, `too_soon`, `frame_too_long`, `internal`; §8.6), never a `beacon`
- The `tx` event (§8.3) with the packet follows right after this response

### 8.8 `msg` (response and event) [Provisional]

One APRS message. The same shape is used as the response to `msg.send` and `msg.cancel`, for each row of `msg.list` (all with `id`), and as an event (without `id`).

| Key | Type | Description |
|---|---|---|
| `dir` | str | `"in"`: received, addressed to this device. `"out"`: sent from the app with `msg.send` |
| `no` | str | Message number. Always present for `"out"`. For `"in"`, present when the sender gave one |
| `src` | str | `"in"` only: the sender (callsign-SSID; the inner sender for third-party traffic) |
| `to` | str | `"out"` only: the addressee |
| `text` | str | Message text. For `"in"`, the received bytes decoded as UTF-8 (so Japanese text sent in UTF-8 arrives as text); if they are not valid UTF-8, each byte outside printable ASCII becomes `?` |
| `state` | str | `"out"` only: `"pending"` (waiting to be sent or for an acknowledgement), `"acked"`, `"rejected"`, `"failed"` (no acknowledgement after 5 tries), `"cancelled"` (`msg.cancel`), `"stopped"` (the device restarted before it ended). **More may be added**; treat an unknown value as ended |
| `tries` | int | `"out"` only: transmissions so far |
| `utc` | str | When it was received (`"in"`) or queued (`"out"`). Omitted if the time was unknown |

Events are sent:

- `"in"`: when a message addressed to this device's callsign-SSID (compared without regard to case) is received for the first time. A repeated copy (the same sender and number, or for a message without number the same sender and text, among the last 32) is not reported again
- `"out"`: after each try is transmitted (`tries` changes) and when `state` changes

Acknowledgements sent by the device:

- For each received copy of a message with a number addressed to this device, including repeated copies (a repeat means the sender did not hear the earlier acknowledgement), the device sends `ack` once, under the same conditions as any transmission (switch on, gate allows, channel clear). An acknowledgement that cannot be sent within 60 s is dropped
- Messages without a number are not acknowledged (APRS does not acknowledge them)
- Messages addressed to other stations, bulletins and announcements are not handled here; they arrive only as `rx` events (`kind` `"message"`)

## 9. Settings items

### 9.1 Item list [Fixed]

| Key | Type | Range / format | Description |
|---|---|---|---|
| `callsign` | str / null | Uppercase letters and digits, 3–6 characters, at least one digit. No SSID. **Lowercase letters, surrounding whitespace, or an SSID are rejected** with `invalid`. The device does not convert or trim, so the stored value is exactly the value sent | Own callsign. null means unset, and the device never transmits |
| `ssid` | int | 0–15 | SSID |
| `symbol` | str | 2 characters. The 1st is the symbol table (`/`, `\`, `0`–`9`, `A`–`Z`); the 2nd is `!`–`~` | Symbol |
| `path` | list[str] | 0–2 entries. Each is of the form `WIDE1-1` (1–6 letters/digits with an optional `-0`–`-15`). Lowercase is converted to uppercase. No trailing `*`. For `WIDEn-N` and `TRACEn-N` entries: n is 1–2 and N is 1–n, and the N of all such entries add up to 3 or less (`WIDE1-1,WIDE2-1`, `WIDE2-2` and `WIDE1-1,WIDE2-2` are accepted; `WIDE7-7`, `WIDE2-3` and `WIDE2-2,WIDE2-2` are not). Other entries (a digipeater's callsign) are not counted | Digipeater path |
| `comment` | str | Printable ASCII (0x20–0x7E), 0–40 characters | Beacon comment |
| `mice_message` | int | 0–6 | Mic-E message: 0 Off Duty, 1 En Route, 2 In Service, 3 Returning, 4 Committed, 5 Special, 6 Priority. 7 (Emergency) cannot be set from the app |
| `beacon_moving_s` | int | 60–3600 | Beacon interval **while moving** (seconds) |
| `beacon_still_s` | int | 60–7200 | Beacon interval **while not moving** (seconds) |
| `beacon_move_m` | float | 25–1000 | Counted as "moving" when farther than this from the position of the previous beacon (m) **and** the GNSS speed is 2 knots (3.7 km/h) or more |
| `beacon_mode` | str | `"auto"` or `"manual"` | `"auto"`: the device sends beacons by itself by the rule below. `"manual"`: it sends only on `beacon.send` (§7.5). In both modes the switch must be on |
| `tx_enabled` | bool | | Transmit permission. Even when true, the device does not transmit while the unit's switch is off |
| `fx25_tx` | bool | | Transmit with FX.25 (with forward error correction) |

- Types are strict: `1.0` or `true` is not accepted for an int item. An integer is accepted for a float item
- Beacon rule (`beacon_mode` `"auto"`): with the switch on and a fix, the first beacon is sent immediately. After that, every `beacon_moving_s` while moving, or every `beacon_still_s` while not moving. "Moving" needs both the distance (`beacon_move_m`) and a speed of 2 knots or more, so that GNSS jitter while parked does not count as moving. Turning the switch off and on starts over (the next beacon is immediate). When `beacon_mode` changes from `"manual"` to `"auto"`, the rule continues from the last beacon; if there was none since startup or since the switch was last turned on, a beacon is sent immediately. A due beacon waits for a clear channel (§7.9), so it can come a little later than the interval; `next_beacon_s` stays 0 while it waits
- Items may be added later. Build the screen from the result of `settings.schema`, and either hide unknown items or handle them with a generic input

### 9.2 Shape of `settings.schema` [Fixed]

```json
{"t": "schema", "id": 2, "items": [
  {"key": "callsign", "desc": "Own callsign without SSID (null = not set, never transmit)",
   "type": "str", "nullable": true, "pattern": "^[A-Z0-9]{3,6}$"},
  {"key": "ssid", "desc": "SSID", "type": "int", "min": 0, "max": 15},
  {"key": "symbol", "desc": "Symbol table and code (2 chars)", "type": "str", "len": 2},
  {"key": "path", "desc": "Digipeater path", "type": "list", "max_items": 2},
  {"key": "comment", "desc": "Beacon comment", "type": "str", "max_len": 40},
  {"key": "tx_enabled", "desc": "Transmit permission (the beacon switch must also be on)", "type": "bool"}
]}
```

(An excerpt. The real response lists all items from §9.1 in that order.)

| Key | Meaning |
|---|---|
| `key` | Item key |
| `desc` | Short English description (usable as a screen hint; the wording may change) |
| `type` | `"str"`, `"int"`, `"float"`, `"bool"`, `"list"` (list of strings) |
| `nullable` | If true, null is also accepted |
| `min`, `max` | Numeric range (both ends inclusive) |
| `len` | Fixed string length |
| `max_len` | Maximum string length |
| `max_items` | Maximum number of list entries |
| `choices` | For `"str"`: the only accepted values (list). Show a selection. When absent, any string matching the other conditions is accepted |
| `pattern` | A guide to the string format (regular expression). Some conditions cannot be expressed, such as "contains a digit" for `callsign` |

The schema is an aid to input; the device makes the final decision (an invalid value returns `err` with `invalid`).

## 10. Notes for building the Web app

- Web Bluetooth requires an HTTPS page (or `localhost` during development), and `requestDevice` must be called from a user action such as a button press
- The target is Chrome on Android. Web Bluetooth also works in Chrome on Windows and macOS, so development can be done on a PC (the pairing flow differs per OS)
- Reconnecting: a device the user has already allowed can be retrieved with `navigator.bluetooth.getDevices()`. Reconnect with `device.gatt.connect()` and detect disconnection with the `gattserverdisconnected` event
- After reconnecting, repeat the flow in §6.2 from the beginning (the device sends the state from scratch on every connection)
- Reception history is not persisted. Put an upper limit on how many entries the screen keeps (the device sends an `rx` for every received packet; in a busy area that can be dozens per minute)
- The device changes its behavior immediately on `settings.set`. Putting a confirmation dialog in front of the action that sets `tx_enabled` to true is recommended
- `beacon.send` puts a signal on the air at once. Make it a deliberate action (for example a button that is disabled while `tx_ready` is false, or a confirmation), and do not send it automatically or repeatedly from the app. Do not send other requests while waiting for its response
- Use fictitious values for callsigns in examples and test data (such as `Q1TEST`, starting with Q and not a real callsign)

### 10.1 Known issue: Windows stops receiving notifications

Seen on 2026-11-17 on one Windows 11 PC with a **Bluetooth 4.0** adapter, using `bleak` (not yet tried in Chrome):

- A response made of a single notification works every time (60 requests in a row). But when the device sends **several notifications back to back** (for example `settings.schema`, which is about 1.1 KB), the first such response arrives in full, and **partway through a later one, notifications stop arriving**
- After that, Windows does not process anything from the device, even after reconnecting or restarting the device. **Only turning Bluetooth off and on in Windows recovers it**
- The device sends everything correctly. Android received the same traffic in full every time, with MTU 23 and with MTU 517. Changing the notification size (20–514 bytes), adding 50 ms between notifications, or limiting the MTU to 23 did not help on Windows

For now, develop and test the Web app on **Android**. Whether Windows works with a newer adapter (Bluetooth 5.x) has not been tested yet (U8). If the app must support Windows, expect the connection to stop answering, and tell the user to turn Bluetooth off and on.

## 11. Open items

| # | Item | Section | When it will be decided |
|---|---|---|---|
| U2 | Whether to add the Device Information Service | §3 | When the Web app needs it |
| U3 | What Web Bluetooth in Chrome reports for a wrong or dismissed passkey and for a bond removed on one side only; Chrome on Windows. (Android Chrome pairs as described in §4, with the dialog reached through a notification) | §4 | Further tests from the Web app (issue #5) |
| U5 | Whether to put the firmware version in `hello` | §7.1 | When the Web app needs it |
| U6 | How much reception is lost while `settings.set` saves | §7.2 | Task 7 (measurement) |
| U8 | Whether Windows with a Bluetooth 5.x adapter avoids the issue in §10.1 | §10.1 | Test with a USB Bluetooth 5.x adapter |
| U9 | The channel threshold (§7.9), and whether to make it a setting | §7.9 | Measure the level with the squelch closed, open, and with a signal |

When an item is decided, remove it from this table, write it into the body, and record it in §12.

## 12. Change log

| Version | Date | Change |
|---|---|---|
| 0.9 | 2026-11-22 | §9.1: tighter limits for transmissions. `path` limits `WIDEn-N`/`TRACEn-N` (n ≤ 2, N ≤ n, total N ≤ 3). `beacon_moving_s` minimum 30 → 60, `beacon_move_m` minimum 1 → 25 (default 10 → 25). "Moving" also needs a speed of 2 knots or more. A stored value outside the new limits is dropped at startup and the default is used |
| 0.8 | 2026-11-20 | APRS messaging (provisional, being implemented): `msg.send`, `msg.list`, `msg.cancel` (§7.6–§7.8), the `msg` response and event (§8.8), error codes `tx_not_ready`, `queue_full`, `unknown_msg`, and `status.msg_unread`, `msg_pending`, `ch_busy`. The device acknowledges messages addressed to it by itself. Channel check from the received audio level (§7.9), used for automatic beacons, messages and acknowledgements (not `beacon.send`). §8.3: a `tx` event may be a message or an acknowledgement. §1 updated. `proto` stays 1 (additions only) |
| 0.7 | 2026-11-20 | §4: results of the first Chrome test (issue #5). On Android the passkey dialog may appear only as a notification the user has to tap; the app's hint text should say so and point to the LCD. Added the Chrome row to the verification table. U3 narrowed to the failure cases and Chrome on Windows |
| 0.6 | 2026-11-19 | Answers to issues #7 and #8. §7.5: measured time until the reply (at most about 2 s, recommended timeout 5 s, no carrier sense); `frame_too_long` cannot happen with the current setting limits. Added §8.7 for the `beacon` response (`dry` is always a boolean) |
| 0.5 | 2026-11-19 | Added `beacon.send` (§7.5) with the response `beacon` and the error codes `switch_off`, `no_fix`, `too_soon`, `frame_too_long`. Added the setting `beacon_mode` (`"auto"` / `"manual"`, §9.1), `status.beacon_mode`, and `choices` in the schema (§9.2). §1: the app can now command a beacon; the switch still has to be on. `proto` stays 1 (additions only) |
| 0.4 | 2026-11-18 | Answers to issues #1–#6. Added §5.3 (partial lines, resync with a bare `\n`). §4: how the app learns that authentication completed, time limits; the device now disconnects a connection that is not authenticated within 60 s. §8.1: `call` is omitted while the callsign is unset. §8.2: added `obj`; `src` is always the sender (the inner sender for `}`); `lat`/`lon` are the packet's position. §8.4: a row is a station or an object, added `obj`; an object named like a station is now a separate row. §8.6: guarantees of `busy`. §9.1: lowercase or whitespace in `callsign` is rejected. Time limits measured on hardware (60 s cut, 30 s passkey) |
| 0.3 | 2026-11-17 | BLE layer verified on real hardware: §2, §3, §5.2 fixed. Advertising interval 100 ms, public address. Notifications capped at 244 bytes. App writes fixed at 20 bytes or fewer. Pre-authentication commands are silently dropped. Added the hardware results to §4, the note about the first `status` to §6.2, and the Windows known issue (§10.1). Closed U1, U4, U7; added U8 |
| 0.2 | 2026-11-16 | Added the `busy` error. Pairing and bonding confirmed available in the firmware |
| 0.1 | 2026-11-16 | First version. Message handling (§6–§9) is fixed to match the implementation; the BLE layer (§2–§5.2) is provisional |
