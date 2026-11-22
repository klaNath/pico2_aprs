# pico2_aprs

[English](README.en.md)

Raspberry Pi Pico 2 W 上の MicroPython で動く、APRS の局のファームウェアです。144 MHz 帯の FM 無線機に外付けの TNC としてつなぎ、GNSS モジュールと液晶を合わせて、単体で動くトラッカーとして使います。電源を入れると次のことを自動で行います。

- APRS パケット(AX.25、1200 bps AFSK)の受信と、局の一覧・距離・方位の表示
- GNSS(u-blox MAX-M10M)による自局の位置の取得と、Mic-E 形式のビーコンの送出(FX.25 にも対応)
- ST7789 液晶への表示(局の一覧、最新局の詳細、自局の状態、地図、衛星の強度、受信音量)
- microSD への受信ログ・NMEA ログの記録と、局の一覧・メッセージの保存
- BLE でスマートフォンとつなぎ、設定の変更、受信の表示、ビーコンの送出、APRS メッセージの送受信

```
 無線機(受信音声)──── GP26 ADC ──► Pico 2 W ──► 液晶(ST7789)
 GNSS(MAX-M10M)────── UART0 ───►   │  ├──► microSD
 ビーコンスイッチ ───── GP21 ────►   │  ├──► 状態 LED
 スマートフォン ◄──────── BLE ──────►   │
                                      └──► 送信音声(PWM)/ PTT ──► 無線機
```

周波数と電波型式は無線機側で決まります。本機が扱うのは音声と PTT だけです。

---

## 1. 必要なもの

### ハードウェア

| 部品 | 内容 |
|------|------|
| ボード | Raspberry Pi Pico 2 W(RP2350)。BLE を使わないなら Pico 2 でも同じ配線で動きます |
| 無線機 | 144 MHz 帯 FM。受信音声を Pico に入れ、送信音声と PTT を Pico から出すインターフェースが必要です。FT-818ND(DATA 端子)と FT3D / FT5D(MIC/SP 端子)に対応したインターフェースの回路を [hw/ft818_if/](hw/ft818_if/) に置いています(`ft818_if.pdf`) |
| GNSS | u-blox MAX-M10M(UART0、115200 bps) |
| 表示 | ST7789 液晶。既定は 2.4 インチ 240x320(横長 320x240 で使う)。Pimoroni Pico Display Pack(240x135、ボタン付き)にも対応 |
| 記録 | microSD カード(カード検出スイッチ付きのスロット) |
| スイッチ | ビーコン送出のオン/オフ(GP21 を GND に落とすとオン) |
| PC との接続 | **データ通信対応の** USB ケーブル |

試作基板(開発途中の rev1)のネットリストは [hw/Pico_MicroSD_M10_Board_rev1.net](hw/Pico_MicroSD_M10_Board_rev1.net) にあります。

### ピン割り当て

正は [src/config.py](src/config.py) の `PIN_*` です。**GPIO は 5 V 非耐性**です(3.3 V 系)。

| GPIO | 用途 |
|------|------|
| GP0 / GP1 | GNSS との UART0(TX / RX) |
| GP2 | PTT(出力。既定は High で送信。`PTT_ACTIVE_HIGH`) |
| GP3 | 送信音声(PWM) |
| GP6 | GNSS の RESET_N(通常は駆動しない) |
| GP7 | 状態 LED(送信中は点灯、受信で短く点灯) |
| GP9 | microSD のカード検出 |
| GP10〜12, GP15 | microSD(SPI1: SCK / MOSI / MISO / CS) |
| GP16〜20 | 液晶(SPI0: DC / CS / SCK / MOSI、バックライト) |
| GP21 | ビーコンスイッチ(内部プルアップ。GND でオン、開放・断線はオフ) |
| GP22 | GNSS の PPS(駆動しない) |
| GP26 | 受信音声(ADC0) |

GP4 / GP5 / GP8 / GP27 は今は使っていません。Pico 2 W の無線チップが使う GP23 / 24 / 25 / 29 も使いません。

### ファームウェア

標準の MicroPython ではなく、次のようにビルドしたファームウェア(ボード定義 `RPI_PICO2_W_ROMFS2`、MicroPython v1.29.0)を使います。

