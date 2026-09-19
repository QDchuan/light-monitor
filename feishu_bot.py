# -*- coding: utf-8 -*-
"""
 —— 飞书 AI 查询与数据导出机器人（轮询版）
通过轮询飞书 API 接收群消息，不依赖事件订阅/WebSocket。
运行：python feishu_bot.py
"""
import io
import json
import re
import sys
import time
from datetime import datetime, timedelta

import requests
import pandas as pd

import ai_query
import config as C

EXPORT_KEYWORDS = ("导出", "下载", "表格", "excel", "csv", "文件")
QUERY_GUIDE = (
    "🤖 AI 助手在线。可以这样问我：\n"
    "· 今天几点光照最低？\n"
    "· 各节点平均光照是多少？\n"
    "· 导出今天的数据（CSV/Excel 发到群里）\n"
    "· 导出最近3天的数据 / 导出全部"
)

BASE = "https://open.feishu.cn/open-apis"
POLL_INTERVAL = 3


def get_tenant_token():
    r = requests.post(f"{BASE}/auth/v3/tenant_access_token/internal", json={
        "app_id": C.FEISHU_APP_ID, "app_secret": C.FEISHU_APP_SECRET,
    }, timeout=10)
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取 token 失败: {data}")
    return data["tenant_access_token"]


def list_chats(token):
    r = requests.get(f"{BASE}/im/v1/chats",
                     headers={"Authorization": f"Bearer {token}"},
                     params={"page_size": 50}, timeout=10)
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取群列表失败: {data}")
    return data.get("data", {}).get("items", [])


def get_latest_messages(token, chat_id, page_size=10):
    r = requests.get(f"{BASE}/im/v1/messages",
                     headers={"Authorization": f"Bearer {token}"},
                     params={
                         "container_id_type": "chat",
                         "container_id": chat_id,
                         "sort_type": "ByCreateTimeDesc",
                         "page_size": page_size,
                     }, timeout=10)
    data = r.json()
    if data.get("code") != 0:
        return []
    return data.get("data", {}).get("items", [])


def send_text(token, chat_id, text):
    r = requests.post(f"{BASE}/im/v1/messages?receive_id_type=chat_id",
                     headers={"Authorization": f"Bearer {token}",
                              "Content-Type": "application/json"},
                     json={"receive_id": chat_id, "msg_type": "text",
                           "content": json.dumps({"text": text})}, timeout=10)
    return r.json()


def upload_file(token, file_bytes, file_name):
    r = requests.post(f"{BASE}/im/v1/files",
                     headers={"Authorization": f"Bearer {token}"},
                     files={
                         "file_type": (None, "stream"),
                         "file_name": (None, file_name),
                         "file": (file_name, file_bytes),
                     }, timeout=30)
    data = r.json()
    if data.get("code") != 0:
        return None
    return data["data"]["file_key"]


def send_file_message(token, chat_id, file_key):
    r = requests.post(f"{BASE}/im/v1/messages?receive_id_type=chat_id",
                     headers={"Authorization": f"Bearer {token}",
                              "Content-Type": "application/json"},
                     json={"receive_id": chat_id, "msg_type": "file",
                           "content": json.dumps({"file_key": file_key})}, timeout=10)
    return r.json()


def parse_export(text):
    """解析导出请求，返回 (start, end, label, fmt, device)
    fmt: 'csv' / 'xlsx' / 'both'
    device: 传感器编号(1-4) 或 None(全部)
    """
    now = datetime.now()
    start = now - timedelta(days=7)
    end = now
    label = "最近7天"
    fmt = "both"
    device = None

    # 传感器识别：1号/一号/第1号/0x01
    m = re.search(r"([1-4])\s*号\s*(传感器|节点|棚)?", text)
    if m:
        device = int(m.group(1))
        label = f"{device}号传感器"
    m2 = re.search(r"0x0([1-4])", text)
    if m2:
        device = int(m2.group(1))

    # 格式判断
    if re.search(r"excel|xlsx|电子表格", text, re.I):
        fmt = "xlsx"
    elif re.search(r"csv|文本", text, re.I):
        fmt = "csv"

    # 全部
    if any(k in text for k in ("全部", "所有")):
        start = datetime(2020, 1, 1)
        label = "全部数据"
        return start, end, label, fmt, device

    # 时间段：9:00~14:00 / 9点到14点 / 9:00-14:00
    m = re.search(r"(\d{1,2})[:：点](\d{0,2})\s*[~至到\-—]+\s*(\d{1,2})[:：点](\d{0,2})", text)
    if m:
        h1, m1, h2, m2 = int(m.group(1)), int(m.group(2) or 0), int(m.group(3)), int(m.group(4) or 0)
        start = now.replace(hour=h1, minute=m1, second=0, microsecond=0)
        end = now.replace(hour=h2, minute=m2, second=0, microsecond=0)
        label = f"今天 {h1:02d}:{m1:02d}~{h2:02d}:{m2:02d}"
        return start, end, label, fmt, device

    # 今天
    if any(k in text for k in ("今天", "今日")):
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        label = "今天"
        return start, end, label, fmt, device

    # 最近N天
    m = re.search(r"(\d+)\s*天", text)
    if m:
        days = int(m.group(1))
        start = now - timedelta(days=days)
        label = f"最近{days}天"
        return start, end, label, fmt, device

    # 最近N小时
    m = re.search(r"(\d+)\s*小时", text)
    if m:
        hours = int(m.group(1))
        start = now - timedelta(hours=hours)
        label = f"最近{hours}小时"
        return start, end, label, fmt, device

    return start, end, label, fmt, device


