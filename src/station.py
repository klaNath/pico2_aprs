"""APRS 局の本体: 受信(Core1)・ビーコン送信・GNSS・画面・microSD の記録を 1 つのループで回す。

main.py が main() を呼ぶ。開発中は `python tools/dev.py run src/main.py -t 60` で、フラッシュに
書かずに動かせる(タイムアウトで切れても、デバイス上では次の接続まで動き続ける)。
main() は例外で止まったら内容を LCD・USB・SD に出し、待ってからハードリセットする(電源投入だけで
使うため)。dev.py run/exec(PC の src をマウント中)で main() がリセットまで進むと、その後の接続で
デバイスが応答しなくなることがあった(USB の抜き差しで戻る)。異常時の経路の試験は、書き込み後に
`dev.py exec --no-mount` で行う。
起動から FAIL_WINDOW_MS 以内の失敗が続くと待ちを 10, 20, 40 ... 300 秒と延ばす。失敗の回数は
ウォッチドッグのスクラッチレジスタに置く(リセットでは消えず、電源を切ると消える。フラッシュに書かない)。
USB から Ctrl-C で止めたとき(dev.py の接続)は再起動しない。
    受信: Core1 の Receiver が復号したフレームを取り出し、局の表・画面・SD の記録に渡す
    送信: ビーコンスイッチ(GP21、High でオン)がオンで測位があれば、core.beacon の規則で
          Mic-E を送る。
          config.TX_ENABLED = False の間は送るはずだったパケットを SD に TX(dry) と記録するだけで、
          PTT と音声は出さない
    LED(GP7): 送信中は点灯、受信したら 0.1 秒点灯
"""

import gc
import sys
import time

import machine
from machine import SPI, Pin

import config
from aprs.packet import parse_frame
from core.beacon import Beacon, mice_frame
from core.stations import Stations
from hw.afsk_rx import Receiver
from hw.afsk_tx import AfskTx
from hw.m10 import M10
from hw.sdlog import SdLog
from hw.st7789 import ST7789
from tnc.ax25 import DecodeError, Frame
from ui.controller import Ui
from ui.screen import Screen
from ui.views import Status

LOOP_MS = 20
RX_RECENT_MS = 2000  # 画面の [RX] を出しておく時間
LED_RX_MS = 100
REPORT_MS = 10_000  # USB に状態を出す間隔
FAIL_WINDOW_MS = 60_000  # 起動からこの時間内の失敗は「続けて失敗」と数える
RESTART_S = 10
RESTART_MAX_S = 300
_SCRATCH = 0x400D8000 + 0x0C  # RP2350 WATCHDOG SCRATCH0(SCRATCH4-7 はブート ROM が使う)
_MAGIC = 0x5A000000


def own_call():
    return config.CALLSIGN if config.SSID == 0 else "%s-%d" % (config.CALLSIGN, config.SSID)


def tnc2(f):
    """Frame -> TNC2 の bytes 'SRC>DST,PATH:info'"""
    return f.header().encode() + b":" + bytes(f.info)


