"""SO-101 の接続確認とキャリブレーション。

使い方:
  python so101_check.py watch      今の角度を表示し続ける（脱力して手で動かすと値が変わる）
  python so101_check.py calib      中央・動ける範囲・待機姿勢を記録して so101_calib.json に保存
  python so101_check.py manual     角度を手入力して動かす（例: 90,80,100,90,90,90）
  オプション: --port /dev/cu.usbmodemXXXX でポート指定（省略すると自動で探す）
非常停止はアームの電源を抜く（Ctrl+C は PC 側を止めるだけ）。
"""

import argparse
import json
import select
import sys
import time

from arm import SO101_CALIB_FILE, SO101_JOINTS, TICKS_PER_DEG, SO101Arm

MARGIN_DEG = 5  # 手で動かした範囲の端から、これだけ内側を安全範囲にする


def watch(arm, until_enter=False):
    """角度を表示し続ける。until_enter なら Enter で止めて、各関節の最小・最大の値を返す。"""
    lo = hi = None
    while True:
        ticks = arm.read_ticks()
        lo = ticks if lo is None else [min(a, b) for a, b in zip(lo, ticks)]
        hi = ticks if hi is None else [max(a, b) for a, b in zip(hi, ticks)]
        degs = arm.get_joints() if not until_enter else ticks
        print("\r" + "  ".join(f"{n[:8]}:{v:7.1f}" for n, v in zip(SO101_JOINTS, degs)), end="", flush=True)
        if until_enter and select.select([sys.stdin], [], [], 0)[0]:
            sys.stdin.readline()
            print()
            return lo, hi
        time.sleep(0.1)


def calib(arm):
    arm.relax()
    print("アームを脱力しました。")
    input("1) 各関節を動ける範囲の真ん中にして、Enter > ")
    center = arm.read_ticks()
    print("2) 各関節を 1 つずつ、端から端までゆっくり動かしてください。終わったら Enter")
    lo, hi = watch(arm, until_enter=True)
    for name, l, h in zip(SO101_JOINTS, lo, hi):
        if h - l > 3500:
            print(f"注意: {name} の値が 0/4095 をまたいだ可能性があります。範囲を確認してください。")
    input("3) 待機姿勢（猫に当たらない、たたんだ姿勢）にして、Enter > ")
    home_ticks = arm.read_ticks()

    def deg(i, t):
        return round(90 + (t - center[i]) / TICKS_PER_DEG, 1)

    data = {
        "center": center,
        "sign": [1] * len(center),
        "lo": [deg(i, t) + MARGIN_DEG for i, t in enumerate(lo)],
        "hi": [deg(i, t) - MARGIN_DEG for i, t in enumerate(hi)],
        "home": [deg(i, t) for i, t in enumerate(home_ticks)],
    }
    SO101_CALIB_FILE.write_text(json.dumps(data, indent=2))
    print(f"保存しました: {SO101_CALIB_FILE}")
    for i, n in enumerate(SO101_JOINTS):
        print(f"  {n:14s} 範囲 {data['lo'][i]:6.1f} 〜 {data['hi'][i]:6.1f}°  待機 {data['home'][i]:6.1f}°")


def manual(arm):
    print("角度をカンマ区切りで入力（例: 90,80,100,90,90,90）。h で待機姿勢、d で脱力、q で終了")
    print("今の角度:", ", ".join(f"{a:.1f}" for a in arm.get_joints()))
    while True:
        line = input("> ").strip()
        if line == "q":
            break
        if line == "h":
            arm.home()
        elif line == "d":
            arm.relax()
        elif line:
            arm.set_joints([float(v) for v in line.split(",")])
        time.sleep(1.0)
        print("今の角度:", ", ".join(f"{a:.1f}" for a in arm.get_joints()))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=["watch", "calib", "manual"])
    p.add_argument("--port")
    p.add_argument("--speed", type=float, default=30, help="動く速さ（度/秒）")
    args = p.parse_args()

    with SO101Arm(port=args.port, speed=args.speed) as arm:
        print(f"接続しました: {arm.port.getPortName()}")
        try:
            if args.mode == "watch":
                print("Ctrl+C で終了")
                watch(arm)
            elif args.mode == "calib":
                calib(arm)
            else:
                manual(arm)
        except KeyboardInterrupt:
            print()


if __name__ == "__main__":
    main()
