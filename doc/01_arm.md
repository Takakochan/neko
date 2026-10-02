# Arm Control — Detailed Steps

Your goal: have the `arm_api.py` functions (see `00_README.md`) running reliably by Sunday 11:00. **Moving the same way every time** and **being safe for the cat** matter more than looking flashy.

> LeRobot command names and APIs can change between versions. Treat the commands below as a guide and always check them against the official docs (the SO-101 page at huggingface.co/docs/lerobot) at the venue.

---

## Friday 19:30–22:00: Setup

### 1. Environment (~30 min)

```bash
conda create -y -n lerobot python=3.10
conda activate lerobot
git clone https://github.com/huggingface/lerobot.git
cd lerobot
pip install -e ".[feetech]"
```

### 2. Connect the assembled arm (~30 min)

- Plug in the power adapter and USB
- Find the port:

```bash
lerobot-find-port
```

- Check whether the "set" includes both a leader arm (moved by hand) and a follower arm (motor-driven). With a leader you can record movements via teleoperation, which speeds things up a lot.

### 3. Calibration (~30 min)

```bash
lerobot-calibrate --robot.type=so101_follower --robot.port=<PORT> --robot.id=cat_arm
# if there is a leader arm
lerobot-calibrate --teleop.type=so101_leader --teleop.port=<PORT2> --teleop.id=cat_leader
```

### 4. Test it (~30 min)

```bash
lerobot-teleoperate \
  --robot.type=so101_follower --robot.port=<PORT> --robot.id=cat_arm \
  --teleop.type=so101_leader --teleop.port=<PORT2> --teleop.id=cat_leader
```

Also confirm you can drive it directly from Python:

```python
from lerobot.robots.so101_follower import SO101Follower, SO101FollowerConfig

robot = SO101Follower(SO101FollowerConfig(port="<PORT>", id="cat_arm"))
robot.connect()
obs = robot.get_observation()
print(obs)  # shoulder_pan.pos, shoulder_lift.pos, elbow_flex.pos,
            # wrist_flex.pos, wrist_roll.pos, gripper.pos
robot.disconnect()
```

### 5. Mount the teaser (~30 min)

- Clamping the handle in the gripper is fastest. If it slips, secure it with tape or zip ties
- Use a long handle. The point is to **keep the arm itself away from the cat's face**
- It's fine to never use the gripper open/close (at most for a "peek" move that hides the feather)

**Friday goal: send joint angles from Python and reach the intended pose.**

---

## Saturday 08:30–12:00: Build the movement primitives

### 6. Write the safety settings first

```python
# safety.py
JOINT_LIMITS = {               # fill in from measurements after calibration
    "shoulder_pan.pos":  (-60, 60),
    "shoulder_lift.pos": (-40, 40),
    "elbow_flex.pos":    (-50, 50),
    "wrist_flex.pos":    (-60, 60),
    "wrist_roll.pos":    (-90, 90),
    "gripper.pos":       (0, 100),
}
MAX_STEP_PER_TICK = 3.0        # max degrees per step (speed cap)
```

- Always clip before sending
- Wrap everything in `try/finally` so `Ctrl+C` triggers `emergency_stop()` (torque OFF)

### 7. Build the "image coordinates → arm pose" lookup (together with Takako)

There's no time to solve inverse kinematics properly, so use a **calibration grid**.

1. Fix the camera position (mark it with tape; never move it after this)
2. Mark a 3×3 = 9-point grid on the floor of the play area
3. Using teleop (or torque off and moving it by hand), bring the teaser tip about 5 cm above each point
4. Record the joint angles + Takako records the tip's coordinates in the camera image
5. Save to `grid.json`:

```json
[
  {"px": [120, 400], "joints": {"shoulder_pan.pos": -35.2, "...": 0}},
  ...
]
```

6. `move_to_pixel(x, y)` computes joint angles as a distance-weighted average of the nearest 4 points (inverse distance weighting)

If there's time, also record 9 "high" points (in the air, ~25 cm) for bird mode.

### 8. Movement with interpolation

```python
def move_joints(target: dict, speed: float):
    # step from current angles toward target in increments based on speed
    # each step <= MAX_STEP_PER_TICK * speed
    # loop at around 50 Hz
    ...
```

---

## Saturday 13:00–16:30: Build the play modes

Each mode should be callable via `play(mode, target_xy, duration_s)`. `target_xy` is the cat's position. Aim **slightly in front of the cat, not directly on top of it** (Takako can adjust this too, but make an offset configurable on the arm side as well).

| Mode | Movement | Intended cat |
|---|---|---|
| `bird` | Fast arcs up high, with occasional sudden stops | Cats who love to leap |
| `mouse` | Slow movement just above the floor with pauses, fleeing away from the cat | Cats who stalk patiently |
| `peek` | Poke just the tip out from behind cover (edge of a box or cloth), then pull back | Observant, cautious cats |
| `tease` | Small rapid wiggles just in front of the cat | Easily bored cats — pulls attention back |

Implementation tips:

- Represent each mode as "a sequence of waypoints + speed per segment" — easy to tune
- Add a bit of randomness (identical movements every time bore both cats and judges)
- Keep motion parameters (speed, height, pause length) in a dict so Takako's logic can change them later

---

## Saturday 16:30–20:00: Integration (everyone)

- Takako's integration loop imports `arm_api` and calls it
- Common issues:
  - **Camera/arm latency** → don't update the target every frame; update every 0.3–0.5 s
  - **Jitter** → ignore small target changes (dead zone)
  - **USB disconnects** → put reconnect handling in `connect()`

**Saturday goal: the arm moves toward a "stand-in cat" in the camera view (a hand, a plush toy, or a cat video on a screen), switching between modes.**

---

## Sunday 08:00–11:00: Polish

- Run each mode 10 times in a row and confirm it never stalls or goes wild
- Tune the movements to read well in a demo (big and fast enough to see from the judges' seats)
- If there's time, assemble the second (unassembled) arm — motor ID setup is required:

```bash
lerobot-setup-motors --robot.type=so101_follower --robot.port=<PORT>
```

  Assembly and calibration can take half a day, so **don't start it if integration isn't working by Saturday evening.**

## Sunday 11:00–14:00

- 11:00 code freeze. Bug fixes only after this
- Two rehearsals. Always record a run that works (insurance for the live demo)
- Decide in advance what happens if the arm stops mid-demo (who triggers `emergency_stop` → `connect` → `go_home`)

## Checklist

- [ ] Arm moves from joint angles sent via Python
- [ ] Safety limits and emergency stop
- [ ] 3×3 grid lookup
- [ ] `move_to_pixel`
- [ ] 4 modes
- [ ] Callable from the integration loop
- [ ] Stable over 10 consecutive runs
- [ ] Recording of a successful demo
