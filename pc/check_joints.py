# check_joints.py - アームを組んだあとの関節ごとの動作確認
#
# 1 関節ずつゆっくり動かして、次のことを確かめる。
#   - 90° のときに中立の姿勢になっているか（ホーンの付け位置）
#   - どこまで動かせるか（ぶつかる手前の角度）
#   - 角度を増やすとどちら向きに動くか
# 最後に、main.py の SERVOS に書き写す範囲の目安を表示する。
#
# 使い方:   python check_joints.py [--port ポート名] [--speed 30] [--step 5]
# 非常停止: サーボ電源のプラグを抜く（これが確実）
#           Ctrl+C は PC 側スクリプトを止めるだけ。Pico は動き続けることがある。
#
# arm.py と同じフォルダ（pc/）に置いて実行する。

import argparse
import re
import time

from arm import SG90Arm

NAMES = ["J0 土台の旋回", "J1 肩", "J2 肘", "J3 手首", "J4 手首回転／グリッパー"]

HELP = """\
操作（入力して Enter）
  +  /  -        step の角度だけ動かす（++ なら 2 倍）
  +10  /  -10    その角度だけ動かす
  Enter だけ      直前の + / - をくり返す
  数字            その角度へ動かす（例: 45）
  step 数字       1 回に動かす角度を変える（例: step 2）
  lo  /  hi       今の角度を可動範囲の端（下限／上限）として記録
  memo 文字       メモを残す（例: memo 角度を増やすと上がる）
  n  /  b         次の関節へ／前の関節へ
  h               全関節を初期位置へ
  q               終了（初期位置に戻してから脱力）
  ?               この説明
非常停止はサーボ電源のプラグを抜く（Ctrl+C はスクリプトを止めるだけ）"""

MARGIN = 5  # 記録した端から内側にとる余裕（度）
RELATIVE = re.compile(r"^[+-]\d+(\.\d+)?$")


def joint_name(j):
    return NAMES[j] if j < len(NAMES) else f"J{j}"


def wait_still(arm, timeout=20.0):
    """全関節が止まるまで待ち、指令角度の一覧を返す。"""
    prev = arm.get_joints()
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(0.1)
        now = arm.get_joints()
        if max(abs(a - b) for a, b in zip(now, prev)) < 0.01:
            return now
        prev = now
    return prev


def move(arm, j, angle):
    """関節 j だけを angle へ動かし、止まった角度を返す。"""
    angle = min(max(float(angle), 0.0), 180.0)
    angles = arm.get_joints()
    angles[j] = angle
    arm.set_joints(angles)
    actual = wait_still(arm)[j]
    if abs(actual - angle) > 0.5:
        print(f"  ! main.py の安全範囲の外なので {actual:.0f}° で止まっています")
    return actual


def banner(j, n):
    print(f"\n===== {joint_name(j)}（{j + 1}/{n}）=====")
    print("  90° のときに中立の姿勢か確認してから、少しずつ動かしてください。")


def fmt(a):
    return "未記録" if a is None else f"{a:.0f}°"


def print_summary(results):
    print("\n===== 結果 =====")
    for j, r in enumerate(results):
        lo, hi = r["lo"], r["hi"]
        if lo is not None and hi is not None and lo > hi:
            lo, hi = hi, lo
        text = f"{joint_name(j)}: 下限 {fmt(lo)} / 上限 {fmt(hi)}"
        if lo is not None and hi is not None:
            s_lo = lo if lo <= 0 else lo + MARGIN
            s_hi = hi if hi >= 180 else hi - MARGIN
            text += f"  → SERVOS に書く目安: {s_lo:.0f}, {s_hi:.0f}"
        print(text)
        for m in r["memo"]:
            print(f"    メモ: {m}")


def run(arm, speed, step):
    arm.set_speed(speed)
    print(f"全関節を初期位置へ戻します（{speed:g}°/秒）…")
    arm.home()
    n = len(wait_still(arm))
    results = [{"lo": None, "hi": None, "memo": []} for _ in range(n)]
    print(HELP)

    j, last = 0, None
    banner(j, n)
    while True:
        cur = arm.get_joints()[j]
        cmd = input(f"[{joint_name(j)} {cur:.0f}°] > ").strip()
        if cmd == "" and last:
            cmd = last

        if cmd and set(cmd) <= {"+"}:
            move(arm, j, cur + step * len(cmd))
            last = cmd
        elif cmd and set(cmd) <= {"-"}:
            move(arm, j, cur - step * len(cmd))
            last = cmd
        elif RELATIVE.match(cmd):
            move(arm, j, cur + float(cmd))
            last = None
        elif cmd.startswith("step"):
            try:
                step = float(cmd.split()[1])
                print(f"  1 回に {step:g}° 動かします")
            except (IndexError, ValueError):
                print("  例: step 2")
        elif cmd in ("lo", "hi"):
            results[j][cmd] = cur
            print(f"  {'下限' if cmd == 'lo' else '上限'}を {cur:.0f}° として記録しました")
        elif cmd.startswith("memo"):
            text = cmd[4:].strip()
            if text:
                results[j]["memo"].append(text)
                print("  メモしました")
        elif cmd == "n":
            if j < n - 1:
                j, last = j + 1, None
                banner(j, n)
            else:
                print("  最後の関節です。q で終了します")
        elif cmd == "b":
            if j > 0:
                j, last = j - 1, None
                banner(j, n)
            else:
                print("  最初の関節です")
        elif cmd == "h":
            arm.home()
            wait_still(arm)
        elif cmd == "q":
            break
        elif cmd == "?":
            print(HELP)
        else:
            try:
                move(arm, j, float(cmd))
                last = None
            except ValueError:
                print("  ? で操作の説明を表示します")

    print("全関節を初期位置へ戻します…")
    arm.home()
    wait_still(arm)
    print_summary(results)
    input("\nアームを手で支えてから Enter を押すと脱力します")


def main():
    p = argparse.ArgumentParser(description="関節ごとの動作確認")
    p.add_argument("--port", help="Pico のシリアルポート（省略すると自動で探す）")
    p.add_argument("--speed", type=float, default=30, help="動く速さ（度/秒）")
    p.add_argument("--step", type=float, default=5, help="+ / - 1 回で動かす角度")
    args = p.parse_args()

    with SG90Arm(port=args.port) as arm:
        try:
            run(arm, args.speed, args.step)
        except KeyboardInterrupt:
            arm.relax()
            print("\n脱力を指示しました。止まらないときはサーボ電源を抜いてください")
        except EOFError:
            pass
        finally:
            arm.relax()
            time.sleep(0.2)  # 送信し切ってからポートを閉じる


if __name__ == "__main__":
    main()
