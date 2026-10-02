"""Click on the teaser tip to print its HSV value.

Usage: python pc/vision/hsv_picker.py data/videos/popo_01.mp4 [seconds]
"""
import sys

import cv2

path = sys.argv[1]
t = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0

cap = cv2.VideoCapture(path)
cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
ok, frame = cap.read()
cap.release()
if not ok:
    sys.exit(f"could not read a frame at {t}s from {path}")

hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)


def on_click(event, x, y, flags, param):
    if event == cv2.EVENT_LBUTTONDOWN:
        h, s, v = (int(c) for c in hsv[y, x])
        print(f"({x}, {y})  HSV = {h}, {s}, {v}")


cv2.namedWindow("pick")
cv2.setMouseCallback("pick", on_click)
cv2.imshow("pick", frame)
print("Click the teaser tip a few times. Press any key to quit.")
cv2.waitKey(0)
cv2.destroyAllWindows()
