"""何日も動かしたときの ticks_ms の扱い(core.ticks と、各部の sweep)のテスト。
PC と実機の両方で動く。

ticks_diff が正しいのは差が約 6.2 日(2^29 ms)まで。局のループは REPORT_MS ごとに sweep を
呼ぶ。ここでは 1 時間ごとに呼んで 7〜8 日進め、古い時刻が「ついさっき」に見えないことを確かめる。
コールサインはすべて架空の例。
"""

import time

from core import ticks
from core.beacon import Beacon
from core.level import Channel
from core.logger import Logger
from core.stations import Stations

HOUR = 3600_000
DAY = 24 * HOUR
LAT, LON = 35.681236, 139.767125


def advance(t, days, sweep, step=HOUR):
    """t から days 日進める(step ごとに sweep(now) を呼ぶ)。進めたあとの ticks_ms を返す"""
    for _ in range(days * DAY // step):
        t = time.ticks_add(t, step)
        sweep(t)
    return t


def test_ticks_diff_really_wraps_after_six_days():
    # 片付けないとどうなるか(この前提が崩れたら、このファイルの他のテストは意味が無い)
    t0 = time.ticks_ms()
    t = t0
    for _ in range(7 * 24):
        t = time.ticks_add(t, HOUR)
    assert time.ticks_diff(t, t0) < 0


def test_cap_and_passed():
    assert ticks.cap(0, None) is None and ticks.passed(0, None) is None
    assert ticks.cap(DAY, 0) == 0  # 新しい値はそのまま
    now = advance(0, 6, lambda t: None)
    old = ticks.cap(now, 0)
    assert time.ticks_diff(now, old) == ticks.AGE_MAX_MS
    assert ticks.passed(1000, 1000) is None and ticks.passed(999, 1000) == 1000


def test_beacon_sends_again_after_a_week_without_fix():
    b = Beacon(90, 600, 10.0)
    b.hold(0, 120)
    b.sent(LAT, LON, 0)
    now = advance(0, 7, b.sweep)  # 測位が無い間は next_s が呼ばれない
    assert b.hold_ms is None
    assert b.next_s(LAT, LON, now) == 0


def test_station_ages_stay_exact_after_eight_days():
    from aprs.packet import parse

    sts = Stations()
    p = parse("JA1AAA", "APRS", [], b"!3539.53N/13944.70E>")
    sts.update(p, now_ms=0)
    now = advance(0, 8, sts.sweep)
    st = sts.latest()
    assert st is not None
    assert st.age_s(now) == 8 * 86400 and st.pos_age_s(now) == 8 * 86400
    assert st.stale(now)


def test_channel_is_clear_after_a_week():
    ch = Channel(-30, idle_ms=500, slot_ms=1000, busy_max_ms=30_000, rand=lambda n: 0)
    ch.heard(0)  # 受信音量を見ない(update が来ない)ときも、待ちが一周で未来に見えない
    now = advance(0, 7, ch.sweep)
    assert ch.ok(now)
    ch.update(-10, now)  # スケルチが開いたまま 7 日
    now = advance(now, 7, lambda t: (ch.update(-10, t), ch.sweep(t)))
    assert ch.busy and ch.stuck(now) and ch.ok(now)


def test_logger_elapsed_stamp_after_a_week():
    lg = Logger("Q1TEST-7", opener=None, listdir=None, mkdir=None)
    t0 = lg._t0
    now = advance(t0, 7, lg.sweep)
    lg.packet("JA1AAA>APRS:>x", now_ms=time.ticks_add(now, 1234))
    assert lg._buf[0][0].startswith(b"T+%d.234 " % (7 * 86400 + 1))
