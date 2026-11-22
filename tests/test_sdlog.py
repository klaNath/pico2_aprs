"""hw.sdlog(microSD の検出・マウントとやり直し)のテスト。
PC だけで動く(偽の machine / sdcard / vfs)。

ログのファイルは test_logger の偽のファイルシステム(メモリ上)に書く。
"""

import _fake_hw
from test_logger import FakeFs

DET = 9


def make(fs):
    from core.logger import Logger
    from hw import sdlog

    sd = sdlog.SdLog(1, 10, 11, 12, 15, DET)
    sd.logger = Logger("Q1TEST-7", opener=fs.open, listdir=fs.listdir, mkdir=fs.mkdir)
    return sd


def insert(v=True):
    _fake_hw.Pin.state[DET] = 0 if v else 1


def run(sd, t0, ms, step=100):
    """t0 から ms だけ step ごとに poll する(時刻は UTC が分かっている前提。1 秒ごとに受信が
    1 行ある)。最後の時刻を返す"""
    t = t0
    while t - t0 < ms:
        if t % 1000 == 0:
            sd.packet("JA1AAA>APRS:>x", utc_s=1792885713)
        sd.poll(1792885713, t)
        t += step
    return t


def test_persistent_failure_backs_off_and_keeps_one_file_set():
    # 満杯のように直らない失敗: やり直しの間隔は倍になっていき、ファイルは 1 組のまま
    if not _fake_hw.install():
        return
    from hw import sdlog

    fs = FakeFs([])
    sd = make(fs)
    insert()
    t = run(sd, 0, 1000)
    assert sd.state == "log 0001"
    fs.fail = True  # これ以降の書き込みはすべて失敗
    t = run(sd, t, 30 * 60_000)  # 30 分
    paths = {p for p, _m in fs.opened}
    assert paths == {"/sd/log/0001_aprs.txt", "/sd/log/0001_nmea.txt"}
    # 5 秒ずつなら約 330 回。倍にしていくと 5+10+...+300 で 30 分に 10 回程度
    assert len(fs.opened) // 2 <= 12
    assert sd._retry_ms == sdlog.RETRY_MAX_MS and sd.state == "error"
    # 直ったら同じ番号に戻り、しばらく続けば待ちも最初に戻る
    fs.fail = False
    t = run(sd, t, sdlog.RETRY_MAX_MS + 1000)
    assert sd.state == "log 0001" and fs.opened[-1] == ("/sd/log/0001_nmea.txt", "ab")
    t = run(sd, t, sdlog.RETRY_MAX_MS + 1000)
    assert sd._retry_ms == sdlog.RETRY_MS


def test_removing_the_card_starts_over():
    if not _fake_hw.install():
        return
    from hw import sdlog

    fs = FakeFs([])
    sd = make(fs)
    insert()
    t = run(sd, 0, 1000)
    fs.fail = True
    t = run(sd, t, 60_000)
    assert sd._retry_ms > sdlog.RETRY_MS
    insert(False)
    t = run(sd, t, 1000)
    assert sd.state == "no card" and sd._retry_ms == sdlog.RETRY_MS and sd._resume is None
    fs.fail = False
    fs.names = ["0001_aprs.txt", "0001_nmea.txt"]  # 別のカードかもしれない: 新しい番号で
    insert()
    t = run(sd, t, 1000)
    assert sd.state == "log 0002"


def test_detect_time_is_not_kept_while_mounted():
    # マウント中に挿入の検出時刻を持ち続けると、何日もあとの失敗でデバウンスが逆になる
    if not _fake_hw.install():
        return
    fs = FakeFs([])
    sd = make(fs)
    insert()
    run(sd, 0, 1000)
    assert sd.mounted and sd._det_since is None
