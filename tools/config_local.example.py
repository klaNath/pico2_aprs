"""ローカル設定の例(仮: 電源を切っても残る設定の保存ができるまでの方法)。

src/config_local.py にコピーして編集する。src/config_local.py は git 管理外で、
src/config.py の値を上書きする。release のイメージにも入る。

下の値は架空の例。外付け TNC モード(既定)は周波数を見ないので、コールサインを設定して
TX_ENABLED を True にすると、ビーコンスイッチがオンで測位があれば無線機へ送信する(PTT を入れる)。
電波を出すのはダミーロードをつないで、オーナーが判断したときだけ。
Si5351 モード(MODE = "si5351")は未完成で、何も送信しない。
"""

# CALLSIGN = "Q1TEST"  # 架空の例。SSID を含めない
# SSID = 7
# TX_ENABLED = False
# MODE = "tnc"  # "tnc" = 外付け TNC(既定)/ "si5351" = トランシーバー(未完成。表示だけで何も動かない)
