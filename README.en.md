# pico2_aprs

[日本語](README.md)

Firmware for an APRS station that runs on MicroPython on a Raspberry Pi Pico 2 W. It connects to a 144 MHz FM radio as an external TNC and, together with a GNSS module and an LCD, works as a stand-alone tracker. When powered on, it does the following by itself:

- Receives APRS packets (AX.25, 1200 bps AFSK) and shows the stations heard, with distance and bearing
- Gets its own position from GNSS (u-blox MAX-M10M) and sends Mic-E position beacons (FX.25 supported)
- Shows everything on an ST7789 LCD (station list, details of the latest station, own status, map, satellite signal strength, receive level)
- Logs received packets and NMEA to microSD, and keeps the station list and messages there
- Connects to a smartphone over BLE to change settings, show received packets, send a beacon, and send and receive APRS messages

```
 Radio (RX audio) ───── GP26 ADC ──► Pico 2 W ──► LCD (ST7789)
 GNSS (MAX-M10M) ───── UART0 ────►   │  ├──► microSD
 Beacon switch ──────── GP21 ─────►   │  ├──► Status LED
 Smartphone ◄──────────── BLE ───────►   │
                                      └──► TX audio (PWM) / PTT ──► Radio
```

The frequency and the mode are set on the radio. The device handles only the audio and PTT.

The development documentation ([docs/development.md](docs/development.md)) and most design notes are written in Japanese. The BLE specification ([docs/ble-protocol.md](docs/ble-protocol.md)) is in English.

---

## 1. What you need

### Hardware

| Part | Details |
|------|---------|
| Board | Raspberry Pi Pico 2 W (RP2350). Without BLE, a Pico 2 works with the same wiring |
| Radio | 144 MHz FM. You need an interface that feeds the receive audio to the Pico and takes the transmit audio and PTT from it. A circuit for the FT-818ND (DATA jack) and the FT3D / FT5D (MIC/SP jack) is in [hw/ft818_if/](hw/ft818_if/) (`ft818_if.pdf`) |
| GNSS | u-blox MAX-M10M (UART0, 115200 bps) |
| Display | ST7789 LCD. The default is a 2.4-inch 240x320 panel (used in landscape, 320x240). The Pimoroni Pico Display Pack (240x135, with buttons) is also supported |
| Storage | microSD card (in a slot with a card-detect switch) |
| Switch | Beacon on/off (on when GP21 is pulled to GND) |
| PC connection | A USB cable **that carries data** |

The netlist of the prototype board (rev1, still in development) is [hw/Pico_MicroSD_M10_Board_rev1.net](hw/Pico_MicroSD_M10_Board_rev1.net).

### Pin assignments

The source of truth is `PIN_*` in [src/config.py](src/config.py). **The GPIOs are not 5 V tolerant** (3.3 V logic).

| GPIO | Use |
|------|-----|
| GP0 / GP1 | UART0 to the GNSS (TX / RX) |
| GP2 | PTT (output; high = transmit by default, `PTT_ACTIVE_HIGH`) |
| GP3 | Transmit audio (PWM) |
| GP6 | GNSS RESET_N (normally not driven) |
| GP7 | Status LED (on while transmitting, short blink on receive) |
| GP9 | microSD card detect |
| GP10–12, GP15 | microSD (SPI1: SCK / MOSI / MISO / CS) |
| GP16–20 | LCD (SPI0: DC / CS / SCK / MOSI, backlight) |
| GP21 | Beacon switch (internal pull-up; GND = on, open or broken wire = off) |
| GP22 | GNSS PPS (not driven) |
| GP26 | Receive audio (ADC0) |

GP4 / GP5 / GP8 / GP27 are not used at present. GP23 / 24 / 25 / 29, used by the Pico 2 W's wireless chip, are not used either.

### Firmware

The station does not run on stock MicroPython. It needs a custom build (board definition `RPI_PICO2_W_ROMFS2`, MicroPython v1.29.0) that:

- Includes `sdcard` and `datetime` from micropython-lib (with the stock .uf2, microSD does not work)
- Reserves a 1 MB ROMFS partition in flash (with the stock .uf2, `release` cannot write the station)
- Patches BTstack to add passkey pairing and bond storage (with the stock .uf2, pairing fails and BLE cannot be used)

The build steps and the patch are described in [hw/micropython/README.md](hw/micropython/README.md) (in Japanese; the build runs on Ubuntu under WSL). Write the resulting `firmware.uf2` by holding the BOOTSEL button while plugging in USB and copying the file to the drive that appears. No prebuilt .uf2 is distributed.

### PC

- Python 3.9 or later
- On Linux, access to the serial port (`sudo usermod -aG dialout $USER`, then log in again)

---

## 2. Setup

```bash
git clone --recurse-submodules <this repository>
cd pico2_aprs
python3 tools/bootstrap.py --board rpi_pico2_w   # on Windows: py tools\bootstrap.py --board rpi_pico2_w
```

`bootstrap.py` creates `.venv` and installs the tools (`mpremote`, `mpy-cross` and others). Then activate `.venv` (in VSCode, "Python: Select Interpreter").

