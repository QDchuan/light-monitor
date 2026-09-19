# -*- coding: utf-8 -*-
"""
 —— 飞书协同告警模块
==============================
通过飞书自定义机器人 Webhook 发送交互卡片告警。
卡片包含当前异常详情与两个动作按钮：
  - 「💡 开启补光灯」→ 跳转本地继电器控制端点 light_control_api.py
  - 「🔍 打开监控大屏」→ 跳转 Streamlit 大屏

Webhook 获取：
  飞书群聊 → 设置 → 群机器人 → 添加机器人 → 自定义机器人 → 复制 Webhook

注意：
  自定义机器人 Webhook 仅支持"发送消息"；卡片按钮使用跳转链接
  实现"群内点击 → 远程控制"，适合本地演示场景。
"""
import json
import time

import requests

# 本地控制端点与监控大屏地址（卡片按钮跳转目标）
LIGHT_CONTROL_URL = "http://localhost:8502/api/light"
DASHBOARD_URL = "http://localhost:8501"

ALARM_COOLDOWN_SECONDS = 300      # 同一节点同类告警冷却时间（5 分钟）
CONFIRM_ROUNDS = 2                # 连续 N 轮低光照确认后告警（防抖）


def build_alarm_card(node_name: str, unit: int, lux, threshold,
                     measure_time: str, recovery: bool = False) -> dict:
    """构造飞书交互告警卡片"""
    if recovery:
        header = {
            "title": {"tag": "plain_text", "content": "✅  · 光照恢复通知"},
            "template": "green",
        }
        status = "光照已恢复正常"
        detail = (
            f"**节点**：{node_name} (0x{unit:02X})\n"
            f"**当前光照**：{lux} lux\n"
            f"**恢复时间**：{measure_time}\n"
            f"**状态**：告警解除"
        )
        actions = [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "🔍 打开监控大屏"},
                "type": "default",
                "multi_url": {"url": DASHBOARD_URL},
            }
        ]
    else:
        header = {
            "title": {"tag": "plain_text", "content": "⚠️  · 光照异常告警"},
            "template": "red",
        }
        status = "光照低于设定阈值"
        detail = (
            f"**节点**：{node_name} (0x{unit:02X})\n"
            f"**当前光照**：{lux} lux\n"
            f"**告警阈值**：{threshold} lux\n"
            f"**告警时间**：{measure_time}\n"
            f"**状态**：{status}"
        )
        actions = [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "💡 开启补光灯"},
                "type": "primary",
                "multi_url": {"url": f"{LIGHT_CONTROL_URL}?unit={unit}&on=1"},
            },
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "🔍 打开监控大屏"},
                "type": "default",
                "multi_url": {"url": DASHBOARD_URL},
            },
        ]

    return {
        "config": {"wide_screen_mode": True},
        "header": header,
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": detail}},
            {"tag": "hr"},
            {"tag": "note", "elements": [
                {"tag": "plain_text",
                 "content": " · 智慧养殖光照监测系统自动推送"}
            ]},
            {"tag": "action", "actions": actions},
        ],
    }


def send_card(card: dict, webhook: str) -> bool:
    """发送交互卡片到飞书群，成功返回 True"""
    if not webhook:
        return False
    try:
        resp = requests.post(
            webhook,
            json={"msg_type": "interactive", "card": card},
            timeout=6,
        )
        data = resp.json()
        return resp.status_code == 200 and data.get("code") == 0
    except Exception:                                   # noqa: BLE001
        return False


def send_alarm(unit: int, lux, threshold: float, measure_time: str,
               webhook: str, node_names: dict) -> bool:
    """发送低光照告警卡片"""
    node_name = node_names.get(unit, f"节点0x{unit:02X}")
    card = build_alarm_card(node_name, unit, lux, threshold, measure_time)
    return send_card(card, webhook)


def send_recovery(unit: int, lux, measure_time: str,
                  webhook: str, node_names: dict) -> bool:
    """发送光照恢复通知卡片"""
    node_name = node_names.get(unit, f"节点0x{unit:02X}")
    card = build_alarm_card(node_name, unit, lux, 0, measure_time, recovery=True)
    return send_card(card, webhook)


def send_test_card(webhook: str) -> bool:
    """发送一条测试卡片（用于验证 Webhook 连通性）"""
    card = build_alarm_card("测试节点", 0x01, 42, 200,
                            time.strftime("%Y-%m-%d %H:%M:%S"))
    return send_card(card, webhook)


if __name__ == "__main__":
    # 直接运行可发送测试卡片：python feishu_alarm.py <webhook>
    import sys
    from config import FEISHU_WEBHOOK, NODE_NAMES
    webhook = sys.argv[1] if len(sys.argv) > 1 else FEISHU_WEBHOOK
    if not webhook:
        print("请提供 Webhook：python feishu_alarm.py <webhook地址>")
        sys.exit(1)
    ok = send_test_card(webhook)
    print("测试卡片发送成功" if ok else "发送失败，请检查 Webhook 地址")
