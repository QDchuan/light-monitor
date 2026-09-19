import re
from datetime import datetime, timedelta

# 读取原文件
p = r'C:\Users\chuan\Doubao\chats\2026-09-19\new-chat-1\feishu_bot.py'
c = open(p, encoding='utf-8').read()

# 新的 parse_export_range + build_export_files + handle_export
new_block = '''
def parse_export(text):
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
        fmt = "csv"

    # 全部
    if any(k in text for k in ("全部", "所有")):
        start = datetime(2020, 1, 1)
        label = "全部数据"
        return start, end, label, fmt

    # 时间段：9:00~14:00 / 9点到14点 / 9:00-14:00
    m = re.search(r"(\\d{1,2})[:：点](\\d{0,2})\\s*[~至到\\-—]+\\s*(\\d{1,2})[:：点](\\d{0,2})", text)
    if m:
        h1, m1, h2, m2 = int(m.group(1)), int(m.group(2) or 0), int(m.group(3)), int(m.group(4) or 0)
        start = now.replace(hour=h1, minute=m1, second=0, microsecond=0)
        end = now.replace(hour=h2, minute=m2, second=0, microsecond=0)
        label = f"今天 {h1:02d}:{m1:02d}~{h2:02d}:{m2:02d}"
        return start, end, label, fmt

    # 今天
    if any(k in text for k in ("今天", "今日")):
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        label = "今天"
        return start, end, label, fmt

    # 最近N天
    m = re.search(r"(\\d+)\\s*天", text)
    if m:
        days = int(m.group(1))
        start = now - timedelta(days=days)
        label = f"最近{days}天"
        return start, end, label, fmt

    # 最近N小时
    m = re.search(r"(\\d+)\\s*小时", text)
    if m:
        hours = int(m.group(1))
        start = now - timedelta(hours=hours)
        label = f"最近{hours}小时"
        return start, end, label, fmt

    return start, end, label, fmt


def build_export_files(start, end):
    sql = ("SELECT id AS 序号, device_address AS 硬件地址, "
           "illuminance AS 光照值lux, measure_time AS 测量时间 "
           "FROM light_records "
           f"WHERE measure_time >= '{start:%Y-%m-%d %H:%M:%S}' "
           f"AND measure_time <= '{end:%Y-%m-%d %H:%M:%S}' "
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
    start, end, label, fmt = parse_export(text)
    try:
        df, csv_bytes, xlsx_bytes = build_export_files(start, end)
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
            msg += f"\\n✅ 已发送：{name}"
        else:
            msg += f"\\n❌ 上传失败：{name}"
    return msg
'''

# 找到并替换旧代码块
old_start = c.index("def parse_export_range(")
old_end = c.index("def main():")
c = c[:old_start] + new_block.strip() + "\n\n\n" + c[old_end:]

open(p, 'w', encoding='utf-8').write(c)
print("done")
