"""PC 上のテスト用の偽のハードウェアモジュール(machine、rp2 など)。実機では何もしない。

station.py や hw.afsk_tx を PC で import して、PTT の扱いを確かめるために使う。
偽の Pin は書き込みを Pin.history に残す。
"""

import sys


class Pin:
    IN = 0
    OUT = 1
    PULL_UP = 2
    PULL_DOWN = 3
    state = {}
    history = {}

    def __init__(self, pin, mode=None, pull=None, value=None):
        self.id = pin
        if pull == Pin.PULL_UP:
            Pin.state.setdefault(pin, 1)  # 開放のときの値
        if value is not None:
            self.value(value)

    def value(self, v=None):
        if v is None:
            return Pin.state.get(self.id, 0)
        Pin.state[self.id] = v
        Pin.history.setdefault(self.id, []).append(v)
        return None

    def __call__(self, v=None):
        return self.value(v)

    @classmethod
    def reset(cls):
        cls.state.clear()
        cls.history.clear()


class Any:
    """何を呼んでも何も起きない部品(SPI、UART など)"""

    def __init__(self, *a, **k):
        pass

    def __getattr__(self, name):
        return lambda *a, **k: None


class Mem32(dict):
    def __missing__(self, addr):
        return 1023  # PWM の TOP として読まれる値(config.PWM_TOP)


class DMA:
    """active(1) のあと、stuck でなければ polls 回の問い合わせで終わる"""

    stuck = False
    polls = 2

    def __init__(self):
        self._left = 0
        self.write = 0

    def config(self, **k):
        pass

    def pack_ctrl(self, **k):
        return 0

    def active(self, v=None):
        if v is not None:
            self._left = DMA.polls if v else 0
            return None
        if DMA.stuck:
            return self._left > 0
        self._left = max(0, self._left - 1)
        return self._left > 0

    def close(self):
        pass


_objs = {}


def _addressof(x):
    _objs[id(x)] = x
    return id(x)


def _struct(addr, desc):
    import types

    return types.SimpleNamespace(v=memoryview(_objs[addr]).cast("H"))


class WDT:
    """偽のウォッチドッグ。作られた順に instances へ残す(有効にした時間とフィードの回数を調べる)"""

    instances = []

    def __init__(self, id=0, timeout=5000):
        self.timeout = timeout
        self.feeds = 0
        WDT.instances.append(self)

    def feed(self):
        self.feeds += 1


class Timer:
    PERIODIC = 1
    instances = []

    def __init__(self, id=-1):
        self.period = None
        self.callback = None
        Timer.instances.append(self)

    def init(self, period=0, mode=1, callback=None):
        self.period, self.callback = period, callback


def install():
    """偽のモジュールを登録する。実機では登録せず False を返す(呼び出し側はテストを飛ばす)"""
    if sys.implementation.name == "micropython":
        return False
    if "machine" in sys.modules:
        return True
    import types  # MicroPython には無いので、実機で読み込まれないようにここで

    machine = types.ModuleType("machine")
    machine.Pin = Pin
    machine.PWM = Any
    machine.SPI = Any
    machine.UART = Any
    machine.ADC = lambda *a: types.SimpleNamespace(read_u16=lambda: 0)
    machine.RTC = Any
    machine.mem32 = Mem32()
    machine.freq = lambda: 150_000_000
    machine.reset = lambda: None
    # reset_cause: 1 = 電源投入
    machine.__dict__.update(WDT=WDT, Timer=Timer, WDT_RESET=3, reset_cause=lambda: 1)
    sys.modules["machine"] = machine

    rp2 = types.ModuleType("rp2")
    rp2.DMA = DMA
    rp2.DMATimer = Any
    rp2.PIO = Any
    rp2.StateMachine = Any
    rp2.asm_pio = lambda **k: (lambda f: f)
    sys.modules["rp2"] = rp2

    uctypes = types.ModuleType("uctypes")
    uctypes.ARRAY = 0
    uctypes.UINT16 = 0
    uctypes.addressof = _addressof
    uctypes.struct = _struct
    sys.modules["uctypes"] = uctypes

    for name in ("framebuf", "sdcard", "vfs"):
        m = types.ModuleType(name)
        m.FrameBuffer = Any
        m.RGB565 = 1
        m.SDCard = Any
        m.VfsFat = Any
        m.mount = m.umount = lambda *a: None
        sys.modules[name] = m
    return True
