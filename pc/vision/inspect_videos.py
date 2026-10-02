import cv2, glob
for p in sorted(glob.glob("videos/*.mp4")):
    cap = cv2.VideoCapture(p)
    print(p, cap.get(cv2.CAP_PROP_FPS), int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
          int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)), int(cap.get(cv2.CAP_PROP_FRAME_COUNT)))
          