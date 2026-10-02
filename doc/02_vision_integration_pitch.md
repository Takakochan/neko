動作確認済み：Linux（CPU）、torch 2.14.1+cpu。
Intel Mac で `import torch` がエラーになる場合は `pip install "numpy<2"`。

# Vision, Integration, Learning Logic & Pitch (Takako) — Detailed Steps

Your goals:
1. Video → cat trajectory → personality profile
2. Personality profile + live reactions → choice of the arm's play mode
3. The integration loop that ties everything together
4. Sunday's presentation

This role covers the most ground, so stick to **"get it working roughly end to end first, improve later"** at every stage.

---

## Friday 19:30–22:00: Get detection and tracking running

### 1. Environment

```bash
pip install ultralytics opencv-python numpy pandas scikit-learn
```

### 2. Cat detection + tracking (no training)

The COCO-pretrained model already includes "cat" (class ID 15), so no custom training is needed at first.

```python
from ultralytics import YOLO

model = YOLO("yolo11n.pt")  # speed first; move up to s / m if accuracy is lacking
results = model.track(
    source="videos/popo_01.mp4",
    classes=[15],            # cat only
    tracker="bytetrack.yaml",
    persist=True,
    stream=True,
)

rows = []
for i, r in enumerate(results):
    t = i / FPS
    if r.boxes.id is None:
        rows.append({"t": t, "cat_xy": None}); continue
    x1, y1, x2, y2 = r.boxes.xyxy[0].tolist()
    rows.append({"t": t, "cat_xy": ((x1+x2)/2, (y1+y2)/2),
                 "cat_box": (x1, y1, x2, y2)})
```

### 3. Teaser tip detection

Reuse the existing teaser code (OpenCV) as is. If it's color-based (HSV thresholds), re-tune the thresholds for the venue lighting.

### 4. Share the videos

Put the cat videos in the shared folder. Name files consistently as `catname_number.mp4`.

**Friday goal: one video in → a CSV with `cat_xy` and `teaser_xy` for every frame.**

---

## Saturday 08:30–12:00: Features and personality classification

### 5. Feature extraction