def build_export_files(start, end, device=None):
    where = f"measure_time >= '{start:%Y-%m-%d %H:%M:%S}' AND measure_time <= '{end:%Y-%m-%d %H:%M:%S}'"
    if device:
        where += f" AND device_address = {device}"
    sql = ("SELECT id AS 序号, device_address AS 硬件地址, "
           "illuminance AS 光照值lux, measure_time AS 测量时间 "
           "FROM light_records "
           f"WHERE {where} "
           "ORDER BY measure_time DESC LIMIT 20000")
    df = ai_query.run_sql(sql)
    if df.empty:
        return df, b"", b""
    if "测量时间" in df.columns:
        df["测量时间"] = pd.to_datetime(df["测量时间"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    csv_bytes = df.to_csv(index=False).encode("utf-8-sig")
    xlsx_buf = io.BytesIO()
    df.to_excel(xlsx_buf, index=False, sheet_name="光照记录")
    return df, csv_bytes, xlsx_buf.getvalue()


def handle_export(token, chat_id, text):
    start, end, label, fmt, device = parse_export(text)
    if device:
        label = f"{device}号传感器 {label}"
    try:
        df, csv_bytes, xlsx_bytes = build_export_files(start, end, device)
    except Exception as e:
        return f"❌ 导出失败：{e}"
    if df.empty:
        return f"⚠️ {label}（{start:%m-%d %H:%M} ~ {end:%m-%d %H:%M}）暂无数据。"
    stamp = start.strftime("%Y%m%d_%H%M")
    msg = f"📤 已生成 {label} 光照数据（共 {len(df)} 条）"
    files = []
    if fmt in ("csv", "both"):
        files.append((f"光照记录_{stamp}.csv", csv_bytes))
    if fmt in ("xlsx", "both"):
        files.append((f"光照记录_{stamp}.xlsx", xlsx_bytes))
    for name, data in files:
        key = upload_file(token, data, name)
        if key:
            send_file_message(token, chat_id, key)
            msg += f"\n✅ 已发送：{name}"
        else:
            msg += f"\n❌ 上传失败：{name}"
    return msg


def main():
    if not (C.FEISHU_APP_ID and C.FEISHU_APP_SECRET):
        print("[机器人] 未配置 FEISHU_APP_ID / FEISHU_APP_SECRET")
        sys.exit(1)

    print("[机器人] 正在获取 access_token...")
    token = get_tenant_token()
    print(f"[机器人] token 获取成功")

    chats = list_chats(token)
    if not chats:
        print("[机器人] 机器人不在任何群里！请先把机器人拉进群。")
        sys.exit(1)

    print("[机器人] 所在群列表：")
    for i, c in enumerate(chats):
        print(f"  {i+1}. {c.get('name', '?')}  chat_id={c['chat_id']}")

    target_chat = None
    for c in chats:
        if "光照" in c.get("name", "") or "监测" in c.get("name", ""):
            target_chat = c
            break
    if not target_chat:
        target_chat = chats[0]

    chat_id = target_chat["chat_id"]
    chat_name = target_chat.get("name", "?")
    print(f"[机器人] 监控群：{chat_name}（{chat_id}）")

    processed = set()
    initial = get_latest_messages(token, chat_id, page_size=20)
    for m in initial:
        processed.add(m["message_id"])
    print(f"[机器人] 已初始化 {len(processed)} 条历史消息，开始轮询...")
    print(f"[机器人] 每 {POLL_INTERVAL} 秒轮询一次，@我提问即可")

    while True:
        try:
            token = get_tenant_token()
            msgs = get_latest_messages(token, chat_id, page_size=10)
            for m in reversed(msgs):
                if m["message_id"] in processed:
                    continue
                processed.add(m["message_id"])
                if m.get("msg_type") != "text":
                    continue
                if m.get("sender", {}).get("sender_type") == "app":
                    continue
                try:
                    content = json.loads(m["body"]["content"])
                    text = content.get("text", "")
                except Exception:
                    continue
                mentions = m.get("mentions", [])
                if not mentions:
                    continue
                print(f"[机器人] 收到消息: {text}")
                clean = re.sub(r"<at[^>]*>.*?</at>", "", text).strip()
                if any(k in clean.lower() for k in EXPORT_KEYWORDS):
                    answer = handle_export(token, chat_id, clean)
                else:
                    answer = parse_and_answer(clean)
                send_text(token, chat_id, answer)
                print(f"[机器人] 已回复")
        except Exception as e:
            print(f"[机器人] 轮询出错: {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
