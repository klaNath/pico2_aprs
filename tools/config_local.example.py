"""ローカル設定の例(仮: APP-05 の設定保存ができるまでの方法)。

src/config_local.py にコピーして編集する。src/config_local.py は git 管理外で、
src/config.py の値を上書きする。release のイメージにも入る。

下の値は架空の例。コールサインを設定して TX_ENABLED を True にしても、周波数が確定するまで
(Si5351 のドライバができるまで)局は送信しない。電波を出すのはダミーロードをつないで、
オーナーが判断したときだけ。
"""

# CALLSIGN = "Q1TEST"  # 架空の例。SSID を含めない
# SSID = 7
# TX_ENABLED = False