class Station:
    def __init__(self):
        self.call = own_call()
        self.status = Status()
        self.stations = Stations(own_call=self.call)
        spi = SPI(config.LCD_SPI, baudrate=config.LCD_BAUD, sck=Pin(config.PIN_LCD_SCK),
                  mosi=Pin(config.PIN_LCD_MOSI))
        lcd = ST7789(spi, config.PIN_LCD_DC, config.PIN_LCD_CS, config.PIN_LCD_BL,
                     config.LCD_PANEL)
        self.ui = Ui(Screen(lcd), self.stations, self.status, config.BUTTONS)
        lcd.backlight(0.8)
        self.sd = SdLog(config.SD_SPI, config.PIN_SD_SCK, config.PIN_SD_MOSI, config.PIN_SD_MISO,
                        config.PIN_SD_CS, config.PIN_SD_DET, header=self.call, log=print)
        self.gnss = M10(config.GNSS_UART, config.PIN_GNSS_TX, config.PIN_GNSS_RX,
                        resetn=config.PIN_GNSS_RESETN, pps=config.PIN_GNSS_PPS,
                        baud=config.GNSS_BAUD, log=print)
        self.gnss.on_nmea = self.sd.nmea
        self.gnss_ok = False
        self.rx = Receiver(config.PIN_RX_ADC, config.FS)
        if config.TX_ENABLED:
            self.tx = AfskTx(config.PIN_AFSK_OUT, config.PIN_PTT, config.FS, config.PWM_TOP,
                             config.TX_AMPLITUDE, config.TX_DELAY_MS, config.TX_TAIL_MS,
                             config.PTT_ACTIVE_HIGH)
        else:
            self.tx = None
            # 送信しない側に固定(PTT の線を浮かせない)
            Pin(config.PIN_PTT, Pin.OUT, value=0 if config.PTT_ACTIVE_HIGH else 1)
        self.sw = Pin(config.PIN_BEACON_SW, Pin.IN, Pin.PULL_UP)
        self.led = Pin(config.PIN_STATUS_LED, Pin.OUT, value=0)
        self.beacon = Beacon(config.BEACON_MOVING_S, config.BEACON_STILL_S, config.BEACON_MOVE_M)
        self._rx_ms = None
        self._report_ms = time.ticks_ms()

    def utc(self):
        return time.time() if self.gnss.rtc.synced else None

    def start(self):
        try:
            for s in self.gnss.begin() or ():
                print("[gnss]", s)
            self.gnss_ok = True
        except (RuntimeError, OSError) as e:
            print("[gnss] not available:", e)
        gc.collect()
        self.rx.start()
        print("[station] %s started, TX %s, free %d" % (
            self.call, "enabled" if self.tx is not None else "disabled (dry run)", gc.mem_free()))

    def step(self, now):
        if self.gnss_ok:
            self.gnss.poll(self.status)
        self._receive(now)
        self._beacon(now)
        self.sd.poll(self.utc(), now)
        self.status.sd = self.sd.state
        if self._rx_ms is not None:
            age = time.ticks_diff(now, self._rx_ms)
            if age >= LED_RX_MS:
                self.led.value(0)
            if age >= RX_RECENT_MS:
                self.status.rx_recent = False
                self._rx_ms = None
        self.ui.poll(now)
        if time.ticks_diff(now, self._report_ms) >= REPORT_MS:
            self._report_ms = now
            self._report()

    # ---- 受信
    def _receive(self, now):
        while True:
            b = self.rx.get(now)
            if b is None:
                return
            try:
                f = Frame.decode(b)
            except DecodeError:
                continue
            text = tnc2(f)
            self.sd.packet(text, utc_s=self.utc())
            try:
                self.stations.update(parse_frame(f), now)
            except Exception as e:  # 解析できない情報フィールドでも記録と受信数は残す
                sys.print_exception(e)
            self.status.rx_count += 1
            self.status.rx_recent = True
            self._rx_ms = now
            self.led.value(1)
            print("[rx]", text)

    # ---- ビーコン
    def _beacon(self, now):
        st = self.status
        on = self.sw.value() == config.BEACON_SW_ON
        if on != st.beacon_on:
            st.beacon_on = on
            self.beacon.reset()  # 次にオンにしたらすぐ送る
        # 時刻も待つ: RMC より先に GLL/GSA を受けると、RTC を合わせる前に測位有効になることがある
        if not on or st.fix < 2 or st.lat is None or not self.gnss.rtc.synced:
            st.next_beacon_s = None
            return
        st.next_beacon_s = self.beacon.next_s(st.lat, st.lon, now)
        if st.next_beacon_s:
            return
        gps = self.gnss.gps
        f = mice_frame(self.call, config.TX_PATH, st.lat, st.lon, gps.speed, gps.course,
                       gps.altitude if st.fix == 3 else None, config.SYMBOL,
                       config.BEACON_COMMENT, config.BEACON_MESSAGE)
        self.beacon.sent(st.lat, st.lon, now)
        text = tnc2(f)
        self.sd.packet(text, tx=True, utc_s=self.utc(), dry=self.tx is None)
        if self.tx is not None:
            st.tx_active = True
            self.ui.draw_status_now()
            self.led.value(1)
            try:
                self.tx.send_blocking([bytes(f.encode())])
            finally:
                self.led.value(0)
                st.tx_active = False
        st.tx_count += 1
        print("[tx%s]" % ("" if self.tx is not None else " dry"), text)

    def _report(self):
        st = self.status
        print("[station] fix %dD sats %d/%d rx %d tx %d bcn %s next %s sd %s free %d lag %d" % (
            st.fix, st.sats, st.sats_view, st.rx_count, st.tx_count,
            "on" if st.beacon_on else "off", st.next_beacon_s, self.sd.state, gc.mem_free(),
            self.rx.lag_max))

    def close(self):
        self.rx.stop()
        if self.tx is not None:
            self.tx.close()
        self.rx.close()
        self.led.value(0)
        self.sd.close()


