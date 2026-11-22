"""microSD(SPI1)への記録。

カードの検出・マウントと、core.logger.Logger のファイルの開け閉めを受け持つ。

カード検出(GP9)は挿入で 0(基板の R6 でプルアップ、スイッチの反対側は GND)。
poll() を Core0 のループから呼ぶ。挿入を 0.5 秒続けて検出し、GNSS の時刻が分かったら
(最大 20 秒待つ)マウントして新しい番号のファイルを開き、抜かれたらファイルを捨てて
マウントを外す。
マウントや書き込みに失敗したら RETRY_MS 後にやり直す。失敗が続くと間隔を倍にしていき(最長
RETRY_MAX_MS)、やり直しでは同じ番号のファイルに追記する(満杯などで直らないカードで、ファイルを
作り続けたり USB への表示を出し続けたりしない)。カードを抜くと最初に戻る。
電源投入後の最初の初期化は失敗するカードがある(この基板の 32 GB で確認)ので、初期化は 3 回まで試す。
stations(core.stations.Stations)を渡すと、受信した局の表を STATIONS_FILE に保存し、起動後の
最初のマウントで読み出して表に加える(core.stationfile。GNSS の時刻が分かってから)。
messages(core.messages.Messages)を渡すと、APRS メッセージを MESSAGES_FILE に保存し、最初の
マウントで読み出す(core.msgfile)。
"""

import time

import sdcard  # pyright: ignore[reportMissingImports]  (ファームウェアに組み込み、型スタブ無し)
import vfs
from machine import SPI, Pin

from core.logger import Logger

MOUNT = "/sd"
LOG_DIR = MOUNT + "/log"
STATIONS_FILE = MOUNT + "/stations.txt"
MESSAGES_FILE = MOUNT + "/messages.txt"
DEBOUNCE_MS = 500
TIME_WAIT_MS = 20_000  # 見出しに時刻を書くため、GNSS の時刻を最大これだけ待ってから開く
RETRY_MS = 5000
RETRY_MAX_MS = 300_000  # やり直しの間隔の上限。この間マウントが続いたら間隔を RETRY_MS に戻す
SD_BAUD = 20_000_000


