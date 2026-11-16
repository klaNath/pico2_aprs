# BLE Interface Specification (for Web app developers)

The agreement for operating pico2_aprs (an APRS station built on a Raspberry Pi Pico 2 W) from a smartphone Web app over BLE.

- Audience: developers of the Web app (separate repository)
- Design background on the device side: [ble-control.md](ble-control.md) (written in Japanese; it covers the device's internals. This document is meant to be sufficient on its own for building the Web app)
- Document version: 0.1 (2026-11-16) / protocol version `proto`: 1

## 0. Status markers

Each section heading carries one of these markers.

| Marker | Meaning |
|---|---|
| **[Fixed]** | Implemented on the device and verified by tests on a PC (with a fake BLE link). Changes must start with this document |
| **[Provisional]** | The direction is decided, but it is not implemented on the device, or not yet verified over real BLE. Values may change |
| **[Open]** | Not decided yet. The Web app should be built so that it survives a change |

Status as of 2026-11-16: message handling (§6–§9) is implemented. The BLE layer (advertising, GATT, pairing, line splitting) is **not implemented yet**, so §2–§4 are entirely provisional.

## 1. Scope

What the app can do (first phase):

- Read the device state (GNSS fix, beacon switch, transmit readiness, receive level, and so on)
- Receive each decoded packet as it arrives (only while connected; the app is not expected to persist them)
- Get the list of stations heard so far, when connecting
- Read, change, and reset settings (changes are saved to the device's flash and survive power cycles)

What the app cannot do: command a transmission (such as "send a beacon now"), APRS messaging, serve map data, KISS, or update firmware.

**Transmit safety**: the device emits a radio signal only when the **physical beacon switch on the unit is on** and the device's transmit gate allows it (a callsign is set and `tx_enabled` is true). Changing settings from the app cannot bypass the switch. The app should show `status.tx_ready` (§8.1) prominently.

## 2. Finding the device [Provisional]

| Item | Value |
|---|---|
| Service UUID in the advertisement | `6E400001-B5A3-F393-E0A9-E50E24DCCA9E` (NUS) |
| Name (scan response) | `PicoAPRS-XXXX` (XXXX is the lower 2 bytes of the MAC address in hex) **[Open]** |
| Concurrent connections | 1 |
| Advertising interval | **[Open]** |

With Web Bluetooth, filter by service UUID. The name format may change, so do not filter by name.

```js
const NUS = "6e400001-b5a3-f393-e0a9-e50e24dcca9e";
const device = await navigator.bluetooth.requestDevice({ filters: [{ services: [NUS] }] });
```

## 3. GATT [Provisional]

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
- Until the connection is authenticated, the device does not process commands and does not send notifications. **A command written before authentication is silently dropped.** The app should start notifications first, then send `hello`, and resend it after a few seconds if there is no reply
- The device stores keys for up to 4 peers (oldest removed first). Keys can be erased only over the device's USB. If the app can no longer reconnect after the device erased its keys, ask the user to "forget" the device in Android's Bluetooth settings

**[Open]**: whether the Web Bluetooth implementation in Android Chrome shows the pairing dialog as described above (to be tested on real hardware). If it does not, the procedure may become "pair in the Android settings first".

## 5. Framing

### 5.1 Lines [Fixed]

- One message is **one UTF-8 JSON object followed by a newline (`\n`, 0x0A)**. No newline may appear inside the JSON
- The device accepts lines of up to **512 bytes**, excluding the newline. A longer line is discarded and an `err` (`too_long`) is returned
- JSON sent by the device has no guaranteed key order and may contain spaces (`{"t": "status", ...}`)

### 5.2 Splitting across BLE writes and notifications [Provisional]

- **App → device**: a line may be split at any point and sent in any number of writes. The device concatenates until `\n`. For now keep each write to **20 bytes or fewer** (works with the minimum MTU of 23). The limit may be raised after the device implementation and measurements **[Open]**
- **Device → app**: one line may arrive across several notifications, and one notification may contain a line boundary. The app must concatenate the received bytes, cut at each `\n`, and parse each line as JSON
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
| `call` | str | Own callsign-SSID. While the callsign is unset, a placeholder display name |
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
| `src` | str | Sender. For objects and items it is still the sending station (read the object name from `tnc2`) |
| `kind` | str | Type from the device's parser: `"position"`, `"mice"`, `"object"`, `"item"`, `"message"`, `"status"`, `"other"`. **More may be added.** Treat an unknown value as `"other"` |
| `lat`, `lon` | float | When the packet contains a position |
| `sym` | str | Symbol (2 characters: symbol table + symbol), when known |
| `lvl_db` | float | Receive level of this packet (dBFS, peak of the last 1 second) |
| `clip` | bool | True if the input was saturated. Omitted otherwise |
| `utc` | str | Time received. Omitted if unknown |

`src`, `kind`, `lat`, and so on are omitted for packets the device could not parse (`tnc2` is always present). The app should parse `tnc2` itself to get further detail (comment, speed, and so on).

### 8.3 `tx` (event)

One each time the device builds a beacon (dry runs included).

```json
{"t": "tx", "tnc2": "Q1TEST-7>SU4PX0,WIDE1-1:`CIXl <0x1C>[/\"4!}Pico2 APRS", "dry": false, "utc": "2026-11-16T03:04:05Z"}
```

- `dry`: if true, nothing was transmitted (`tx_ready` was false)

### 8.4 `station`

| Key | Type | Description |
|---|---|---|
| `src` | str | Station (for objects and items, their name) |
| `last_s` | int | Seconds since last heard |
| `count` | int | Number of times heard (may include counts from before startup) |
| `lat`, `lon` | float | Last received position. Omitted if none |
| `lvl_db` | float | Receive level of the last packet. Omitted if none |
| `own` | bool | True for own station (own packet that came back via a digipeater). Omitted otherwise |

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
| `unknown_op` | Unknown `op` (`msg` holds the first 32 characters of the received `op`) | present |
| `invalid` | A settings value is invalid (`key` is the item, `msg` is the condition). Nothing was changed | present |
| `io` | Could not save to flash. Nothing was changed | present |
| `internal` | An internal device error (`msg` holds the detail). The device keeps running | present |

`msg` is an English string for display and its wording may change. The app should decide by `code` and `key`.

## 9. Settings items

### 9.1 Item list [Fixed]

| Key | Type | Range / format | Description |
|---|---|---|---|
| `callsign` | str / null | Uppercase letters and digits, 3–6 characters, at least one digit. No SSID | Own callsign. null means unset, and the device never transmits |
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

## 11. Open items

| # | Item | Section | When it will be decided |
|---|---|---|---|
| U1 | Name format and advertising interval | §2 | Implementation of the device's BLE layer (task 3) |
| U2 | Whether to add the Device Information Service | §3 | Task 3 |
| U3 | Whether Android Chrome shows the pairing dialog. If not, the procedure changes | §4 | Hardware verification in task 3 |
| U4 | Upper limit of an app → device write (20 bytes for now) | §5.2 | After MTU is measured in task 3 |
| U5 | Whether to put the firmware version in `hello` | §7.1 | Tasks 3–4 |
| U6 | How much reception is lost while `settings.set` saves | §7.2 | Task 7 (measurement) |
| U7 | Whether to silently drop pre-authentication commands or return an `err` | §4 | Task 3 |

When an item is decided, remove it from this table, write it into the body, and record it in §12.

## 12. Change log

| Version | Date | Change |
|---|---|---|
| 0.1 | 2026-11-16 | First version. Message handling (§6–§9) is fixed to match the implementation; the BLE layer (§2–§5.2) is provisional |
