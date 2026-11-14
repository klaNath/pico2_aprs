"""受信音声の音量(ADC 入力)の換算と保持。ハードウェアに依存しないので PC でもテストできる。

hw.afsk_rx.Receiver が約 100 ms ごとに出す値(直流を引いた二乗平均 / 16、ピーク、クリップ)を
dBFS(ADC の満振幅のサイン波 = 0 dB)に直し、画面のメータ用にピークを HOLD_MS 保持する。
APRS は短い送信が時々来るだけで、スケルチが閉じるとほぼ無音になるので、保持しないと調整できない。
送信機が PTT を切るときのテールノイズ(実測: 復号の約 0.1〜0.9 秒後に -3〜0 dBFS)でピークが
跳ねて調整の邪魔になるので、パケットを復号したら、そのパケットの音量(復号の瞬間に取る)をピークに
して、その後 TAIL_GUARD_MS はピークとクリップを更新しない(実効値は更新する)。
適正の目安はピークが OK_LO..OK_HI dBFS。復調器は振幅を自動でそろえるので低めでも復号できるが、
クリップ(ADC の端に当たる)は避ける。
"""

import math
import time

FULL = 2048  # ADC の満振幅(12 bit、中点から)
HOLD_MS = 3000
TAIL_GUARD_MS = 1200  # 復号の後、ピークを更新しない時間
DB_MIN = -40  # メータの下端
OK_LO = -12  # 適正範囲(ピーク、dBFS)
OK_HI = -3
_RMS_FULL = FULL / math.sqrt(2)  # 満振幅のサイン波の実効値


def db_peak(pk):
    """ピーク(ADC の値、中点から)-> dBFS。0 以下は -99"""
    return 20 * math.log10(pk / FULL) if pk > 0 else -99.0


def db_rms(ms16):
    """直流を引いた二乗平均 / 16 -> dBFS(満振幅のサイン波の実効値が 0 dB)。0 以下は -99"""
    return 10 * math.log10(ms16 * 16 / (_RMS_FULL * _RMS_FULL)) if ms16 > 0 else -99.0


def grade(peak_db, clip):
    """'clip' / 'high'(適正より大きい)/ 'ok' / 'low' / 'none'(メータの下端より小さい)"""
    if clip:
        return "clip"
    if peak_db < DB_MIN:
        return "none"
    if peak_db < OK_LO:
        return "low"
    if peak_db <= OK_HI:
        return "ok"
    return "high"


class Level:
    """今の実効値と、HOLD_MS 保持したピーク・クリップ"""

    def __init__(self, hold_ms=HOLD_MS, guard_ms=TAIL_GUARD_MS):
        self.hold_ms = hold_ms
        self.guard_ms = guard_ms
        self._guard_until = None  # この時刻まで、ピークとクリップを更新しない
        self.rms_db = -99.0
        self.peak_db = -99.0  # 保持中のピーク
        self.clip = False  # 保持中のクリップ
        self._peak_ms = None
        self._clip_ms = None

    def update(self, ms16, pk, clip, now_ms=None):
        """Receiver.level() の値を取り込む"""
        now = time.ticks_ms() if now_ms is None else now_ms
        self.rms_db = db_rms(ms16)
        if self._guard_until is not None:
            if time.ticks_diff(now, self._guard_until) < 0:
                return  # テールノイズの間は、実効値だけ
            self._guard_until = None
        p = db_peak(pk)
        if (p >= self.peak_db or self._peak_ms is None
                or time.ticks_diff(now, self._peak_ms) >= self.hold_ms):
            self.peak_db = p
            self._peak_ms = now
        if clip:
            self.clip = True
            self._clip_ms = now
        elif self._clip_ms is not None and time.ticks_diff(now, self._clip_ms) >= self.hold_ms:
            self.clip = False
            self._clip_ms = None

    def packet(self, peak_db, clip, now_ms=None):
        """復号したパケットの音量(復号の瞬間の直近のピーク)をピークにして、しばらく更新を止める"""
        now = time.ticks_ms() if now_ms is None else now_ms
        self.peak_db = peak_db
        self._peak_ms = now
        self.clip = clip
        self._clip_ms = now if clip else None
        self._guard_until = time.ticks_add(now, self.guard_ms)

    def grade(self):
        return grade(self.peak_db, self.clip)
