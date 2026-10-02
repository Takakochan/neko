"""Trajectory CSVs -> 5 behavioral features per video.

Usage:
  python pc/vision/features.py                       # all of data/tracks/*.csv
  python pc/vision/features.py data/tracks/fiep.csv  # just one

Output: data/features/features.csv (one row per video) + a table in the terminal.

Rules:
  - The session starts at the first frame where the cat is detected
    (time before the cat arrives is not play).
  - Gaps up to MAX_INTERP_FRAMES are interpolated (detector flicker).
    Longer gaps stay empty and count as "not moving" (the cat left).
  - Speeds are also expressed in body lengths (bl = median box width),
    so videos shot at different distances can be compared.
  - Teaser features (latency, distance) are NaN if the teaser was not tracked.
"""
import argparse
import glob
from pathlib import Path

import numpy as np
import pandas as pd

# ---- thresholds: tune these on your videos ----
MAX_INTERP_FRAMES = 5     # fill detector flicker up to this many frames
SMOOTH_FRAMES = 5         # moving-average window before differentiating
MOVE_SPEED_BL = 0.3       # body lengths/s: above = "moving"
POUNCE_SPEED_BL = 3.0     # body lengths/s: a burst above this = pounce
POUNCE_REFRACTORY_S = 1.0 # min time between two pounces
WINDOW_S = 30.0           # engagement window length
WINDOW_STEP_S = 5.0       # engagement window step
TEASER_MOVE_BL = 1.0      # teaser speed (bl/s) that counts as "moving"
TEASER_STILL_S = 0.5      # teaser must be still this long before a "start"
REACT_MAX_S = 5.0         # no reaction within this = ignored event
MIN_TEASER_COVERAGE = 0.1 # need teaser in >=10% of frames for teaser features


def load(path: Path):
    df = pd.read_csv(path)
    fps = 1.0 / df["t"].diff().median()
    first = df["cat_x"].first_valid_index()
    if first is None:
        return None, fps
    df = df.loc[first:].reset_index(drop=True)
    df["t"] = df["t"] - df["t"].iloc[0]
    cols = ["cat_x", "cat_y", "cat_x1", "cat_x2", "teaser_x", "teaser_y"]
    for c in cols:
        df[c] = df[c].interpolate(limit=MAX_INTERP_FRAMES, limit_area="inside")
    return df, fps


def smooth(s: pd.Series) -> pd.Series:
    return s.rolling(SMOOTH_FRAMES, center=True, min_periods=3).mean()


def speed(x: pd.Series, y: pd.Series, fps: float) -> pd.Series:
    return np.hypot(smooth(x).diff(), smooth(y).diff()) * fps


def count_pounces(speed_bl: pd.Series, t: pd.Series) -> int:
    above = (speed_bl > POUNCE_SPEED_BL).astype(bool)
    rising = above & ~above.shift(1, fill_value=False)
    times, last = [], -np.inf
    for ti in t[rising]:
        if ti - last >= POUNCE_REFRACTORY_S:
            times.append(ti)
            last = ti
    return len(times)


def engagement_half_life(moving: pd.Series, t: pd.Series):
    """Sliding windows of fraction-of-time-moving; time until it halves.
    Returns (half_life_s, reached). If never halves, returns session length."""
    end = t.iloc[-1]
    if end < WINDOW_S:
        return np.nan, False
    starts = np.arange(0, end - WINDOW_S + 1e-9, WINDOW_STEP_S)
    frac = [moving[(t >= s) & (t < s + WINDOW_S)].mean() for s in starts]
    if not frac or frac[0] == 0:
        return np.nan, False
    for s, f in zip(starts, frac):
        if f <= frac[0] / 2:
            return s + WINDOW_S / 2, True
    return end, False


