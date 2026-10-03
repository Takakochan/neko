"""arm_api: the interface from the README, usable today without the real arm.

    import arm_api as arm
    arm.connect(); arm.go_home()
    arm.play("mouse", (412, 300), duration_s=8)
    arm.emergency_stop(); arm.disconnect()

Backend is chosen with the ARM_BACKEND environment variable:
    stub  (default) prints instead of moving, so the integration loop can run now
    sg90            the practice arm in pc/arm.py
    so101           the venue arm via SO101Arm in pc/arm.py (needs data/grid.json;
                    ARM_PORT is optional, found automatically if unset). Works on any Mac.
    lerobot         the venue arm via LeRobot (Apple Silicon only, e.g. an M4 Mac:
                    pip install -r requirements/lerobot.txt, then lerobot-calibrate once)

Make data/grid.json with pc/record_grid.py once the camera is fixed. The joint values
differ between so101 and lerobot, so record the grid with the backend you will run.

Every backend only needs: connect, disconnect, set_joints(joints), torque_off.
Everything else (pixel -> joints via grid.json, smooth motion, play modes,
workspace limits) lives here and is shared by all backends.

Demo:
    python pc/arm_api.py              # runs each mode for 3 s on the stub
    python pc/arm_api.py --plot       # also saves data/arm_modes.png (planned paths)
"""
import json
import math
import os
import random
import time
from pathlib import Path

GRID_PATH = Path(os.environ.get("ARM_GRID", "data/grid.json"))
TIME_SCALE = float(os.environ.get("ARM_TIME_SCALE", "1.0"))  # 0 = don't sleep (tests)
CONTROL_HZ = 20          # joint commands per second during a move
MAX_PX_PER_S = 900.0     # teaser speed at speed=1.0, in image pixels per second
MODES = ("bird", "mouse", "peek", "tease")


# --------------------------------------------------------------------------- backends
class StubBackend:
    def __init__(self, verbose=False):
        self.verbose = verbose

    def connect(self):
        print("[arm stub] connect")

    def disconnect(self):
        print("[arm stub] disconnect")

    def set_joints(self, joints):
        if self.verbose:
            print("[arm stub] joints", [round(j, 1) for j in joints])

    def torque_off(self):
        print("[arm stub] TORQUE OFF")


class SG90Backend:
    """Practice arm (SG90Arm in pc/arm.py)."""

    def connect(self):
        from arm import SG90Arm
        self.arm = SG90Arm(port=os.environ.get("ARM_PORT"))
        self.arm.set_speed(0)  # arm_api interpolates the motion itself

    def disconnect(self):
        self.arm.close()

    def set_joints(self, joints):
        self.arm.set_joints(joints)

    def read_joints(self):
        return self.arm.get_joints()

    def torque_off(self):
        self.arm.relax()


SO101_JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]
SO101_MAX_STEP = 15.0  # max joint change (degrees) per command, so a bad grid can't make it jump


class SO101Backend:
    """Venue arm (Hugging Face SO-101) via SO101Arm in pc/arm.py (Feetech SDK, no LeRobot).

    Env: ARM_PORT (optional; found automatically), ARM_SPEED (servo speed cap, deg/s).
    Angles are SO101Arm degrees (center of each joint = 90). grid.json is recorded with
    read_joints(), so the units always match. Joint limits come from pc/so101_calib.json.
    """

    def connect(self):
        from arm import SO101Arm
        self.arm = SO101Arm(port=os.environ.get("ARM_PORT"),
                            speed=float(os.environ.get("ARM_SPEED", "180")))
        self.last = self.arm.get_joints()

    def disconnect(self):
        self.arm.close()

    def set_joints(self, joints):
        """Big jumps (e.g. go_home) are split into steps of at most SO101_MAX_STEP."""
        start = self.last[:len(joints)]
        n = max(1, math.ceil(max(abs(j - s) for j, s in zip(joints, start)) / SO101_MAX_STEP))
        for k in range(1, n + 1):
            self.last[:len(joints)] = [s + (j - s) * k / n for s, j in zip(start, joints)]
            self._send(self.last)
            if k < n:
                time.sleep(1.0 / CONTROL_HZ)

    def _send(self, joints):
        self.arm.set_joints(joints)

    def read_joints(self):
        return self.arm.get_joints()

    def torque_off(self):
        self.arm.relax()


