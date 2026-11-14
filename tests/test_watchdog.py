"""hw.watchdog(ウォッチドッグの有効化・フィード・Ctrl-C 後の保持)と、局への組み込みのテスト。

偽の WDT と Timer を渡すので、PC と実機の両方で動く(実機でもウォッチドッグは有効にしない)。
局への組み込み(Station.step、送信の前、異常時の再起動の待ち)は PC だけで確かめる。
"""

from hw import watchdog


class FakeWdt:
    made = []

    def __init__(self, timeout=0):
        self.timeout = timeout
        self.feeds = 0
        FakeWdt.made.append(self)

    def feed(self):
        self.feeds += 1


class FakeTimer:
    PERIODIC = 1
    made = []

    def __init__(self, id=-1):
        self.period = self.callback = None
        FakeTimer.made.append(self)

    def init(self, period=0, mode=0, callback=None):
        self.period, self.callback = period, callback


def make(enabled=True):
    FakeWdt.made.clear()
    FakeTimer.made.clear()
    return watchdog.Watchdog(enabled, FakeWdt, FakeTimer)


def test_arm_feed_and_rearm():
    wd = make()
    wd.feed()  # まだ有効でなければ何もしない
    assert FakeWdt.made == []
    wd.arm(watchdog.STARTUP_MS)
    wd.feed()
    wd.arm(watchdog.LOOP_MS)  # 時間を設定し直す
    wd.feed()
    assert [w.timeout for w in FakeWdt.made] == [watchdog.STARTUP_MS, watchdog.LOOP_MS]
    assert [w.feeds for w in FakeWdt.made] == [1, 1] and wd.timeout_ms == watchdog.LOOP_MS


def test_disabled_does_nothing():
    wd = make(enabled=False)
    wd.arm(3000)
    wd.feed()
    wd.hold()
    wd.sleep_s(0.1, sleep_ms=lambda ms: None)
    assert FakeWdt.made == [] and FakeTimer.made == []


def test_timeouts_are_ordered():
    # ループは APRS 1 回の上限(5 秒)より短く、起動は GNSS の探索(6 速度 x 3 回 x 700 ms)より長い
    assert watchdog.LOOP_MS < 5000
    assert watchdog.STARTUP_MS > 6 * 3 * 700 + 2000
    assert watchdog.STARTUP_MS <= 16_700 and watchdog.HOLD_MS <= 16_700  # RP2350 の上限


def test_sleep_feeds_every_second():
    wd = make()
    wd.arm(watchdog.LOOP_MS)
    slept = []
    wd.sleep_s(300, sleep_ms=slept.append)
    assert sum(slept) == 300_000 and max(slept) <= 1000
    assert FakeWdt.made[0].feeds >= len(slept)
    slept.clear()
    wd.sleep_s(2.5, sleep_ms=slept.append)
    assert slept == [1000, 1000, 500]


def test_hold_keeps_feeding_from_a_timer():
    wd = make()
    wd.hold()  # 有効にしていなければ何もしない
    assert FakeTimer.made == []
    wd.arm(watchdog.LOOP_MS)
    wd.hold()
    wd.hold()  # 2 回目は何もしない
    assert len(FakeTimer.made) == 1
    t = FakeTimer.made[0]
    assert t.period == watchdog.HOLD_PERIOD_MS and t.period < watchdog.HOLD_MS
    assert wd.timeout_ms == watchdog.HOLD_MS
    before = FakeWdt.made[-1].feeds
    t.callback(t)
    assert FakeWdt.made[-1].feeds == before + 1


def test_on_dev_mount():
    assert watchdog.on_dev_mount(lambda: "/remote")
    assert watchdog.on_dev_mount(lambda: "/remote/aprs")
    assert not watchdog.on_dev_mount(lambda: "/")

    def broken():
        raise OSError(2)

    assert not watchdog.on_dev_mount(broken)
