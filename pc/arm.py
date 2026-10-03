"""アーム操作の共通インターフェース。

検出や目標計算のコードは ArmBackend だけを使う。
家では SG90Arm、当日は SO101Arm に差し替えるだけで、他のコードはそのまま動く。
"""

import json
import time
from pathlib import Path

import serial
from serial.tools import list_ports

PICO_VID = 0x2E8A  # Raspberry Pi の USB ベンダーID


class ArmBackend:
    """全アーム共通の操作。角度はすべて度。"""

    num_joints = 0

    def set_joints(self, angles):
        raise NotImplementedError

    def get_joints(self):
        raise NotImplementedError

    def home(self):
        raise NotImplementedError

    def relax(self):
        raise NotImplementedError

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def find_pico_port():
    for p in list_ports.comports():
        if p.vid == PICO_VID:
            return p.device
    raise RuntimeError("Pico が見つかりません。USB の接続と main.py の書き込みを確認してください。")


class SG90Arm(ArmBackend):
    """Pico 経由で SG90 を動かす練習用アーム。"""

    def __init__(self, port=None, num_joints=5, timeout=0.5):
        self.num_joints = num_joints
        self.ser = serial.Serial(port or find_pico_port(), 115200, timeout=timeout)
        time.sleep(0.2)
        self.ser.reset_input_buffer()
        if not self.ping():
            raise RuntimeError("Pico から応答がありません。main.py が動いているか確認してください。")

    # --- 低レベル通信 ---
    def _send(self, line):
        self.ser.write((line + "\n").encode())

    def _ask(self, line, prefix, retries=3):
        for _ in range(retries):
            self.ser.reset_input_buffer()
            self._send(line)
            deadline = time.time() + self.ser.timeout
            while time.time() < deadline:
                resp = self.ser.readline().decode(errors="ignore").strip()
                if resp.startswith(prefix):
                    return resp
                if resp.startswith("ERR"):
                    raise RuntimeError(resp)
        return None

    # --- 共通インターフェース ---
    def ping(self):
        return self._ask("P", "OK") is not None

    def set_joints(self, angles):
        """目標角度を送る。返事は待たないので、カメラのフレームごとに呼んでよい。"""
        if len(angles) > self.num_joints:
            raise ValueError(f"関節は最大 {self.num_joints} 個です")
        self._send("J " + ",".join(f"{a:.1f}" for a in angles))

    def get_joints(self):
        """現在の指令角度。SG90 は位置を測れないので、実際の角度ではない。"""
        resp = self._ask("Q", "POS")
        if resp is None:
            raise RuntimeError("Pico から応答がありません")
        return [float(v) for v in resp[4:].split(",")]

    def home(self):
        self._send("H")

    def relax(self):
        """全サーボを脱力する。終了時に確実に届くよう、数回送って送信し切る。"""
        for _ in range(3):
            self._send("D")
            try:
                self.ser.flush()
            except Exception:
                pass
            time.sleep(0.05)

    def close(self):
        self.ser.close()

    # --- SG90 専用 ---
    def set_speed(self, deg_per_sec):
        """動く速さ（度/秒）。0 で即時。遅いほど震えが少ない。"""
        self._send(f"S {deg_per_sec}")

    def raw_pulse(self, index, us):
        """キャリブレーション用。パルス幅を直接送る。"""
        self._send(f"R {index} {us}")


SO101_JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]
SO101_CALIB_FILE = Path(__file__).with_name("so101_calib.json")
TICKS_PER_DEG = 4096 / 360

# STS3215 のレジスタ（アドレス）。9〜31 は電源を切っても残る EEPROM
ADDR_MIN_LIMIT = 9
ADDR_MAX_LIMIT = 11
ADDR_HOMING_OFFSET = 31
ADDR_TORQUE = 40
ADDR_GOAL_POS = 42
ADDR_GOAL_SPEED = 46
ADDR_LOCK = 55
ADDR_PRESENT_POS = 56
HALF_TURN = 2047  # 補正後、関節の真ん中がこの値になる（LeRobot と同じ）