### Station settings

Put your callsign and other settings in `src/config_local.py`, which git does not track.

```bash
cp tools/config_local.example.py src/config_local.py
```

```python
# src/config_local.py (the values here are fictional examples)
CALLSIGN = "Q1TEST"   # fictional example; replace with your callsign, without the SSID
SSID = 7
TX_ENABLED = False    # allow transmitting; keep False until the radio connection is checked
```

Without a callsign, receiving, the display and logging still work (the screen shows `NOCALL` and the station never transmits). The same items can also be changed over BLE (see [BLE](#connecting-a-smartphone-over-ble)).

Settings override each other in this order:

`src/config.py` (defaults) < `src/config_local.py` < `/settings.json` on the device (values changed over BLE; kept across power cycles)

### Checking the connection

```bash
python tools/dev.py ports         # is the Pico found?
python tools/dev.py info          # MicroPython version and free memory
python tools/dev.py romfs-query   # is there a ROMFS partition (i.e. the custom firmware)?
```

---

## 3. Running the station

### Try it first (nothing is written to flash)

```bash
python tools/dev.py run -t 60     # mount the PC's src/ on the Pico and run for 60 s
```

It stops when the time is up (exit code 124 is normal). Lines like these appear on USB:

```
[station] Q1TEST-7 started, TNC mode, RX ADC GP26, TX TX not enabled, free 244464
[ble] advertising as PicoAPRS-XXXX              BLE advertising started
[sd] logging to /sd/log/0001_*.txt              SD logging started
[rx] SRC>DST,PATH:...                           a received packet
[tx dry] 715 ms SRC>DST,PATH:...                a packet it would have sent (not sent: TX_ENABLED is False)
[station] fix 3D sats 12/30 rx 4 tx 1 bcn on next 540 sd log 0001 ble adv free 157376 lag 3     every 10 s
[station] rx level rms -38 dBFS, peak -8 dBFS (ok), ch clear (busy >= -30)
```

`TX ...` on the `started` line is the reason the station cannot transmit (`callsign not set`, `TX not enabled`). When it can, it shows `TX enabled`. On the 10-second line, `bcn` is the beacon switch (`next None` while it is `off`), `sd` the SD state (`no card` / `wait` / `log <number>`) and `ble` the BLE state (`adv` advertising / `conn` connected / `auth` authenticated / `hold` advertising paused after repeated pairing failures / `stop` the 10-minute advertising window is over).

### Make it start at power-on

```bash
python tools/dev.py release
```

This runs lint and the tests, writes the station to ROMFS (a read-only partition in flash), restarts the board and checks that `[station] ... started` appears. `src/config_local.py` goes into the image too, so **create it before writing**. From then on the station starts when powered on.

Connecting with `dev.py` stops the running station. When you are done, restart it with `python tools/dev.py monitor --reset -s 20`.

---

## 4. Using the station

### When it transmits

The station keys PTT only when all of these hold. If any is missing, it does a "dry run": beacon timing, the display and the SD log (marked `TX(dry)`) behave the same, but PTT and the transmit audio stay off.

1. A callsign in a valid format is set (`CALLSIGN`)
2. `TX_ENABLED = True`
3. The beacon switch (GP21) is on

A single transmission is cut off at 5 seconds at most. The receiver is muted while transmitting (so the station does not decode its own audio).

> **Test transmissions into a dummy load.** Transmitting on the air is your own decision, within your licence and operating conditions.

### Beacons

The station sends its own position beacon (Mic-E) once it has at least a 2D fix and the GNSS time.

| Setting | Behaviour |
|---------|-----------|
| `BEACON_MODE = "auto"` (default) | When the switch is turned on and a fix is available, the first beacon goes out immediately. After that, every `BEACON_MOVING_S` (90 s) if it has moved `BEACON_MOVE_M` (10 m) or more since the last beacon, otherwise every `BEACON_STILL_S` (600 s) |
| `BEACON_MODE = "manual"` | No automatic beacons; it sends only when the smartphone asks (`beacon.send`) |

- With the switch off, nothing is sent. Turning it on again sends a beacon immediately
- Automatic beacons and messages wait for a clear channel (judged from the receive level, i.e. whether the radio's squelch is open; the threshold `CH_BUSY_DB` is still provisional)
- The path, symbol, comment and Mic-E message are set by `TX_PATH`, `SYMBOL`, `BEACON_COMMENT` and `BEACON_MESSAGE`
- `FX25_TX = True` sends with FX.25 (forward error correction). Receivers without FX.25 still decode it as plain AX.25

### Setting the receive level

Adjust the radio's volume with the receive-level meter on the screen (and the `rx level` line on USB). Aim for peaks of -12 to -3 dBFS and avoid clipping (hitting the ends of the ADC range).

### Screen

| Layout | Contents |
|--------|----------|
| 320x240 (default, `LCD_PANEL = "st7789_320x240"`) | Top: station list and details of the latest station (callsign, type, distance and bearing, position, comment). Bottom: own status (fix, satellites, time, position, next beacon, RX/TX counts), satellite signal strength and a small map (switches between the station map and the satellite sky plot; stations with your own callsign, any SSID, are left off the map) |
| 240x135 (`LCD_PANEL = "pico_display"`) | List on the left, details top right, own status bottom right. The X button switches to a full-screen map |

- The top right shows the fix indicator and a BLE status icon. The own-status area shows the SD state (`SD:LOG` / `SD:WAIT` / `SD:ERR` / `SD:---`), `[TX]` while transmitting and `[RX]` just after receiving
- During BLE pairing, the 6-digit passkey fills the screen
- A message addressed to this station is shown full screen for 30 seconds

Buttons exist only on the 240x135 layout (put the GPIO numbers in `config.BUTTONS` in the order `(A, B, X, Y)`). A / B move the selection up / down the list, X switches between the map and the main screen, and Y clears the selection and goes back to showing the latest station.

### microSD

After a card is inserted, logging starts once the GNSS time is known (it waits up to 20 seconds).

| File | Contents |
|------|----------|
| `/sd/log/NNNN_aprs.txt` | Received packets (TNC2 format). Own transmissions are prefixed `TX `, dry runs `TX(dry) `. A new number is used on each start |
| `/sd/log/NNNN_nmea.txt` | NMEA RMC and GGA sentences |
| `/sd/stations.txt` | Stations heard; read back into the list on the next start |
| `/sd/messages.txt` | APRS messages; read back on the next start |

Logs are written in batches every 10 seconds (up to 10 seconds are lost when the power is cut). Removing the card closes the files; reinserting it starts new files with a new number.

### Connecting a smartphone over BLE

On a Pico 2 W, a smartphone Web app (Chrome on Android, developed in a separate repository) or a PC can connect over BLE (advertised name `PicoAPRS-XXXX`).

- **Pairing**: on the first connection, a 6-digit passkey appears on the LCD; enter it on the phone. Later connections need no passkey. Only a paired connection can operate the station
- **What it can do**: show the status, show received packets, get the station list, change settings (callsign, SSID, symbol, path, comment, beacon intervals and mode, `tx_enabled`, FX.25), send a beacon now, and send and receive APRS messages (it resends until the other station acknowledges, and acknowledges messages to itself automatically)
- Transmitting needs the beacon switch on, even when BLE asks for it. The phone cannot bypass the switch
- Only one device can connect at a time, and the station stops advertising while connected
- The station advertises **only for the first 10 minutes after power-on** (`config.BLE_ADV_WINDOW_S`). After that the BLE icon on the LCD turns grey and the phone cannot find it. A connection open at that moment keeps working, but once it drops you need to power the station off and on to connect again
- The specification is [docs/ble-protocol.md](docs/ble-protocol.md) (for Web app developers). Some parts are provisional
- From a PC, try `python tools/ble_cli.py pair` (first time; type the passkey on stdin), `get`, `stations`, `listen -s 30`, `send` and so on. On Windows there is a known issue where notifications stop arriving partway ([docs/ble-protocol.md §10.1](docs/ble-protocol.md#101-known-issue-windows-stops-receiving-notifications)). Android (nRF Connect) is the reliable way to check BLE

### When something goes wrong

- If the firmware hangs, the watchdog restarts the board
- If the station stops with an exception, it shows the error on the LCD, USB and SD, waits, then restarts. On repeated failures the wait grows to 10, 20, 40 … seconds (at most 300)
- Right after an abnormal restart, the first beacon is held for 90 seconds (so a restart loop does not keep transmitting)
- `boot.py` turns PTT off right after start-up

---

## 5. Troubleshooting

- **Cannot connect**: check that no other tool (MicroPico, Thonny, a serial monitor) holds the port. `dev.py ports` shows what is detected
- **Not responding or frozen**: unplug and replug USB. If it keeps restarting and does not respond, try `dev.py rm-main` (stops the automatic start)
- **Last resort**: hold BOOTSEL while plugging in USB and write the firmware again
- **To pair again**: remove the device from the phone's Bluetooth settings, then connect again. How to erase the keys on the device is in [docs/development.md](docs/development.md)

---

## 6. For developers

The development environment, workflow, tests, debugging, directory layout and the list of design notes are in [docs/development.md](docs/development.md) (in Japanese).

## 7. Licence

Because it includes micro-aprs, the project as a whole is licensed under **GPL-3.0** ([LICENSE](LICENSE)).

The following code is generated from submodules:

| Generated into | Source | Licence |
|----------------|--------|---------|
| `src/tnc/` | branch `pico2-subset` of [klaNath/micro-aprs](https://github.com/klaNath/micro-aprs) (a fork of [stephanelsmith/micro-aprs](https://github.com/stephanelsmith/micro-aprs)) | GPL-3.0 |
| `src/maxm10/` | branch `pico2-aprs` of [klaNath/micropython-maxm10](https://github.com/klaNath/micropython-maxm10) | MIT |
| `src/micropyGPS.py` | branch `pico2-aprs` of [klaNath/micropyGPS](https://github.com/klaNath/micropyGPS) | MIT |
