"""core.level(受信音量の換算と保持)のテスト。PC と実機の両方で動く。"""

from core.level import DB_MIN, Level, db_peak, db_rms, grade


def near(a, b, tol=0.05):
    return abs(a - b) <= tol


def test_db_conversions():
    assert near(db_peak(2048), 0.0) and near(db_peak(1024), -6.02) and db_peak(0) < -90
    # 満振幅のサイン波: 二乗平均 = 2048^2 / 2。/ 16 して渡す
    assert near(db_rms(2048 * 2048 // 2 // 16), 0.0)
    assert near(db_rms(512 * 512 // 2 // 16), -12.04) and db_rms(0) < -90


def test_grade():
    assert grade(-50, False) == "none" and grade(DB_MIN, False) == "low"
    assert grade(-12.1, False) == "low" and grade(-12, False) == "ok" and grade(-3, False) == "ok"
    assert grade(-2.9, False) == "high" and grade(-20, True) == "clip"


def test_peak_and_clip_are_held():
    lv = Level(hold_ms=3000)
    lv.update(512 * 512 // 2 // 16, 512, False, now_ms=0)
    assert near(lv.rms_db, -12.04) and near(lv.peak_db, -12.04) and lv.grade() == "low"
    lv.update(0, 0, False, now_ms=1000)  # スケルチが閉じた: 実効値は下がるがピークは残る
    assert lv.rms_db < -90 and near(lv.peak_db, -12.04)
    lv.update(0, 1024, True, now_ms=2000)  # より大きいピークとクリップ
    assert near(lv.peak_db, -6.02) and lv.clip and lv.grade() == "clip"
    lv.update(0, 100, False, now_ms=4999)
    assert near(lv.peak_db, -6.02) and lv.clip  # 3 秒たつまで保持
    lv.update(0, 100, False, now_ms=5000)
    assert near(lv.peak_db, db_peak(100)) and not lv.clip


def test_packet_sets_peak_and_ignores_tail_noise():
    lv = Level(hold_ms=3000, guard_ms=1200)
    lv.update(0, 100, False, now_ms=0)
    lv.packet(-10.7, False, now_ms=1000)  # 復号した: パケットの音量
    assert near(lv.peak_db, -10.7) and lv.grade() == "ok"
    lv.update(2048 * 2048 // 2 // 16, 2047, True, now_ms=1200)  # テールノイズ(ほぼ満振幅、クリップ)
    assert near(lv.peak_db, -10.7) and not lv.clip and lv.rms_db > -10  # 実効値だけ更新する
    lv.update(0, 2047, True, now_ms=2199)
    assert near(lv.peak_db, -10.7) and not lv.clip
    lv.update(0, 100, False, now_ms=2200)  # 1.2 秒たった: 通常に戻る(保持は 3 秒)
    assert near(lv.peak_db, -10.7)
    lv.update(0, 100, False, now_ms=4000)
    assert near(lv.peak_db, db_peak(100))


def test_packet_clip_is_held():
    lv = Level(hold_ms=3000, guard_ms=1200)
    lv.packet(-0.5, True, now_ms=0)
    assert lv.clip and lv.grade() == "clip"
    lv.update(0, 100, False, now_ms=3000)
    assert not lv.clip


# ---- core.level.Channel(受信音量によるチャネルの使用中の判定)
def _ch(**kw):
    from core.level import Channel

    return Channel(-30, idle_ms=500, slot_ms=1000, busy_max_ms=30_000, rand=lambda n: n // 2, **kw)


def test_channel_busy_while_level_is_high_and_for_the_tail():
    ch = _ch()
    ch.update(-60, 0)
    assert not ch.busy and ch.ok(0)  # スケルチが閉じている: 送ってよい
    ch.update(-30, 100)  # しきい値ちょうどは使用中
    assert ch.busy and not ch.ok(100)
    ch.update(-50, 200)
    ch.update(-50, 699)  # 下回ってから 0.5 秒たつまでは使用中(スケルチテール)
    assert ch.busy and not ch.ok(699)
    ch.update(-50, 700)
    assert not ch.busy and not ch.ok(700)  # 空いた。乱数の待ち(ここでは 500 ms)のあいだは送らない
    assert not ch.ok(1199) and ch.ok(1200)
    ch.update(-20, 800)
    ch.update(-50, 900)  # 一瞬下がっただけ
    ch.update(-20, 1000)
    assert ch.busy


def test_channel_heard_packet_delays_sending():
    ch = _ch()
    ch.update(-60, 0)
    ch.heard(1000)  # しきい値が高すぎて音量では分からなくても、復号した直後は待つ
    assert not ch.ok(1999) and ch.ok(2000)  # 0.5 秒 + 乱数 0.5 秒


def test_channel_stuck_open_squelch():
    ch = _ch()
    ch.update(-10, 0)
    ch.update(-10, 29_999)
    assert not ch.stuck(29_999) and not ch.ok(29_999)
    assert ch.stuck(30_000) and ch.ok(30_000)  # 開きっぱなしとみなして送る
    ch.update(-60, 30_100)
    ch.update(-60, 30_600)
    assert not ch.busy and not ch.stuck(30_600)


def test_channel_random_wait_is_in_range():
    from core.level import _rand_ms

    vals = [_rand_ms(1000) for _ in range(200)]
    assert min(vals) >= 0 and max(vals) < 1000 and len(set(vals)) > 10
    assert _rand_ms(0) == 0
