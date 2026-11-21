# プロジェクト: Raspberry Pi Pico 2 上の MicroPython アプリ

このファイルは Claude Code が毎回読む作業ルールです。人間が読んでも分かるように書いています。
説明(手順の背景、ディレクトリ構成、テストやデバッグの詳細)は [docs/development.md](docs/development.md) にあります。ここには毎回守る規則と要点だけを置きます。
利用者向けの説明は README.md(日本語)と README.en.md(英語)。未完成の si5351 モードは README に書かず、docs/development.md §1 に書く。

## ターゲット

- ボード: Raspberry Pi Pico 2 W(RP2350, Arm Cortex-M33 デュアルコア, SRAM 520KB, フラッシュ 4MB, 無線チップ CYW43439 を BLE に使う)
    - 無線チップが使う GP23/24/25/29 と ADC3 は使わない(Pico 2 でも同じ配線で動く。BLE は無効になる)
- ファームウェア: MicroPython 1.29 のカスタムビルド `RPI_PICO2_W_ROMFS2`(正確なバージョンは `python tools/dev.py info` で確認する)
    - ROMFS を 1MB 設定。標準のライブラリに加えて micropython-lib から `sdcard` と `datetime` を読み込みビルドしている
    - BTstack にペアリングとボンドの保存を足すパッチを当てている。ボード定義は `hw/RPI_PICO2_W_ROMFS2/`、パッチとビルド手順は `hw/micropython/`(README.md)。ビルドは WSL のツリーで行い、書き込み(BOOTSEL)はユーザーが行う
    - ROMFS が有効であるため、必要ならばライブラリなどをROMFSへDeployし高速化を図ってください
- オンボード LED は `Pin("LED", Pin.OUT)`(Pico 2 W では無線チップ経由。GP25 ではない)。基板の状態表示 LED は GP7
- PC とは USB シリアルで接続。操作は全て `tools/dev.py`(mpremote のラッパー)経由
- 自局のコールサインをソース、テスト、サンプルに書かない。開発用の設定は git 管理外の `src/config_local.py` に書く(`tools/config_local.example.py` を参照)。テストと例示には架空の値(`Q1TEST` など)を使い、架空であることを明記する
- 設定の優先順位: `src/config.py`(既定値)< `src/config_local.py` < デバイスの `/settings.json`(BLE の `settings.set` で保存。電源を切っても残る)。スマホから変えられる項目は docs/ble-protocol.md §9

## 動作モード(`config.MODE`、`src/config_local.py` で上書き)

- `"tnc"`(既定): 無線機の外付け TNC。受信は GP26 の音声(ADC)、送信は GP3 の音声(PWM)と PTT(GP2)。送信ゲート(`hw.txctl.tnc_gate`)は周波数を見ず、コールサインと `TX_ENABLED` がそろったときだけ送信する
- `"si5351"`: **未完成**。`src/transceiver.py` が LCD に「NOT COMPLETE」と出して待つだけ(PTT は切ったまま、Si5351 にも触れない)。関係するコードは docs/development.md §1
- 切り替えは電源投入時の設定のみ。BLE(`config.BLE_ENABLED`)は `"tnc"` モードだけで動く

## ディレクトリ構成(詳細は docs/development.md §3)

- `src/` … デバイスに載るコード。`main.py` が `station.main()` を呼ぶ。`station.py` が局(tnc モード)の本体
    - `src/core/` … ハードに依存しない処理。`src/hw/` … ドライバ。`src/modem/`、`src/ui/`、`src/aprs/`
    - `src/tnc/`、`src/maxm10/`、`src/micropyGPS.py` … **生成物。手で編集しない**(下記)
    - `src/*_demo.py`、`src/*_test.py`、`src/m10_check.py` など … 確認用スクリプト(`release` では除く。dev.py の `RELEASE_EXCLUDE`)
    - `src/` には、デバイスに要らないファイル(ドキュメントなど)を置かない。ROMFS のイメージに丸ごと入る
