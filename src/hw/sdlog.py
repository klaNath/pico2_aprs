"""microSD(SPI1)への記録。

カードの検出・マウントと、core.logger.Logger のファイルの開け閉めを受け持つ。

カード検出(GP9)は挿入で 0(基板の R6 でプルアップ、スイッチの反対側は GND)。
poll() を Core0 のループから呼ぶ。挿入を 0.5 秒続けて検出し、GNSS の時刻が分かったら
(最大 20 秒待つ)マウントして新しい番号のファイルを開き、抜かれたらファイルを捨てて
マウントを外す。
マウントや書き込みに失敗したら RETRY_MS 後にやり直す。
電源投入後の最初の初期化は失敗するカードがある(この基板の 32 GB で確認)ので、初期化は 3 回まで試す。
"""

import time

import sdcard  # pyright: ignore[reportMissingImports]  (ファームウェアに組み込み、型スタブ無し)
import vfs
from machine import SPI, Pin

from core.logger import Logger

MOUNT = "/sd"
LOG_DIR = MOUNT + "/log"
DEBOUNCE_MS = 500
TIME_WAIT_MS = 20_000  # 見出しに時刻を書くため、GNSS の時刻を最大これだけ待ってから開く
RETRY_MS = 5000
SD_BAUD = 20_000_000


class SdLog:
    def __init__(self, spi_id, sck, mosi, miso, cs, det, header="", log=None):
        self.spi = SPI(spi_id, baudrate=400_000, sck=Pin(sck), mosi=Pin(mosi), miso=Pin(miso))
        self.cs = Pin(cs, Pin.OUT, value=1)
        self.det = Pin(det, Pin.IN, Pin.PULL_UP)
        self.logger = Logger(header)
        self.log = log or (lambda *a: None)
        self.mounted = False
        self.state = "no card"  # 表示用: 'no card' / 'log 0001' / 'error'
        self._det_since = None  # 挿入を検出し始めた ticks_ms
        self._retry_at = None

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
            if self.mounted:
                self.log("[sd] card removed")
                self._unmount(flush=False)
            self.state = "no card"
            return
        if self._det_since is None:
            self._det_since = now
        if not self.mounted:
            if time.ticks_diff(now, self._det_since) < DEBOUNCE_MS:
                return
            if self._retry_at is not None and time.ticks_diff(now, self._retry_at) < 0:
                return
            if utc_s is None and time.ticks_diff(now, self._det_since) < TIME_WAIT_MS:
                return  # 待つ間の行は Logger にたまる
            self._mount(utc_s, now)
            return
        try:
            self.logger.poll(now)
        except OSError as e:
            self._fail("write", e, now)

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
            seq = self.logger.start(LOG_DIR, utc_s)
        except OSError as e:
            self._fail("mount", e, now)
            return
        self._retry_at = None
        self.state = "log %04d" % seq
        self.log("[sd] logging to %s/%04d_*.txt" % (LOG_DIR, seq))

    def _fail(self, what, e, now):
        self.log("[sd] %s failed: %r" % (what, e))
        self._unmount(flush=False)
        self.state = "error"
        self._retry_at = time.ticks_add(now, RETRY_MS)

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

    def close(self):
        """書き残しを書いてマウントを外す(電源を切る前・カードを抜く前)"""
        self._unmount(flush=True)
        self.state = "no card" if not self.inserted() else "closed"
