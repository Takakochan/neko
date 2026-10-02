"""アーム操作の共通インターフェース。

検出や目標計算のコードは ArmBackend だけを使う。
家では SG90Arm、当日は SO101Arm に差し替えるだけで、他のコードはそのまま動く。
"""

import time

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


class SO101Arm(ArmBackend):
    """当日、LeRobot を使って SO-101 用に実装する。

    set_joints / get_joints / home / relax を LeRobot の制御に対応させれば、
    検出・目標計算のコードは変更不要。SG90 と SO-101 では関節の向きと
    ゼロ点が違うので、角度の変換もここで行う。
    """

    num_joints = 6

    def __init__(self, *args, **kwargs):
        raise NotImplementedError("当日 LeRobot で実装する")
