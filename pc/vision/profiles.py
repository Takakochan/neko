"""features.csv -> personality profile per cat (JSON) + radar charts (PNG).

Usage:
  python pc/vision/profiles.py

Inputs : data/features/features.csv  (from features.py)
Outputs: data/profiles/<cat>.json     (format from the README, + "reason")
         data/profiles/<cat>_radar.png
         data/profiles/all_radar.png  (all cats side by side, for the pitch)

Classification is RELATIVE to the cats in the data (we have no ground truth):
  1. short_attention : boredom half-life was reached and is clearly shorter
                       than the group's (or under SHORT_ABS_S)
  2. hunter          : speed + pounces (+ reactivity) close to the group's best
  3. watcher         : everyone else
This is only the starting guess; the bandit corrects it during live play.
"""
import json
import re
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

FEATURES_CSV = Path("data/features/features.csv")
OUT_DIR = Path("data/profiles")

# ---- thresholds: tune these ----
SHORT_ABS_S = 30.0  # half-life under this = short_attention, whatever the group
SHORT_REL = 0.75    # ... or under this fraction of the group's median half-life
HUNTER_REL = 0.7    # hunter score (0-1, 1 = best in group) at or above this = hunter

MODE_PRIOR = {
    "hunter":          {"bird": 0.5, "mouse": 0.2, "peek": 0.1, "tease": 0.2},
    "watcher":         {"bird": 0.1, "mouse": 0.4, "peek": 0.4, "tease": 0.1},
    "short_attention": {"bird": 0.2, "mouse": 0.1, "peek": 0.2, "tease": 0.5},
}

# radar axes: (label, column, higher_is_more)
AXES = [
    ("Speed",       "max_speed_bl_s",             True),
    ("Pounces",     "pounce_rate_per_min",        True),
    ("Persistence", "engagement_half_life_s",     True),
    ("Reactivity",  "reaction_latency_s",         False),  # faster reaction = more
    ("Closeness",   "mean_distance_to_teaser_bl", False),  # closer = more
]


def cat_name(video: str) -> str:
    """fiep_01 -> fiep, orange_cat -> orange_cat"""
    return re.sub(r"_\d+$", "", str(video))


def aggregate(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["cat"] = df["video"].map(cat_name)
    num = df.select_dtypes("number").columns
    agg = df.groupby("cat")[list(num)].median()
    agg["half_life_reached"] = df.groupby("cat")["half_life_reached"].mean() >= 0.5
    agg["sessions"] = df.groupby("cat").size()
    return agg


def scores(agg: pd.DataFrame) -> pd.DataFrame:
    """0-1 per axis, 1 = best in the group. Columns with missing data are dropped."""
    out = pd.DataFrame(index=agg.index)
    for label, col, higher in AXES:
        if col not in agg or agg[col].isna().any():
            continue
        v = agg[col].astype(float)
        if higher:
            out[label] = v / v.max() if v.max() > 0 else 0.0
        else:
            out[label] = v.min() / v.where(v > 0) if v.min() > 0 else 0.0
    return out.fillna(0.0)


def classify(agg: pd.DataFrame, sc: pd.DataFrame):
    half = agg["engagement_half_life_s"]
    group_half = half.median()
    hunter_axes = [a for a in ("Speed", "Pounces", "Reactivity") if a in sc]
    result = {}
    for cat in agg.index:
        h, reached = half[cat], bool(agg.loc[cat, "half_life_reached"])
        hs = float(sc.loc[cat, hunter_axes].mean())
        if reached and not np.isnan(h) and (h < SHORT_ABS_S or h <= SHORT_REL * group_half):
            t = "short_attention"
            why = f"gets bored first: half-life {h:.0f}s vs group median {group_half:.0f}s"
        elif hs >= HUNTER_REL:
            t = "hunter"
            why = f"hunter score {hs:.2f} ({' + '.join(hunter_axes)} vs best in group)"
        else:
            t = "watcher"
            why = f"hunter score {hs:.2f} < {HUNTER_REL}, not bored early"
        if not reached:
            why += "; never got bored within the video (persistence is a lower bound)"
        result[cat] = (t, why, hs)
    return result


def radar(ax, labels, values, title, color):
    n = len(labels)
    ang = np.linspace(0, 2 * np.pi, n, endpoint=False).tolist()
    vals = list(values) + [values[0]]
    ang_c = ang + [ang[0]]
    ax.plot(ang_c, vals, color=color, linewidth=2)
    ax.fill(ang_c, vals, color=color, alpha=0.25)
    ax.set_xticks(ang)
    ax.set_xticklabels(labels, fontsize=10)
    ax.set_ylim(0, 1)
    ax.set_yticks([0.25, 0.5, 0.75, 1.0])
    ax.set_yticklabels([])
    ax.set_title(title, fontsize=12, pad=18)


def jsonable(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    return float(v) if isinstance(v, (np.floating, float)) else v


def main():
    if not FEATURES_CSV.exists():
        raise SystemExit(f"{FEATURES_CSV} not found: run features.py first (from the repo root)")
    agg = aggregate(pd.read_csv(FEATURES_CSV))
    if len(agg) < 2:
        print("warning: classification is relative; with one cat it is not meaningful")
    sc = scores(agg)
    types = classify(agg, sc)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    colors = {"hunter": "#d9822b", "watcher": "#3a7bbf", "short_attention": "#5a9a3a"}
    labels = list(sc.columns)

    for cat, (t, why, _) in types.items():
        a = agg.loc[cat]
        profile = {
            "cat_name": cat,
            "type": t,
            "reason": why,
            "sessions": int(a["sessions"]),
            "features": {
                "reaction_latency_s": jsonable(a.get("reaction_latency_s")),
                "max_speed_px_s": jsonable(a.get("max_speed_px_s")),
                "pounce_rate_per_min": jsonable(a.get("pounce_rate_per_min")),
                "mean_distance_to_teaser_px": jsonable(a.get("mean_distance_to_teaser_px")),
                "engagement_half_life_s": jsonable(a.get("engagement_half_life_s")),
            },
            "mode_prior": MODE_PRIOR[t],
        }
        (OUT_DIR / f"{cat}.json").write_text(json.dumps(profile, indent=2, ensure_ascii=False))

        fig = plt.figure(figsize=(4.5, 4.5))
        radar(fig.add_subplot(polar=True), labels, sc.loc[cat].tolist(), f"{cat} - {t}", colors[t])
        fig.tight_layout()
        fig.savefig(OUT_DIR / f"{cat}_radar.png", dpi=150)
        plt.close(fig)

    fig = plt.figure(figsize=(4.5 * len(types), 4.8))
    for i, (cat, (t, _, _)) in enumerate(types.items(), 1):
        radar(fig.add_subplot(1, len(types), i, polar=True), labels,
              sc.loc[cat].tolist(), f"{cat} - {t}", colors[t])
    fig.tight_layout()
    fig.savefig(OUT_DIR / "all_radar.png", dpi=150)
    plt.close(fig)

    for cat, (t, why, _) in types.items():
        print(f"{cat:12s} {t:16s} {why}")
    print(f"\nradar axes: {', '.join(labels)} (1 = best in group)")
    print(f"-> {OUT_DIR}/")


if __name__ == "__main__":
    main()
