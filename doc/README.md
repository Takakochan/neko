# SG90 練習用アーム（猫じゃらしロボット）

PC（カメラ・検出・目標計算）→ USB シリアル → Pico → SG90

## 配線

| SG90 の線 | つなぎ先 |
| --- | --- |
| 茶（GND） | 外部 5V 電源の GND と Pico の GND（両方をつなぐ） |
| 赤（5V） | 外部 5V 電源の＋（2A 以上） |
| 橙（信号） | Pico の GP0〜GP4 |

| 関節 | Pico のピン |
| --- | --- |
| J0 土台の旋回 | GP0（物理ピン1） |
| J1 肩 | GP1（物理ピン2） |
| J2 肘 | GP2（物理ピン4） |
| J3 手首 | GP3（物理ピン5） |
| J4 手首回転／グリッパー | GP4（物理ピン6） |

GND は物理ピン3などが使える。サーボの電源は Pico や USB から取らないこと（動いた瞬間に電流が足りずリセットする）。

## セットアップ

1. Pico に MicroPython を入れる（Thonny から入れるのが簡単）
2. Thonny で `pico/main.py` を Pico に `main.py` という名前で保存する（電源を入れると自動で動く）
3. Thonny を閉じる（開いたままだとシリアルポートを取り合う）
4. PC 側: `pip install pyserial`

## 動作確認の順番

1. アームを組む前に、サーボ単体で `python pc/test_arm.py calib 0` を実行し、パルス幅の限界を探す（全サーボ）
2. 見つけた値を `pico/main.py` の `SERVOS` に書き込み、Pico に保存し直す
3. アームを組んでから `python pc/test_arm.py sweep`
4. `python pc/test_arm.py manual` で好きな角度を試す

### アームを組んだあとの関節チェック

各アームにサーボを取り付けたあとは、`python pc/check_joints.py` を使う。
1 関節ずつゆっくり動かして可動範囲（lo / hi）を記録し、最後に出る
「SERVOS に書く目安」を `pico/main.py` の SERVOS に書き写す。
組み立て後は生パルスの calib ではなく、こちらを使うこと（ハードストップに
突っ込みにくい）。

## 猫を追いかける（cat_chase.py）

投げて着地したあと、MacBook のカメラで猫の動きを見て、猫がいた場所へ猫じゃらしを動かす。
準備: `pip install opencv-python numpy`（カメラは、システム設定 → プライバシーとセキュリティ → カメラ で
ターミナルか VS Code を許可する）

1. `python pc/cat_chase.py --watch` カメラを置き、アームなしで動きの検出だけ確かめる。
   緑の枠の数字（面積）を見て、猫だけが拾えるよう `--min-area` を決める
2. `python pc/cat_chase.py --calib` アームが 9 か所に動くので、そのつど画面上の猫じゃらしを
   クリックして Enter。結果は `pc/chase_calib.json` に保存される。**カメラかアームを動かしたらやり直す**
3. `python pc/cat_chase.py --no-cast` 投げずに追いかけだけ試す
4. `python pc/cat_chase.py` 本番（投げる → 120 秒追う → 引き寄せ）。q で早めに終わる

## 自分のコードから使う

```python
from arm import SG90Arm

with SG90Arm() as arm:
    arm.set_speed(120)            # 度/秒
    arm.set_joints([90, 60, 120, 80])
```

当日は `SG90Arm` を `SO101Arm`（LeRobot で実装）に差し替える。
