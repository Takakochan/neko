"""猫じゃらしのキャスティング。釣り竿を振るように、糸の先のおもちゃを遠くへ飛ばす。

  構え → 振りかぶり → ため → 肩・肘・手首の順に一気に振り出して急停止
  → 竿先を下げておもちゃを着地 → ピクピク動かしながら手前へ引き寄せ → 構え

関節の向き（組み立て後に確認）: 全関節 90° で竿は真上。J1 肩・J2 肘・J3 手首は
角度を増やすと前に倒れる。竿の前への傾き = (J1-90) + (J2-90) + (J3-90)。

使い方:
  python cast.py --slow        全部ゆっくり（30°/秒）で姿勢だけ確かめる。最初はこれ
  python cast.py               本番
  python cast.py --times 3     3 回くり返す
  オプション: --relax 最後に脱力 / --port ポート指定
非常停止はサーボ電源を抜く（Ctrl+C は PC 側を止めるだけ）。
"""

import argparse
import time

from arm import SG90Arm

# (J1 肩, J2 肘, J3 手首) の姿勢。J0 と J4 は 90° のまま
READY = (100, 110, 110)    # 構え: 竿は前へ 50°
BACK = (40, 40, 65)        # 振りかぶり: 後ろへ 90°（水平）
STOP = (105, 115, 110)     # 振り出しの止め: 前へ 80°（ここでピタッと止めて糸を飛ばす）
LAND = (110, 130, 135)     # 着地: 竿は水平より 15° 下
REEL = (100, 110, 120)     # 引き寄せの終わり: 前へ 50°

STAGGER = 0.04  # 振り出しで肩 → 肘 → 手首をずらす時間（秒）。一度に電流を食わないように


def pose(arm, p):
    arm.set_joints([90, *p, 90])


def go(arm, p, speed, wait):
    arm.set_speed(speed)
    pose(arm, p)
    time.sleep(wait)



def travel(a, b, speed):
    """a から b へ speed で動くのにかかる時間（秒）。"""
    return max(abs(x - y) for x, y in zip(a, b)) / speed


def throw(arm, slow):
    """構えから振りかぶって投げ、竿先を下げて着地させる（LAND の姿勢で終わる）。"""
    speed = 30 if slow else None  # --slow のときは全部この速さ

    print("  振りかぶり")
    go(arm, BACK, speed or 60, travel(READY, BACK, speed or 60) + (0.5 if slow else 0.2))

    print("  振り出し")
    if slow:
        go(arm, STOP, speed, travel(BACK, STOP, speed) + 0.5)
    else:
        arm.set_speed(0)  # 最速
        for k in range(3):  # 肩 → 肘 → 手首 の順に目標を切り替える
            target = list(BACK)
            target[: k + 1] = STOP[: k + 1]
            pose(arm, target)
            time.sleep(STAGGER)
        time.sleep(0.6)  # 止めた姿勢で糸が伸び切るのを待つ

    print("  着地")
    go(arm, LAND, speed or 40, travel(STOP, LAND, speed or 40) + 0.5)


def reel(arm, slow, start=LAND):
    """start の姿勢から手前へ引き寄せて構えに戻る。"""
    speed = 30 if slow else None

    print("  引き寄せ")
    go(arm, REEL, speed or 40, travel(start, REEL, speed or 40) + 0.3)
    steps = 5
    # for i in range(1, steps + 1):
    #     # LAND から REEL へ少しずつ戻しつつ、手首をピクッと動かす
    #     p = [l + (r - l) * i / steps for l, r in zip(LAND, REEL)]
    #     twitch = list(p)
    #     twitch[2] -= 8
    #     pose(arm, twitch)
    #     time.sleep(0.25 if not slow else 0.6)
    #     pose(arm, p)
    #     time.sleep(0.5 if not slow else 0.8)

    print("  構え")
    go(arm, READY, speed or 40, travel(REEL, READY, speed or 40) + 0.5)


def cast_once(arm, slow):
    throw(arm, slow)
    reel(arm, slow)


def main():
    p = argparse.ArgumentParser(description="猫じゃらしのキャスティング")
    p.add_argument("--slow", action="store_true", help="全部ゆっくり動かして姿勢を確かめる")
    p.add_argument("--times", type=int, default=1)
    p.add_argument("--relax", action="store_true", help="最後に脱力する")
    p.add_argument("--port")
    args = p.parse_args()

    with SG90Arm(port=args.port) as arm:
        try:
            print("構えの姿勢へ")
            arm.set_speed(30)
            pose(arm, READY)
            time.sleep(travel((90, 90, 90), READY, 30) + 1.0)
            for n in range(args.times):
                print(f"--- {n + 1} 投目 {time.strftime('%H:%M:%S')} ---", flush=True)
                cast_once(arm, args.slow)
            print("終了（構えの姿勢で保持中）")
        except KeyboardInterrupt:
            print("\n中断しました（止まらなければサーボ電源を抜く）")
        finally:
            if args.relax:
                arm.relax()
                time.sleep(0.2)
                print("脱力しました")


if __name__ == "__main__":
    main()