- `tests/` … テスト(`test_*.py` の `test_*` 関数)。実機専用は `test_hw_*`(PC ではスキップ)。モジュールの `SKIP = "理由"` で飛ばす(今は `test_si5351`)
- `tools/dev.py` … ビルド・実行・デバッグ用 CLI。`tools/vendor.py` … 生成物を作る。`tools/ble_cli.py` … BLE の検証。`tools/afsk_bench.py` … 復調器のベンチ
- `docs/` … 設計メモ。`third_party/` … サブモジュール。`typings/` … 型スタブ(自動生成、編集しない)

## サブモジュール由来のコード(詳細は docs/development.md §7)

- 生成物の変更は必ず `third_party/` のサブモジュール側で行い、そこでコミットしてから `python tools/vendor.py` で再生成する。試行錯誤中は `--dirty`
- 派生ファイルの先頭には出典(上流のファイルとコミット)と変更点を書く
- フォークへの push はユーザーが行う(本体より先にフォークを push する)

## 絶対に守ること

1. **デバイス操作は必ず `python tools/dev.py <cmd>` を使う。** `mpremote` を直接、特に引数なしや `repl` で起動しない(対話モードに入って固まる)。`dev.py repl` も使わない。
2. **実行には必ずタイムアウトを付ける**(`-t 秒`)。無限ループのプログラムがタイムアウトで終わるのは正常(終了コード 124)。`run` / `exec` / `test` は、時間切れになると Ctrl-C を送ってデバイス上のプログラムを止めてから終わる(止められなければ終了コード 125)。
3. **`deploy` / `romfs-deploy` / `release`(フラッシュへの書き込み)、`rm-main`、`mip` はユーザーに確認してから。** 開発中の動作確認は `run` / `test` で十分(フラッシュに書かずに PC 上の `src/` をマウントして実行する)。
4. **物理的な結果は見えない。** LED・モーター・センサー値の実際の振る舞いは、print 出力で確かめるか、ユーザーに目視確認を頼む。推測で「動いた」と報告しない。
5. 配線を変えるような指示を出すときは、ピン番号と接続先を明記し、電源(3.3V 系、GPIO は 5V 非耐性)に注意を促す。

## PTT の扱い

- PTT(GP2)は `core/txgate.py` のゲートだけが操作する。テストとデモは既定で PTT を入れない。
- 実機テスト(`dev.py test`、`check`、`build`、`release`)は偽のピンを使い、本物の PTT を駆動しない。
- 本物の PTT を入れるデモは `src/tx_demo.py` だけで、`config.BENCH_TX_HZ` の明示が必要。

## 開発ループ(コマンドの一覧と書き込み方式は docs/development.md §4)

```
python tools/dev.py lint              # 1. vendor --check + ruff + pyright(ホスト側、デバイス不要)
python tools/dev.py test --host       # 2a. PC(CPython)上で tests/ を実行
python tools/dev.py test              # 2b. デバイス上で tests/ を実行
python tools/dev.py run -t 15         # 3. src/main.py を実行して出力を見る
python tools/dev.py run src/xxx.py -t 10   # 個別スクリプトの実行
python tools/dev.py exec "import config; print(config.MODE)" -t 10   # 対話の代わりのコード片実行
python tools/dev.py check             # lint + test --host + test をまとめて
python tools/afsk_bench.py -n 200     # 復調器のベンチ(PC のみ、数分。モデムを変えたら回す)
python tools/dev.py build             # check のあと ROMFS イメージを作る(書き込みなし)
python tools/dev.py release           # build + ROMFS へ書き込み + 起動の確認(要確認)
```

- 変更後は少なくとも `lint` と `test` を通してから完了を報告する。
- 書き込んだ本体を動かすときは `dev.py exec --no-mount "..."`。ROMFS 版のトレースバックは `src/xxx.py`(`_romfs_main` は `src/main.py`)と出る。

## デバッグで守ること(手順の詳細は docs/development.md §8)

