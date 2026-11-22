"""APRS 局の本体: 受信(Core1)・ビーコン送信・GNSS・画面・microSD の記録を 1 つのループで回す。

動作モード(config.MODE):
    "tnc"     外付け TNC(既定)。受信は GP26 の音声(ADC)、送信は GP3 の音声(PWM)と PTT(GP2)。
              周波数と電波型式は無線機側で決まるので、送信ゲートは周波数を見ない(hw.txctl.tnc_gate)
    "si5351"  Si5351B のトランシーバー(未完成)。run() が transceiver.run を呼び、未完成と表示して
              何も動かさない。以下の説明は "tnc" の局のこと

main.py が main() を呼ぶ。開発中は `python tools/dev.py run src/main.py -t 60` で、フラッシュに
書かずに動かせる(タイムアウトになると dev.py が Ctrl-C を送って止める)。
main() は例外で止まったら内容を LCD・USB・SD に出し、待ってからハードリセットする(電源投入だけで
使うため)。リセットするとマウントが切れて dev.py が出力を追えないので、異常時の経路の試験は、
書き込み後に `dev.py exec --no-mount` で行う。
Watchdog (hw.watchdog): 16 s during startup, 3 s once in the loop. Fed at each loop step and
before each transmission. Not enabled under dev.py run/exec (src mounted). If stopped with
Ctrl-C while enabled, a timer keeps feeding it.
起動から FAIL_WINDOW_MS 以内の失敗が続くと待ちを 10, 20, 40 ... 300 秒と延ばす。失敗の回数は
ウォッチドッグのスクラッチレジスタに置く(リセットでは消えず、電源を切ると消える。フラッシュに書かない)。
USB から Ctrl-C で止めたとき(dev.py の接続)は再起動しない。
    受信: Core1 の Receiver が復号したフレームを取り出し、局の表・画面・SD の記録に渡す
    局の表: SD の stations.txt に 1 分ごと(変わったとき)と終了時に保存し、起動後に GNSS の時刻が
          分かったら読み出して表に加える(core.stationfile)
    TX: when the beacon switch (GP21; on when pulled to GND, off when open or broken) is on
          and there is a fix, send Mic-E following the rules in core.beacon
          (config.BEACON_MODE "auto"). In "manual" mode, send only on BLE beacon.send
          (send_now(); the switch must also be on). beacon.send also works in "auto" mode.
          ゲート(core.txgate)が拒否する間(コールサインが未設定、config.TX_ENABLED =
          False)は送るはずだったパケットを SD に TX(dry) と記録するだけで、PTT と音声は出さない
    APRS メッセージ(core.messages): スマホの msg.send で積んだメッセージを送り、ack が来るまで
          再送する。自局宛のメッセージには ack を返し、LCD に出して SD とスマホに残す。
          送るのはスイッチがオンでゲートが許可するときだけ(dry run にしない)
    チャネル(core.level.Channel): 受信音量でスケルチが開いているか(使用中)を見て、自動のビーコン・
          メッセージ・ack は空くまで待つ。BLE の beacon.send は待たない(docs/ble-protocol.md §7.9)
    LED(GP7): 送信中は点灯、受信したら 0.1 秒点灯
    受信音量: 地図の左の縦棒(今の実効値と 3 秒保持のピーク)と、詳細欄の局ごとの LVL
送信波形のバッファ(config.TX_BUFFER_MS 分)は、ヒープが断片化する前に確保する
(このモジュールの読み込みの最初)。
dry run でも波形は作る(メモリの使い方を実際の送信と同じにする)。
"""

import gc
from array import array

import config

