# -*- coding: utf-8 -*-
"""
 —— Modbus 模拟从站（替代 RS485 光照传感器 / 继电器实物）
====================================================================
以 pymodbus 3.8.x 启动一个 Modbus TCP Server，模拟 4 个从站单元
(unit 0x01-0x04)。每个单元提供：
  - 保持寄存器 HR[0]：当前光照值 (lux, 整数)
  - 线圈 Coil[0]    ：补光灯开关 (0=关, 1=开)

光照模型：
  - 白天(6:00-18:00)：正弦日照 300~3500 lux + 噪声
  - 夜间：微弱环境光 0~35 lux
  - 内置异常时段（与历史数据剧情一致）：
      0x01 每天 10:00-11:30 遮挡 → 光照骤降至 60~120 lux
      0x03 每天 15:00-16:00 波动 → 光照波动至 50~90 lux
  - 补光灯联动：Coil[0]=1 且当前光照 < 500 lux 时，光照抬升至 300~450 lux
    （模拟"光照不足 → 继电器闭合 → 补光生效"）

用法：
  python modbus_slave_server.py --port 5020
  python modbus_slave_server.py --sim-hour 22.0   # 强制模拟夜间时刻（便于演示）
  python modbus_slave_server.py --speed 60        # 时间加速：1分钟=1小时（实时演示模式）
"""
import argparse
import math
import random
import threading
import time
from datetime import datetime, timedelta

from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusServerContext,
    ModbusSlaveContext,
)
from pymodbus.server import StartTcpServer

# ---------------- 模拟参数 ----------------
NODES = [0x01, 0x02, 0x03, 0x04]

# 各节点个体差异：白天基准偏差 / 夜间微光范围
NODE_PROFILE = {
    0x01: {"day_bias": 0,    "night_range": (0.0, 30.0)},
    0x02: {"day_bias": -200, "night_range": (0.0, 25.0)},
    0x03: {"day_bias": 150,  "night_range": (0.0, 35.0)},
    0x04: {"day_bias": -50,  "night_range": (0.0, 20.0)},
}

# 内置异常时段（当天循环）
ABNORMAL_WINDOWS = [
    # (节点, 开始小时, 结束小时, 光照范围, 说明)
    (0x01, 10.0, 11.5, (60.0, 120.0), "0x01 遮挡异常"),
    (0x03, 15.0, 16.0, (50.0, 90.0), "0x03 传感器波动"),
]

LIGHT_ON_LUX = (300.0, 450.0)   # 补光生效后的光照范围
LIGHT_TRIGGER = 500.0           # 光照低于该值视为"光照不足"
HOST = "127.0.0.1"
PORT = 5020


def current_hour() -> float:
    now = datetime.now()
    return now.hour + now.minute / 60.0 + now.second / 3600.0


def compute_lux(addr: int, hour_f: float, coil_on: bool) -> int:
    """按昼夜模型 + 异常窗口 + 补光联动计算当前光照值"""
    prof = NODE_PROFILE[addr]
    value = None

    # 1) 异常窗口注入
    for node, s_h, e_h, rng, _ in ABNORMAL_WINDOWS:
        if addr == node and s_h <= hour_f < e_h:
            value = random.uniform(*rng)
            break

    # 2) 正常昼夜模型
    if value is None:
        if 6.0 <= hour_f <= 18.0:
            base = 300.0 + 3200.0 * math.sin(math.pi * (hour_f - 6.0) / 12.0)
            value = base + prof["day_bias"] + random.gauss(0, 60)
        else:
            value = random.uniform(*prof["night_range"])

    # 3) 补光联动：光照不足且补光灯开启 → 抬升
    if coil_on and value < LIGHT_TRIGGER:
        value = random.uniform(*LIGHT_ON_LUX)

    return max(0, int(round(value)))


def build_context(init_speed=1):
    """创建 4 个从站单元的 Modbus 数据上下文
    0x01 单元额外提供 HR[1] = 全局模拟倍速寄存器（页面可实时调整）
    """
    slaves = {}
    for unit in NODES:
        # 注意：pymodbus 3.8 数据块需从地址 1 起始（0 起始块会被视为空块）
        if unit == 0x01:
            # HR[0]=光照值, HR[1]=模拟倍速
            hr = ModbusSequentialDataBlock(0x01, [0, int(init_speed)])
        else:
            hr = ModbusSequentialDataBlock(0x01, [0])   # 保持寄存器: 光照值
        co = ModbusSequentialDataBlock(0x01, [0])   # 线圈: 补光灯开关
        di = ModbusSequentialDataBlock(0x01, [0])
        ir = ModbusSequentialDataBlock(0x01, [0])
        slaves[unit] = ModbusSlaveContext(di=di, co=co, hr=hr, ir=ir)
    return ModbusServerContext(slaves=slaves, single=False), slaves


def main():
    parser = argparse.ArgumentParser(description="Modbus 模拟从站（4 节点光照传感器）")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--sim-hour", type=float, default=None,
                        help="强制模拟时刻(小时, 如 22.0 模拟夜间)，缺省用真实时间")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="时间加速因子：1=真实时间；60=1分钟模拟1小时"
                             "（演示用，几分钟跑完一个昼夜循环，曲线起伏明显）")
    args = parser.parse_args()

    context, slaves = build_context(init_speed=args.speed)

    t0 = time.time()
    start_virtual = datetime.now()
    prev_speed = None

    def update_loop():
        nonlocal t0, start_virtual, prev_speed
        while True:
            if args.sim_hour is not None:
                hour_f = args.sim_hour
            else:
                # 读取全局倍速寄存器 HR[1]（0x01 节点），支持页面实时调速
                speed_vals = slaves[0x01].getValues(3, 1, 1)
                speed = int(speed_vals[0]) if speed_vals and speed_vals[0] > 0 else 1
                if prev_speed is None:
                    prev_speed = speed
                if speed != prev_speed:
                    # 倍速变化：重置基准，保持虚拟时刻连续不跳变
                    elapsed = time.time() - t0
                    start_virtual = start_virtual + timedelta(seconds=elapsed * prev_speed)
                    t0 = time.time()
                    prev_speed = speed
                elapsed = time.time() - t0
                virtual_dt = start_virtual + timedelta(seconds=elapsed * speed)
                hour_f = (virtual_dt.hour + virtual_dt.minute / 60.0
                          + virtual_dt.second / 3600.0)
            for unit in NODES:
                slave = slaves[unit]
                # 读补光灯线圈状态 (fc=1 -> coils)；初始 0 值块可能返回空，容错
                coil_vals = slave.getValues(1, 0, 1)
                coil = coil_vals[0] if coil_vals else 0
                lux = compute_lux(unit, hour_f, bool(coil))
                # 写保持寄存器 (fc=3 -> holding registers)
                slave.setValues(3, 0, [lux])
            time.sleep(1.0)

    threading.Thread(target=update_loop, daemon=True).start()

    if args.sim_hour is not None:
        print(f"[从站] 模拟时刻 {args.sim_hour:0.1f} 时 (固定)")
    elif args.speed != 1.0:
        print(f"[从站] 初始倍速：{args.speed:g}（HR[1] 可运行时调整）")
    print(f"[从站] 监听 {args.host}:{args.port}，模拟节点 {[hex(n) for n in NODES]}")
    print("[从站] HR[0]=光照lux, HR[1]=倍速(0x01), Coil[0]=补光灯(主站可写 0x05)")
    StartTcpServer(context=context, address=(args.host, args.port))


if __name__ == "__main__":
    main()
