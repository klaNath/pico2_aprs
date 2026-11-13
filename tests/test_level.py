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
