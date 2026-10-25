"""hw.restart(異常時の再起動の待ち時間と失敗回数の記録)の実機専用テスト。

machine を使うので PC ではスキップされる。station は import しない(大きなバッファを確保して、
後のテストのメモリが足りなくなるため)。PC でも読み込めるよう関数の中で import する。
"""


def test_hw_restart_wait():
    from hw import restart

    assert [restart.wait_s(n) for n in (1, 2, 3, 4, 5, 6, 30)] == [10, 20, 40, 80, 160, 300, 300]
    assert restart.wait_s(1, 3) == 3


def test_hw_fail_counter_in_scratch():
    from hw import restart

    keep = restart.fails()
    try:
        restart.set_fails(3)
        assert restart.fails() == 3
        restart.set_fails(0)
        assert restart.fails() == 0
    finally:
        restart.set_fails(keep)
