# main.py - Raspberry Pi Pico / Pico W 用 SG90 アーム制御 (MicroPython)
#
# PC から USB シリアルで 1 行ずつコマンドを受け取り、サーボを動かす。
#
# コマンド一覧（改行で終わる 1 行）
#   J a0,a1,a2,...   関節の目標角度（度）。先頭から指定した数だけ更新する
#   S v              動く速さ（度/秒）。0 で即時に動く
#   H                初期位置へ戻る
#   D                全サーボを脱力（PWM 停止）
#   R i us           サーボ i にパルス幅 us（マイクロ秒）を直接送る（キャリブレーション用）
#   P                生存確認。"OK" を返す
#   Q                現在の指令角度を "POS a0,a1,..." で返す
#                    ※SG90 は位置を返せないので、実際の角度ではなく指令値

import sys
import select
import time
from machine import Pin, PWM

# ===== 設定（キャリブレーション後に書き換える）=====
# (GPピン番号, 最小パルスus, 最大パルスus, 安全範囲の下限°, 上限°, 初期角°)
SERVOS = [
    (0, 500, 2400, 0, 180, 90),   # J0 土台の旋回
    (1, 500, 2400, 20, 160, 90),  # J1 肩（一番負荷が大きいので範囲を狭めに）
    (2, 500, 2400, 0, 180, 90),   # J2 肘
    (3, 500, 2400, 0, 180, 90),   # J3 手首（スナップ用）
    (4, 500, 2400, 0, 180, 90),   # J4 手首回転／グリッパー（使わなければ消してよい）
]

TICK_MS = 20          # 角度を更新する間隔（50Hz）
DEFAULT_SPEED = 180   # 度/秒。小さくすると震え（ジッター）が減る
PERIOD_US = 20000     # SG90 の PWM 周期（50Hz）


class Servo:
    def __init__(self, pin, min_us, max_us, lo, hi, home):
        self.pwm = PWM(Pin(pin))
        self.pwm.freq(50)
        self.min_us = min_us
        self.max_us = max_us
        self.lo = lo
        self.hi = hi
        self.home = float(home)
        self.pos = float(home)
        self.target = float(home)
        self.attached = True
        self.write(self.pos)

    def write_us(self, us):
        self.pwm.duty_u16(int(us * 65535 / PERIOD_US))

    def write(self, angle):
        us = self.min_us + (self.max_us - self.min_us) * angle / 180
        self.write_us(us)

    def set_target(self, angle):
        # 安全範囲の外は切り詰める
        self.target = min(max(float(angle), self.lo), self.hi)
        if not self.attached:
            # 脱力中や raw の後は PWM を出し直す（目標が今と同じでも力が入るように）
            self.attached = True
            self.write(self.pos)

    def step(self, max_delta):
        # 目標へ少しずつ近づける（急な変化による震えと負荷を減らす）
        if not self.attached:
            return
        diff = self.target - self.pos
        if diff > max_delta:
            diff = max_delta
        elif diff < -max_delta:
            diff = -max_delta
        if diff != 0:
            self.pos += diff
            self.write(self.pos)

    def raw(self, us):
        # キャリブレーション用。滑らか移動を止めてパルス幅を直接出す
        self.attached = False
        self.write_us(us)
        # 次に J で動かすとき、この位置から滑らかに動き出すように角度を合わせておく
        angle = (us - self.min_us) * 180 / (self.max_us - self.min_us)
        self.pos = min(max(angle, 0.0), 180.0)

    def detach(self):
        self.attached = False
        self.pwm.duty_u16(0)


servos = [Servo(*cfg) for cfg in SERVOS]
speed = DEFAULT_SPEED


def reply(msg):
    sys.stdout.write(msg + "\n")


def handle(line):
    global speed
    if not line:
        return
    cmd = line[0].upper()
    arg = line[1:].strip()
    try:
        if cmd == "J":
            values = [float(v) for v in arg.split(",") if v.strip()]
            if len(values) > len(servos):
                reply("ERR too many joints")
                return
            for s, a in zip(servos, values):
                s.set_target(a)
        elif cmd == "S":
            speed = max(0.0, float(arg))
        elif cmd == "H":
            for s in servos:
                s.set_target(s.home)
        elif cmd == "D":
            for s in servos:
                s.detach()
        elif cmd == "R":
            i, us = arg.split()
            servos[int(i)].raw(float(us))
        elif cmd == "P":
            reply("OK")
        elif cmd == "Q":
            reply("POS " + ",".join("%.1f" % s.pos for s in servos))
        else:
            reply("ERR unknown " + line)
    except (ValueError, IndexError):
        reply("ERR bad " + line)


poll = select.poll()
poll.register(sys.stdin, select.POLLIN)
buf = ""
last = time.ticks_ms()
reply("READY")

while True:
    # 届いている文字をすべて読む（止まらない読み方）
    while poll.poll(0):
        c = sys.stdin.read(1)
        if c in "\r\n":
            if buf:
                handle(buf.strip())
                buf = ""
        else:
            buf += c
            if len(buf) > 200:  # 壊れた入力で溢れないように
                buf = ""

    now = time.ticks_ms()
    dt = time.ticks_diff(now, last)
    if dt >= TICK_MS:
        last = now
        max_delta = speed * dt / 1000 if speed > 0 else 360
        for s in servos:
            s.step(max_delta)
    time.sleep_ms(1)