def run(duration_ms=None):
    """局を動かす。duration_ms を渡すとその時間で止まる(試験用)"""
    station = Station()
    try:
        station.start()
        t0 = time.ticks_ms()
        while duration_ms is None or time.ticks_diff(time.ticks_ms(), t0) < duration_ms:
            station.step(time.ticks_ms())
            time.sleep_ms(LOOP_MS)
    except Exception as e:
        station.sd.logger.note("ERROR %s" % _err_text(e))  # close() で SD に書かれる
        raise
    finally:
        station.close()
        print("[station] stopped")


def _err_text(e):
    return "%s: %s" % (type(e).__name__, e)


def restart_wait_s(fails):
    """続けて fails 回失敗したときの再起動までの秒(10, 20, 40, ... 最大 300)"""
    return min(RESTART_S << min(max(fails - 1, 0), 8), RESTART_MAX_S)


def _fails():
    v = machine.mem32[_SCRATCH]
    return v & 0xFFFF if v & 0xFFFF0000 == _MAGIC else 0


def _set_fails(n):
    machine.mem32[_SCRATCH] = _MAGIC | n if n else 0


def _show_fatal(e, wait_s):
    """例外の内容を LCD に出す(LCD を作り直す。失敗しても無視)"""
    try:
        from ui.views import BLACK, CH, RED, ROW, WHITE

        gc.collect()
        spi = SPI(config.LCD_SPI, baudrate=config.LCD_BAUD, sck=Pin(config.PIN_LCD_SCK),
                  mosi=Pin(config.PIN_LCD_MOSI))
        lcd = ST7789(spi, config.PIN_LCD_DC, config.PIN_LCD_CS, config.PIN_LCD_BL,
                     config.LCD_PANEL)
        cols = lcd.width // CH
        text = _err_text(e)
        lines = ["STATION ERROR", ""] + [text[i:i + cols] for i in range(0, len(text), cols)][:8]
        lines += ["", "restart in %d s" % wait_s]

        def draw(fb, dy):
            for i, s in enumerate(lines):
                fb.text(s, 0, 1 + i * ROW + dy, RED if i == 0 else WHITE)

        # 作業領域は小さく(メモリ不足で止まった直後でも描けるように)
        Screen(lcd, 4096).region(0, 0, lcd.width, lcd.height, draw, BLACK)
        lcd.backlight(0.8)
    except Exception as e2:
        sys.print_exception(e2)


def main():
    """電源投入時の入口(main.py から呼ぶ)。例外で止まったら表示して再起動する"""
    t0 = time.ticks_ms()
    try:
        run()
    except KeyboardInterrupt:
        _set_fails(0)
        print("[station] stopped by Ctrl-C (no restart)")
        return
    except Exception as e:
        sys.print_exception(e)
        quick = time.ticks_diff(time.ticks_ms(), t0) < FAIL_WINDOW_MS
        fails = _fails() + 1 if quick else 1
        _set_fails(fails)
        wait = restart_wait_s(fails)
        print("[station] error (%d in a row), restart in %d s" % (fails, wait))
        _show_fatal(e, wait)
        try:
            time.sleep(wait)
        except KeyboardInterrupt:
            _set_fails(0)
            print("[station] restart cancelled by Ctrl-C")
            return
        machine.reset()
