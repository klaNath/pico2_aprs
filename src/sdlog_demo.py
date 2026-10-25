"""microSD への記録の確認: GNSS の NMEA(RMC と GGA)を記録し、終わったらファイルの中身を表示する。

    python tools/dev.py run src/sdlog_demo.py -t 60

35 秒動いて止まる。/sd/log/ に新しい番号の 1 組(NNNN_aprs.txt と NNNN_nmea.txt)を作る。
受信ログ(aprs)は無線機をつなぐまでは見出しの行だけになる。終わる前に書き残しを書いて閉じる。
"""

import os
import time

import config
from hw.m10 import M10
from hw.sdlog import LOG_DIR, SdLog

DURATION_MS = 35_000


def main():
    own = config.CALLSIGN if config.SSID == 0 else "%s-%d" % (config.CALLSIGN, config.SSID)
    sd = SdLog(config.SD_SPI, config.PIN_SD_SCK, config.PIN_SD_MOSI, config.PIN_SD_MISO,
               config.PIN_SD_CS, config.PIN_SD_DET, header=own, log=print)
    gnss = M10(config.GNSS_UART, config.PIN_GNSS_TX, config.PIN_GNSS_RX,
               resetn=config.PIN_GNSS_RESETN, pps=config.PIN_GNSS_PPS, baud=config.GNSS_BAUD)
    gnss.on_nmea = sd.nmea
    gnss.begin()
    print("card inserted:", sd.inserted())
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < DURATION_MS:
        gnss.poll()
        sd.poll(time.time() if gnss.rtc.synced else None)
        time.sleep_ms(50)
    seq = sd.logger.seq
    if seq is None:
        print("not logging:", sd.state)
        return
    sd.logger.flush()
    print("state:", sd.state, "lines:", sd.logger.lines, "dropped:", sd.logger.dropped)
    for kind in ("aprs", "nmea"):
        path = "%s/%04d_%s.txt" % (LOG_DIR, seq, kind)
        print("----", path, os.stat(path)[6], "bytes")
        with open(path) as f:
            for i, line in enumerate(f):
                if i < 4:
                    print(line.rstrip())
    print("files:", sorted(os.listdir(LOG_DIR))[-4:])
    sd.close()


main()