class SdLog:
    def __init__(self, spi_id, sck, mosi, miso, cs, det, header="", log=None, stations=None,
                 messages=None):
        self.spi = SPI(spi_id, baudrate=400_000, sck=Pin(sck), mosi=Pin(mosi), miso=Pin(miso))
        self.cs = Pin(cs, Pin.OUT, value=1)
        self.det = Pin(det, Pin.IN, Pin.PULL_UP)
        self.logger = Logger(header)
        self.log = log or (lambda *a: None)
        self.stations = stations
        # core.stationfile は最初にマウントしたときに読み込む。dev.py run/exec(src をマウント)では
        # 読み込むたびにコンパイルでヒープが細切れになり、局の起動(Core1 のスタック 4 KB)が
        # MemoryError になった
        self.stfile = None
        self.messages = messages
        self.msgfile = None
        self.mounted = False
        # 表示用: 'no card' / 'wait'(挿入済みで開く前)/ 'log 0001' / 'error'(やり直し待ち)
        self.state = "no card"
        self._det_since = None  # 挿入を検出し始めた ticks_ms(マウントしていない間だけ)
        self._retry_at = None
        self._retry_ms = RETRY_MS  # 次に失敗したときの待ち(失敗のたびに倍)
        self._mounted_ms = time.ticks_ms()  # マウントした ticks_ms
        self._resume = None  # このカードで書いているログの番号(失敗のあとのやり直しでは追記する)

    def inserted(self):
        return self.det.value() == 0

    # ---- 記録(Logger に渡すだけ。カードが無い間もためる)
    def packet(self, text, tx=False, utc_s=None, dry=False):
        self.logger.packet(text, tx, utc_s, dry=dry)

    def nmea(self, line):
        self.logger.nmea(line)

    # ---- カード
    def poll(self, utc_s=None, now_ms=None):
        """カードの抜き差しを見て、ログを書く。utc_s は今の UTC の秒(不明なら None、見出しに使う)"""
        now = time.ticks_ms() if now_ms is None else now_ms
        if not self.inserted():
            self._det_since = None
            # 別のカードかもしれない: 新しい番号で始め、やり直しの待ちも最初に戻す
            self._retry_at = None
            self._retry_ms = RETRY_MS
            self._resume = None
            if self.mounted:
                self.log("[sd] card removed")
                self._unmount(flush=False)
            self.state = "no card"
            return
        if not self.mounted:
            if self._det_since is None:
                self._det_since = now
            if self.state != "error":
                self.state = "wait"
            if time.ticks_diff(now, self._det_since) < DEBOUNCE_MS:
                return
            if self._retry_at is not None and time.ticks_diff(now, self._retry_at) < 0:
                return
            if utc_s is None and time.ticks_diff(now, self._det_since) < TIME_WAIT_MS:
                return  # 待つ間の行は Logger にたまる
            self._mount(utc_s, now)
            return
        if (self._retry_ms != RETRY_MS
                and time.ticks_diff(now, self._mounted_ms) >= RETRY_MAX_MS):
            self._retry_ms = RETRY_MS  # しばらく失敗しなかった: 待ちを最初に戻す
        try:
            self.logger.poll(now)
        except OSError as e:
            self._fail("write", e, now)
            return
        if self.stations is not None:
            if self.stfile is None:
                from core.stationfile import StationFile

                self.stfile = StationFile(STATIONS_FILE, log=self.log)
            try:
                self.stfile.poll(self.stations, utc_s, now)
            except OSError as e:
                self._fail("stations", e, now)
                return
        if self.messages is not None:
            if self.msgfile is None:
                from core.msgfile import MsgFile

                self.msgfile = MsgFile(MESSAGES_FILE, log=self.log)
            try:
                self.msgfile.poll(self.messages, now)
            except OSError as e:
                self._fail("messages", e, now)

    def _mount(self, utc_s, now):
        try:
            sd = None
            for k in range(3):
                try:
                    sd = sdcard.SDCard(self.spi, self.cs, baudrate=SD_BAUD)
                    break
                except OSError:
                    if k == 2:
                        raise
                    time.sleep_ms(50)
            vfs.mount(vfs.VfsFat(sd), MOUNT)  # pyright: ignore[reportArgumentType]
            self.mounted = True
            seq = self.logger.start(LOG_DIR, utc_s, resume=self._resume)
        except OSError as e:
            self._fail("mount", e, now)
            return
        self._retry_at = None
        self._resume = seq
        self._det_since = None  # マウント中は使わない(何日も持つと ticks の一周で逆になる)
        self._mounted_ms = now
        self.state = "log %04d" % seq
        self.log("[sd] logging to %s/%04d_*.txt" % (LOG_DIR, seq))

    def _fail(self, what, e, now):
        wait = self._retry_ms
        self._retry_ms = min(wait * 2, RETRY_MAX_MS)
        self.log("[sd] %s failed: %r, retry in %d s" % (what, e, wait // 1000))
        self._unmount(flush=False)
        self.state = "error"
        self._retry_at = time.ticks_add(now, wait)

    def sweep(self, now):
        """古い時刻を片付ける(局のループから定期的に。core.ticks)"""
        self.logger.sweep(now)
        if self.stfile is not None:
            self.stfile.sweep(now)
        if self.msgfile is not None:
            self.msgfile.sweep(now)

    def _unmount(self, flush=True):
        if flush:
            try:
                self.logger.close()
            except OSError:
                pass
        self.logger.abandon()
        if self.mounted:
            try:
                vfs.umount(MOUNT)
            except OSError:
                pass
        self.mounted = False

    def close(self, utc_s=None):
        """書き残し(と局の表)を書いてマウントを外す(電源を切る前・カードを抜く前)。
        utc_s: 今の UTC の秒(不明なら局の表は書かない)"""
        if self.mounted and self.stfile is not None:
            try:
                self.stfile.close(self.stations, utc_s, time.ticks_ms())
            except OSError as e:
                self.log("[sd] stations save failed: %r" % (e,))
        if self.mounted and self.msgfile is not None:
            try:
                self.msgfile.close(self.messages, time.ticks_ms())
            except OSError as e:
                self.log("[sd] messages save failed: %r" % (e,))
        self._unmount(flush=True)
        self.state = "no card" if not self.inserted() else "closed"
