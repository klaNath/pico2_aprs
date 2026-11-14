"""無線機インターフェース(FT-818ND の DATA 端子 / FT3D・FT5D の MIC/SP 端子 両対応)の
回路図(ft818_if.kicad_sch)を生成する。

    python hw/ft818_if/gen_sch.py

無線機は J2(FT-818ND、6 ピン ミニ DIN)か J4(FT3D/FT5D、3.5 mm 4 極プラグ)のどちらか一方をつなぐ。
機種の違いはジャンパー 3 か所で切り替える。
    JP1  PTT の掛け方   1-2 = 専用線(FT-818)        / 2-3 = マイク線を 2 kΩ で GND へ(FT3D/FT5D)
    JP2  送信レベル     閉 = R13 を短絡(FT-818)      / 開 = R13 33 kΩ を直列に入れる(FT3D/FT5D)
    JP3  受信ゲイン     閉 = 3.2 倍(FT-818)          / 開 = 1 倍(FT3D/FT5D。音量ツマミで合わせる)

メイン基板(hw/Pico_MicroSD_M10_Board_rev1.net)とは次の 2 か所でつなぐ。
    J5(JST ZH 6 ピン)  1 = GP2(PTT)、2 = GP3(R11 0 Ω 経由。R12 2.2 kΩ と C12 10 nF が GND へ)、
                        3 = +3.3V、4 = GP5、5 = GND、6 = GP4
    U2 のパッド(周辺光センサの位置。センサは載せない)  1 = +3.3V、2 = GND、
                        3 = GP26(R1 10 kΩ が GND へ)
ADC_VREF と AGND は引き出せないので、電源は +3.3V と GND だけを使う。

座標は 2.54 mm を 1 とする格子で書く。左がメイン基板、右が無線機。上から PTT・送信・受信の 3 段。
定数の根拠は docs/spice/ のシミュレーション。
生成物なので .kicad_sch を手で直したら、このスクリプトにも反映すること。
"""

from schlib import OUT, Sch

R, C, FB = "Device:R", "Device:C", "Device:FerriteBead"
CMC = "Filter:Choke_CommonMode_FerriteCore_1243"
OPAMP = "Amplifier_Operational:MCP6002-xP"
JP2P, JP3P = "Jumper:Jumper_2_Open", "Jumper:Jumper_3_Open"


