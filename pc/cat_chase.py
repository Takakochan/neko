"""猫を追いかける猫じゃらし。投げて着地したあと、MacBook のカメラで猫の動きを見て、
猫がいた場所へ猫じゃらしを動かす。

  構え → 投げて着地（cast.py と同じ動き）
  → 動いたもの（猫）を見つけたら、その足元へ猫じゃらしを動かし、着いたらピクッと動かす
  → 時間切れか q で終了。正面に戻して引き寄せ → 構え

猫じゃらしの位置は 2 つの値で決める。
  J0   土台の旋回（左右）
  t    届く距離。0 = 手前（REEL の姿勢）、1 = 奥（LAND の姿勢）。J1〜J3 はこの間を補間する
カメラの画面上のどこがどの (J0, t) に当たるかは、最初に --calib で調べておく。

使い方:
  python cat_chase.py --watch        アームなしで動きの検出だけ見る（カメラの置き場所と --min-area の調整用）
  python cat_chase.py --calib        アームを 9 か所に動かし、そのつど猫じゃらしの位置をクリックする
  python cat_chase.py                本番（投げる → 猫を追う 120 秒 → 引き寄せ）
  python cat_chase.py --no-cast      投げずにゆっくり着地の姿勢へ（動作確認用）
  オプション: --duration 秒 / --speed 度/秒 / --min-area 画素 / --camera 番号 / --relax / --port
非常停止はサーボ電源を抜く（Ctrl+C や q は PC 側を止めるだけ）。

カメラが開けないときは、システム設定 → プライバシーとセキュリティ → カメラ で
ターミナル（VS Code から実行するなら VS Code）を許可する。
"""

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np

from arm import SG90Arm
from cast import LAND, READY, REEL, pose, reel, throw, travel

CALIB_FILE = Path(__file__).with_name("chase_calib.json")
NEAR, FAR = REEL, LAND  # t = 0 / t = 1 の姿勢 (J1 肩, J2 肘, J3 手首)
FRAME_W = 640           # 処理する画像の幅（画素）。距離や面積の設定はこの大きさが基準
WIN = "cat chase"


# ---------- アームの姿勢 ----------

def reach_pose(t):
    return tuple(n + (f - n) * t for n, f in zip(NEAR, FAR))


def arm_angles(j0, t):
    return [j0, *reach_pose(t), 90]


def move_time(a, b, speed):
    """(J0, t) の a から b へ動くのにかかる時間（秒）。"""
    return max(abs(a[0] - b[0]) / speed, travel(reach_pose(a[1]), reach_pose(b[1]), speed))


# ---------- カメラ ----------

def open_camera(index):
    cap = cv2.VideoCapture(index, cv2.CAP_AVFOUNDATION)
    if not cap.isOpened():
        raise RuntimeError(
            "カメラを開けません。システム設定 → プライバシーとセキュリティ → カメラ で、"
            "ターミナル（または VS Code）を許可してください。")
    return cap


def read_frame(cap):
    ok, frame = cap.read()
    if not ok:
        raise RuntimeError("カメラから画像を取れません")
    h, w = frame.shape[:2]
    if w != FRAME_W:
        frame = cv2.resize(frame, (FRAME_W, round(h * FRAME_W / w)))
    return frame


def flush_camera(cap, n=5):
    """アームの動作で待っている間にたまった古い画像を捨てる。"""
    for _ in range(n):
        cap.grab()


def label(img, text, y=24, color=(255, 255, 255)):
    # putText は日本語を描けないので、画面の表示は英語にする
    cv2.putText(img, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 3)
    cv2.putText(img, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 1)


def pressed(wait_ms=1):
    return cv2.waitKey(wait_ms) & 0xFF


# ---------- 画面の位置 ↔ アームの姿勢 ----------

def _terms(j0, t, k):
    u = (np.asarray(j0, float) - 90) / 30
    t = np.asarray(t, float)
    return np.stack([np.ones_like(u), u, t, u * t, u * u, t * t], -1)[..., :k]


