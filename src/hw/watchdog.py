"""ウォッチドッグ(ファームウェアが止まったときの保護)。設計は docs/tx-time-limit.md §4。

局(station.main)は 2 段階で使う。起動中(import、GNSS の探索、SD のマウント)は STARTUP_MS、
ループに入ったら LOOP_MS。フィードは局のループ(Station.step)だけで行い、送信の待ちのループでは
行わない(送信の開始直前に 1 回だけ。待ちの上限は core.txgate の時間の上限が担い、ここは Python が
止まったときのためのもの)。LOOP_MS は APRS 1 回の上限(5 秒)より短い。

- dev.py の run/exec(PC の src をマウント中)では有効にしない。マウント中にリセットが起きると
  デバイスが応答しなくなる(CLAUDE.md)。on_dev_mount() で判定する。
- 有効にしたウォッチドッグは止められない。USB から Ctrl-C で局を止めたときは hold() で、タイマーが
  フィードし続ける(REPL で作業できる)。ソフトリセット(mpremote は接続のたびに行う)でタイマーは
  消えるので、boot.py が wdt_bridge でフィードを引き継ぐ。局が起動して arm() するとき止める
  (mpremote で REPL に入っただけなら、main.py は走らないので、引き継ぎが続く)。
- リセット後に PTT(GP2)が Low に戻るかは実機で確認する(docs/tx-time-limit.md §4)。
  対策として boot.py の先頭で GP2 を Low にする。本物の WDT を入れたら PSM_WDSEL を読み戻し
  (wdsel_ok)、リセットが PTT の出力まで及ばない設定なら、局は送信を拒否する(scope_ok)。

machine は使うときに import するので、偽の Watchdog(wdt_class 引数)で PC でもテストできる。
"""

import os
import time

STARTUP_MS = 16_000  # RP2350 の上限(約 16.7 秒)に近く、GNSS の探索の最長(約 13 秒)を超える
LOOP_MS = 3_000
HOLD_MS = 16_000  # Ctrl-C で止めたあと。タイマーが HOLD_PERIOD_MS ごとにフィードする
HOLD_PERIOD_MS = 1_000
_SLEEP_STEP_MS = 1_000
_REASON = 0x400D8008  # WATCHDOG REASON(bit0 = タイムアウト、bit1 = machine.reset などの強制)
_REASON_TIMER = 1
# PSM WDSEL(PSM_BASE 0x40018000 + 8): ウォッチドッグのリセットが及ぶ範囲。pico-sdk の
# watchdog_enable は ROSC・XOSC 以外の全部を立てる(測定 0x01fffff3)。RESETS が入っていれば
# 周辺(IO_BANK0・PADS)もリセットされ、GP2(PTT)のパッドは既定に戻る。SIO は GPIO の出力値と出力許可
PSM_WDSEL = 0x40018008
WDSEL_NEED = 0x00000010 | 0x00200000  # RESETS | SIO(hardware/regs/psm.h)


def release_bridge():
    """boot.py の wdt_bridge が引き継いでいるフィードを止める(局が自分でフィードするので)"""
    try:
        import wdt_bridge
    except ImportError:
        return
    wdt_bridge.release()


def on_dev_mount(getcwd=None):
    """dev.py の run/exec が PC の src をマウントして動かしているか(カレントが /remote)"""
    try:
        return (getcwd or os.getcwd)().startswith("/remote")
    except OSError:
        return False


def timed_out():
    """今回の起動が、ウォッチドッグのタイムアウトによるリセットの後か(machine.reset() は含まない)"""
    try:
        import machine

        return machine.reset_cause() == machine.WDT_RESET and bool(
            machine.mem32[_REASON] & _REASON_TIMER)
    except (ImportError, AttributeError):
        return False


def wdsel_ok(read=None):
    """ウォッチドッグのリセットが PTT の出力まで戻す設定か(PSM_WDSEL に WDSEL_NEED がそろっている)。
    read: 値を返す関数(テスト用)。machine が無い(PC)なら True"""
    if read is not None:
        v = read()
    else:
        try:
            import machine
        except ImportError:
            return True
        v = machine.mem32[PSM_WDSEL]
    return v & WDSEL_NEED == WDSEL_NEED


class Watchdog:
    def __init__(self, enabled=True, wdt_class=None, timer_class=None):
        self.enabled = enabled
        self._wdt_class = wdt_class
        self._timer_class = timer_class
        self._wdt = None
        self._timer = None
        self.timeout_ms = None
        # 本物の machine.WDT を入れたあとの wdsel_ok()。None = まだ / 偽物の WDT。
        # False なら局は送らない
        self.scope_ok = None

    def arm(self, timeout_ms):
        """有効にする。すでに有効なら時間を設定し直す(止めることはできない)"""
        if not self.enabled:
            return
        release_bridge()
        cls = self._wdt_class
        if cls is None:
            import machine

            self._wdt = machine.WDT(timeout=timeout_ms)
            self.scope_ok = wdsel_ok()
        else:
            self._wdt = cls(timeout=timeout_ms)
        self.timeout_ms = timeout_ms

    def feed(self):
        if self._wdt is not None:
            self._wdt.feed()

    def sleep_s(self, seconds, sleep_ms=time.sleep_ms):
        """秒単位で待つ。1 秒ごとにフィードする(異常時の再起動の待ちは最大 300 秒)"""
        left = int(seconds * 1000)
        while left > 0:
            self.feed()
            step = min(left, _SLEEP_STEP_MS)
            sleep_ms(step)
            left -= step
        self.feed()

    def hold(self):
        """局を止めて REPL に戻るとき。タイマーがフィードし続けるので、REPL で作業できる"""
        if self._wdt is None or self._timer is not None:
            return
        cls = self._timer_class
        if cls is None:
            import machine

            cls = machine.Timer
        self.arm(HOLD_MS)
        wdt = self._wdt
        self._timer = cls(-1)
        self._timer.init(period=HOLD_PERIOD_MS, mode=cls.PERIODIC, callback=lambda _t: wdt.feed())