- micropython-lib の `sdcard` と `datetime` を組み込む(標準の .uf2 では microSD が使えません)
- フラッシュに 1 MB の ROMFS 区画を取る(標準の .uf2 では `release` で書き込めません)
- BTstack に、パスキーでのペアリングとボンドの保存を足すパッチを当てる(標準の .uf2 ではペアリングできず、BLE が使えません)

ビルドの手順とパッチの内容は [hw/micropython/README.md](hw/micropython/README.md) にあります(WSL の Ubuntu でビルドします)。できた `firmware.uf2` は、BOOTSEL ボタンを押しながら USB を挿し、出てきたドライブにコピーして書き込みます。配布用の .uf2 はありません。

### PC 側

- Python 3.9 以上
- Linux ではシリアルポートの権限が必要です(`sudo usermod -aG dialout $USER` のあと再ログイン)

---

## 2. セットアップ

```bash
git clone --recurse-submodules <このリポジトリ>
cd pico2_aprs
python3 tools/bootstrap.py --board rpi_pico2_w   # Windows は py tools\bootstrap.py --board rpi_pico2_w
```

`bootstrap.py` が `.venv` を作り、必要なツール(`mpremote`、`mpy-cross` など)を入れます。終わったら `.venv` を有効にします(VSCode なら「Python: Select Interpreter」)。

### 自局の設定

コールサインなどは、git 管理外の `src/config_local.py` に書きます。

```bash
cp tools/config_local.example.py src/config_local.py
```

```python
# src/config_local.py(ここに書く値は架空の例)
CALLSIGN = "Q1TEST"   # 架空の例。自分のコールサインに置き換える。SSID は含めない
SSID = 7
TX_ENABLED = False    # 送信を許可するか。無線機との接続を確かめるまで False
```

