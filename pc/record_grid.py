"""arm_api 用の data/grid.json を作る。カメラを固定してから使う。

カメラ映像に 3×3 の目安の点が出るので、点ごとに
  1. アームを手で動かして、じゃらしの先端を目安の点のあたりへ持っていく
     （l で今の姿勢のまま固定できる。もう一度 l で脱力）
  2. 映像の中のじゃらしの先端をクリック → そのときの関節角度を記録
を繰り返す。目安の点ぴったりでなくてよい（クリックした場所が記録される）。
最後に待機姿勢（猫に当たらない、たたんだ姿勢）にして h を押し、s で保存。

キー:  l 固定/脱力   h 今の姿勢を待機姿勢として記録   u 最後の点を取り消し
       s 保存して終了   q 保存せずに終了

使い方:
  python pc/record_grid.py              SO-101 で記録（neko フォルダで実行する）
  python pc/record_grid.py --backend lerobot
                                        M4 Mac などで LeRobot を使う場合（--port 必須）
  python pc/record_grid.py --no-arm     アームなしでクリックだけ試す（関節角度は 0）
  オプション: --camera 番号 / --port ポート / --out 保存先（既定 data/grid.json）
so101 と lerobot では関節角度の値が違うので、本番で使うバックエンドで記録する。
非常停止はアームの電源を抜く。
"""

import argparse
import json
import shutil
import time
from pathlib import Path

import cv2

from arm_api import BACKENDS

WIN = "record grid"
GUIDE = (0.2, 0.5, 0.8)  # 目安の点（画面の幅・高さに対する割合）


class NoArm:
    def connect(self): pass
    def disconnect(self): pass
    def read_joints(self): return [0.0] * 6
    def set_joints(self, joints): pass
    def torque_off(self): pass


def label(img, text, y, color=(255, 255, 255)):
    # putText は日本語を描けないので、画面の表示は英語にする
    cv2.putText(img, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 3)
    cv2.putText(img, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 1)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--port")
    p.add_argument("--out", default="data/grid.json")
    p.add_argument("--backend", choices=["so101", "lerobot"], default="so101")
    p.add_argument("--no-arm", action="store_true")
    args = p.parse_args()

    if args.port:
        import os
        os.environ["ARM_PORT"] = args.port
    arm = NoArm() if args.no_arm else BACKENDS[args.backend]()
    cap = cv2.VideoCapture(args.camera, cv2.CAP_AVFOUNDATION)
    if not cap.isOpened():
        raise SystemExit("カメラを開けません。システム設定 → プライバシーとセキュリティ → カメラ で、"
                         "ターミナル（または VS Code）を許可してください。")

    arm.connect()
    arm.torque_off()
    locked = False
    points, home, clicks = [], None, []
    cv2.namedWindow(WIN)
    cv2.setMouseCallback(WIN, lambda ev, x, y, *_: ev == cv2.EVENT_LBUTTONDOWN and clicks.append((x, y)))
    print("アームを脱力しました。手で支えながら動かしてください。")

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                raise SystemExit("カメラから画像を取れません")
            h, w = frame.shape[:2]

            for x, y in clicks:
                joints = arm.read_joints()
                points.append({"pixel": [x, y], "joints": [round(j, 2) for j in joints]})
                print(f"点 {len(points)}: 画素 ({x}, {y})  関節 {[round(j, 1) for j in joints]}")
            clicks.clear()

            for gx in GUIDE:
                for gy in GUIDE:
                    cv2.drawMarker(frame, (int(gx * w), int(gy * h)), (180, 180, 180), cv2.MARKER_CROSS, 20, 1)
            for i, pt in enumerate(points, 1):
                cv2.circle(frame, tuple(pt["pixel"]), 6, (0, 200, 255), -1)
                cv2.putText(frame, str(i), (pt["pixel"][0] + 8, pt["pixel"][1] - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1)
            label(frame, f"points {len(points)}/9   home {'OK' if home else '--'}   "
                         f"arm {'LOCKED' if locked else 'relaxed'}", 24)
            label(frame, "click tip | l lock | h home | u undo | s save | q quit", 48, (200, 200, 200))
            cv2.imshow(WIN, frame)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("l"):
                if locked:
                    arm.torque_off()
                else:
                    arm.set_joints(arm.read_joints())  # 今の姿勢のまま力を入れる
                locked = not locked
            elif key == ord("h"):
                home = [round(j, 2) for j in arm.read_joints()]
                print("待機姿勢:", home)
            elif key == ord("u") and points:
                print(f"点 {len(points)} を取り消しました")
                points.pop()
            elif key == ord("q"):
                print("保存せずに終了します")
                break
            elif key == ord("s"):
                if home is None:
                    print("先に待機姿勢を h で記録してください")
                    continue
                if len(points) < 9:
                    print(f"注意: 点が {len(points)} 個です（9 個あると動きが正確になります）")
                out = Path(args.out)
                out.parent.mkdir(parents=True, exist_ok=True)
                if out.exists():
                    backup = out.with_name(f"{out.stem}.bak-{time.strftime('%Y%m%d-%H%M%S')}{out.suffix}")
                    shutil.copy(out, backup)
                    print(f"前の {out} を {backup} に残しました")
                out.write_text(json.dumps({"backend": None if args.no_arm else args.backend,
                                            "image_size": [w, h], "home": home, "points": points}, indent=2))
                print(f"保存しました: {out}（点 {len(points)} 個）")
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()
        arm.disconnect()  # l で固定していたら力が入ったまま。腕を支えてから電源を切る


if __name__ == "__main__":
    main()