# 大きなバッファは、ほかのモジュールを読み込む前に確保する。dev.py run/exec(src をマウント)では
# 読み込みのたびにソースをコンパイルするのでヒープが細切れになり、後からでは数十 KB の連続領域が
# 取れない(実測: 読み込み後の最大の連続領域は 48 KB)。ROMFS からの起動では問題ないが、同じ作りにする
gc.collect()
_TX_RAW = bytearray(2 * (config.FS * config.TX_BUFFER_MS // 1000))  # 送信波形(1.5 秒で 57.6 KB)
# 受信のリング: hw.afsk_rx.RING_ALLOC_BYTES(16 KB)と同じ大きさ
_RX_RING = array("H", bytes(2 * 8192))
_UI_SCRATCH = bytearray(24 * 1024)  # 画面の帯描画の作業領域
# Core1 のスタック(_thread、4 KB)の場所: 受信を始める直前に手放す。マウントでは読み込みの後の
# 最大の連続領域が約 5 KB しか残らず、GNSS の初期化のあとでスタックが取れなかった(MemoryError、
# メッセージの機能を足したとき。2026-11-20)
_CORE1_RESERVE: "bytearray | None" = bytearray(8 * 1024)
# Station() の作成(受信のデフレーマ、画面、SD など)の場所: Station.__init__ の最初で手放す。
# マウントでは読み込みの後の最大の連続領域が 4〜8 KB しかなく、モジュールが少し大きくなるだけで
# Receiver の作成が MemoryError になった(2026-11-22)
_INIT_RESERVE: "bytearray | None" = bytearray(16 * 1024)

import sys
import time

try:
    from typing import Any  # noqa: F401  (型注釈用。MicroPython には無い)
except ImportError:
    pass

import machine
from machine import SPI, Pin

from aprs.message import MessageError, check_text, check_to
from aprs.packet import parse_frame
from core.beacon import Beacon, mice_frame
from core.level import Channel, Level, db_peak
from core.messages import Messages, QueueFull
from core.settings import Settings
from core.stations import Stations
from core.txgate import display_call, valid_callsign
from hw import restart, txctl, watchdog
from hw.afsk_rx import Receiver
from hw.afsk_tx import AfskTx
from hw.m10 import M10
from hw.sdlog import SdLog
from hw.st7789 import ST7789
from tnc.ax25 import Address, DecodeError, Frame
from ui.controller import Ui
from ui.screen import Screen
from ui.views import Status

LOOP_MS = 20
RX_RECENT_MS = 2000  # 画面の [RX] を出しておく時間
LED_RX_MS = 100
REPORT_MS = 10_000  # USB に状態を出す間隔(古い ticks_ms の片付け upkeep.sweep も同じ間隔)
FAIL_WINDOW_MS = 60_000  # 起動からこの時間内の失敗は「続けて失敗」と数える
RESTART_S = 10  # 試験で短くできるよう、ここで持つ(hw.restart.wait_s に渡す)
_gate = None  # main() の最後の砦で PTT を切るためのゲート(Station が作る)


def tnc2(f):
    """Frame -> TNC2 の bytes 'SRC>DST,PATH:info'"""
    return f.header().encode() + b":" + bytes(f.info)


class Station:
    ble: "Any" = None  # hw.ble.Ble / None = 使わない(config.BLE_ENABLED = False、起動に失敗)
    remote: "Any" = None  # core.remote.Remote(BLE で接続されたスマホ)/ None = 使わない

    def __init__(self, wdt=None, settings=None):
        """settings: core.settings.Settings(読み込み済み)。変更されたら apply_settings() で
        反映する"""
        # 送信波形のバッファは import の時に確保済み(_TX_RAW)。
        # PTT は送信しない側、GP3 はデューティ 0 にしておく
        global _gate, _INIT_RESERVE
        _INIT_RESERVE = None
        gc.collect()
        self.wdt = wdt if wdt is not None else watchdog.Watchdog(enabled=False)
        self.gate = _gate = txctl.tnc_gate()
        self.tx = AfskTx(config.PIN_AFSK_OUT, self.gate, config.FS, config.PWM_TOP,
                         config.TX_AMPLITUDE, config.TX_DELAY_MS, config.TX_TAIL_MS, raw=_TX_RAW,
                         fx25=config.FX25_TX)
        self._refused = None  # 最後に記録した拒否の理由(同じ理由を毎回書かない)
        self.rx = Receiver(config.PIN_RX_ADC, config.FS, ring_buf=_RX_RING)
        self.call = display_call(config.CALLSIGN, config.SSID)
        self.status = Status()
        self.status.callsign = self.call
        self.stations = Stations(own_call=self.call)
        spi = SPI(config.LCD_SPI, baudrate=config.LCD_BAUD, sck=Pin(config.PIN_LCD_SCK),
                  mosi=Pin(config.PIN_LCD_MOSI))
        lcd = ST7789(spi, config.PIN_LCD_DC, config.PIN_LCD_CS, config.PIN_LCD_BL,
                     config.LCD_PANEL)
        # 受信音量(地図の左の縦棒と、詳細欄の局ごとの LVL)
        self.level = Level()
        self.status.level = self.level
        self.channel = Channel(config.CH_BUSY_DB, config.CH_IDLE_MS, config.CH_SLOT_MS,
                               config.CH_BUSY_MAX_S * 1000)
        self._stuck_noted = False  # スケルチが開きっぱなしの記録(使用中が続くあいだ 1 回)
        # メッセージの番号は SD の続きから(core.msgfile)。SD が無ければ前回と重なりにくい乱数から
        import random

        self.msgs = Messages(self.call, retry_s=config.MSG_RETRY_S,
                             first_no=random.getrandbits(16) % 900 + 100,
                             on_change=self._msg_changed)
        self.ui = Ui(Screen(lcd, scratch=_UI_SCRATCH), self.stations, self.status, config.BUTTONS,
                     meter=self.level is not None)
        lcd.backlight(0.8)
        self.sd = SdLog(config.SD_SPI, config.PIN_SD_SCK, config.PIN_SD_MOSI, config.PIN_SD_MISO,
                        config.PIN_SD_CS, config.PIN_SD_DET, header=self.call, log=print,
                        stations=self.stations, messages=self.msgs)
        self.gnss = M10(config.GNSS_UART, config.PIN_GNSS_TX, config.PIN_GNSS_RX,
                        resetn=config.PIN_GNSS_RESETN, pps=config.PIN_GNSS_PPS,
                        baud=config.GNSS_BAUD, log=print)
        self.gnss.on_nmea = self.sd.nmea
        self.gnss_ok = False
        self.sw = Pin(config.PIN_BEACON_SW, Pin.IN, Pin.PULL_UP)
        self.led = Pin(config.PIN_STATUS_LED, Pin.OUT, value=0)
        self.beacon = Beacon(config.BEACON_MOVING_S, config.BEACON_STILL_S, config.BEACON_MOVE_M)
        # 異常で再起動した直後(失敗の記録、またはウォッチドッグのリセット): 最初のビーコンを待つ
        if restart.fails() or watchdog.timed_out():
            self.beacon.hold(time.ticks_ms(), config.BEACON_RESTART_HOLD_S)
        self._rx_ms = None
        self._report_ms = time.ticks_ms()
        self.settings = settings
        if settings is not None:
            settings.listeners.append(self.apply_settings)
        self._tx_ms = None  # 最後にビーコンを作った ticks_ms(dry run を含む)
        self._air_ms = None  # 最後に電波を出し終えた ticks_ms(ビーコン・メッセージ・ack)
        self._fatal = None  # send_now() の送信中の例外(step() が投げ直す)
        self._ble_retry = None  # upkeep.BleRetry(BLE がエラーで止まってから作る)
        self.status.beacon_mode = config.BEACON_MODE

    def start_ble(self):
        """BLE を始める(config.BLE_ENABLED のとき)。失敗しても局は動かす(スマホから操作できない)"""
        if not config.BLE_ENABLED or self.settings is None:
            return
        try:
            from core.remote import Remote
            from hw.ble import Ble

            self.remote = Remote(self.settings, None, self.remote_status, self.remote_stations,
                                 log=print, send_fn=self.send_now, msgs=self)
            self.ble = Ble(self.remote, on_passkey=self._on_passkey, log=print)
            self.ble.start()
        except Exception as e:
            sys.print_exception(e)
            print("[ble] not available")
            self._stop_ble()

    def _stop_ble(self):
        ble, self.ble, self.remote = self.ble, None, None
        if ble is None:
            return
        self.status.ble = None
        self.status.passkey = None
        try:
            ble.stop()
        except Exception as e:  # 止めるときの失敗は無視(局の停止を妨げない)
            sys.print_exception(e)

    def _on_passkey(self, passkey):
        self.status.passkey = passkey  # 画面(ui)が全画面で出す。None で通常の画面に戻る

    def apply_settings(self, changed=None):
        """config の値を各部に反映する(設定の変更のあと。step() の中から呼ばれ、送信中には来ない)。
        ビーコンの PATH、シンボル、コメント、メッセージは _beacon() が毎回 config を読む"""
        self.gate.callsign = config.CALLSIGN
        self.gate.allowed = config.TX_ENABLED
        self.tx.set_fx25(config.FX25_TX)
        b = self.beacon
        b.moving_s = config.BEACON_MOVING_S
        b.still_s = config.BEACON_STILL_S
        b.move_m = config.BEACON_MOVE_M
        self.call = display_call(config.CALLSIGN, config.SSID)
        self.status.callsign = self.call
        self.stations.own_call = self.call
        self.msgs.set_call(self.call)
        self.status.beacon_mode = config.BEACON_MODE  # 手動なら次の _beacon() から自動では送らない
        self._refused = None  # 送信できない理由が変わっていれば、次の送信で記録し直す
        print("[settings] applied %s, TX %s" % (
            ", ".join(changed) if changed else "all", self.gate.refuse_reason() or "enabled"))

    # ---- スマホ(core.remote)へ渡す情報
    def remote_status(self):
        """status の中身(docs/ble-control.md §5.4)。None の項目は remote が省く"""
        st = self.status
        pos = st.fix >= 2 and st.lat is not None
        why = self.gate.refuse_reason()
        return {
            "utc_s": self.utc(),
            "fix": st.fix,
            "sats": st.sats,
            "lat": st.lat if pos else None,
            "lon": st.lon if pos else None,
            "sw": st.beacon_on,
            "beacon_mode": config.BEACON_MODE,
            "tx_enabled": bool(config.TX_ENABLED),
            # スイッチがオンで、ゲートが許可する(次のビーコンで実際に電波を出す)
            "tx_ready": st.beacon_on and why is None,
            "tx_refuse": why,
            # 未設定のときは省く(LCD とログは NOCALL。docs/ble-protocol.md §8.1)
            "call": self.call if valid_callsign(self.gate.callsign) else None,
            "last_tx_s": (None if self._tx_ms is None
                          else time.ticks_diff(time.ticks_ms(), self._tx_ms) // 1000),
            "next_beacon_s": st.next_beacon_s,
            "rx_count": st.rx_count,
            "tx_count": st.tx_count,
            "lvl_db": self.level.rms_db if self.level is not None else None,
            "sd": self.sd.state,
            "msg_unread": self.msgs.unread,
            "msg_pending": self.msgs.pending(),
            "ch_busy": self.channel.busy,
        }

    def remote_stations(self):
        """局の表の要約を新しい順に 1 局ずつ(remote が送れた分だけ取り出す)"""
        now = time.ticks_ms()
        for s in list(self.stations.order):  # 送る途中で受信して表が変わってもよいように写す
            yield {"src": s.call, "last_s": s.age_s(now), "count": s.count, "lat": s.lat,
                   "lon": s.lon, "lvl_db": s.level_db, "own": s.own or None,
                   "obj": s.obj or None}

    def utc(self):
        return time.time() if self.gnss.rtc.synced else None

    def start(self):
        try:
            for s in self.gnss.begin() or ():
                print("[gnss]", s)
            self.gnss_ok = True
        except (RuntimeError, OSError) as e:
            print("[gnss] not available:", e)
        global _CORE1_RESERVE
        _CORE1_RESERVE = None
        gc.collect()
        self.rx.start()
        print("[station] %s started, TNC mode, RX ADC GP%d, TX %s, free %d" % (
            self.call, config.PIN_RX_ADC, self.gate.refuse_reason() or "enabled",
            gc.mem_free()))

    def step(self, now):
        self.wdt.feed()
        if self.gnss_ok:
            self.gnss.poll(self.status)
        if self.level is not None:
            ms, pk, clip = self.rx.level()
            self.level.update(ms, pk, clip, now)
            self.channel.update(self.level.rms_db, now)
        self._receive(now)
        self._beacon(now)
        self._messages(now)
        self.status.msg_unread = self.msgs.unread
        self.status.msg_pending = self.msgs.pending()
        if self.ble is not None:
            try:
                self.ble.poll(now)
            except Exception as e:  # BLE の不具合で局を止めない: BLE を止めて、あとで起動し直す
                sys.print_exception(e)
                import upkeep

                upkeep.ble_failed(self, now)
        elif self._ble_retry is not None:
            import upkeep

            if upkeep.restart_due(self, now):
                upkeep.restart_ble(self)
        if self._fatal is not None:  # beacon.send の送信中の例外(自動の送出と同じく局を止める)
            e, self._fatal = self._fatal, None
            raise e
        b = self._ble_state()
        self.status.ble = None if b == "off" else b  # 右上のアイコン(ui)
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
            self._sweep(now)
            self._report()

    def _sweep(self, now):
        import upkeep  # 起動の後に読む(マウント中の起動はヒープに余裕がない。upkeep の説明)

        upkeep.sweep(self, now)

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
            self.channel.heard(now)  # 受信の直後は送らない(デジピータの中継が続く)
            text = tnc2(f)
            self.sd.packet(text, utc_s=self.utc())
            lvl = None
            clip = False
            if self.level is not None:  # このパケットの音量: 直近 1 秒のピーク
                pk, clip = self.rx.recent_peak()
                lvl = db_peak(pk)
                self.level.packet(lvl, clip, now)  # 縦棒のピークをこのパケットにする
            p = None
            try:
                p = parse_frame(f)
                self.stations.update(p, now, lvl, clip)
                self.msgs.on_packet(p, now, self.utc())
            except Exception as e:  # 解析できない情報フィールドでも記録と受信数は残す
                sys.print_exception(e)
            if self.remote is not None:
                d = {"tnc2": text, "lvl_db": lvl, "clip": clip or None, "utc_s": self.utc()}
                if p is not None:
                    d["src"], d["kind"], d["lat"], d["lon"] = p.src, p.kind, p.lat, p.lon
                    if p.kind in ("object", "item"):
                        d["obj"] = p.name  # src は送信局のまま(§8.2)
                    if p.table is not None and p.code is not None:
                        d["sym"] = p.table + p.code
                self.remote.rx(d)
            self.status.rx_count += 1
            self.status.rx_recent = True
            self._rx_ms = now
            self.led.value(1)
            print("[rx]", text)

    # ---- ビーコン
    def _switch_on(self):
        return self.sw.value() == config.BEACON_SW_ON

    def _position_ok(self):
        # 時刻も待つ: RMC より先に GLL/GSA を受けると、RTC を合わせる前に測位有効になることがある
        st = self.status
        return st.fix >= 2 and st.lat is not None and self.gnss.rtc.synced

    def _beacon(self, now):
        """自動の送出(config.BEACON_MODE が "auto")。スイッチの状態はどちらのモードでも読む"""
        st = self.status
        on = self._switch_on()
        if on != st.beacon_on:
            st.beacon_on = on
            self.beacon.reset()  # 次にオンにしたらすぐ送る
        if not on or config.BEACON_MODE == "manual" or not self._position_ok():
            st.next_beacon_s = None
            return
        st.next_beacon_s = self.beacon.next_s(st.lat, st.lon, now, self.gnss.gps.speed)
        if st.next_beacon_s:
            return
        # 電波を出すときは、チャネルが空くまで待つ(next_beacon_s は 0 のまま)。dry run は待たない
        if self.gate.refuse_reason() is None and not self._channel_ok(now):
            return
        self._send_beacon(now)

    def send_now(self):
        """BLE の beacon.send(core.remote が step() の中から呼ぶ)。条件を確かめて、その場で送る。
        条件はスイッチがオン、測位と時刻、前回の送信から BEACON_MANUAL_MIN_S 秒。
        自動のモードでも使え、送った時点から自動の次回を数え直す。
        再起動直後の hold(自動の送出だけ)には従わない。
        ゲートが拒否する間は自動と同じく dry run。
        送ったら {"dry": bool}、送らなかったら (code, msg, extra)(docs/ble-protocol.md §7.5)。
        送信中の例外は _fatal に置き、step() が投げ直す(remote と BLE の except で消えないように)"""
        now = time.ticks_ms()
        if not self._switch_on():
            return ("switch_off", "beacon switch is off", None)
        if not self._position_ok():
            return ("no_fix", "no position fix or time yet", None)
        if self._tx_ms is not None:
            left = config.BEACON_MANUAL_MIN_S - time.ticks_diff(now, self._tx_ms) // 1000
            if left > 0:
                return ("too_soon", "wait %d s" % left, {"wait_s": left})
        try:
            dry = self._send_beacon(now, manual=True)
        except Exception as e:
            self._fatal = e
            return ("internal", "%s: %s" % (type(e).__name__, e), None)
        if dry is None:
            return ("frame_too_long", "beacon does not fit in the TX buffer", None)
        return {"dry": dry}

    def _send_beacon(self, now, manual=False):
        """ビーコンを作って送る(ゲートが拒否すれば dry run)。dry run なら True、送ったら False、
        波形のバッファに入りきらず送らなかったら None"""
        st = self.status
        gps = self.gnss.gps
        f = mice_frame(self.call, config.TX_PATH, st.lat, st.lon, gps.speed, gps.course,
                       gps.altitude if st.fix == 3 else None, config.SYMBOL,
                       config.BEACON_COMMENT, config.BEACON_MESSAGE)
        self.beacon.sent(st.lat, st.lon, now)
        self._tx_ms = now
        return self._transmit(f, "manual" if manual else None)

    def _transmit(self, f, label=None):
        """フレームを送る(ゲートが拒否すれば dry run: 記録だけ)。dry run なら True、送ったら
        False、波形のバッファに入りきらず送らなかったら None。label: 記録の印("manual"、"msg")"""
        st = self.status
        text = tnc2(f)
        try:
            n = self.tx.build([bytes(f.encode())])
        except ValueError as e:  # TX_BUFFER_MS に入りきらない
            print("[tx] not sent:", e)
            self.sd.logger.note("TX skipped: %s" % e)
            return None
        if label == "manual":
            self.sd.logger.note("TX manual (beacon.send)")
        why = self.gate.refuse_reason()
        self.sd.packet(text, tx=True, utc_s=self.utc(), dry=why is not None)
        if why != self._refused:
            self._refused = why
            if why is not None:
                print("[tx] refused:", why)
                self.sd.logger.note("TX refused: %s" % why)
        if why is None:
            st.tx_active = True
            self.ui.draw_status_now()
            self.led.value(1)
            self.wdt.feed()  # 待ちのループではフィードしない(最長約 2 秒。上限は txgate)
            self.rx.mute()  # 送信音の回り込みで自局のパケットを復号しないように
            try:
                self.tx.start(n=n)
                while not self.tx.finish():
                    time.sleep_ms(5)
            finally:
                # Never leave PTT on after an exception or Ctrl-C (no-op on normal completion)
                self.tx.abort()
                self.rx.unmute(config.RX_MUTE_TAIL_MS)
                self.led.value(0)
                st.tx_active = False
                self._air_ms = time.ticks_ms()
        st.tx_count += 1
        if self.remote is not None:
            self.remote.tx({"tnc2": text, "dry": why is not None, "utc_s": self.utc()})
        print("[tx%s%s%s] %d ms" % (" " + label if label else "", "" if why is None else " dry",
                                    " fx25" if self.tx.fx_used else "", n * 1000 // config.FS),
              text)
        return why is not None

    def _channel_ok(self, now):
        """自動の送信(ビーコン・メッセージ・ack)をしてよいか: 前回の送信から MSG_GAP_MS たち、
        チャネルが空いている(またはスケルチが開きっぱなし)"""
        if self._air_ms is not None and time.ticks_diff(now, self._air_ms) < config.MSG_GAP_MS:
            return False
        ch = self.channel
        if ch.stuck(now):
            if not self._stuck_noted:
                self._stuck_noted = True
                print("[ch] busy for %d s, squelch open? sending anyway" % config.CH_BUSY_MAX_S)
                self.sd.logger.note("channel busy %d s, sending anyway" % config.CH_BUSY_MAX_S)
            return True
        if not ch.busy:
            self._stuck_noted = False
        return ch.ok(now)

    # ---- APRS メッセージ
    def _messages(self, now):
        """送るもの(ack、期限の来たメッセージ)があり、送れる条件がそろえば 1 つ送る"""
        self.msgs.poll(now)
        if not self._switch_on() or self.gate.refuse_reason() is not None:
            return  # メッセージは dry run にしない: 送れるようになるまで待つ
        item = self.msgs.next_tx(now)
        if item is None or not self._channel_ok(now):
            return
        info, key = item
        f = Frame(Address(config.TOCALL), Address.parse(self.call),
                  [Address.parse(p) for p in config.TX_PATH], info)
        self._transmit(f, "msg")
        self.msgs.sent(key, time.ticks_ms())

    def _msg_changed(self, m, why):
        """core.messages の項目が変わった(BLE のイベント、LCD、記録)"""
        if why == "rx":
            self.status.message = (m.peer, m.text)
            self.status.message_until = time.ticks_add(time.ticks_ms(), config.MSG_SHOW_S * 1000)
            print("[msg] from %s%s: %s" % (m.peer, "" if m.no is None else " {%s}" % m.no, m.text))
            self.sd.logger.note("MSG from %s %s" % (m.peer, m.no or "-"))
        else:
            print("[msg] %s %s to %s %s tries %d" % (why, m.no, m.peer, m.state, m.tries))
            if why == "state":
                self.sd.logger.note("MSG %s to %s %s" % (m.no, m.peer, m.state))
        if why != "new" and self.remote is not None:
            self.remote.msg(m.to_dict())

    # BLE の msg.*(core.remote が step() の中から呼ぶ。docs/ble-protocol.md §7.6-§7.8)
    def msg_send(self, to, text):
        try:
            check_to(to)
            check_text(text)
        except MessageError as e:
            return ("invalid", e.msg, {"key": e.key})
        if not self._switch_on():
            return ("switch_off", "beacon switch is off", None)
        why = self.gate.refuse_reason()
        if why is not None:
            return ("tx_not_ready", why, None)
        try:
            m = self.msgs.send(to, text, time.ticks_ms(), self.utc())
        except MessageError as e:
            return ("invalid", e.msg, {"key": e.key})
        except QueueFull:
            return ("queue_full", "%d messages pending" % self.msgs.pending_max, None)
        return m.to_dict()

    def msg_cancel(self, no):
        try:
            m = self.msgs.cancel(no)
        except MessageError as e:
            return ("invalid", e.msg, {"key": e.key})
        if m is None:
            return ("unknown_msg", "no message %s" % (no,), None)
        return m.to_dict()

    def msg_list(self):
        self.msgs.mark_read()
        for m in list(self.msgs.items):  # 送る途中で変わってもよいように写す
            yield m.to_dict()

    def _report(self):
        st = self.status
        print("[station] fix %dD sats %d/%d rx %d tx %d bcn %s next %s sd %s ble %s "
              "free %d lag %d" % (
            st.fix, st.sats, st.sats_view, st.rx_count, st.tx_count,
            ("on" if st.beacon_on else "off") + (" manual" if st.beacon_mode == "manual" else ""),
            st.next_beacon_s, self.sd.state, self._ble_state(),
            gc.mem_free(), self.rx.lag_max))
        if self.level is not None:
            lv = self.level
            print("[station] rx level rms %.0f dBFS, peak %.0f dBFS (%s), ch %s (busy >= %d)" % (
                lv.rms_db, lv.peak_db, lv.grade(), "busy" if self.channel.busy else "clear",
                config.CH_BUSY_DB))

    def _ble_state(self):
        b = self.ble
        if b is None:
            return "off"
        return "auth" if b.authenticated else "conn" if b.conn is not None else "adv"

    def close(self):
        # 最初に PTT を切る。受信の停止で待たされたり、ここで Ctrl-C を受けたりしても残らないように
        try:
            self.tx.close()
        finally:
            try:
                self.rx.close()
            finally:
                self.led.value(0)
                self._stop_ble()
                self.sd.close(self.utc())


def run(duration_ms=None, wdt=None):
    """局を動かす。duration_ms を渡すとその時間で止まる(試験用)。
    wdt: main() が起動中の設定で有効にした hw.watchdog.Watchdog(ループに入るとき LOOP_MS に直す)"""
    if config.MODE == "si5351":  # 未完成。表示だけで、局は作らない
        import transceiver

        return transceiver.run(duration_ms, wdt)
    if config.MODE != "tnc":
        raise ValueError("unknown config.MODE: %r" % (config.MODE,))
    settings = Settings(config, log=print)
    settings.load()  # Station が config を読む前に反映する
    station = Station(wdt, settings)
    try:
        station.start()
        # GNSS の探索(最長約 13 秒)と BLE の起動(CYW43 の BT 初期化)を合わせると STARTUP_MS を
        # 超えうるので、BLE の前に張り直してフィードする
        station.wdt.arm(watchdog.STARTUP_MS)
        station.wdt.feed()
        if station.wdt.scope_ok is False:  # リセットで PTT の出力が戻らない設定: 送らない
            station.gate.fault = "watchdog reset scope"
            print("[station] PSM_WDSEL lacks RESETS/SIO: transmit refused")
        station.start_ble()
        station.wdt.arm(watchdog.LOOP_MS)
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


def _show_fatal(e, wait_s):
    """例外の内容を LCD に出す(LCD を作り直す。失敗しても無視)"""
    try:
        from ui import notice
        from ui.views import RED

        notice.show(["STATION ERROR", "", _err_text(e), "", "restart in %d s" % wait_s], RED)
    except Exception as e2:
        sys.print_exception(e2)


def _ptt_off():
    """run() の後始末の途中で Ctrl-C を受けても、PTT を残したまま REPL に戻らないように"""
    if _gate is not None:
        _gate.unkey()


def main():
    """電源投入時の入口(main.py から呼ぶ)。例外で止まったら表示して再起動する"""
    t0 = time.ticks_ms()
    wdt = watchdog.Watchdog(enabled=not watchdog.on_dev_mount())
    wdt.arm(watchdog.STARTUP_MS)
    try:
        run(wdt=wdt)
    except KeyboardInterrupt:
        _ptt_off()
        restart.set_fails(0)
        wdt.hold()  # 有効にしたウォッチドッグは止められない。REPL に戻っても再起動しないようにする
        print("[station] stopped by Ctrl-C (no restart)")
        return
    except Exception as e:
        _ptt_off()
        sys.print_exception(e)
        quick = time.ticks_diff(time.ticks_ms(), t0) < FAIL_WINDOW_MS
        fails = restart.fails() + 1 if quick else 1
        restart.set_fails(fails)
        wait = restart.wait_s(fails, RESTART_S)
        print("[station] error (%d in a row), restart in %d s" % (fails, wait))
        _show_fatal(e, wait)
        try:
            wdt.sleep_s(wait)
        except KeyboardInterrupt:
            restart.set_fails(0)
            wdt.hold()
            print("[station] restart cancelled by Ctrl-C")
            return
        machine.reset()