コールサインが未設定でも、受信・表示・記録は動きます(画面には `NOCALL` と出て、送信はしません)。BLE からも同じ項目を変えられます(→ [BLE](#ble-でスマートフォンとつなぐ))。

設定は次の順で上書きされます。

`src/config.py`(既定値) < `src/config_local.py` < デバイスの `/settings.json`(BLE で変えた値。電源を切っても残る)

### 接続の確認

```bash
python tools/dev.py ports         # Pico が見えるか
python tools/dev.py info          # MicroPython のバージョン・空きメモリ
python tools/dev.py romfs-query   # ROMFS 区画があるか(パッチ済みのファームウェアか)
```

---

## 3. 局を動かす

### まず試す(フラッシュに書かない)

```bash
python tools/dev.py run -t 60     # PC の src/ を Pico にマウントして 60 秒動かす
```

時間が来ると止まります(終了コード 124 は正常)。USB には次のような行が出ます。

```
[station] Q1TEST-7 started, TNC mode, RX ADC GP26, TX TX not enabled, free 244464
[ble] advertising as PicoAPRS-XXXX              BLE の広告を始めた
[sd] logging to /sd/log/0001_*.txt              SD への記録を始めた
[rx] SRC>DST,PATH:...                           受信したパケット
[tx dry] 715 ms SRC>DST,PATH:...                送るはずだったパケット(TX_ENABLED が False なので出していない)
[station] fix 3D sats 12/30 rx 4 tx 1 bcn on next 540 sd log 0001 ble adv free 157376 lag 3     10 秒ごと
[station] rx level rms -38 dBFS, peak -8 dBFS (ok), ch clear (busy >= -30)
```

`started` の行の `TX ...` は送信できない理由です(`callsign not set`、`TX not enabled`)。送信できるときは `TX enabled` と出ます。10 秒ごとの行の `bcn` はビーコンスイッチ(`off` のときは `next None`)、`sd` は SD の状態(`no card` / `wait` / `log 番号`)、`ble` は BLE の状態(`adv` 広告中 / `conn` 接続中 / `auth` 認証済み)です。

### 電源を入れるだけで動くようにする

```bash
python tools/dev.py release
```

lint とテストを通してから、局のコードを ROMFS(フラッシュの読み出し専用の区画)に書き込み、再起動して `[station] ... started` が出るのを確かめます。`src/config_local.py` もイメージに入るので、**書き込む前に作っておきます**。以後は電源を入れると局が起動します。

`dev.py` で接続すると、動いている局は止まります。作業のあとは `python tools/dev.py monitor --reset -s 20` で起動し直してください。

---

## 4. 使い方

### 送信の条件

PTT を入れるのは、次がすべてそろったときだけです。1 つでも欠けると「ドライラン」になり、ビーコンの時機の管理、画面、SD の記録(`TX(dry)` の印)は同じに動き、PTT と送信音声だけを出しません。

1. コールサインが正しい形式で設定されている(`CALLSIGN`)
2. `TX_ENABLED = True`
3. ビーコンスイッチ(GP21)がオン

1 回の送信は 5 秒を上限に打ち切ります。送信中は受信を止めます(自局の音声を復号しないため)。

> **送信を伴う試験は、ダミーロードにつないで行ってください。** 空中への送信は、免許と運用の条件を満たしたうえで、自分の判断で行います。

### ビーコン

送るのは自局の位置ビーコン(Mic-E)です。2D 以上の測位と GNSS の時刻がそろうと送ります。

| 設定 | 動作 |
|------|------|
| `BEACON_MODE = "auto"`(既定) | スイッチをオンにして測位がそろうと、最初の 1 回をすぐ送る。その後は、前回の位置から `BEACON_MOVE_M`(10 m)以上動いていれば `BEACON_MOVING_S`(90 秒)ごと、動いていなければ `BEACON_STILL_S`(600 秒)ごと |
| `BEACON_MODE = "manual"` | 自動では送らず、スマートフォンの指示(`beacon.send`)のときだけ送る |

- スイッチをオフにすると送りません。次にオンにしたときは、またすぐ送ります
- 自動のビーコンとメッセージは、チャネルが空くのを待ってから送ります(受信音量でスケルチが開いているかを見る。しきい値 `CH_BUSY_DB` はまだ仮の値です)
- 経路、シンボル、コメント、Mic-E のメッセージは `TX_PATH`、`SYMBOL`、`BEACON_COMMENT`、`BEACON_MESSAGE` で決めます
- `FX25_TX = True` で FX.25(誤り訂正付き)で送ります。FX.25 に対応しない受信機でも、従来の AX.25 として受かります

### 受信音量の調整

画面の受信音量のメータ(と USB の `rx level` の行)で、無線機の音量を合わせます。目安はピークが -12〜-3 dBFS で、クリップ(ADC の端に当たる)は避けます。

### 画面

| 構成 | 内容 |
|------|------|
| 320x240(既定。`LCD_PANEL = "st7789_320x240"`) | 上段に局の一覧と最新局の詳細(コールサイン、種別、距離・方位、位置、コメント)。下段に自局の状態(測位、衛星数、時刻、位置、ビーコンの次回、送受信数)、衛星の強度、小さな地図(局の分布と衛星の空図を自動で切り替え) |
| 240x135(`LCD_PANEL = "pico_display"`) | 左に一覧、右上に詳細、右下に自局の状態。地図は X ボタンで全画面に切り替え |

- 右上には、測位状態の丸と BLE の状態のアイコンが出ます。自局の状態の欄には、SD の状態(`SD:LOG` / `SD:WAIT` / `SD:ERR` / `SD:---`)と、送信中の `[TX]`、受信直後の `[RX]` が出ます
- BLE のペアリング中は、6 桁のパスキーを全画面で出します
- 自局宛のメッセージを受けると、30 秒間、全画面で出します

ボタンは 240x135 の構成だけにあります(`config.BUTTONS` に GPIO 番号を `(A, B, X, Y)` の順で書く)。A / B で一覧の選択を上下、X で地図とメイン画面の切り替え、Y で選択を解除して最新の局の自動表示に戻ります。

### microSD

カードを挿すと、GNSS の時刻が分かってから(最大 20 秒待つ)記録を始めます。

| ファイル | 内容 |
|----------|------|
| `/sd/log/NNNN_aprs.txt` | 受信パケット(TNC2 形式)。自局の送信は `TX `、ドライランは `TX(dry) ` を付ける。起動ごとに通し番号で作る |
| `/sd/log/NNNN_nmea.txt` | NMEA の RMC と GGA |
| `/sd/stations.txt` | 受信した局の一覧。次の起動で読み出して表に戻す |
| `/sd/messages.txt` | APRS メッセージ。次の起動で読み出す |

ログは 10 秒ごとにまとめて書きます(電源を切ると最大 10 秒分を失います)。カードを抜くとファイルを閉じ、挿し直すと新しい番号で作ります。

### BLE でスマートフォンとつなぐ

Pico 2 W では、BLE(広告名 `PicoAPRS-XXXX`)でスマートフォンの Web アプリ(Android の Chrome、別のリポジトリで開発中)や PC からつなげます。

- **ペアリング**: 初めてつなぐと、液晶に 6 桁のパスキーが出るので、スマートフォンに入力します。2 回目からは入力は要りません。ペアリングが済んだ接続だけが操作できます
- **できること**: 状態の表示、受信したパケットの表示、局の一覧の取得、設定の変更(コールサイン、SSID、シンボル、経路、コメント、ビーコンの間隔とモード、`tx_enabled`、FX.25)、今すぐビーコンを送る、APRS メッセージの送受信(相手の確認応答が来るまで送り直し、自局宛には自動で応答)
- 送信には、BLE からの指示でもビーコンスイッチのオンが必要です。スイッチはスマートフォンから迂回できません
- 同時につなげるのは 1 台だけで、つながっている間は広告を止めます
- 仕様は [docs/ble-protocol.md](docs/ble-protocol.md)(英語、Web アプリの開発者向け)です。一部の項目は暫定です
- PC からは `python tools/ble_cli.py pair`(初回、パスキーは標準入力)、`get`、`stations`、`listen -s 30`、`send` などで試せます。Windows の PC では、通知の受信が途中で止まる既知の問題があります([docs/ble-control.md §7](docs/ble-control.md#7-windows-で通知が止まる問題2026-11-17))。動作の確認には Android(nRF Connect)が確実です

### 異常時の動作

- ファームウェアが止まったときは、ウォッチドッグで再起動します
- 局が例外で止まると、内容を液晶・USB・SD に出し、待ってから再起動します。続けて失敗すると、待ち時間を 10、20、40 … 秒と延ばします(最大 300 秒)
- 異常で再起動した直後は、最初のビーコンを 90 秒待ちます(再起動を繰り返して送り続けないように)
- 起動直後に `boot.py` が PTT を切った状態にします

---

## 5. 困ったとき

- **接続できない**: MicroPico、Thonny、シリアルモニタなど、ほかのツールがポートを掴んでいないか確かめます。`dev.py ports` で検出の状況が見られます
- **応答しない・固まった**: USB を抜き差しします。起動を繰り返して応答しないときは `dev.py rm-main`(自動起動を止める)を試します
- **最後の手段**: BOOTSEL ボタンを押しながら USB を挿し、ファームウェアを書き直します
- **ペアリングし直したい**: スマートフォン側で本機の登録を削除してから、つなぎ直します。本機側の鍵を消す方法は [docs/development.md](docs/development.md) にあります

---

## 6. 開発する人へ

開発環境、開発の手順、テスト、デバッグ、ディレクトリ構成、設計メモの一覧は [docs/development.md](docs/development.md) にまとめています。

## 7. ライセンス

micro-aprs を含むため、このプロジェクト全体のライセンスは **GPL-3.0** です([LICENSE](LICENSE))。

次のコードは、サブモジュールから生成したものです。

| 生成先 | 出典 | ライセンス |
|--------|------|------------|
| `src/tnc/` | [klaNath/micro-aprs](https://github.com/klaNath/micro-aprs) の `pico2-subset` ブランチ(本家 [stephanelsmith/micro-aprs](https://github.com/stephanelsmith/micro-aprs) のフォーク) | GPL-3.0 |
| `src/maxm10/` | [klaNath/micropython-maxm10](https://github.com/klaNath/micropython-maxm10) の `pico2-aprs` ブランチ | MIT |
| `src/micropyGPS.py` | [klaNath/micropyGPS](https://github.com/klaNath/micropyGPS) の `pico2-aprs` ブランチ | MIT |