Compute from the trajectory CSV. Pixel units are fine (videos shot with the same camera setup are comparable; if setups differ, normalize by the width of the cat's bounding box).

| Feature | How to compute | Meaning |
|---|---|---|
| `reaction_latency_s` | Median time from the teaser starting to move until the cat's speed exceeds a threshold | How fast it reacts |
| `max_speed_px_s` | 95th percentile of the cat center's speed | Explosiveness |
| `pounce_rate_per_min` | Number of acceleration peaks (sudden bursts) above a threshold ÷ minutes | How often it pounces |
| `mean_distance_to_teaser_px` | Mean distance between cat and teaser | Plays up close vs. watches from afar |
| `engagement_half_life_s` | Compute "fraction of time moving" in 30 s windows; time until it drops to half the initial value | How quickly it gets bored |

Speed and acceleration are noisy, so smooth the trajectory with a moving average (~5 frames) before differentiating.

### 6. Personality types

With only 2–3 cats, **rule-based** classification is enough — and easier to explain in the demo than clustering.

```python
def classify(f):
    if f["engagement_half_life_s"] < 30:
        return "short_attention"
    if f["pounce_rate_per_min"] > 3 and f["reaction_latency_s"] < 1.0:
        return "hunter"
    return "watcher"
```

Tune thresholds on the videos you have. Define initial preferences per type:

```python
MODE_PRIOR = {
    "hunter":          {"bird": 0.5, "mouse": 0.2, "peek": 0.1, "tease": 0.2},
    "watcher":         {"bird": 0.1, "mouse": 0.4, "peek": 0.4, "tease": 0.1},
    "short_attention": {"bird": 0.2, "mouse": 0.1, "peek": 0.2, "tease": 0.5},
}
```

For the pitch, make one visualization (a radar chart of the features) that lets you say "this cat is this type because of X."

---

## 12:00 Checkpoint #1

- Review all three people's progress
- If something is behind, decide here what to cut (e.g. drop live detection and use recorded video as input)

---

## Saturday 13:00–16:30: Learning logic (bandit)

### 7. Thompson sampling

Keep a Beta distribution of successes/failures per mode, with the prior built from the personality profile.

```python
import numpy as np

class ModeSelector:
    def __init__(self, prior: dict, strength: float = 4.0):
        # convert prior probabilities into pseudo-counts
        self.a = {m: 1 + p * strength for m, p in prior.items()}
        self.b = {m: 1 + (1 - p) * strength for m, p in prior.items()}

    def choose(self) -> str:
        samples = {m: np.random.beta(self.a[m], self.b[m]) for m in self.a}
        return max(samples, key=samples.get)

    def update(self, mode: str, reward: bool):
        if reward: self.a[mode] += 1
        else:      self.b[mode] += 1
```

### 8. Defining the reward (did the cat "bite"?)

During one mode run (e.g. 8 seconds), it's a success if any of these happen:

- The cat moved toward the teaser faster than a speed threshold
- The cat–teaser distance dropped below a threshold
- At least one pounce (sudden acceleration)

If there's no real cat at the demo, have a person play the cat, or add a mode where reward comes from keyboard input (`y` / `n`). You could even invite the judges to press the keys.

### 9. Integration loop

```python
profile = load_profile("Popo")
selector = ModeSelector(profile["mode_prior"])
arm.connect(); arm.go_home()
try:
    while True:
        frame = cam.read()
        state = vision.step(frame)          # cat_xy, teaser_xy
        if state["cat_xy"] is None:
            continue
        mode = selector.choose()
        arm.play(mode, state["cat_xy"], duration_s=8)
        reward = judge_reward(vision.history(last_s=8))
        selector.update(mode, reward)
        dashboard.update(selector, mode, reward)
finally:
    arm.emergency_stop(); arm.disconnect()
```

- `arm.play` blocks; meanwhile vision keeps recording history on a separate thread
- Show a dashboard next to the camera feed with "current mode" and "success probability per mode" updating live (drawing text in an OpenCV window is enough). **This is the highlight of the demo**

---

## Saturday 16:30–20:00: Integration (everyone)

- Build the 3×3 grid lookup with the arm owner (you record the teaser tip's image coordinates at each point)
- Keep going until camera → classification → arm runs end to end. Once it does, record a video

**Saturday goal: it runs end to end at least once.**

---

## Sunday 08:00–11:00: Sharpen the presentation

- Use profiles for 2+ cats so it's obvious at a glance that **different modes get chosen** (e.g. switch screens: "Popo gets mouse, the other cat gets bird")
- Agree with the product owner on how the "automatic training pipeline" connects to their mock screens
  - This time the COCO cat class was enough, but identifying individual cats in multi-cat homes, or detecting "playing" more accurately, needs per-cat data
  - The product story: zero-shot models (YOLO-World, Grounding DINO + SAM2, etc.) auto-label the data → auto-train, so the owner does nothing

## Sunday 11:00–14:00: Code freeze and pitch

### 10. Pitch structure (check the time limit and adjust; assumes 5 min)

| Time | Content | Who |
|---|---|---|
| 0:00–0:40 | Problem: indoor cats are under-exercised and bored. Each cat likes to play differently, yet automatic toys all move the same way | Takako |
| 0:40–2:30 | **Live demo**: video → personality → arm moves → success probabilities update | Takako + arm owner operating |
| 2:30–3:15 | Tech: training-free detection → behavioral features → personality → bandit. Future: automatic training pipeline | Takako |
| 3:15–4:30 | Market and business model | Takako (materials by product owner) |
| 4:30–5:00 | Next steps / team intro | Takako |

- If the demo fails, switch to the recording immediately. Agree on the switch signal in advance
- A line about your own background (ideas drawn from other fields) in the team intro makes it memorable

### 11. Rehearsals

- Two runs, at 12:00 and 13:00. Time them
- Practice with the product owner's list of expected questions

## Checklist

- [ ] Video → trajectory CSV
- [ ] 5 features
- [ ] Personality types + radar chart
- [ ] ModeSelector and reward judgment
- [ ] Integration loop
- [ ] Dashboard display
- [ ] Demo with 2 cats getting different modes
- [ ] Pitch script and two rehearsals