class LeRobotBackend(SO101Backend):
    """Venue arm via LeRobot, for Apple Silicon Macs (PyTorch for Intel Macs is too old).

    Needs: pip install -r requirements/lerobot.txt, and a one-time
        lerobot-calibrate --robot.type=so101_follower --robot.port=<PORT> --robot.id=so101_follower
    Env: ARM_PORT (required; find it with `lerobot-find-port`), ARM_ID (calibration id).
    Angles are LeRobot degrees (center of each joint = 0, gripper 0-100), not SO101Arm's.
    """

    def connect(self):
        try:  # newer LeRobot
            from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
        except ImportError:  # older LeRobot
            from lerobot.robots.so101_follower import SO101Follower, SO101FollowerConfig
        import dataclasses

        port = os.environ.get("ARM_PORT")
        if not port:
            raise SystemExit("set ARM_PORT (run `lerobot-find-port` to find it)")
        kw = {"port": port, "id": os.environ.get("ARM_ID", "so101_follower")}
        fields = {f.name for f in dataclasses.fields(SO101FollowerConfig)}
        if "use_degrees" in fields:
            kw["use_degrees"] = True
        if "max_relative_target" in fields:
            kw["max_relative_target"] = SO101_MAX_STEP
        self.robot = SO101Follower(SO101FollowerConfig(**kw))
        self.robot.connect()
        self.last = self.read_joints()

    def disconnect(self):
        self.robot.disconnect()

    def _send(self, joints):
        self.robot.send_action({f"{n}.pos": float(v) for n, v in zip(SO101_JOINTS, joints)})

    def read_joints(self):
        obs = self.robot.get_observation()
        return [float(obs[f"{n}.pos"]) for n in SO101_JOINTS]

    def torque_off(self):
        self.robot.bus.disable_torque()


BACKENDS = {"sg90": SG90Backend, "so101": SO101Backend, "lerobot": LeRobotBackend}


def _make_backend():
    name = os.environ.get("ARM_BACKEND", "stub").lower()
    if name == "stub":
        return StubBackend(verbose=os.environ.get("ARM_VERBOSE") == "1")
    if name not in BACKENDS:
        raise SystemExit(f"unknown ARM_BACKEND={name!r} (stub / {' / '.join(BACKENDS)})")
    if not GRID_PATH.exists():
        raise SystemExit(f"{name} needs a real {GRID_PATH} (make it with pc/record_grid.py; "
                         "the fake grid is for the stub only)")
    recorded = json.loads(GRID_PATH.read_text()).get("backend")
    if recorded and recorded != name:
        raise SystemExit(f"{GRID_PATH} was recorded with {recorded!r}, not {name!r}; "
                         "the joint values differ, so record it again with this backend")
    return BACKENDS[name]()


# --------------------------------------------------------------------------- calibration grid
def _fake_grid():
    """3x3 grid over a 640x480 image with made-up joints; replaced by data/grid.json."""
    pts = []
    for y in (80, 240, 400):
        for x in (100, 320, 540):
            pts.append({"pixel": [x, y], "joints": [(x - 320) / 4, (y - 240) / 4, 90.0]})
    return {"home": [0.0, -60.0, 90.0], "points": pts}


def _load_grid():
    if GRID_PATH.exists():
        return json.loads(GRID_PATH.read_text())
    return _fake_grid()


def pixel_to_joints(x, y, grid):
    """Inverse-distance-weighted interpolation between the calibrated points."""
    num, den = None, 0.0
    for p in grid["points"]:
        px, py = p["pixel"]
        d2 = (x - px) ** 2 + (y - py) ** 2
        if d2 < 1e-6:
            return list(p["joints"])
        w = 1.0 / d2
        j = p["joints"]
        num = [w * v for v in j] if num is None else [a + w * v for a, v in zip(num, j)]
        den += w
    return [v / den for v in num]


def workspace(grid):
    xs = [p["pixel"][0] for p in grid["points"]]
    ys = [p["pixel"][1] for p in grid["points"]]
    return min(xs), min(ys), max(xs), max(ys)


# --------------------------------------------------------------------------- state
_backend = None
_grid = None
_pos = None          # current teaser tip in pixels
_stopped = False


def _sleep(s):
    if TIME_SCALE > 0 and s > 0:
        time.sleep(s * TIME_SCALE)


def _clamp(x, y):
    x0, y0, x1, y1 = workspace(_grid)
    return min(max(x, x0), x1), min(max(y, y0), y1)


# --------------------------------------------------------------------------- public API
def connect() -> None:
    global _backend, _grid, _stopped
    _grid = _load_grid()
    _backend = _make_backend()
    _backend.connect()
    _stopped = False


def go_home() -> None:
    global _pos
    _backend.set_joints(_grid["home"])
    x0, y0, x1, y1 = workspace(_grid)
    _pos = ((x0 + x1) / 2, y0)  # assume home is above the top-center of the workspace
    _sleep(1.0)


def move_to_pixel(x: int, y: int, speed: float) -> None:
    """Move the teaser tip to image (x, y) in a straight line; speed 0.0-1.0."""
    global _pos
    if _stopped:
        return
    speed = min(max(speed, 0.05), 1.0)
    x, y = _clamp(x, y)
    sx, sy = _pos if _pos else (x, y)
    dist = math.hypot(x - sx, y - sy)
    steps = max(1, int(dist / (speed * MAX_PX_PER_S) * CONTROL_HZ))
    for i in range(1, steps + 1):
        if _stopped:
            return
        f = i / steps
        _backend.set_joints(pixel_to_joints(sx + (x - sx) * f, sy + (y - sy) * f, _grid))
        _sleep(1.0 / CONTROL_HZ)
    _pos = (x, y)


