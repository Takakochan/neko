import pandas as pd, numpy as np
from ultralytics import YOLO

def extract(path, fps):
    model = YOLO("yolo11n.pt")
    rows = []
    for i, r in enumerate(model.track(source=path, classes=[15], tracker="bytetrack.yaml",
                                      persist=True, stream=True, verbose=False)):
        row = {"video": path, "frame": i, "t": i / fps}
        if len(r.boxes):
            k = int(r.boxes.conf.argmax())
            x1, y1, x2, y2 = r.boxes.xyxy[k].tolist()
            row.update(cat_x=(x1+x2)/2, cat_y=(y1+y2)/2, cat_x1=x1, cat_y1=y1,
                       cat_x2=x2, cat_y2=y2, cat_conf=float(r.boxes.conf[k]),
                       track_id=int(r.boxes.id[k]) if r.boxes.id is not None else None)
        tx, ty = detect_teaser(r.orig_img)   # 既存のOpenCVコード、見つからなければ (None, None)
        row.update(teaser_x=tx, teaser_y=ty)
        rows.append(row)
    return pd.DataFrame(rows)