def find_so101_port():
    """Pico 以外の USB シリアル（usbmodem / usbserial）を探す。"""
    found = [p.device for p in list_ports.comports()
             if p.vid is not None and p.vid != PICO_VID
             and ("usbmodem" in p.device or "usbserial" in p.device or p.device.startswith(("COM", "/dev/ttyACM", "/dev/ttyUSB")))]
    if len(found) == 1:
        return found[0]
    if not found:
        raise RuntimeError("SO-101 が見つかりません。USB と電源（12V/5V アダプタ）の接続を確認してください。")
    raise RuntimeError(f"候補が複数あります。--port で指定してください: {found}")


def _decode_pos(v):
    # STS3215 は 15 ビット目が符号
    return -(v & 0x7FFF) if v & 0x8000 else v


def _encode_offset(v):
    # 補正値は 11 ビット目が符号
    v = max(-2047, min(2047, int(v)))
    return (-v) | 0x800 if v < 0 else v


class SO101Arm(ArmBackend):
    """Feetech STS3215 × 6 個の SO-101 を、Feetech 公式 SDK で直接動かす（LeRobot・PyTorch 不要）。

    角度は SG90Arm と同じく「各関節の中央 = 90°」。中央・向き・安全範囲は
    so101_calib.json から読む（python so101_check.py calib で作る）。
    ファイルがなければ、サーボの中央 2048 を 90° とし、90±60° に制限する。
    """

    num_joints = 6

    def __init__(self, port=None, ids=(1, 2, 3, 4, 5, 6), baudrate=1_000_000,
                 calib_file=SO101_CALIB_FILE, speed=30):
        import scservo_sdk as scs  # SG90 だけ使う人は入れなくてよいように、ここで読み込む

        self.scs = scs
        self.ids = list(ids)
        self.calib = self._load_calib(calib_file)
        self.port = scs.PortHandler(port or find_so101_port())
        self.ph = scs.PacketHandler(0)
        if not self.port.openPort() or not self.port.setBaudRate(baudrate):
            raise RuntimeError(f"{self.port.getPortName()} を開けません")
        missing = [i for i in self.ids if self.ph.ping(self.port, i)[1] != scs.COMM_SUCCESS]
        if missing:
            self.port.closePort()
            raise RuntimeError(f"サーボ ID {missing} から応答がありません。電源とケーブルを確認してください。")
        self.target = self.get_joints()  # 今の姿勢から動き始める（急に跳ねないように）
        self.set_speed(speed)

    def _load_calib(self, path):
        n = self.num_joints
        calib = {"center": [2048] * n, "sign": [1] * n,
                 "lo": [30.0] * n, "hi": [150.0] * n, "home": [90.0] * n}
        path = Path(path)
        if path.exists():
            calib.update(json.loads(path.read_text()))
        return calib

    # --- 角度 ↔ サーボの値 ---
    def _to_deg(self, i, ticks):
        c = self.calib
        return 90 + c["sign"][i] * (ticks - c["center"][i]) / TICKS_PER_DEG

    def _to_ticks(self, i, deg):
        c = self.calib
        deg = min(max(deg, c["lo"][i]), c["hi"][i])  # 安全範囲で切る
        return int(round(c["center"][i] + c["sign"][i] * (deg - 90) * TICKS_PER_DEG))

    # --- 低レベル通信 ---
    def _sync_write(self, addr, size, values):
        scs = self.scs
        gw = scs.GroupSyncWrite(self.port, self.ph, addr, size)
        for i, v in zip(self.ids, values):
            data = [scs.SCS_LOBYTE(v), scs.SCS_HIBYTE(v)] if size == 2 else [v]
            gw.addParam(i, data)
        gw.txPacket()

    def read_ticks(self):
        scs = self.scs
        gr = scs.GroupSyncRead(self.port, self.ph, ADDR_PRESENT_POS, 2)
        for i in self.ids:
            gr.addParam(i)
        if gr.txRxPacket() == scs.COMM_SUCCESS and all(gr.isAvailable(i, ADDR_PRESENT_POS, 2) for i in self.ids):
            return [_decode_pos(gr.getData(i, ADDR_PRESENT_POS, 2)) for i in self.ids]
        # 一括読み出しに失敗したら 1 個ずつ読む
        ticks = []
        for i in self.ids:
            v, res, _ = self.ph.read2ByteTxRx(self.port, i, ADDR_PRESENT_POS)
            if res != scs.COMM_SUCCESS:
                raise RuntimeError(f"サーボ {i} の位置を読めません")
            ticks.append(_decode_pos(v))
        return ticks

    # --- 共通インターフェース ---
    def set_joints(self, angles):
        """目標角度を送る。先頭から指定した数だけ更新する。返事は待たない。"""
        if len(angles) > self.num_joints:
            raise ValueError(f"関節は最大 {self.num_joints} 個です")
        self.target[:len(angles)] = [float(a) for a in angles]
        self._sync_write(ADDR_TORQUE, 1, [1] * len(self.ids))
        self._sync_write(ADDR_GOAL_POS, 2, [self._to_ticks(i, a) for i, a in enumerate(self.target)])

    def get_joints(self):
        """実際の角度（SO-101 はサーボが位置を返せる）。"""
        return [self._to_deg(i, t) for i, t in enumerate(self.read_ticks())]

    def home(self):
        self.set_joints(self.calib["home"])

    def relax(self):
        """全サーボを脱力する。SO-101 は脱力すると腕が落ちるので、手で支えてから呼ぶ。"""
        for _ in range(3):
            self._sync_write(ADDR_TORQUE, 1, [0] * len(self.ids))
            time.sleep(0.05)

    def close(self):
        self.port.closePort()

    # --- SG90Arm と同じ追加機能 ---
    def set_speed(self, deg_per_sec):
        """動く速さ（度/秒）。0 で最速。"""
        self._sync_write(ADDR_GOAL_SPEED, 2, [int(deg_per_sec * TICKS_PER_DEG)] * len(self.ids))

    # --- キャリブレーション（サーボの EEPROM に書く。電源を切っても残る）---
    def _write_eeprom(self, addr, size, values):
        """脱力してロックを外してから 1 個ずつ書き、最後にロックし直す。"""
        scs = self.scs
        self.relax()
        for i, v in zip(self.ids, values):
            self.ph.write1ByteTxRx(self.port, i, ADDR_LOCK, 0)
            write = self.ph.write2ByteTxRx if size == 2 else self.ph.write1ByteTxRx
            res, err = write(self.port, i, addr, v)
            if res != scs.COMM_SUCCESS or err:
                raise RuntimeError(f"サーボ {i} のアドレス {addr} に書き込めません")
            self.ph.write1ByteTxRx(self.port, i, ADDR_LOCK, 1)

    def reset_calibration(self):
        """補正値 0、動ける範囲 0〜4095 に戻す。"""
        self._write_eeprom(ADDR_HOMING_OFFSET, 2, [0] * len(self.ids))
        self._write_eeprom(ADDR_MIN_LIMIT, 2, [0] * len(self.ids))
        self._write_eeprom(ADDR_MAX_LIMIT, 2, [4095] * len(self.ids))

    def write_homing_offsets(self):
        """今の姿勢が各関節の真ん中（値 2047）になるよう補正値を書く。LeRobot の calibrate と同じ。

        reset_calibration() のあとに、腕を動ける範囲の真ん中にしてから呼ぶ。
        真ん中が 2047 になるので、動かしても値が 0/4095 をまたがない。
        """
        offsets = [t - HALF_TURN for t in self.read_ticks()]
        self._write_eeprom(ADDR_HOMING_OFFSET, 2, [_encode_offset(o) for o in offsets])
        return offsets

    def write_limits(self, lo_ticks, hi_ticks):
        """サーボ自身が動ける範囲を書く（範囲外の指令はサーボが受け付けない）。"""
        self._write_eeprom(ADDR_MIN_LIMIT, 2, [max(0, int(v)) for v in lo_ticks])
        self._write_eeprom(ADDR_MAX_LIMIT, 2, [min(4095, int(v)) for v in hi_ticks])


def open_arm(kind="sg90", port=None):
    """kind に "sg90" か "so101" を指定してアームを開く。"""
    if kind == "so101":
        return SO101Arm(port=port)
    return SG90Arm(port=port)
