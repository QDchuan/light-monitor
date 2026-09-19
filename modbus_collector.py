# -*- coding: utf-8 -*-
"""
 —— Modbus 主站轮询采集与 MySQL 入库
==============================================
按可配置采样周期轮询 4 个从站单元(0x01-0x04)的光照保持寄存器(HR[0])，
将 (硬件地址, 光照值, 测量时间) 实时写入 MySQL labview_DAQ 库
light_records 表，构成 "采集 -> 入库" 数据链路。

用法：
  python modbus_collector.py                 # 默认 6s 周期，持续采集
  python modbus_collector.py --interval 2    # 2s 采样周期（单号进阶要求）
  python modbus_collector.py --once          # 只采集一轮（用于测试）
  python modbus_collector.py --sim-hour 22.0 # 配合从站模拟夜间场景
"""
import argparse
import sys
import time
from datetime import datetime

import pymysql
from pymodbus.client import ModbusTcpClient

# ---------------- 配置 ----------------
MODBUS_HOST = "127.0.0.1"
MODBUS_PORT = 5020
NODES = [0x01, 0x02, 0x03, 0x04]

MYSQL = dict(host="127.0.0.1", port=3306, user="root", password="123456",
             database="labview_DAQ", charset="utf8mb4")
TABLE = "light_records"


def collect_once(client, nodes, now_str):
    """轮询一轮，返回 [(address, lux, time_str), ...]"""
    rows = []
    for unit in nodes:
        try:
            rr = client.read_holding_registers(0, count=1, slave=unit)
            if rr.isError():
                print(f"  [WARN] 节点 0x{unit:02X} 读取异常: {rr}", flush=True)
                continue
            lux = rr.registers[0]
            rows.append((unit, lux, now_str))
            print(f"  0x{unit:02X} -> {lux} lux", flush=True)
        except Exception as e:
            print(f"  [WARN] 节点 0x{unit:02X} 通信失败: {e}", flush=True)
    return rows


def save_rows(conn, rows):
    if not rows:
        return 0
    sql = f"INSERT INTO `{TABLE}` (`device_address`, `illuminance`, `measure_time`) VALUES (%s, %s, %s)"
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    conn.commit()
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description="Modbus 主站轮询采集入库")
    parser.add_argument("--host", default=MODBUS_HOST)
    parser.add_argument("--port", type=int, default=MODBUS_PORT)
    parser.add_argument("--interval", type=int, default=6,
                        choices=[2, 4, 6, 8, 10],
                        help="采样周期(秒)，默认 6")
    parser.add_argument("--once", action="store_true", help="只采集一轮后退出")
    args = parser.parse_args()

    client = ModbusTcpClient(args.host, port=args.port)
    if not client.connect():
        print("[采集] 无法连接 Modbus 从站，请先启动 modbus_slave_server.py")
        sys.exit(1)
    print(f"[采集] 已连接从站 {args.host}:{args.port}")

    conn = pymysql.connect(**MYSQL)
    print(f"[采集] 采样周期 {args.interval}s，轮询节点 {[hex(n) for n in NODES]}，Ctrl+C 停止\n")

    try:
        while True:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            rows = collect_once(client, NODES, now_str)
            n = save_rows(conn, rows)
            print(f"  >>> {now_str} 入库 {n} 条 (累计轮询)\n", flush=True)
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[采集] 用户中断，已停止。")
    finally:
        client.close()
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
