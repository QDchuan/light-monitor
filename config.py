# -*- coding: utf-8 -*-
"""
 —— 全局配置
=====================
集中管理数据库、Modbus 从站、登录账号、告警阈值等常量，
供 streamlit_app.py 及各采集/告警模块复用。

敏感信息（MySQL 密码、飞书 Webhook、飞书 App Secret、DeepSeek Key）
均从系统环境变量读取，不要硬编码。
"""
import os

# ---------------- MySQL 数据库 ----------------
MYSQL = dict(
    host="127.0.0.1",
    port=3306,
    user="root",
    password=os.environ.get("MYSQL_PASSWORD", "123456"),
    database="labview_DAQ",
    charset="utf8mb4",
)
DB_NAME = "labview_DAQ"
TABLE = "light_records"

# ---------------- Modbus 从站 ----------------
MODBUS_HOST = "127.0.0.1"
MODBUS_PORT = 5020          # 模拟从站监听端口（modbus_slave_server.py）
MODBUS_REG_ADDR = 0         # 光照保持寄存器地址
MODBUS_COIL_ADDR = 0        # 补光灯线圈地址
NODES = [0x01, 0x02, 0x03, 0x04]
NODE_NAMES = {0x01: "1号传感器", 0x02: "2号传感器", 0x03: "3号传感器", 0x04: "4号传感器"}

# ---------------- 采样周期（秒） ----------------
INTERVAL_OPTIONS = [2, 4, 6, 8, 10]
DEFAULT_INTERVAL = 6

# ---------------- 登录账号（演示用，可自行修改） ----------------
ACCOUNTS = {"admin": "123456"}

# ---------------- 补光灯自动控制 ----------------
AUTO_LIGHT_THRESHOLD = 200.0
AUTO_LIGHT_HYSTERESIS = 200.0
LIGHT_TRIGGER = 500.0

# ---------------- 飞书协同告警 ----------------
FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")

# ---------------- 飞书自建应用（AI 查询机器人） ----------------
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")

# ---------------- AI 大模型（自然语言查询） ----------------
LLM_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
LLM_BASE_URL = "https://api.deepseek.com/v1"
LLM_MODEL = "deepseek-chat"

# ---------------- 曲线颜色选项 ----------------
COLOR_OPTIONS = {
    "红": "#e60012",
    "绿": "#00a65a",
    "蓝": "#2f6fed",
    "黄": "#f6c026",
    "白": "#f5f5f5",
    "橙": "#ff7f27",
    "紫": "#8e44ad",
}
