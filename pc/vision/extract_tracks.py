"""Video -> per-frame trajectory CSV (cat: YOLO + ByteTrack, teaser: HSV).

Usage:
  python pc/vision/extract_tracks.py data/videos/popo_01.mp4 --overlay
  python pc/vision/extract_tracks.py data/videos/popo_01.mp4 --overlay --teaser-hsv 35,80,80,85,255,255

Outputs (in --out, default data/tracks/):
  popo_01.csv          one row per frame, empty cells when nothing was detected
  popo_01_overlay.mp4  (with --overlay) boxes, trail and teaser drawn on the video
"""
import argparse
import time
from collections import deque
from pathlib import Path

import cv2
import pandas as pd
from ultralytics import YOLO

from teaser import detect_teaser, parse_hsv

CAT_CLASS = 15  # COCO "cat"
COLUMNS = ["video", "frame", "t", "cat_x", "cat_y", "cat_x1", "cat_y1",
           "cat_x2", "cat_y2", "cat_conf", "track_id", "teaser_x", "teaser_y"]


def pick_device(requested: str) -> str:
    if requested != "auto":
        return requested
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass
    return "cpu"


def video_info(path: Path):
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise SystemExit(f"cannot open {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    return fps, w, h, n


def draw(frame, row, trail):
    green, orange, magenta, white = (0, 200, 0), (0, 165, 255), (255, 0, 255), (255, 255, 255)
    if row["cat_x"] is not None:
        p1 = (int(row["cat_x1"]), int(row["cat_y1"]))
        p2 = (int(row["cat_x2"]), int(row["cat_y2"]))
        cv2.rectangle(frame, p1, p2, green, 2)
        label = f"cat {row['cat_conf']:.2f}"
        if row["track_id"] is not None:
            label += f" id{row['track_id']}"
        cv2.putText(frame, label, (p1[0], max(20, p1[1] - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, green, 2)
    pts = list(trail)
    for a, b in zip(pts, pts[1:]):
        if a is not None and b is not None:
            cv2.line(frame, a, b, orange, 2)
    if row["teaser_x"] is not None:
        cv2.circle(frame, (int(row["teaser_x"]), int(row["teaser_y"])), 10, magenta, 2)
    cv2.putText(frame, f"t={row['t']:.2f}s", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, white, 2)
    return frame


def summarize(df: pd.DataFrame, fps: float, elapsed: float):
    n = len(df)
    det = df["cat_x"].notna()
    gaps = (~det).astype(int).groupby(det.cumsum()).sum()
    longest = int(gaps.max()) if len(gaps) else 0
    ids = df["track_id"].dropna().unique()
    print()
    print(f"frames processed : {n} in {elapsed:.1f}s ({n / max(elapsed, 1e-6):.1f} fps)")
    print(f"cat detected     : {det.mean() * 100:.1f}% of frames")
    print(f"longest gap      : {longest} frames ({longest / fps:.2f}s)")
    print(f"track ids        : {len(ids)} {list(map(int, ids))[:10]}")
    if df["teaser_x"].notna().any():
        print(f"teaser detected  : {df['teaser_x'].notna().mean() * 100:.1f}% of frames")
    else:
        print("teaser detected  : (not run, or never found)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--model", default="yolo11n.pt", help="yolo11n.pt / yolo11s.pt / yolo11m.pt")
    ap.add_argument("--conf", type=float, default=0.25, help="detection confidence threshold")
    ap.add_argument("--device", default="auto", help="auto / cpu / mps / cuda")
    ap.add_argument("--out", default="data/tracks")
    ap.add_argument("--overlay", action="store_true", help="also write an annotated video")
    ap.add_argument("--teaser-hsv", default=None, help="h1,s1,v1,h2,s2,v2 (see hsv_picker.py)")
    args = ap.parse_args()

    path = Path(args.video)
    fps, w, h, n = video_info(path)
    device = pick_device(args.device)
    print(f"{path.name}: {w}x{h} @ {fps:.2f} fps, {n} frames ({n / fps:.1f}s), device={device}")

    hsv = parse_hsv(args.teaser_hsv) if args.teaser_hsv else None
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"{path.stem}.csv"
    overlay_path = out_dir / f"{path.stem}_overlay.mp4"

    model = YOLO(args.model)
    results = model.track(source=str(path), classes=[CAT_CLASS], conf=args.conf,
                          tracker="bytetrack.yaml", persist=True, stream=True,
                          verbose=False, device=device)

    rows, writer = [], None
    trail = deque(maxlen=int(fps * 2))  # last ~2 s of cat positions
    t0 = time.time()

    for i, r in enumerate(results):
        row = dict.fromkeys(COLUMNS)
        row.update(video=path.name, frame=i, t=i / fps)

        boxes = r.boxes
        if boxes is not None and len(boxes):
            k = int(boxes.conf.argmax())  # most confident cat
            x1, y1, x2, y2 = boxes.xyxy[k].tolist()
            row.update(cat_x=(x1 + x2) / 2, cat_y=(y1 + y2) / 2,
                       cat_x1=x1, cat_y1=y1, cat_x2=x2, cat_y2=y2,
                       cat_conf=float(boxes.conf[k]))
            if boxes.id is not None:
                row["track_id"] = int(boxes.id[k])

        if hsv is not None:
            tx, ty = detect_teaser(r.orig_img, *hsv)
            row.update(teaser_x=tx, teaser_y=ty)

        rows.append(row)

        if args.overlay:
            frame = r.orig_img.copy()
            if writer is None:  # size from the real frame (handles rotated phone videos)
                fh, fw = frame.shape[:2]
                writer = cv2.VideoWriter(str(overlay_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (fw, fh))
            trail.append((int(row["cat_x"]), int(row["cat_y"])) if row["cat_x"] is not None else None)
            writer.write(draw(frame, row, trail))

        if i and i % 300 == 0:
            print(f"  {i}/{n} frames ...")

    if writer is not None:
        writer.release()

    df = pd.DataFrame(rows, columns=COLUMNS)
    df.to_csv(csv_path, index=False, float_format="%.3f")
    summarize(df, fps, time.time() - t0)
    print(f"\nCSV     -> {csv_path}")
    if args.overlay:
        print(f"overlay -> {overlay_path}")


if __name__ == "__main__":
    main()
