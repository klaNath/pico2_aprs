# BLE Interface Specification (for Web app developers)

The agreement for operating pico2_aprs (an APRS station built on a Raspberry Pi Pico 2 W) from a smartphone Web app over BLE.

- Audience: developers of the Web app (separate repository)
- Design background on the device side: [ble-control.md](ble-control.md) (written in Japanese; it covers the device's internals. This document is meant to be sufficient on its own for building the Web app)
- Document version: 0.4 (2026-11-18) / protocol version `proto`: 1

## 0. Status markers

Each section heading carries one of these markers.

| Marker | Meaning |
|---|---|
| **[Fixed]** | Implemented on the device and verified: by tests on a PC (with a fake BLE link), and for the BLE layer (§2–§5) also over real BLE. Changes must start with this document |
| **[Provisional]** | The direction is decided, but it is not implemented on the device, or not yet verified over real BLE. Values may change |
| **[Open]** | Not decided yet. The Web app should be built so that it survives a change |

Status as of 2026-11-17: everything is implemented on the device. The BLE layer (advertising, GATT, pairing, line splitting) was verified on real hardware with nRF Connect on Android and with `bleak` on Windows. **It has not been tried from a Web app in Chrome yet**, so the pairing flow (§4) stays provisional. On Windows, see the known issue in §10.

## 1. Scope

What the app can do (first phase):

- Read the device state (GNSS fix, beacon switch, transmit readiness, receive level, and so on)
- Receive each decoded packet as it arrives (only while connected; the app is not expected to persist them)
- Get the list of stations heard so far, when connecting
- Read, change, and reset settings (changes are saved to the device's flash and survive power cycles)

What the app cannot do: command a transmission (such as "send a beacon now"), APRS messaging, serve map data, KISS, or update firmware.

**Transmit safety**: the device emits a radio signal only when the **physical beacon switch on the unit is on** and the device's transmit gate allows it (a callsign is set and `tx_enabled` is true). Changing settings from the app cannot bypass the switch. The app should show `status.tx_ready` (§8.1) prominently.

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
  2. Android shows a pairing dialog
  3. The device's LCD shows a 6-digit passkey
  4. The user types it into the dialog
- On later connections the stored bonding key is used and no passkey is shown
- Until the connection is authenticated, the device does not process commands and does not send notifications. RX accepts writes only on an authenticated connection, so a write before authentication is refused by the GATT layer (Insufficient Authentication), which is what makes the phone start pairing. Any bytes that still reach the device before authentication are **silently dropped** (no `err` is returned)
- **How the app learns that authentication completed**: start notifications on TX first (that does not need authentication), then write `hello`. As soon as the connection is authenticated, the device sends a `status` event (§6.2), so the first `status` (or the `hello` response) is the signal. If neither arrives, resend `hello` after a few seconds. Do not tear the connection down before the time limit below while the user may still be typing the passkey
- **Time limits**: the device has no passkey timeout of its own. While the passkey is shown, pairing is limited by the Bluetooth Security Manager timeout (30 s without a pairing message, from the Bluetooth Core Specification; the phone may have its own limit; not measured yet) and by this rule: **if a connection is not authenticated within 60 seconds of connecting, the device disconnects it** and advertises again. When pairing fails or the link drops, the passkey disappears from the LCD. A new attempt needs a new connection and shows a new passkey
- While any central is connected, authenticated or not, the device does not advertise (§2), so other phones cannot find it. The 60-second limit keeps an abandoned, unauthenticated connection from hiding the device
- A central that already has a bond reconnects without a passkey (verified on Android and Windows, below)
- The device refuses pairing methods without MITM protection (Just Works) and legacy (non-Secure-Connections) pairing
- The device stores keys for up to 4 peers (oldest removed first). Keys can be erased only over the device's USB. If the app can no longer reconnect after the device erased its keys, ask the user to "forget" the device in the phone's or PC's Bluetooth settings

Verified on real hardware (2026-11-17):

| Central | Result |
|---|---|
| Android, nRF Connect | The pairing dialog appears when bonding is started. Passkey pairing succeeds. Reconnects without a passkey, also after the device is powered off and on |
| Windows 11, `bleak` with WinRT passkey pairing (`tools/ble_cli.py pair`) | Passkey pairing succeeds. Reconnects without a passkey. See the known issue in §10 |

**[Open]**: whether the Web Bluetooth implementation in Chrome (Android and Windows) shows the pairing dialog as described above. If it does not, the procedure may become "pair in the OS Bluetooth settings first".

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

## 8. Responses and events [Fixed]

### 8.1 `status`

Sent both as a response to a request and as an event. It is sent as an event:

- right after connecting
- every 5 seconds
- when any of `fix`, `sw`, `tx_ready`, `tx_enabled`, or `call` changes (at the latest about 20 ms later, in the device's loop)

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
| `tx_enabled` | bool | Value of the `tx_enabled` setting |
| `tx_ready` | bool | **Whether the next beacon will actually go on the air.** True when `sw` is on and the transmit conditions are met (callsign set, `tx_enabled`). When false, a beacon is a dry run (recorded only, no radio output) |
| `tx_refuse` | str | Why the transmit conditions are not met. Omitted when they are. `"callsign not set"`, `"TX not enabled"`, `"TX clock off failed"`. **A display string; more values may be added** |
| `call` | str | Own callsign-SSID, as in the settings (`callsign`, plus `-ssid` when `ssid` is not 0). **Omitted while the callsign is unset** (the LCD shows a placeholder, which is not sent) |
| `last_tx_s` | int | Seconds since the last beacon was built (dry runs included). Omitted if none since startup |
| `next_beacon_s` | int | Seconds until the next beacon; 0 = now. Omitted when the switch is off, there is no fix, or the time is unknown |
| `rx_count`, `tx_count` | int | Packets received and transmitted since startup (the transmit count includes dry runs) |
| `lvl_db` | float | RMS level of the received audio (dBFS). Shows the noise level even with no signal |
| `sd` | str | microSD state: `"no card"`, `"wait"` (waiting for time), `"log NNNN"` (file number being logged), `"error"`, `"closed"`. **A display string** |
| `dropped` | int | Number of `rx` events dropped in this connection because they could not be sent in time (§8.5) |

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

One each time the device builds a beacon (dry runs included).

```json
{"t": "tx", "tnc2": "Q1TEST-7>SU4PX0,WIDE1-1:`CIXl <0x1C>[/\"4!}Pico2 APRS", "dry": false, "utc": "2026-11-16T03:04:05Z"}
```

- `dry`: if true, nothing was transmitted (`tx_ready` was false)

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
- The event queue holds at most 8 lines. When it overflows, the oldest `rx` is dropped and counted in `status.dropped` (`dropped` counts from 0 for each connection)
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
| `internal` | An internal device error (`msg` holds the detail). The device keeps running | present |

`msg` is an English string for display and its wording may change. The app should decide by `code` and `key`.

Guarantees of `busy`:

- The device keeps up to 4 complete lines waiting to be processed (it processes them in its loop, about every 20 ms). A line that completes while 4 are waiting is **discarded without being parsed or executed**: the newest line is dropped, and the lines already waiting are kept
- No response with the dropped line's `id` will follow, so **resending is always safe**, for every `op` (`settings.set` and `settings.reset` included: nothing was changed)
- `busy` comes after the responses to the lines that were already waiting. One `busy` may stand for several dropped lines
- An app that sends one request at a time and waits for its response never gets `busy`

## 9. Settings items

### 9.1 Item list [Fixed]

| Key | Type | Range / format | Description |
|---|---|---|---|
| `callsign` | str / null | Uppercase letters and digits, 3–6 characters, at least one digit. No SSID. **Lowercase letters, surrounding whitespace, or an SSID are rejected** with `invalid`. The device does not convert or trim, so the stored value is exactly the value sent | Own callsign. null means unset, and the device never transmits |
| `ssid` | int | 0–15 | SSID |
| `symbol` | str | 2 characters. The 1st is the symbol table (`/`, `\`, `0`–`9`, `A`–`Z`); the 2nd is `!`–`~` | Symbol |
| `path` | list[str] | 0–2 entries. Each is of the form `WIDE1-1` (1–6 letters/digits with an optional `-0`–`-15`). Lowercase is converted to uppercase. No trailing `*` | Digipeater path |
| `comment` | str | Printable ASCII (0x20–0x7E), 0–40 characters | Beacon comment |
| `mice_message` | int | 0–6 | Mic-E message: 0 Off Duty, 1 En Route, 2 In Service, 3 Returning, 4 Committed, 5 Special, 6 Priority. 7 (Emergency) cannot be set from the app |
| `beacon_moving_s` | int | 30–3600 | Beacon interval **while moving** (seconds) |
| `beacon_still_s` | int | 60–7200 | Beacon interval **while not moving** (seconds) |
| `beacon_move_m` | float | 1–1000 | Counted as "moving" when farther than this from the position of the previous beacon (m) |
| `tx_enabled` | bool | | Transmit permission. Even when true, the device does not transmit while the unit's switch is off |
| `fx25_tx` | bool | | Transmit with FX.25 (with forward error correction) |

- Types are strict: `1.0` or `true` is not accepted for an int item. An integer is accepted for a float item
- Beacon rule: with the switch on and a fix, the first beacon is sent immediately. After that, every `beacon_moving_s` while moving, or every `beacon_still_s` while not moving
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
| `pattern` | A guide to the string format (regular expression). Some conditions cannot be expressed, such as "contains a digit" for `callsign` |

The schema is an aid to input; the device makes the final decision (an invalid value returns `err` with `invalid`).

## 10. Notes for building the Web app

- Web Bluetooth requires an HTTPS page (or `localhost` during development), and `requestDevice` must be called from a user action such as a button press
- The target is Chrome on Android. Web Bluetooth also works in Chrome on Windows and macOS, so development can be done on a PC (the pairing flow differs per OS)
- Reconnecting: a device the user has already allowed can be retrieved with `navigator.bluetooth.getDevices()`. Reconnect with `device.gatt.connect()` and detect disconnection with the `gattserverdisconnected` event
- After reconnecting, repeat the flow in §6.2 from the beginning (the device sends the state from scratch on every connection)
- Reception history is not persisted. Put an upper limit on how many entries the screen keeps (the device sends an `rx` for every received packet; in a busy area that can be dozens per minute)
- The device changes its behavior immediately on `settings.set`. Putting a confirmation dialog in front of the action that sets `tx_enabled` to true is recommended
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
| U3 | Whether Chrome (Android, Windows) shows the pairing dialog, at which call, and what Web Bluetooth reports for a wrong or dismissed passkey. If no dialog appears, the procedure changes | §4 | First test from the Web app (issue #5) |
| U5 | Whether to put the firmware version in `hello` | §7.1 | When the Web app needs it |
| U6 | How much reception is lost while `settings.set` saves | §7.2 | Task 7 (measurement) |
| U8 | Whether Windows with a Bluetooth 5.x adapter avoids the issue in §10.1 | §10.1 | Test with a USB Bluetooth 5.x adapter |

When an item is decided, remove it from this table, write it into the body, and record it in §12.

## 12. Change log

| Version | Date | Change |
|---|---|---|
| 0.4 | 2026-11-18 | Answers to issues #1–#6. Added §5.3 (partial lines, resync with a bare `\n`). §4: how the app learns that authentication completed, time limits; the device now disconnects a connection that is not authenticated within 60 s. §8.1: `call` is omitted while the callsign is unset. §8.2: added `obj`; `src` is always the sender (the inner sender for `}`); `lat`/`lon` are the packet's position. §8.4: a row is a station or an object, added `obj`; an object named like a station is now a separate row. §8.6: guarantees of `busy`. §9.1: lowercase or whitespace in `callsign` is rejected |
| 0.3 | 2026-11-17 | BLE layer verified on real hardware: §2, §3, §5.2 fixed. Advertising interval 100 ms, public address. Notifications capped at 244 bytes. App writes fixed at 20 bytes or fewer. Pre-authentication commands are silently dropped. Added the hardware results to §4, the note about the first `status` to §6.2, and the Windows known issue (§10.1). Closed U1, U4, U7; added U8 |
| 0.2 | 2026-11-16 | Added the `busy` error. Pairing and bonding confirmed available in the firmware |
| 0.1 | 2026-11-16 | First version. Message handling (§6–§9) is fixed to match the implementation; the BLE layer (§2–§5.2) is provisional |