def reaction_latency(teaser_bl: pd.Series, cat_bl: pd.Series, t: pd.Series, fps: float):
    still_frames = int(TEASER_STILL_S * fps)
    moving = (teaser_bl > TEASER_MOVE_BL).fillna(False).to_numpy()
    cat_moving = (cat_bl > MOVE_SPEED_BL).fillna(False).to_numpy()
    tt = t.to_numpy()
    lat = []
    for i in range(still_frames, len(moving)):
        if moving[i] and not moving[i - still_frames:i].any():
            if cat_moving[i]:
                continue  # cat already moving: not a reaction to this start
            j_end = np.searchsorted(tt, tt[i] + REACT_MAX_S)
            hits = np.nonzero(cat_moving[i:j_end])[0]
            if len(hits):
                lat.append(tt[i + hits[0]] - tt[i])
    return (float(np.median(lat)) if lat else np.nan), len(lat)


def features_for(path: Path) -> dict:
    df, fps = load(path)
    row = {"video": path.stem, "cat": path.stem.split("_")[0]}
    if df is None:
        print(f"{path.name}: no cat detected, skipped")
        return row

    t = df["t"]
    session_s = float(t.iloc[-1])
    body_px = float((df["cat_x2"] - df["cat_x1"]).median())
    cat_px = speed(df["cat_x"], df["cat_y"], fps)
    cat_bl = cat_px / body_px
    moving = (cat_bl > MOVE_SPEED_BL).fillna(False)  # absent = not moving

    half, reached = engagement_half_life(moving, t)
    row.update(
        fps=round(fps, 2),
        session_s=round(session_s, 1),
        cat_present=round(df["cat_x"].notna().mean(), 3),
        body_px=round(body_px, 1),
        max_speed_px_s=round(float(cat_px.quantile(0.95)), 1),
        max_speed_bl_s=round(float(cat_bl.quantile(0.95)), 2),
        pounce_rate_per_min=round(count_pounces(cat_bl, t) / (session_s / 60), 2),
        engagement_half_life_s=round(half, 1) if not np.isnan(half) else np.nan,
        half_life_reached=reached,
    )

    teaser_cov = df["teaser_x"].notna().mean()
    row["teaser_present"] = round(teaser_cov, 3)
    if teaser_cov >= MIN_TEASER_COVERAGE:
        dist = np.hypot(df["cat_x"] - df["teaser_x"], df["cat_y"] - df["teaser_y"])
        teaser_bl = speed(df["teaser_x"], df["teaser_y"], fps) / body_px
        lat, n_events = reaction_latency(teaser_bl, cat_bl, t, fps)
        row.update(
            mean_distance_to_teaser_px=round(float(dist.mean()), 1),
            mean_distance_to_teaser_bl=round(float(dist.mean() / body_px), 2),
            reaction_latency_s=round(lat, 2) if not np.isnan(lat) else np.nan,
            reaction_events=n_events,
        )
    else:
        row.update(mean_distance_to_teaser_px=np.nan, mean_distance_to_teaser_bl=np.nan,
                   reaction_latency_s=np.nan, reaction_events=0)
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", nargs="*", help="track CSVs (default: data/tracks/*.csv)")
    ap.add_argument("--out", default="data/features/features.csv")
    args = ap.parse_args()

    paths = [Path(p) for p in (args.csv or sorted(glob.glob("data/tracks/*.csv")))]
    if not paths:
        raise SystemExit("no CSVs found (run from the repo root)")

    feats = pd.DataFrame([features_for(p) for p in paths])
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    feats.to_csv(out, index=False)

    show = ["video", "session_s", "cat_present", "max_speed_bl_s", "pounce_rate_per_min",
            "engagement_half_life_s", "half_life_reached", "reaction_latency_s",
            "mean_distance_to_teaser_bl"]
    with pd.option_context("display.width", 200, "display.max_columns", None):
        print(feats[[c for c in show if c in feats]].to_string(index=False))
    print(f"\n-> {out}")


if __name__ == "__main__":
    main()