- **トレースバック**の `/remote/` はマウントされた `src/` のこと。状態を覗くのは `dev.py exec "<コード>"`(REPL の代わり)。
- **マウント中に PC 側を切らない**: mpremote の途中終了でデバイスが固まる(「could not enter raw repl」)。局の起動中は PC の src/ から import を続ける(約 23 秒)。dev.py の `run` / `exec` / `test` を使えば切らずに止める。
- **固まったとき**: `boot.py` の `wdt_bridge` がフィードし続けるので自然には戻らない。USB の抜き差しを頼む。
- **リセットを伴う試験**は、書き込み後に `dev.py exec --no-mount` で行う(リセットするとマウントが切れる)。
- **ウォッチドッグ**: 局はマウント中の `run/exec` 以外で有効にする(起動中 16 秒、ループ 3 秒)。止められない。書き込み済みの局を触るときは、基板が再起動してもよい状態にしておく。
- **局を止めたあと**: dev.py で接続すると局は止まったまま(LCD も消える)。作業を終えるときは `dev.py monitor --reset -s 20` で局を起動し直す。
- **応答しない / 起動ループ**: まず `dev.py rm-main`(要確認)。それでも駄目ならユーザーに USB の抜き差しを頼む。最終手段は BOOTSEL でファームウェア再書き込み(ユーザー作業)。

## BLE(詳細は docs/development.md §9)

- 仕様は docs/ble-protocol.md(Web アプリとの取り決め。変えるときはまずここを直す)、本機の作りは docs/ble-control.md
- ボンドの鍵はデバイスの `/ble_bonds.json`(LTK を含む。**中身をそのまま表示したり外へ出したりしない**)。消すのは USB から、BLE を止めた状態で `Ble.forget_all()`
- `ble_cli.py send` は電波が出るので `tx_enabled` を確かめてから
- Windows の開発用 PC では通知が途中で止まる(本機は正常)。BLE の動作確認は Android(nRF Connect)で行う
- nRF Connect(Android 版)は TEXT でしか Write できない。改行を付けられないので、行の終わりが要る確認は `ble_cli.py` などで行う
- `bluetooth.irq` のハンドラの中の `print` は失われることがある。IRQ では記録だけして、ループで表示する

## MicroPython で書くときの注意(一覧は docs/development.md §10)

- 時間は `time.ticks_ms()` / `ticks_diff()` / `ticks_add()`(引き算しない)。割り込みハンドラの中ではメモリを確保しない。
- `@micropython.viper` のポインタは**境界検査をしない**。書き込み先の大きさを必ず自前で守る(溢れるとメモリ破壊)。`ptr16`/`ptr32` の読み出しは符号なし。
- ハードウェア依存部分はクラスの引数として渡し、テストでは偽オブジェクトに差し替えられるようにする(`tests/_fake_hw.py`)。

## 配線メモ(ユーザーが追記する)

正は `src/config.py` の先頭の説明と `PIN_*`(基板 `hw/Pico_MicroSD_M10_Board_rev1.net`)。配線を変えたら config.py を直し、この表も合わせる。

| 用途 | GPIO | 接続先 | 備考 |
|------|------|--------|------|
| GNSS UART0 | GP00 (TX) / GP01 (RX) | u-blox MAX-M10M | |
| PTT | GP02 | 無線機ボード J5-1 | `core/txgate.py` のゲートだけが操作する |
| オーディオ出力 | GP03 | 出力アナログフロントエンド(J5-2) | PWM |
| I2C0 | GP04 (SDA) / GP05 (SCL) | 無線機ボード J5 | 将来の Si5351 用 |
| GNSS RESET_N | GP06 | MAX-M10M | 通常は駆動しない |
| 状態表示 LED | GP07 | 赤 LED | |
| FM IF 入力 | GP08 | (未接続) | si5351 モード用 |
| microSD | GP09 (検出) / GP10–12, GP15 (SPI) | microSD | |
| LCD ST7789 | GP16–20 | LCD | DC / CS / SCK / MOSI / BL |
| ビーコンスイッチ | GP21 | 外部スイッチ J1 | 内部プルアップ。GND に落とすとオン |
| GNSS PPS | GP22 | MAX-M10M | 駆動しない |
| オーディオ入力 | GP26 (ADC0) | 入力アナログフロントエンド | |
| 電源電圧の監視 | GP27 (ADC1) | 分圧抵抗 | rev1 では未実装 |
| オンボード LED | `"LED"` | 無線チップ経由 | Pico 2 W では GP25 ではない |