def build():
    s = Sch()
    PTT, TX, RXI = 26, 44, 64
    RXO = RXI + 1  # 受信はオペアンプの前後で 1 行ずれる
    JX, JY = 144, RXI  # J2(FT-818)の位置
    XG, XM, XP = 134, JX + 5, JX + 7  # 無線機側 GND / マイク(DATA IN) / PTT の縦の列

    s.text("メイン基板 (Pico_MicroSD_M10_Board_rev1) 側の関係部品(この回路図には描いていない)\n"
           " ・GP3 → R11 (0 Ω) → J5-2。J5-2 から GND へ R12 2.2 kΩ と C12 10 nF(C12 が GPIO に直結している。"
           "R11 を 100 Ω 以上にするか C12 を外すのが望ましい)\n"
           " ・GP26 から GND へ R1 10 kΩ。R7 と分圧になり、ADC の中心は約 1.35 V、利得は 0.82 倍になる(R1 を外せば 1.65 V・1 倍)\n"
           " ・U2 (GA1A2S100SS) は実装しない(出力が GP26 でぶつかる)。そのパッドを受信音声の入口に使う\n"
           " ・ADC_VREF と AGND は引き出せないので、電源は +3.3V と GND だけを使う", 4, 10.5)

    # ===== 無線機側コネクタ(どちらか一方をつなぐ) =====
    j2 = s.place("Connector:Mini-DIN-6", "J2", "FT-818ND DATA", JX, JY, mirror="x",
                 ref_at=(JX - 1.5, JY + 3.6, None), val_at=(JX - 1.5, JY + 4.6, None))
    s.nc(j2["5"])
    s.nc(j2["6"])
    j4 = s.place("Connector_Audio:AudioPlug4", "J4", "FT3D/FT5D MIC/SP", JX - 4, JY - 8,
                 ref_at=(JX - 5, JY - 11.6, None), val_at=(JX - 5, JY - 10.6, None))
    s.nc(j4["R1"])
    s.text("J2: FT-818ND 背面 DATA(6 ピン ミニ DIN)\n(FT-817/818 の一般的な並び。配線前に\n取説 P10 の図と現物で必ず確認すること)\n"
           " 1  DATA IN\n 2  GND\n 3  PTT\n 4  DATA OUT 1200bps\n 5  DATA OUT 9600bps (未使用)\n 6  SQL (未使用)",
           129, JY + 14)
    s.text("J4: FT3D/FT5D MIC/SP(3.5 mm 4 極プラグ)\n(取説には CT-44 の結線しか載っていない。極の並びは\nCT-44 か実機で必ず確認すること。下は一般に言われる並び)\n"
           " T (先端)  SP\n R1        データ (未接続)\n R2        MIC / PTT\n S (根元)  GND",
           129, JY + 22.5)

    # ===== メイン基板 J5 へのコネクタ =====
    j1 = s.place("Connector_Generic:Conn_01x06", "J1", "メイン基板 J5 へ (JST ZH 6 ピン)", 12, TX + 1, mirror="y",
                 ref_at=(12, TX - 2.5, None), val_at=(8, TX + 6.3, None))
    for i, t in enumerate(["GP2  PTT (High=送信)  →", "GP3  AFSK 送信音声 PWM  →", "+3.3V", "GP5 (未使用)",
                           "GND", "GP4 (未使用)"]):
        s.text(t, 10.5, TX - 1 + i, just="right")
    s.wire(j1["3"], (16, TX + 1))
    s.power("+3V3", 16, TX + 1, rot=270)
    s.nc(j1["4"])
    s.wire(j1["5"], (16, TX + 3))
    s.power("GND", 16, TX + 3, rot=90)
    s.nc(j1["6"])

    # ===== PTT =====
    s.text("PTT:  GP2 (High = 送信) → オープンコレクタ → JP1 → FT-818: PTT 専用線 / FT3D・FT5D: マイク線を 2 kΩ で GND へ",
           22, PTT - 11, size=2, bold=True)
    r10 = s.H(R, "R10", "4.7k", 30, PTT)
    s.wire(j1["1"], (19, TX - 1), (19, PTT), r10[0])
    q1 = s.place("Transistor_BJT:2SC1815", "Q1", "2SC1815", 42, PTT,
                 ref_at=(44.5, PTT - 0.5, "left"), val_at=(44.5, PTT + 0.5, "left"))
    s.wire(r10[1], (36, PTT), q1["3"])
    r11 = s.V(R, "R11", "100k", 36, PTT + 4)
    s.wire((36, PTT), r11[0])
    s.power("GND", *r11[1])
    s.wire(q1["1"], (43, PTT + 3.5))
    s.power("GND", 43, PTT + 3.5)
    jp1 = s.place(JP3P, "JP1", "PTT 切替", 56, PTT - 4, rot=270,
                  ref_at=(57.5, PTT - 4.5, "left"), val_at=(57.5, PTT - 3.5, "left"))
    s.wire(q1["2"], (43, PTT - 4), jp1["2"])
    fb = s.H(FB, "FB1", "フェライトビーズ", 66, PTT - 8)
    s.wire(jp1["1"], (56, PTT - 8), fb[0])
    s.wire(fb[1], (XP, PTT - 8), (XP, JY), j2["3"])
    r12 = s.H(R, "R12", "2k", 66, PTT)
    s.wire(jp1["3"], (56, PTT), r12[0])
    s.text("1-2: FT-818", 57.5, PTT - 6.6)
    s.text("2-3: FT3D/FT5D", 57.5, PTT - 1.2)
    s.text("R11: 起動時・未設定時に\nPTT を開放に保つ", 22, PTT + 4.5)
    s.text("FT-818 の PTT", 128, PTT - 8.3)
    s.text("取説(FT5D 詳細編 P35)の 2 kΩ。\nマイク線の直流を GND へ流して送信にする", 69, PTT + 2.8)

    # ===== 送信: GP3 -> LPF -> レベル調整 -> CMC -> DATA IN / MIC =====
    s.text("送信音声 (TX):  GP3 PWM → 2次 RC LPF → DC カット → レベル調整 → DC カット → コモンモードチョーク → DATA IN / MIC",
           22, TX - 7, size=2, bold=True)
    r1 = s.H(R, "R1", "2.2k", 26, TX)
    s.wire(j1["2"], r1[0])
    c1 = s.V(C, "C1", "10n", 32, TX + 4)
    r2 = s.H(R, "R2", "2.2k", 38, TX)
    s.wire(r1[1], (32, TX), r2[0])
    s.wire((32, TX), c1[0])
    s.power("GND", *c1[1])
    c2 = s.V(C, "C2", "10n", 44, TX + 4)
    c3 = s.H(C, "C3", "1u", 50, TX)
    s.wire(r2[1], (44, TX), c3[0])
    s.wire((44, TX), c2[0])
    s.power("GND", *c2[1])
    r3 = s.H(R, "R3", "10k", 58, TX)
    s.wire(c3[1], r3[0])
    r13 = s.H(R, "R13", "33k", 66, TX)
    s.wire(r3[1], (62, TX), r13[0])
    jp2 = s.place(JP2P, "JP2", "送信レベル", 66, TX - 3,
                  ref_at=(64.5, TX - 4.6, None), val_at=(68.5, TX - 4.6, None))
    s.wire((62, TX), (62, TX - 3), jp2["1"])
    rv = s.place("Device:R_Potentiometer_Trim", "RV1", "2k", 74, TX + 5,
                 ref_at=(73, TX + 4.5, "right"), val_at=(73, TX + 5.5, "right"))
    s.wire(r13[1], (70, TX), (74, TX), rv["1"])
    s.wire(jp2["2"], (70, TX - 3), (70, TX))
    s.power("GND", *rv["3"])
    c4 = s.H(C, "C4", "1u", 82, TX + 5)
    s.wire(rv["2"], c4[0])
    fl1 = s.place(CMC, "FL1", "SDCW3216-2-222TF", 124, TX + 6,
                  ref_at=(124, TX + 1.8, None), val_at=(124, TX + 2.8, None))
    s.wire(c4[1], (88, TX + 5), fl1["1"])
    s.wire(r12[1], (88, PTT), (88, TX + 5))  # FT3D/FT5D の PTT: C4 より無線機側のマイク線へ
    s.wire(fl1["4"], (120, TX + 7), (120, TX + 8.5))
    s.power("GND", 120, TX + 8.5)
    s.wire(fl1["2"], (XM, TX + 5), (XM, JY - 8), (XM, JY - 1), j2["1"])
    s.wire(fl1["3"], (XG, TX + 7), (XG, JY - 2), (XG, JY - 1), j2["2"])
    # J4: S = GND、R2 = MIC
    s.wire((XG, TX + 7), (XM - 1.5, TX + 7), (XM - 1.5, JY - 9), j4["S"])
    s.wire((XM, JY - 8), j4["R2"])
    s.text("PWM 146.5 kHz\n0-3.3 V", 21, TX + 4.8)
    s.text("LPF: fc ≈ 7.2 kHz × 2 段\n(146.5 kHz で約 -50 dB)", 29, TX - 1.6)
    s.text("直流(約 1.65 V)を除く", 46, TX - 2.5)
    s.text("閉: FT-818 / 開: FT3D・FT5D", 71.5, TX - 2.6)
    s.text("レベル調整(PWM 1.3 Vp-p のときの最大)\nFT-818: 約 130 mVp-p(負荷 10 kΩ)\nFT3D/FT5D: 約 17-25 mVp-p(負荷 1-2 kΩ と仮定)", 76, TX + 9.8)
    s.text("無線機側の直流\n(マイクのバイアス)を除く", 76, TX + 2.3)
    s.text("FT-818: 目標 50-100 mVp-p(メニュー 39 PKT MIC と合わせて偏移を調整)\nFT3D/FT5D: 無線機側に調整がないので RV1 と TX_AMPLITUDE で偏移を合わせる", 90, TX + 4.2)
    s.text("DATA IN / MIC", 128, TX + 4.7)
    s.text("GND(コネクタ側で 1 点に束ねる)", 127.5, TX + 6.7)

    # ===== 受信: DATA OUT / SP -> CMC -> 保護 -> AC 結合 -> バッファ -> AAF -> GP26 =====
    s.text("受信音声 (RX):  GP26 ADC ← 折り返し防止 LPF ← 増幅 ×3.2 / ×1 (1.65 V バイアス) ← DC カット ← 保護抵抗 ← コモンモードチョーク ← DATA OUT / SP",
           22, RXI - 7.5, size=2, bold=True)
    j3 = s.place("Connector_Generic:Conn_01x03", "J3", "メイン基板 U2 のパッドへ", 12, RXO - 1, mirror="y",
                 ref_at=(12, RXO - 4, None), val_at=(9, RXO + 2.2, None))
    for i, t in enumerate(["パッド 1  +3.3V (未使用)", "パッド 2  GND", "パッド 3  GP26 ADC0 受信音声  ←"]):
        s.text(t, 10.5, RXO - 2 + i, just="right")
    s.nc(j3["1"])
    s.wire(j3["2"], (16, RXO - 1))
    s.power("GND", 16, RXO - 1, rot=90)

    fl2 = s.place(CMC, "FL2", "SDCW3216-2-222TF", 124, RXI - 1,
                  ref_at=(124, RXI - 5.2, None), val_at=(124, RXI - 4.2, None))
    XS = JX - 6.5  # SP の分岐の列
    s.wire(j2["4"], (XS, RXI), fl2["3"])
    # J4: T = SP。GND の横線(J2-2 へ)を 1 か所だけまたぐ(交差。接続ではない)
    s.wire((XS, RXI), (XS, JY - 4), (XM - 1.5, JY - 4), (XM - 1.5, JY - 6), j4["T"])
    s.wire(fl2["2"], (XG, RXI - 2))
    s.wire(fl2["1"], (119, RXI - 2))
    s.power("GND", 119, RXI - 2, rot=270)
    r14 = s.H(R, "R14", "10k", 114, RXI)
    s.wire(fl2["4"], r14[1])
    c5 = s.H(C, "C5", "1u", 108.5, RXI)
    s.wire(r14[0], c5[1])
    u1a = s.place(OPAMP, "U1", "MCP6002", 98, RXO, mirror="y", unit=1,
                  ref_at=(96, RXO - 2.2, None), val_at=(96, RXO + 2.2, None))
    s.wire(c5[0], (105, RXI), u1a["3"])
    r4 = s.V(R, "R4", "100k", 105, RXI + 4)
    s.wire((105, RXI), r4[0])
    VB = RXI + 15  # VBIAS の行
    s.wire(r4[1], (105, VB), (118, VB), (124, VB))
    r8 = s.V(R, "R8", "47k", 118, VB - 4)
    r9 = s.V(R, "R9", "47k", 118, VB + 4)
    s.wire(r8[1], (118, VB), r9[0])  # 分圧の中点 = VBIAS(交差ではなく接続)
    s.wire(r8[0], (118, VB - 6.5))
    s.power("+3V3", 118, VB - 6.5)
    s.power("GND", *r9[1])
    c8 = s.V(C, "C8", "1u", 124, VB + 4)
    s.wire((124, VB), c8[0])
    s.power("GND", *c8[1])
    s.label("VBIAS", 107.5, VB)
    s.text("= 1.65 V", 110.5, VB - 0.2)
    s.text("C8: +3.3V の雑音を除く\n(fc ≈ 7 Hz)", 119.5, VB + 9.5)
    s.text("R14: 過大入力のとき U1 の\n入力保護に流れる電流を抑える", 107.5, RXI + 9)
    # 帰還
    r5 = s.H(R, "R5", "22k", 98, RXO + 7)
    r7 = s.H(R, "R7", "2.2k", 86, RXO)
    s.wire(u1a["1"], (93, RXO), r7[1])
    s.wire((93, RXO), (93, RXO + 7), r5[0])
    jp3 = s.place(JP2P, "JP3", "受信ゲイン", 101, RXO + 10, rot=270,
                  ref_at=(99.5, RXO + 9.5, "right"), val_at=(99.5, RXO + 10.5, "right"))
    s.wire(u1a["2"], (101, RXO + 7), jp3["1"])
    s.wire(r5[1], (101, RXO + 7))
    r6 = s.V(R, "R6", "10k", 101, RXO + 14.5)
    s.wire(jp3["2"], r6[0])
    c6 = s.V(C, "C6", "10u", 101, RXO + 19.5)
    s.wire(r6[1], c6[0])
    s.power("GND", *c6[1])
    c7 = s.V(C, "C7", "10n", 80, RXO + 4)
    s.wire(r7[0], (80, RXO), j3["3"])
    s.wire((80, RXO), c7[0])
    s.power("GND", *c7[1])
    s.text("FT-818: 300 mVp-p 一定\nFT3D/FT5D: 音量ツマミで\n約 1 Vp-p に合わせる", 127, RXI + 3.6)
    s.text("JP3 閉: AC ゲイン 1 + R5/R6 = 3.2 (FT-818)\nJP3 開: ゲイン 1 (FT3D/FT5D)\nDC ゲインは常に 1 (C6)", 62, RXO + 12.5)
    s.text("折り返し防止 fc ≈ 7-9 kHz\n(サンプリング 19.2 kHz)", 76, RXO - 1.6)
    s.text("約 0.6-0.8 Vp-p、中心 約 1.35 V(メイン基板の R1 10 kΩ と分圧)", 22, RXO - 0.4)

    # ===== 電源・未使用ユニット =====
    s.text("オペアンプの電源と未使用ユニット", 4, 93, size=2, bold=True)
    px = 14  # U1 の電源ピンの列
    u1c = s.place(OPAMP, "U1", "MCP6002", px + 1, 100, unit=3,
                  ref_at=(px - 1.5, 100, "right"), val_at=(px - 1.5, 101, "right"), hide_value=True)
    s.wire(u1c["8"], (px, 96), (px, 95))
    s.power("+3V3", px, 95)
    c9 = s.V(C, "C9", "0.1u", px + 7, 100)
    s.wire((px, 96), (px + 7, 96), c9[0])
    s.wire((px + 7, 96), (px + 12, 96))
    s.flag(px + 12, 96)
    s.wire(u1c["4"], (px, 104), (px, 105))
    s.power("GND", px, 105)
    s.wire((px, 104), (px + 7, 104), c9[1])
    s.wire((px + 7, 104), (px + 12, 104))
    s.flag(px + 12, 104)
    s.text("C9: U1 の電源ピンの\nすぐそばに付ける", px + 10.8, 100.8)

    bx = 44  # 未使用ユニットの中心
    u1b = s.place(OPAMP, "U1", "MCP6002", bx, 100, unit=2,
                  ref_at=(bx + 1, 97.5, None), val_at=(bx + 1, 102.5, None), hide_value=True)
    s.wire(u1b["7"], (bx + 5, 100), (bx + 5, 104), (bx - 5, 104), (bx - 5, 101), u1b["6"])
    s.wire(u1b["5"], (bx - 7.5, 99))
    s.label("VBIAS", bx - 7.5, 99)
    s.text("未使用ユニット: VBIAS のボルテージフォロワにして\n入力を浮かせない(出力は未接続)", bx - 8, 107.5)

    s.text("機種の切り替え(無線機は J2 か J4 のどちらか一方だけをつなぐ)", 58, 96, size=2, bold=True)
    s.text("FT-818ND (J2):  JP1 = 1-2、JP2 = 閉、JP3 = 閉\n"
           "    無線機は PKT モード、PKT RATE 1200bps\n"
           "FT3D / FT5D (J4):  JP1 = 2-3、JP2 = 開、JP3 = 開\n"
           "    無線機は受信セーブを OFF(CONFIG 17)。\n"
           "    受信レベルは音量ツマミ、送信レベルは RV1 で合わせる。\n"
           "    スピーカー出力はディエンファシス後なので、\n"
           "    2200 Hz が 1200 Hz より低く出る", 58, 104.5)
    return s


if __name__ == "__main__":
    build().write(OUT)
    print("wrote", OUT)
