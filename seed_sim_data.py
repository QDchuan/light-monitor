# -*- coding: utf-8 -*-
"""
 —— 模拟光照数据生成与入库脚本
============================================
功能：生成 4 个 Modbus 节点(0x01-0x04)最近 72 小时的模拟光照数据
      （每 15 分钟一条，约 1150+ 条），写入 MySQL labview_DAQ 库
      的 light_records 表，用于"智慧养殖光照监测系统"功能演示。

数据模型：
  - 白天(6:00-18:00)：正弦日照曲线 300~3500 lux + 高斯噪声
  - 夜间：微弱环境光 0~35 lux
  - 节点间存在个体差异（传感器偏差）
  - 内置 3 段演示事件（遮挡异常 / 补光灯开启 / 传感器波动），
    供飞书告警与继电器联动演示使用

用法：
  python seed_sim_data.py             # 建表(如不存在)并追加数据
  python seed_sim_data.py --reset     # 重建表结构并重新生成（清空旧数据）
"""
import argparse
import math
import random
import sys
from datetime import datetime, timedelta

import pymysql

# ---------- 数据库配置 ----------
MYSQL = dict(host="127.0.0.1", port=3306, user="root", password="123456", charset="utf8mb4")
DB_NAME = "labview_DAQ"
TABLE = "light_records"

# ---------- 模拟参数 ----------
NODES = [0x01, 0x02, 0x03, 0x04]     # 硬件地址
HOURS_BACK = 72                      # 模拟最近 72 小时
INTERVAL_MIN = 15                    # 采样间隔（分钟）
SEED = 42                            # 随机种子，保证可复现

# 各节点特性：白天基准偏差 / 夜间微光范围
NODE_PROFILE = {
    0x01: {"day_bias": 0,    "night_range": (0.0, 30.0)},
    0x02: {"day_bias": -200, "night_range": (0.0, 25.0)},
    0x03: {"day_bias": 150,  "night_range": (0.0, 35.0)},
    0x04: {"day_bias": -50,  "night_range": (0.0, 20.0)},
}

# 演示事件：node 硬件地址 / day 相对起始日第几天 / 时间段(小时) / 光照值范围(lux)
# day: 0=起始日, 1=第二天, 2=第三天(即"昨天"), 3=今天
EVENTS = [
    # 事件1：0x01 前两天上午 10:00-11:30 被遮挡 → 光照骤降（模拟异常，触发告警）
    {"node": 0x01, "day": 2, "start_h": 10.0, "end_h": 11.5, "level": (60.0, 120.0), "note": "0x01 遮挡异常"},
    # 事件2：0x02 昨天夜间 20:00-23:00 补光灯开启 → 夜间光照升高（模拟继电器联动）
    {"node": 0x02, "day": 2, "start_h": 20.0, "end_h": 23.0, "level": (300.0, 450.0), "note": "0x02 补光灯开启"},
    # 事件3：0x03 前天下午 15:00-16:00 传感器读数波动异常
    {"node": 0x03, "day": 1, "start_h": 15.0, "end_h": 16.0, "level": (50.0, 90.0), "note": "0x03 传感器波动"},
]

CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS `{TABLE}` (
    `id` INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '采集序号(自增)',
    `device_address` TINYINT UNSIGNED NOT NULL COMMENT '采集硬件地址(0x01-0x04)',
    `illuminance` DECIMAL(8,1) NOT NULL COMMENT '光照测量值(lux)',
    `measure_time` DATETIME NOT NULL COMMENT '测量时间',
    PRIMARY KEY (`id`),
    KEY `idx_address` (`device_address`),
    KEY `idx_time` (`measure_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='光照度采集记录表'
"""


def daylight_lux(hour_f: float, day_bias: float = 0.0):
    """白天(6:00-18:00)正弦日照模型，夜间返回 None"""
    if 6.0 <= hour_f <= 18.0:
        base = 300.0 + 3200.0 * math.sin(math.pi * (hour_f - 6.0) / 12.0)
        return base + day_bias
    return None


def generate_records():
    """生成模拟数据记录列表 [(address, illuminance, time_str), ...]"""
    random.seed(SEED)
    now = datetime.now().replace(second=0, microsecond=0)
    start = now - timedelta(hours=HOURS_BACK)

    records = []
    t = start
    while t <= now:
        hour_f = t.hour + t.minute / 60.0
        day_offset = (t.date() - start.date()).days
        for addr in NODES:
            prof = NODE_PROFILE[addr]
            value = None

            # 1) 优先检查是否命中演示事件
            for ev in EVENTS:
                if (ev["node"] == addr and day_offset == ev["day"]
                        and ev["start_h"] <= hour_f < ev["end_h"]):
                    value = random.uniform(*ev["level"])
                    break

            # 2) 否则按昼夜模型生成
            if value is None:
                day = daylight_lux(hour_f, prof["day_bias"])
                if day is not None:
                    value = day + random.gauss(0, 60)      # 白天：日照 + 噪声
                else:
                    value = random.uniform(*prof["night_range"])  # 夜间微光

            value = max(0.0, round(value, 1))
            records.append((addr, value, t.strftime("%Y-%m-%d %H:%M:%S")))
        t += timedelta(minutes=INTERVAL_MIN)
    return records


def main():
    parser = argparse.ArgumentParser(description="生成模拟光照数据并写入 MySQL")
    parser.add_argument("--reset", action="store_true", help="重建表结构并清空旧数据")
    args = parser.parse_args()

    try:
        conn = pymysql.connect(**MYSQL, database=DB_NAME)
    except pymysql.err.OperationalError as e:
        # 数据库不存在则先创建
        if e.args[0] == 1049:
            conn = pymysql.connect(**MYSQL)
            with conn.cursor() as cur:
                cur.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` "
                    "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci"
                )
            conn.commit()
            conn.close()
            conn = pymysql.connect(**MYSQL, database=DB_NAME)
        else:
            raise

    with conn.cursor() as cur:
        if args.reset:
            cur.execute(f"DROP TABLE IF EXISTS `{TABLE}`")
            print(f"[重置] 已删除旧表 {TABLE}")
        cur.execute(CREATE_SQL)
        conn.commit()

        # 生成数据
        records = generate_records()
        print(f"[生成] 共 {len(records)} 条模拟记录")

        sql = f"INSERT INTO `{TABLE}` (`device_address`, `illuminance`, `measure_time`) VALUES (%s, %s, %s)"
        cur.executemany(sql, records)
        conn.commit()
        print(f"[入库] 成功写入 {cur.rowcount} 条记录")

        # 验证统计
        cur.execute(f"SELECT COUNT(*) FROM `{TABLE}`")
        total = cur.fetchone()[0]
        cur.execute(
            f"SELECT device_address, COUNT(*), MIN(illuminance), MAX(illuminance), "
            f"MIN(measure_time), MAX(measure_time) FROM `{TABLE}` GROUP BY device_address ORDER BY device_address"
        )
        print("\n===== 入库验证 =====")
        print(f"总记录数: {total}")
        print(f"{'节点':<6}{'条数':<8}{'最小lux':<12}{'最大lux':<12}{'最早时间':<22}{'最晚时间'}")
        for row in cur.fetchall():
            addr, cnt, mn, mx, t_min, t_max = row
            print(f"0x{addr:02X}  {cnt:<8}{float(mn):<12.1f}{float(mx):<12.1f}{str(t_min):<22}{t_max}")

    conn.close()
    print("\n完成。")


if __name__ == "__main__":
    sys.exit(main())