def play(mode: str, target_xy: tuple, duration_s: float) -> None:
    """Run one play mode around the cat at target_xy for duration_s seconds."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    print(f"[arm] play {mode} at {tuple(int(v) for v in target_xy)} for {duration_s}s")
    t_end = time.monotonic() + duration_s * TIME_SCALE if TIME_SCALE > 0 else None
    budget = duration_s  # used when not sleeping (tests / planning)
    for x, y, speed, hold in plan(mode, target_xy, _grid):
        if _stopped:
            return
        before = _pos
        move_to_pixel(x, y, speed)
        _sleep(hold)
        if t_end is not None:
            if time.monotonic() >= t_end:
                return
        else:
            budget -= _move_time(before, (x, y), speed) + hold
            if budget <= 0:
                return


def emergency_stop() -> None:
    global _stopped
    _stopped = True
    if _backend:
        _backend.torque_off()


def disconnect() -> None:
    if _backend:
        _backend.disconnect()


# --------------------------------------------------------------------------- play modes
def _move_time(a, b, speed):
    if a is None:
        return 0.0
    return math.hypot(b[0] - a[0], b[1] - a[1]) / (max(speed, 0.05) * MAX_PX_PER_S)


def plan(mode, target_xy, grid):
    """Endless generator of waypoints (x, y, speed 0-1, hold seconds) for a mode.
    Coordinates are image pixels; smaller y = higher in the image."""
    x0, y0, x1, y1 = workspace(grid)
    W, H = x1 - x0, y1 - y0
    cx, cy = target_xy
    r = random.Random()

    def away(dist):  # a point `dist` away from the cat, toward the open side
        side = 1 if cx < (x0 + x1) / 2 else -1
        return cx + side * dist, cy

    if mode == "bird":      # fast, erratic, mostly above the cat; sometimes "lands" near it
        while True:
            for _ in range(r.randint(2, 4)):
                yield (cx + r.uniform(-0.35, 0.35) * W, cy - r.uniform(0.2, 0.45) * H,
                       r.uniform(0.8, 1.0), r.uniform(0.05, 0.3))
            yield cx + r.uniform(-0.15, 0.15) * W, cy - 0.05 * H, 0.9, r.uniform(0.4, 0.8)

    elif mode == "mouse":   # low along the floor, runs away from the cat, freezes
        while True:
            ax, _ = away(r.uniform(0.15, 0.45) * W)
            yield ax, cy + r.uniform(0.0, 0.1) * H, r.uniform(0.5, 0.9), r.uniform(0.3, 1.0)
            yield ax + r.uniform(-0.05, 0.05) * W, cy + 0.05 * H, 0.3, r.uniform(0.2, 0.6)

    elif mode == "peek":    # hide far away, pop out near the cat, vanish again
        hx = x0 if cx > (x0 + x1) / 2 else x1
        hy = y0 if cy > (y0 + y1) / 2 else y1
        while True:
            yield hx, hy, 1.0, r.uniform(1.0, 2.0)                       # hidden
            px, py = away(r.uniform(0.12, 0.2) * W)
            yield px, py, 0.6, r.uniform(0.5, 1.0)                       # peek out
            yield px + r.uniform(-0.03, 0.03) * W, py, 0.4, 0.3          # tiny twitch

    elif mode == "tease":   # slow approach right up to the cat, wiggle, snatch away
        while True:
            nx, ny = away(0.08 * W)
            yield nx, ny, 0.25, 0.2
            for _ in range(3):
                yield nx + r.uniform(-0.03, 0.03) * W, ny + r.uniform(-0.02, 0.02) * H, 0.6, 0.05
            fx, fy = away(0.3 * W)
            yield fx, fy, 1.0, r.uniform(0.6, 1.2)


# --------------------------------------------------------------------------- demo
def _plot_modes(path="data/arm_modes.png", seconds=8.0):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    grid = _load_grid()
    x0, y0, x1, y1 = workspace(grid)
    cat = (x0 + 0.35 * (x1 - x0), y0 + 0.6 * (y1 - y0))
    fig, axes = plt.subplots(1, 4, figsize=(16, 4.2))
    for ax, mode in zip(axes, MODES):
        pts, t, prev = [], 0.0, ((x0 + x1) / 2, y0)
        for x, y, s, hold in plan(mode, cat, grid):
            x, y = min(max(x, x0), x1), min(max(y, y0), y1)
            t += _move_time(prev, (x, y), s) + hold
            pts.append((x, y))
            prev = (x, y)
            if t > seconds:
                break
        xs, ys = zip(*pts)
        ax.plot(xs, ys, "-o", ms=3, lw=1.2, color="#d9822b")
        ax.plot(*cat, "s", ms=14, color="#3a7bbf", label="cat")
        ax.set_xlim(x0 - 20, x1 + 20)
        ax.set_ylim(y1 + 20, y0 - 20)  # image coordinates: y grows downward
        ax.set_title(f"{mode} ({seconds:.0f}s)")
        ax.set_aspect("equal")
        ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    print(f"-> {path}")


if __name__ == "__main__":
    import sys

    if "--plot" in sys.argv:
        _plot_modes()
    connect()
    go_home()
    try:
        for m in MODES:
            play(m, (250, 300), duration_s=3)
    finally:
        emergency_stop()
        disconnect()
