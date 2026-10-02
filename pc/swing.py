# swing.py - 1 関節を往復させる（動いて、待って、反対へ）
#
# 使い方の例:
#   python swing.py 1 60 120              J1 を 60° ↔ 120° で往復（間に 3 秒待つ）
#   python swing.py 1 60 120 --wait 1.5   待ち時間を 1.5 秒にする
#   python swing.py 1 60 120 --speed 90   動く速さを 90 度/秒にする
#   python swing.py 1 60 120 --times 5    5 往復で終わる（省略すると止めるまで続く）
#
# 途中でやめるときは Ctrl+C。ただし非常停止はサーボ電源を抜くこと。
#
# arm.py と同じフォルダ（pc/）に置いて実行する。

import argparse
import time

from arm import SG90Arm


def wait_still(arm, timeout=20.0):
    """全関節が止まるまで待つ。"""
    prev = arm.get_joints()
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(0.1)
        now = arm.get_joints()
        if max(abs(a - b) for a, b in zip(now, prev)) < 0.01:
            return now
        prev = now
    return prev


def move(arm, joint, angle):
    """指定の関節だけを angle へ動かし、止まるまで待つ。"""
    angles = arm.get_joints()
    angles[joint] = float(angle)
    arm.set_joints(angles)
    actual = wait_still(arm)[joint]
    if abs(actual - angle) > 0.5:
        print(f"  ! main.py の安全範囲の外なので {actual:.0f}° で止まっています")
    return actual


def main():
    p = argparse.ArgumentParser(description="1 関節を往復させる")
    p.add_argument("joint", type=int, help="関節の番号（0〜4）")
    p.add_argument("a", type=float, help="一方の角度")
    p.add_argument("b", type=float, help="もう一方の角度")
    p.add_argument("--wait", type=float, default=3.0, help="動いたあとの待ち時間（秒）")
    p.add_argument("--speed", type=float, default=60, help="動く速さ（度/秒）")
    p.add_argument("--times", type=int, default=0, help="往復の回数。0 で止めるまで続ける")
    p.add_argument("--port", help="Pico のシリアルポート（省略すると自動で探す）")
    args = p.parse_args()

    with SG90Arm(port=args.port) as arm:
        try:
            arm.set_speed(args.speed)
            print(f"J{args.joint} を {args.a:.0f}° ↔ {args.b:.0f}° で往復します"
                  f"（{args.wait:g} 秒待ち、{args.speed:g} 度/秒）")
            print("止めるときは Ctrl+C")

            n = 0
            while args.times == 0 or n < args.times:
                n += 1
                for angle in (args.a, args.b):
                    print(f"[{n} 往復目] {angle:.0f}° へ")
                    move(arm, args.joint, angle)
                    time.sleep(args.wait)
            print("完了")
        except KeyboardInterrupt:
            print("\n中断しました")
        finally:
            print("初期位置へ戻します")
            arm.home()
            wait_still(arm)
            input("アームを手で支えてから Enter を押すと脱力します")
            arm.relax()
            time.sleep(0.2)


if __name__ == "__main__":
    main()