class ToyMap:
    """アームの姿勢 (J0, t) → 画面上の猫じゃらしの位置。キャリブレーションの点に多項式を当てはめる。

    逆向き（画面の位置 → 姿勢）は、動ける範囲を細かく調べて一番近い点を選ぶ。
    届かない場所を指定されたときは、届く範囲で一番近いところになる。
    """

    def __init__(self, points, j0_range):
        if len(points) < 3:
            raise ValueError("キャリブレーションの点が 3 つ以上必要です")
        # 点が少ないときは式を簡単にする（9 点なら 2 次式）
        self.k = 6 if len(points) >= 8 else 4 if len(points) >= 5 else 3
        j0 = [p["j0"] for p in points]
        t = [p["t"] for p in points]
        px = np.array([p["px"] for p in points], float)
        self.coef, *_ = np.linalg.lstsq(_terms(j0, t, self.k), px, rcond=None)

        lo, hi = j0_range
        J, T = np.meshgrid(np.arange(lo, hi + 0.5, 1.0), np.linspace(0, 1, 51))
        self.grid = np.stack([J.ravel(), T.ravel()], -1)
        self.grid_px = _terms(J.ravel(), T.ravel(), self.k) @ self.coef

    def to_pixel(self, j0, t):
        x, y = _terms(j0, t, self.k) @ self.coef
        return float(x), float(y)

    def from_pixel(self, x, y):
        i = np.argmin(((self.grid_px - (x, y)) ** 2).sum(1))
        return float(self.grid[i, 0]), float(self.grid[i, 1])

    @classmethod
    def load(cls):
        if not CALIB_FILE.exists():
            raise SystemExit(f"{CALIB_FILE.name} がありません。先に python cat_chase.py --calib を実行してください")
        data = json.loads(CALIB_FILE.read_text())
        return cls(data["points"], data["j0_range"])


# ---------- 動きの検出 ----------

class MotionDetector:
    """背景との差で動いたものを見つける。猫かどうかは見分けず、大きさだけで選ぶ。"""

    def __init__(self, min_area):
        self.bg = cv2.createBackgroundSubtractorMOG2(history=200, varThreshold=40, detectShadows=False)
        self.kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        self.min_area = min_area

    def update(self, frame, learning=-1.0):
        """動いた部分のマスクと、min_area 以上の塊の外接矩形 (x, y, w, h) の一覧を返す。

        learning を大きくすると背景をすぐ覚え直す（アームが動いた直後に使う）。
        """
        mask = self.bg.apply(cv2.GaussianBlur(frame, (5, 5), 0), learningRate=learning)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self.kernel)
        mask = cv2.dilate(mask, self.kernel, iterations=2)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        blobs = [cv2.boundingRect(c) for c in contours if cv2.contourArea(c) >= self.min_area]
        return mask, blobs


def foot(b):
    """塊の下端の中央。猫の足元＝床の上の位置として使う。"""
    x, y, w, h = b
    return x + w / 2, y + h


