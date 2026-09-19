p = r'C:\Users\chuan\Doubao\chats\2026-09-19\new-chat-1\feishu_bot.py'
c = open(p, encoding='utf-8').read()

# 1. parse_export 加传感器识别
old_parse = '''def parse_export(text):
    """解析导出请求，返回 (start, end, label, fmt)
    fmt: 'csv' / 'xlsx' / 'both'
    """
    now = datetime.now()
    start = now - timedelta(days=7)
    end = now
    label = "最近7天"
    fmt = "both"

    # 格式判断
    if re.search(r"excel|xlsx|电子表格", text, re.I):
        fmt = "xlsx"
    elif re.search(r"csv|文本", text, re.I):
        fmt = "csv"'''

new_parse = '''def parse_export(text):
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
    m = re.search(r"([1-4])\\s*号\\s*(传感器|节点|棚)?", text)
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
        fmt = "csv"'''

c = c.replace(old_parse, new_parse)

# 2. 所有 return 加上 device
c = c.replace(
    'return start, end, label, fmt\n\n    # 今天',
    'return start, end, label, fmt, device\n\n    # 今天'
)
c = c.replace(
    'return start, end, label, fmt\n\n    # 最近N天',
    'return start, end, label, fmt, device\n\n    # 最近N天'
)
c = c.replace(
    'return start, end, label, fmt\n\n    # 最近N小时',
    'return start, end, label, fmt, device\n\n    # 最近N小时'
)
c = c.replace(
    'return start, end, label, fmt\n\n    return start, end, label, fmt',
    'return start, end, label, fmt, device\n\n    return start, end, label, fmt, device'
)
# 全部
c = c.replace(
    'start = datetime(2020, 1, 1)\n        label = "全部数据"\n        return start, end, label, fmt',
    'start = datetime(2020, 1, 1)\n        label = "全部数据"\n        return start, end, label, fmt, device'
)
# 时间段
c = c.replace(
    'label = f"今天 {h1:02d}:{m1:02d}~{h2:02d}:{m2:02d}"\n        return start, end, label, fmt',
    'label = f"今天 {h1:02d}:{m1:02d}~{h2:02d}:{m2:02d}"\n        return start, end, label, fmt, device'
)
# 今天
c = c.replace(
    'start = now.replace(hour=0, minute=0, second=0, microsecond=0)\n        label = "今天"\n        return start, end, label, fmt',
    'start = now.replace(hour=0, minute=0, second=0, microsecond=0)\n        label = "今天"\n        return start, end, label, fmt, device'
)
# 最近N天
c = c.replace(
    'start = now - timedelta(days=days)\n        label = f"最近{days}天"\n        return start, end, label, fmt',
    'start = now - timedelta(days=days)\n        label = f"最近{days}天"\n        return start, end, label, fmt, device'
)
# 最近N小时
c = c.replace(
    'start = now - timedelta(hours=hours)\n        label = f"最近{hours}小时"\n        return start, end, label, fmt',
    'start = now - timedelta(hours=hours)\n        label = f"最近{hours}小时"\n        return start, end, label, fmt, device'
)

# 3. build_export_files 加 device 参数
c = c.replace(
    'def build_export_files(start, end):\n    sql = ("SELECT id AS 序号, device_address AS 硬件地址, "\n           "illuminance AS 光照值lux, measure_time AS 测量时间 "\n           "FROM light_records "\n           f"WHERE measure_time >= \'{start:%Y-%m-%d %H:%M:%S}\' "\n           f"AND measure_time <= \'{end:%Y-%m-%d %H:%M:%S}\' "\n           "ORDER BY measure_time DESC LIMIT 20000")',
    'def build_export_files(start, end, device=None):\n    where = f"measure_time >= \'{start:%Y-%m-%d %H:%M:%S}\' AND measure_time <= \'{end:%Y-%m-%d %H:%M:%S}\'"\n    if device:\n        where += f" AND device_address = {device}"\n    sql = ("SELECT id AS 序号, device_address AS 硬件地址, "\n           "illuminance AS 光照值lux, measure_time AS 测量时间 "\n           "FROM light_records "\n           f"WHERE {where} "\n           "ORDER BY measure_time DESC LIMIT 20000")'
)

# 4. handle_export 改
c = c.replace(
    'def handle_export(token, chat_id, text):\n    start, end, label, fmt = parse_export(text)\n    try:\n        df, csv_bytes, xlsx_bytes = build_export_files(start, end)',
    'def handle_export(token, chat_id, text):\n    start, end, label, fmt, device = parse_export(text)\n    if device:\n        label = f"{device}号传感器 {label}"\n    try:\n        df, csv_bytes, xlsx_bytes = build_export_files(start, end, device)'
)

open(p, 'w', encoding='utf-8').write(c)
print("done")
