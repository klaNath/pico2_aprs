"""station(局の本体)の実機専用テスト。machine を使うので PC ではスキップされる。

局は動かさない(import と、再起動の待ち時間・失敗回数の記録だけを確かめる)。
station は machine を import するので、PC でも読み込めるよう関数の中で import する。
"""


def test_hw_restart_wait():
    import station

    assert [station.restart_wait_s(n) for n in (1, 2, 3, 4, 5, 6, 30)] == [10, 20, 40, 80, 160, 300,
                                                                          300]


def test_hw_fail_counter_in_scratch():
    import station

    keep = station._fails()
    try:
        station._set_fails(3)
        assert station._fails() == 3
        station._set_fails(0)
        assert station._fails() == 0
    finally:
        station._set_fails(keep)