def dist(p, q):
    return ((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2) ** 0.5


def learn_background(cap, detector, seconds):
    print(f"背景を覚えています（{seconds:g} 秒）")
    end = time.time() + seconds
    while time.time() < end:
        frame = read_frame(cap)
        detector.update(frame, learning=0.1)
        label(frame, "learning background...")
        cv2.imshow(WIN, frame)
        pressed()


# ---------- --watch: 検出だけ見る ----------

def watch(cap, args):
    detector = MotionDetector(args.min_area)
    learn_background(cap, detector, 2)
    print("動いたものを緑の枠で表示します。m = マスク表示の切り替え、q = 終了")
    show_mask = False
    while True:
        frame = read_frame(cap)
        mask, blobs = detector.update(frame)
        view = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR) if show_mask else frame
        for b in blobs:
            x, y, w, h = b
            cv2.rectangle(view, (x, y), (x + w, y + h), (0, 255, 0), 2)
            cv2.circle(view, tuple(map(int, foot(b))), 5, (0, 255, 255), -1)
            cv2.putText(view, str(w * h), (x, y - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        label(view, f"min-area {args.min_area}  blobs {len(blobs)}   m: mask  q: quit")
        cv2.imshow(WIN, view)
        key = pressed()
        if key in (ord("q"), 27):
            return
        if key == ord("m"):
            show_mask = not show_mask


# ---------- --calib: 画面の位置と姿勢の対応を調べる ----------

def calibrate(arm, cap, args):
    lo, hi = 90 - args.span, 90 + args.span
    rows = [1.0, 0.5, 0.0]  # 奥 → 手前
    cols = [lo, 90, hi]
    order = [(j0, t) for i, t in enumerate(rows) for j0 in (cols if i % 2 == 0 else cols[::-1])]

    print("アームを 9 か所に動かします。止まったら、画面上の猫じゃらし（床に着いているところ）を")
    print("クリックして Enter（またはスペース）。見えない場所は s で飛ばす。q で中止。")
    print("糸が揺れているときは、手で止めてからクリックしてかまいません。")

    click = {}
    cv2.setMouseCallback(WIN, lambda ev, x, y, *_: click.update(p=(x, y)) if ev == cv2.EVENT_LBUTTONDOWN else None)

    arm.set_speed(30)
    pose(arm, READY)
    time.sleep(travel((90, 90, 90), READY, 30) + 1.0)
    arm.set_joints(arm_angles(90, 1.0))
    time.sleep(travel(READY, reach_pose(1.0), 30) + 0.5)

    points = []
    for n, (j0, t) in enumerate(order, 1):
        print(f"  {n}/{len(order)}: J0 {j0:.0f}°  t {t:.1f}")
        arm.set_joints(arm_angles(j0, t))
        click.clear()
        while True:
            frame = read_frame(cap)
            for p in points:
                cv2.circle(frame, tuple(map(int, p["px"])), 5, (255, 0, 0), -1)
            if "p" in click:
                cv2.circle(frame, click["p"], 8, (0, 0, 255), 2)
            label(frame, f"{n}/{len(order)}  J0 {j0:.0f}  t {t:.1f}   click toy, Enter=OK  s=skip  q=quit")
            cv2.imshow(WIN, frame)
            key = pressed(30)
            if key in (13, 32) and "p" in click:
                points.append({"j0": j0, "t": t, "px": list(click["p"])})
                break
            if key == ord("s"):
                print("    飛ばしました")
                break
            if key in (ord("q"), 27):
                print("中止しました（保存していません）")
                return

    try:
        ToyMap(points, (lo, hi))
    except ValueError as e:
        print(f"保存できません: {e}")
        return
    CALIB_FILE.write_text(json.dumps({"j0_range": [lo, hi], "points": points}, indent=2))
    print(f"{len(points)} 点を {CALIB_FILE.name} に保存しました")

    arm.set_joints(arm_angles(90, 0.0))
    time.sleep(args.span / 30 + 0.5)
    pose(arm, READY)
    time.sleep(1.5)


# ---------- 本番: 猫を追う ----------

def chase(arm, cap, tmap, args):
    """着地の姿勢 (J0 90°, t 1) から始めて、猫を追う。最後の (J0, t) を返す。"""
    detector = MotionDetector(args.min_area)
    pos = (90.0, 1.0)
    now = time.time()
    end = now + args.duration
    busy_until = now + args.settle + 1.0  # 着地直後は糸の揺れと背景の変化が落ち着くまで待つ
    events = []  # (時刻, 関節角度の一覧) ピクッと動かす予定
    hits = []
    moves = 0
    show_mask = False
    flush_camera(cap)
    print(f"猫を追います（{args.duration:g} 秒。q で終了、m でマスク表示）")

    while now < end:
        frame = read_frame(cap)
        now = time.time()
        while events and events[0][0] <= now:
            arm.set_joints(events.pop(0)[1])

        busy = now < busy_until
        # アームが動いている間は背景をすぐ覚え直し、検出は使わない
        mask, blobs = detector.update(frame, learning=0.05 if busy else -1.0)
        toy = tmap.to_pixel(*pos)
        # 猫じゃらし自身の揺れは無視する
        cands = [b for b in blobs if dist(foot(b), toy) > args.ignore_radius]

        if busy or not cands:
            hits = []
        else:
            hits = (hits + [foot(max(cands, key=lambda b: b[2] * b[3]))])[-args.confirm:]

        target = None
        if len(hits) == args.confirm:
            # 数フレーム続けて見えたら本物の動きとみなす
            target = tuple(np.median(hits, axis=0))
            new = tmap.from_pixel(*target)
            if dist(tmap.to_pixel(*new), toy) > args.min_move:
                moves += 1
                print(f"  {moves}: 猫を見つけた → J0 {new[0]:.0f}°  t {new[1]:.2f}", flush=True)
                arrive = now + move_time(pos, new, args.speed)
                arm.set_speed(args.speed)
                arm.set_joints(arm_angles(*new))
                twitch = arm_angles(*new)
                twitch[3] -= 8  # 着いたら手首をピクッ
                events = [(arrive + 0.1, twitch), (arrive + 0.35, arm_angles(*new))]
                busy_until = arrive + 0.35 + args.settle
                pos = new
            hits = []

        view = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR) if show_mask else frame
        for b in blobs:
            x, y, w, h = b
            color = (0, 255, 0) if b in cands else (128, 128, 128)
            cv2.rectangle(view, (x, y), (x + w, y + h), color, 2)
        cv2.circle(view, tuple(map(int, toy)), int(args.ignore_radius), (0, 0, 255), 1)
        cv2.circle(view, tuple(map(int, toy)), 6, (0, 0, 255), -1)
        if target:
            cv2.drawMarker(view, tuple(map(int, target)), (0, 255, 255), cv2.MARKER_CROSS, 24, 2)
        state = "moving" if busy else "watching"
        label(view, f"{state}  J0 {pos[0]:.0f}  t {pos[1]:.2f}  moves {moves}  left {end - now:.0f}s   q: quit")
        cv2.imshow(WIN, view)
        key = pressed()
        if key in (ord("q"), 27):
            break
        if key == ord("m"):
            show_mask = not show_mask

    # 予定していたピクッが残っていたら、最後の姿勢に戻しておく
    if events:
        arm.set_joints(events[-1][1])
    return pos


def main():
    p = argparse.ArgumentParser(description="猫を追いかける猫じゃらし")
    p.add_argument("--watch", action="store_true", help="アームなしで動きの検出だけ見る")
    p.add_argument("--calib", action="store_true", help="画面の位置とアームの姿勢の対応を調べる")
    p.add_argument("--no-cast", action="store_true", help="投げずにゆっくり着地の姿勢へ")
    p.add_argument("--slow", action="store_true", help="投げる動作をゆっくりにする（cast.py と同じ）")
    p.add_argument("--duration", type=float, default=120, help="猫を追う時間（秒）")
    p.add_argument("--speed", type=float, default=60, help="猫じゃらしを動かす速さ（度/秒）")
    p.add_argument("--min-area", type=int, default=1500, help="これより小さい動きは無視する（画素）")
    p.add_argument("--ignore-radius", type=float, default=60, help="猫じゃらしの周りのこの範囲の動きは無視（画素）")
    p.add_argument("--min-move", type=float, default=40, help="これより近い場所へは動かない（画素）")
    p.add_argument("--confirm", type=int, default=3, help="何フレーム続けて見えたら動くか")
    p.add_argument("--settle", type=float, default=0.8, help="動いたあと検出を休む時間（秒）")
    p.add_argument("--span", type=float, default=30, help="--calib で左右に振る角度（J0 は 90 ± この値）")
    p.add_argument("--camera", type=int, default=0, help="カメラの番号（iPhone がつながると 0 にならないことがある）")
    p.add_argument("--relax", action="store_true", help="最後に脱力する")
    p.add_argument("--port")
    args = p.parse_args()

    tmap = None if args.watch or args.calib else ToyMap.load()
    cap = open_camera(args.camera)
    cv2.namedWindow(WIN)
    try:
        if args.watch:
            watch(cap, args)
            return
        with SG90Arm(port=args.port) as arm:
            try:
                if args.calib:
                    calibrate(arm, cap, args)
                    return

                print("構えの姿勢へ")
                arm.set_speed(30)
                pose(arm, READY)
                time.sleep(travel((90, 90, 90), READY, 30) + 1.0)
                if args.no_cast:
                    print("  ゆっくり着地の姿勢へ")
                    arm.set_speed(30)
                    pose(arm, LAND)
                    time.sleep(travel(READY, LAND, 30) + 0.5)
                else:
                    throw(arm, args.slow)

                j0, t = chase(arm, cap, tmap, args)

                print("正面に戻します")
                arm.set_speed(40)
                arm.set_joints(arm_angles(90, t))
                time.sleep(abs(j0 - 90) / 40 + 0.3)
                reel(arm, args.slow, start=reach_pose(t))
                print("終了（構えの姿勢で保持中）")
            except KeyboardInterrupt:
                print("\n中断しました（止まらなければサーボ電源を抜く）")
            finally:
                if args.relax:
                    arm.relax()
                    time.sleep(0.2)
                    print("脱力しました")
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
