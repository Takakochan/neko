"""SG90 アームの動作確認とキャリブレーション。

使い方:
  python test_arm.py sweep            各関節を順番に少し動かす
  python test_arm.py manual           角度を手入力して動かす（例: 90,45,120,60）
  python test_arm.py calib 2          サーボ2 のパルス幅を手入力して 0°/90°/180° の位置を探す
  オプション: --port COM5 や --port /dev/ttyACM0 でポート指定
"""

import argparse
import time

from arm import SG90Arm


def sweep(arm):
    arm.set_speed(90)
    arm.home()
    time.sleep(1.5)
    for i in range(arm.num_joints):
        print(f"関節 {i} を動かします")
        for a in (60, 120, 90):
            angles = [90.0] * arm.num_joints
            angles[i] = a
            arm.set_joints(angles)
            time.sleep(1.0)
    print("完了")


def manual(arm):
    print("角度をカンマ区切りで入力（例: 90,45,120,60）。h=初期位置 d=脱力 q=終了")
    while True:
        line = input("> ").strip()
        if line == "q":
            break
        if line == "h":
            arm.home()
        elif line == "d":
            arm.relax()
        elif line:
            try:
                arm.set_joints([float(v) for v in line.split(",")])
                print("指令角度:", arm.get_joints())
            except ValueError as e:
                print("入力エラー:", e)


def calib(arm, index):
    print(f"サーボ {index} のキャリブレーション")
    print("パルス幅（us）を入力。500 付近が 0°、1450 付近が 90°、2400 付近が 180° の目安。")
    print("サーボが唸ったり震え続けたりしたら行き過ぎ。その手前が限界。q=終了")
    while True:
        line = input("us> ").strip()
        if line == "q":
            break
        try:
            arm.raw_pulse(index, float(line))
        except ValueError:
            print("数字を入力してください")
    print("見つけた最小・最大パルス幅を pico/main.py の SERVOS に書き込んでください。")
    arm.home()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=["sweep", "manual", "calib"])
    p.add_argument("index", nargs="?", type=int, default=0)
    p.add_argument("--port")
    p.add_argument("--joints", type=int, default=5)
    args = p.parse_args()

    with SG90Arm(port=args.port, num_joints=args.joints) as arm:
        try:
            if args.mode == "sweep":
                sweep(arm)
            elif args.mode == "manual":
                manual(arm)
            else:
                calib(arm, args.index)
        except KeyboardInterrupt:
            pass
        finally:
            arm.relax()


if __name__ == "__main__":
    main()
