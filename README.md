# pico2_aprs

Raspberry Pi Pico 2 上の MicroPython で動く、TNC 内蔵の APRS トラッカーのファームウェアです。144 MHz 帯の FM 無線機につなぎ、電源を入れると自動で次のことを行います。

- APRS パケット(AX.25、1200 bps AFSK)の受信と、局の一覧・距離・方位の表示
- GNSS(u-blox MAX-M10M)による自局位置の取得と、Mic-E 形式のビーコン送出
- ST7789 液晶への表示(局の一覧、最新局の詳細、自局の状態、地図、衛星の強度)
- microSD への受信ログと NMEA ログの記録

```
 無線機(受信音声 / 455 kHz FM IF)──► Pico 2 ──► 液晶(ST7789)
 GNSS(MAX-M10M)─────────UART0────►   │  ├──► microSD(SPI1)
 ビーコンスイッチ ───────GP21─────►   │  └──► 状態 LED
                                      └──► 送信音声(PWM)/ PTT ──► 無線機
```

**現在の状態**: 対応している動作は、この自動で動く TNC 内蔵トラッカーだけです。ただし、送信周波数を確定する部分が局にまだ組み込まれていないため、局本体は設定にかかわらず電波を出しません(→ [送信の仕組み](#送信の仕組み))。受信、GNSS、表示、SD への記録は動きます。

---

## 1. 必要なもの

### ハードウェア

| 部品 | 内容 |
|------|------|
| ボード | Raspberry Pi Pico 2(RP2350)または Pico 2 W。無線チップが使う GP23/24/25/29 と ADC3 は使っていないので、W でも同じ配線で使えます |
| 無線機 | 144 MHz 帯 FM。受信音声(または 455 kHz の FM IF)を Pico に入れ、送信音声と PTT を Pico から出す |
| GNSS | u-blox MAX-M10M(UART0、115200 bps) |
| 表示 | ST7789 液晶。既定は 2.4 インチ 240x320(横長 320x240)。Pimoroni Pico Display Pack(240x135、ボタン付き)にも対応 |
| 記録 | microSD カードとカード検出スイッチ |
| スイッチ | ビーコン送出のオン/オフ(外部スイッチ。GP21 を GND に落とすとオン) |
| PC との接続 | **データ通信対応の** USB ケーブル |

試作基板の回路(開発途中の rev1)は `hw/Pico_MicroSD_M10_Board_rev1.net` にあります。

### ピン割り当て

すべて [src/config.py](src/config.py) で決めています。配線を変えるときはそこだけ直します。**GPIO は 5 V 非耐性**です。

| GPIO | 用途 |
|------|------|
| GP0 / GP1 | GNSS との UART0(TX / RX) |
| GP2 | PTT(出力。High で送信) |
| GP3 | AFSK 送信音声(PWM) |
| GP4 / GP5 | I2C0(SDA / SCL。無線機ボードへの接続用。現在の局本体は使わない) |
| GP6 | GNSS RESET_N(通常は駆動しない) |
| GP7 | 状態 LED(送信中は点灯、受信で 0.1 秒点灯) |
| GP8 | 455 kHz FM IF の入力(`RX_SOURCE = "if"` のとき) |
| GP9 | microSD カード検出 |
| GP10〜12, GP15 | microSD(SPI1: SCK / MOSI / MISO / CS) |
| GP16〜20 | 液晶(SPI0: DC / CS / SCK / MOSI、バックライト) |
| GP21 | ビーコンスイッチ(内部プルアップ。GND でオン、開放・断線はオフ) |
| GP22 | GNSS の PPS(駆動しない) |
| GP26 | 受信音声の ADC 入力(`RX_SOURCE = "adc"` のとき) |

### ファームウェア

この局は、次のようにビルドした MicroPython で動かします。

- 標準のライブラリに加えて、micropython-lib の `sdcard` と `datetime` を組み込む
- フラッシュに 1 MB の ROMFS 区画を取る(`python tools/dev.py romfs-query` で確認できます)

動作を確認したバージョンは MicroPython v1.29.0(Pico 2 W)です。`mpy-cross` も 1.29 系列に合わせています。

標準の .uf2 のままでは、microSD の記録が使えず、ROMFS への書き込みもできません。

### PC 側

- Python 3.9 以上
- `mpremote`、`mpy-cross`、`ruff`、`pyright`([requirements-dev.txt](requirements-dev.txt))。`tools/bootstrap.py` が `.venv` を作って入れます
- Linux ではシリアルポートの権限が必要です(`sudo usermod -aG dialout $USER` のあと再ログイン)

---

## 2. セットアップ

```bash
git clone --recurse-submodules <このリポジトリ>
cd pico2_aprs
python3 tools/bootstrap.py        # Windows は py tools\bootstrap.py
```

Pico 2 W なら `--board rpi_pico2_w`、ファームウェアのバージョンに合わせるなら `--mp-version 1.28.0` のように指定します。終わったら `.venv` を有効にします(VSCode なら「Python: Select Interpreter」)。

### 自局の設定

コールサインなどの設定は、git 管理外の `src/config_local.py` に書きます。`src/config.py` の値を上書きします。

```bash
cp tools/config_local.example.py src/config_local.py
```

```python
# src/config_local.py(ここに書く値は架空の例)
CALLSIGN = "Q1TEST"   # 架空の例。自分のコールサインに置き換える。SSID は含めない
SSID = 7
TX_ENABLED = False    # 送信を許可するか。無線機の接続を確かめるまで False
```

コールサインを未設定のままでも、受信・表示・記録は動きます(画面には `NOCALL` と出ます)。設定は電源を切っても残りますが、`release` で ROMFS に入れる場合は、書き込む前にこのファイルを作っておく必要があります。

### 動作確認

```bash
python tools/dev.py ports         # Pico が見えるか
python tools/dev.py info          # MicroPython のバージョン・空きメモリ
python tools/dev.py lint          # ruff + pyright + 生成物の確認
python tools/dev.py test --host   # PC(CPython)上でテスト
python tools/dev.py test          # 実機でテスト → 最後に RESULT: OK
```

テストは偽のピンを使うので、PTT は駆動されません。

---

## 3. 局を動かす

### 動作確認(フラッシュに書かない)

```bash
python tools/dev.py run -t 60     # src/main.py を 60 秒実行して出力を見る
```

`run` / `exec` / `test` は PC の `src/` を Pico にマウントして実行するので、フラッシュを書き換えません。

起動すると、次のような行が USB に出ます。

```
[station] NOCALL-7 started, RX ADC GP26, TX refused: ..., free 12345
[rx] SRC>DST,PATH:...                     受信したパケット
[tx dry] 700 ms SRC>DST,PATH:...          送るはずだったパケット(実際には出していない)
[station] fix 3D sats 12/30 rx 4 tx 1 bcn on next 540 sd log 0001 free 12345 lag 3     10 秒ごと
```

### 電源を入れるだけで動かす

```bash
python tools/dev.py release       # 検査 → ROMFS へ書き込み → 起動の確認
```

`release` は、`check`(lint とテスト)を通し、デモ・試験用のスクリプトを除いた ROMFS イメージを作り、書き込んで、再起動後に `[station] ... started,` が出るのを待ちます。書き込んだ本体は `dev.py exec --no-mount "..."` で操作します。

ROMFS は読み出し専用のイメージで、`.mpy` にコンパイルしたコードをフラッシュから直接実行します。`.py` のまま使うより import が速く、RAM の消費も小さくなります。フラッシュには `/rom` の本体を呼ぶ `main.py` と、起動直後に PTT(GP2)を Low にする `boot.py` が残ります。

`.py` のままフラッシュに置くなら `dev.py deploy` です(`main.py` が上書きされ、ROMFS 版から戻ります)。

書き込みを伴う `deploy` / `romfs-deploy` / `release` / `rm-main` / `mip` は、実行前に内容を確かめてください。

### 異常時の動作

**ウォッチドッグ**: ファームウェアが止まったときのために、RP2350 のウォッチドッグを使います。起動中(import、GNSS の探索、SD のマウント)は 16 秒、ループに入ってからは 3 秒で、局のループが毎回フィードします。送信の待ちのループではフィードしません(止まった DMA を見つけるため)。ウォッチドッグのリセットの後は、最初のビーコンを 90 秒待ちます。

- `dev.py run` / `exec` で PC の `src/` をマウントして動かしているときは有効にしません(リセットでデバイスが応答しなくなるため)。有効になるのは、`deploy` / `release` で書き込んだ局の起動と、`exec --no-mount` で `station.main()` を呼んだときです。
- 有効にしたウォッチドッグは止められません。USB から Ctrl-C で局を止めたときは、タイマーがフィードを続けるので、REPL で作業できます。`mpremote` は接続のたびにソフトリセットしますが、`boot.py` がフィードを引き継ぐので、書き込み済みの局に `dev.py` で接続しても約 16 秒で再起動することはありません。

**例外**: 局が例外で止まると、内容を液晶・USB・SD に出し、待ってからハードリセットします。起動から 60 秒以内の失敗が続くと、待ち時間を 10、20、40 … 秒と延ばします(最大 300 秒)。失敗の回数はウォッチドッグのスクラッチレジスタに置くので、電源を切ると消えます。異常で再起動した直後は、最初のビーコンを 90 秒待ちます。

USB から Ctrl-C で止めたときは再起動しません。

---

## 4. 動作

対応しているのは、自動で動く TNC 内蔵トラッカーとしての動作だけです。電源を入れると、次を 1 つのループで同時に行います。操作は必要ありません。

1. 受信: 無線機の音声を復調して AX.25 のフレームに復号し、局の表と画面、SD の記録に渡す
2. 測位: GNSS から位置と時刻を得て、画面に出す。有効な時刻で RTC を UTC に合わせる(最初の 1 回と、その後 1 時間ごと)
3. ビーコン: ビーコンスイッチがオンで、測位と時刻がそろったら、Mic-E のビーコンを決まった間隔で送る
4. 表示と記録: 液晶の更新と、microSD への記録

設定で変えられる部分は、次のとおりです。

### 受信の入力(`config.RX_SOURCE`)

入力の取り方は設定で選びます。どちらでも、動作の流れは同じです。

| 値 | 入力 | 内容 |
|----|------|------|
| `"adc"`(既定) | GP26 | 無線機の復調音声を ADC で 19.2 kHz に取り、DMA でリングに溜め、Core1 で復調します。画面に受信音量(縦棒と局ごとの LVL)が出ます |
| `"if"` | GP8 | 455 kHz FM IF(リミッタを通した 0〜3.3 V の方形波)を PIO でエッジの時刻から復調します。受信音量は出ません |

`"if"` のときの周波数の求め方は `config.RX_IF_METHOD` で選びます。

- `"pll"`(既定): DPLL。弱い信号で乱れたエッジに強い
- `"count"`: 周期の逆数計数

どちらも、雑音向けとハム向けの 2 つの復調器とデフレーマで復号します。

### 送信

送るのは自局の位置ビーコン(Mic-E)だけです。経路、シンボル、メッセージ、コメントは `config.py` の `TX_PATH`、`SYMBOL`、`BEACON_MESSAGE`、`BEACON_COMMENT` で決めます。

**ビーコンスイッチ(GP21)**

| 状態 | 動作 |
|------|------|
| GND に落とす(オン) | 2D 以上の測位と GNSS の時刻がそろうと、最初の 1 回をすぐ送る |
| 開放・断線(オフ) | 送らない。オフにすると前回の記録を消し、次にオンにしたときにまたすぐ送る |

**送出間隔**

| 条件 | 間隔 |
|------|------|
| 前回の位置から `BEACON_MOVE_M`(10 m)以上動いている | `BEACON_MOVING_S`(90 秒)ごと |
| 動いていない | `BEACON_STILL_S`(600 秒)ごと |

**実際に電波を出すか(送信の仕組み)**

送信は [src/core/txgate.py](src/core/txgate.py) のゲートだけが PTT(GP2)を操作します。次の条件がすべてそろったときだけ PTT を入れ、1 つでも欠けると「ドライラン」になります。

1. コールサインが正しい形式で設定されている(`CALLSIGN`)
2. `TX_ENABLED = True`
3. 送信周波数が確定していて、144〜146 MHz の範囲に入っている
4. 周波数源の準備ができている

ドライランでは、ビーコンの時機の管理、画面、SD の記録(`TX(dry)` の印)は本番と同じに動き、PTT と送信音声だけを出しません。拒否の理由は USB と SD に 1 回ずつ記録します。

> **今は、送信周波数を確定する部分が局に組み込まれていないため、周波数は常に「未確定」で、`TX_ENABLED = True` にしても局本体は送信しません。** 電波を出せるのは、周波数を明示する `tx_demo.py`(下の表)だけです。

送信の時間は、1 回あたり「波形の長さ + 200 ms」と 5 秒の短い方を上限にしています。止め方、PTT の後始末、ウォッチドッグの設計は [docs/tx-time-limit.md](docs/tx-time-limit.md) にあります。

送信を伴う試験は、**ダミーロードにつないで**行ってください。空中への送信は、免許と運用の条件を満たしたうえで、自分で判断して行います。

### 画面

| 画面 | 内容 |
|------|------|
| 320x240(既定。`LCD_PANEL = "st7789_320x240"`) | 上段に局の一覧と最新局の詳細(コールサイン、種別、距離・方位、位置、コメント)。下段に自局の状態(測位、衛星数、時刻、位置、ビーコンの次回、送受信数)、衛星の強度の棒グラフ、小さな地図。地図は局の分布と衛星の空図を自動で切り替えます |
| 240x135(`LCD_PANEL = "pico_display"`) | 左に一覧、右上に詳細、右下に自局の状態。地図は X ボタンで全画面に切り替えます |

右上の丸は測位状態です(1 Hz で点滅)。自局の状態の欄には、SD の状態(`SD:LOG` / `SD:WAIT` / `SD:ERR` / `SD:---`)と、送信中の `[TX]`、受信直後の `[RX]` が出ます。

ボタンは 240x135 の構成だけにあります(`config.BUTTONS` に GPIO 番号を `(A, B, X, Y)` の順で書きます)。

| ボタン | 動作 |
|--------|------|
| A / B | 一覧の選択を 1 つ上(新しい側)/ 下(古い側)へ |
| X | 地図とメイン画面の切り替え |
| Y | 選択を解除して、最新の局を自動表示に戻す |

### microSD の記録

カードを挿すと、GNSS の時刻が分かってから(最大 20 秒待つ)`/sd/log/` に通し番号の 1 組のファイルを作ります。

| ファイル | 内容 |
|----------|------|
| `NNNN_aprs.txt` | 受信パケット(TNC2 形式)。自局の送信は `TX `、ドライランは `TX(dry) ` を付ける |
| `NNNN_nmea.txt` | NMEA の RMC と GGA |

行は 10 秒ごとにまとめて書きます(電源を切ると最大 10 秒分を失う)。カードを抜くとファイルを閉じ、挿し直すと新しい番号で作ります。書き込みに失敗したら 5 秒後にやり直します。

### デモ・試験用スクリプト

`src/` には、局本体とは別に、部品ごとの確認用スクリプトがあります。`python tools/dev.py run src/<名前>.py -t <秒>` で実行します。`release` の ROMFS には入りません。

| スクリプト | 内容 |
|------------|------|
| `tx_demo.py` | 試験ビーコンを GP3 から 5 秒おきに 12 回出す。**既定では PTT を入れず、音声だけ**。PC のライン入力につないで Dire Wolf などで復号を確かめる。`config.BENCH_TX_HZ` に周波数を明示したときだけ PTT を入れる(電波が出る) |
| `rx_loopback.py` | 送信音声を受信側に戻して、送ったフレームが復号できるかを数える |
| `ui_demo.py` | 試験用の局データで画面を表示する(無線機・GNSS 不要。局はすべて架空) |
| `gnss_demo.py`、`m10_check.py` | MAX-M10M の通信と測位の確認 |
| `sdlog_demo.py` | microSD への記録の確認 |
| `lcd_test.py` | 液晶の向き・色・オフセットの確認 |
| `fm_if_test.py`、`fm_if_afsk_test.py`、`fm_if_rt_test.py`、`fm_if_pll_test.py` | 455 kHz IF の FM 復調の測定と比較(信号発生器が要るものもある) |
| `gt505_demo.py` | 旧 GNSS モジュール(GT-505)の確認 |

---

## 5. 開発ツール(`tools/dev.py`)

PC 側で動く `mpremote` のラッパーです。実行にタイムアウトを付け、対話モードに入らないようにしてあります。`mpremote` を直接起動すると対話モードに入って固まることがあるので、デバイスの操作はこれを経由してください。

```bash
python tools/dev.py --help
```

| コマンド | 内容 |
|----------|------|
| `ports` / `info` | 接続中の Pico の列挙 / ファームウェア・メモリ・ストレージ |
| `run [FILE] -t N` | `src/` をマウントして実行(既定 `src/main.py`)。タイムアウトで終わると終了コード 124 |
| `exec "CODE" -t N` | コード片の実行(REPL の代わり)。書き込み済みの本体に対しては `--no-mount` |
| `test [PATTERN] [--host]` | `tests/test_*.py` の実行。`--host` は PC 上、省略すると実機。`test_hw_*` は実機専用 |
| `lint` | 生成物の確認(`vendor.py --check`)+ ruff + pyright |
| `check` | `lint` + `test --host` + `test` |
| `build` | `check` のあと、デモを除いた ROMFS イメージを作る(書き込みなし) |
| `release` | `build` + ROMFS への書き込み + 起動の確認 |
| `deploy [--reset]` | `src/` を `.py` のままフラッシュに書く |
| `romfs-query` / `romfs-build` / `romfs-deploy` | ROMFS 区画の確認 / イメージの作成 / 書き込み |
| `monitor -s N [--reset]` | シリアル出力を N 秒読む(起動時の挙動の確認) |
| `ls` / `cat` / `rm-main` / `mip` / `reset` | デバイス上のファイル操作など |
| `repl` | 人間用の対話 REPL |

複数台つないでいるときは、環境変数 `PICO_PORT` でポートを指定します。

そのほかのツール:

- `python tools/afsk_bench.py -n 200` … 復調器を劣化条件(雑音、ハム、周波数ずれなど)で試すベンチ。PC 上で数分かかります。モデムを変えたら回します
- `python tools/vendor.py` … サブモジュールのコードを `src/` に生成します(次節)

### トラブルシュート

- **接続できない**: MicroPico、Thonny、シリアルモニタなど、ほかのツールがポートを掴んでいないか確認します。`dev.py ports` で検出の状況を見られます
- **トレースバックの `File "/remote/station.py"`**: `/remote/` はマウントした `src/` のことです(`/remote/station.py` なら `src/station.py`)。ROMFS から動かしているときは元のパス(`src/xxx.py`)が出るので、手元のソースの該当行を見ます
- **マウント中のリセット**: `dev.py run/exec` で動かしたプログラムが `machine.reset()` までいくと、その後デバイスが応答しなくなります(USB の抜き差しで戻ります)。再起動を伴う試験は、書き込んだあとに `dev.py exec --no-mount` で行います
- **起動ループで応答しない**: まず `dev.py rm-main`。それでも駄目なら USB を抜き差しします。最後の手段は BOOTSEL ボタンを押しながら USB を挿し、ファームウェアを書き直すことです
- **メモリ不足(`MemoryError`)**: 送信波形のバッファ(57.6 KB)など大きな領域を、`station.py` の読み込みの最初に確保しています。ほかのモジュールを先に読み込むとヒープが細切れになって確保に失敗することがあります

---

## 6. ファイル構成

```
.
├── src/                    デバイスに載るコード
│   ├── boot.py             起動直後に PTT を Low にし、ウォッチドッグのフィードを引き継ぐ
│   ├── main.py             起動時のエントリポイント(station.main())
│   ├── station.py          局の本体(受信・ビーコン・GNSS・画面・SD を 1 つのループで回す)
│   ├── config.py           ピン割り当てと動作設定
│   ├── core/               ハードに依存しない処理(ビーコンの時機、送信ゲート、局の表、距離計算、ログ)
│   ├── hw/                 ハード依存の処理(AFSK の送受信、IF 復調、GNSS、液晶、SD、ウォッチドッグ)
│   ├── aprs/               APRS の解析と Mic-E のエンコード
│   ├── modem/              AFSK 復調器
│   ├── ui/                 画面の描画とボタン操作
│   ├── tnc/                [生成物] micro-aprs 由来の AX.25 / HDLC / AFSK 変調
│   ├── maxm10/             [生成物] MAX-M10 のドライバ
│   ├── micropyGPS.py       [生成物] NMEA パーサ
│   └── *_demo.py, *_test.py  部品ごとの確認用スクリプト(上の表)
├── tests/                  テスト(_runner.py、test_*.py)。実機でも PC でも動く
├── tools/
│   ├── dev.py              ビルド・書き込み・実行用の CLI
│   ├── vendor.py           third_party/ から src/ の生成物を作る
│   ├── bootstrap.py        開発環境の初回セットアップ
│   ├── afsk_bench.py       復調器のベンチ
│   └── config_local.example.py  自局設定の例
├── docs/tx-time-limit.md   送信時間の制限と PTT の後始末の設計
├── hw/                     試作基板のネットリスト
├── third_party/            サブモジュール(下記)
├── typings/                MicroPython の型スタブ(bootstrap.py が生成)
└── LICENSE                 GPL-3.0
```

## 7. サブモジュールとライセンス

`src/` の次のファイルは、`third_party/` のサブモジュールから `tools/vendor.py` が生成したものです。**手で編集しないでください**。変更はサブモジュール側でコミットしてから `python tools/vendor.py` で再生成します(試行錯誤中は `--dirty` で未コミットの変更も取り込めます)。各ファイルの先頭に出典のリポジトリとコミットが入ります。

| 生成先 | サブモジュール | ライセンス |
|--------|----------------|------------|
| `src/tnc/` | [klaNath/micro-aprs](https://github.com/klaNath/micro-aprs) の `pico2-subset` ブランチ(本家は [stephanelsmith/micro-aprs](https://github.com/stephanelsmith/micro-aprs) のフォーク) | GPL-3.0 |
| `src/maxm10/` | [klaNath/micropython-maxm10](https://github.com/klaNath/micropython-maxm10) の `pico2-aprs` ブランチ | MIT |
| `src/micropyGPS.py` | [klaNath/micropyGPS](https://github.com/klaNath/micropyGPS) の `pico2-aprs` ブランチ | MIT |

micro-aprs を含むため、このプロジェクト全体のライセンスは **GPL-3.0** です。
