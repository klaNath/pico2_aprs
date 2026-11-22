# MicroPython ファームウェアへのパッチ

本機のファームウェアは MicroPython v1.29.0 の `ports/rp2` を、ボード設定 `hw/RPI_PICO2_W_ROMFS2/` でビルドしたもの。
BLE のペアリングのために、次のパッチを当てる。

## btstack-pairing.patch

MicroPython v1.29.0 の BTstack 版(rp2 の Pico W 系)は、ペアリングの設定(`config(bond=, mitm=, le_secure=, io=)`)を受け付けるが、ペアリングは成立しない。

- セキュリティマネージャのイベントを受け取っていない(`sm_add_event_handler` がない)
  - Just Works の確認要求に答えないので、約 30 秒で時間切れになる
  - パスキーの表示要求が Python に届かない(`_IRQ_PASSKEY_ACTION` が来ない)
- `gap_passkey()` が常に `EOPNOTSUPP`
- ボンドの保存先が RAM(`le_device_db_memory`)で、再起動すると消える
- ER/IR 鍵が固定のゼロ
- LE Secure Connections の楕円曲線暗号が組み込まれていない(`ENABLE_LE_SECURE_CONNECTIONS` は btstack_config.h 側で定義しないと効かない)

パッチの変更点(`extmod/btstack/` と `extmod/modbluetooth.c`。BTstack 側は `MICROPY_PY_BLUETOOTH_ENABLE_PAIRING_BONDING` のときだけ効く):

| 項目 | 変更 |
|---|---|
| SM のイベント | `sm_add_event_handler` で受ける。パスキーの表示は `_IRQ_PASSKEY_ACTION`(conn, 3, パスキー)で Python に渡す(パスキーは BTstack が作る)。取り消しとペアリングの失敗は action 0。入力・数値比較も渡す |
| Just Works | `mitm=True` なら断る(`sm_bonding_decline`)。`mitm=False` なら承認する |
| 受ける方式 | `mitm=True` かつ `le_secure=True` なら LE Secure Connections だけ(`sm_set_secure_connections_only_mode`)、方式はパスキーと数値比較だけ。旧方式(Legacy)のパスキーは傍受から割り出せるので受けない。設定は `active(False)/active(True)` のあとも引き継ぐ |
| `gap_passkey()` | BLE が止まっていれば ENODEV。入力(2)は 0〜999999 を `sm_passkey_input`、数値比較(4)は承認/拒否、表示(3)は何もしない(0 を返す) |
| ボンドの保存 | BTstack の TLV を `_IRQ_GET_SECRET` / `_IRQ_SET_SECRET` につなぐ(種類 0x10、key はタグの 4 バイト、ビッグエンディアン)。`le_device_db_tlv`(4 台分)を使う。ER/IR も初回に乱数で作って同じ所に保存する |
| `_IRQ_GET_SECRET` の戻り値 | バッファでなければ「無い」扱い(例外を出さない。BTstack の中から呼ばれるため。`modbluetooth.c`) |
| LE Secure Connections | `ENABLE_LE_SECURE_CONNECTIONS` と micro-ecc(`ENABLE_MICRO_ECC_P256`)を有効にする |
| BLE の停止 | `btstack_crypto_deinit()` も呼ぶ。呼ばないと、ソフトリセットや `active(False)` のあとの起動で暗号処理の状態が初期化されず、ボンドした相手(Android のプライベートアドレス)の解決が終わらないので、再接続の暗号化が失敗する(電源投入後の最初の起動では成功するので気づきにくい) |
| 通知が送れないとき | ACL のバッファが満杯なら、`gatts_notify` は ENOMEM(`OSError`)を返し、複製を取っておかない。上流は複製して後で送り、成功を返していた。その場合、アプリは満杯に気づかない。相手が受け取らなくなると、通知のたびに複製がヒープに積まれ続ける(2026-11-23 のレビューの #6)。`src/hw/ble.py` は OSError で送り残しを持ち、次の poll で送り直す。indicate は上流のまま |
| その他 | 暗号化の更新で接続が見つからないときの NULL 参照を防ぐ |

未対応: Make でビルドするポート(unix、stm32 の `btstack.mk`)には同じ変更を入れていない(リンクに失敗する)。
楕円曲線の計算はソフトウェア(`uECC_asm_none`)で、起動時とペアリングのたびに局のループを止める(時間は実機で測る)。

アプリ側の注意: 起動時(`active(True)` の中)に BTstack が既存のボンドと ER/IR を読むので、`irq()` は `active(True)` より前に設定する。`src/hw/ble.py` はそうしている。

## ビルド

WSL(Ubuntu)の MicroPython v1.29.0 のツリーで:

```
cd ~/micropython
git apply /mnt/d/rp2/pico2_aprs/hw/micropython/btstack-pairing.patch
cp -r /mnt/d/rp2/pico2_aprs/hw/RPI_PICO2_W_ROMFS2 ports/rp2/boards/
make -C ports/rp2 BOARD=RPI_PICO2_W_ROMFS2 -j8
```

できたもの: `ports/rp2/build-RPI_PICO2_W_ROMFS2/firmware.uf2`(BOOTSEL で書き込む)。
パッチで増えるのは約 16 KB(フラッシュ使用 910108 B / 1 MB、87%、2026-11-17)。

実機での確認(2026-11-17、Android の nRF Connect): パスキーでのペアリング、認証された接続での
通知、電源投入後とソフトリセット後の再接続(パスキーなしで暗号化・認証)。

BTstack のログ(`ENABLE_LOG_INFO` と `hci_dump_embedded_stdout`)は pico-sdk の printf に出るので、
USB には出ない(UART 側)。Python から見たいときは、IRQ の中で print せずに記録してループで表示する
(IRQ の中の print は失われることがある)。

パッチを作り直すときは `git diff -- extmod/btstack/ extmod/modbluetooth.c > .../btstack-pairing.patch`。
