"""組み立てたアームの準備運動。関節を 1 つずつ小さく動かして、動きを確かめる。

動かしていない関節は 90° で保持する。終わると全関節 90° で保持したまま止まる。

使い方:
  python warmup.py                 J0 から J4 まで順番に ±20° 動かす
  python warmup.py 2 3             J2 と J3 だけ動かす
  オプション:
    --amp 30      振れ幅（度）。90 を中心に ± この角度
    --speed 20    動く速さ（度/秒）
    --repeat 3    くり返す回数
    --relax       最後に脱力する（アームが垂れるので手で支える）
    --port COM5   ポート指定（省略すると自動で探す）
"""

import argparse
import time

from arm import SG90Arm

NAMES = ["J0 土台", "J1 肩", "J2 肘", "J3 手首", "J4 手首回転"]
CENTER = 90.0


def wiggle(arm, j, amp, speed, num_joints):
    for a in (CENTER - amp, CENTER + amp, CENTER):
        angles = [CENTER] * num_joints
        prev = arm.get_joints()[j]
        angles[j] = a
        arm.set_joints(angles)
        time.sleep(abs(a - prev) / speed + 0.8)


def main():
    p = argparse.ArgumentParser(description="関節の準備運動")
    p.add_argument("joints", nargs="*", type=int, help="動かす関節の番号（省略すると全部）")
    p.add_argument("--amp", type=float, default=20)
    p.add_argument("--speed", type=float, default=20)
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--relax", action="store_true")
    p.add_argument("--port")
    args = p.parse_args()

    with SG90Arm(port=args.port) as arm:
        n = arm.num_joints
        joints = args.joints or list(range(n))
        try:
            arm.set_speed(args.speed)
            arm.set_joints([CENTER] * n)
            time.sleep(2)
            for r in range(args.repeat):
                if args.repeat > 1:
                    print(f"=== {r + 1}/{args.repeat} 回目 ===")
                for j in joints:
                    print(f"--- {NAMES[j]} 開始 {time.strftime('%H:%M:%S')} ---", flush=True)
                    wiggle(arm, j, args.amp, args.speed, n)
                    time.sleep(2)
            print(f"終了 {time.strftime('%H:%M:%S')}（全関節 90° で保持中）")
        except KeyboardInterrupt:
            print("\n中断しました（Pico は動き続けることがあります。止まらなければサーボ電源を抜く）")
        finally:
            if args.relax:
                arm.relax()
                time.sleep(0.2)  # 送信し切ってからポートを閉じる
                print("脱力しました")


if __name__ == "__main__":
    main()
