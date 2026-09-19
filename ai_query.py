# -*- coding: utf-8 -*-
"""
 —— AI 自然语言查询模块
=================================
把用户的中文问题转换为 SQL 查询数据库，并返回 AI 总结。

两种模式：
  1. AI 模式（已配置 LLM_API_KEY）：
     大模型将问题转为只读 SQL -> 白名单校验 -> 执行 -> 大模型中文总结
  2. 规则模式（未配置 Key，兜底演示）：
     内置规则解析"最低/最高/平均/今日/节点"等意图，生成确定 SQL
     并输出模板化中文总结

安全设计：AI 生成的 SQL 必须通过白名单校验（仅允许单条只读 SELECT，
限制表名/列名，禁止修改类语句），防止注入风险。
"""
import re
from datetime import datetime, timedelta

import pandas as pd
import pymysql
import requests

import config as C

# ---------- 白名单 ----------
ALLOWED_TABLES = {"light_records", "`light_records`"}
ALLOWED_COLUMNS = {"id", "device_address", "illuminance", "measure_time"}
FORBIDDEN_KEYWORDS = [
    "insert", "update", "delete", "drop", "alter", "create", "truncate",
    "grant", "revoke", "replace", "load_file", "into outfile", "into dumpfile",
    "information_schema", "mysql.", "sleep(", "benchmark(", "--", "/*",
]

SCHEMA_HINT = (
    "数据库 labview_DAQ 中的表 light_records：\n"
    "- id INT 自增主键\n"
    "- device_address TINYINT 硬件地址（1=0x01, 2=0x02, 3=0x03, 4=0x04）\n"
    "- illuminance DECIMAL 光照值(lux)\n"
    "- measure_time DATETIME 测量时间\n"
)


# ================= LLM 调用 =================

def llm_chat(messages, temperature=0.2, max_tokens=800):
    """调用 OpenAI 兼容大模型接口，失败返回 None"""
    if not C.LLM_API_KEY:
        return None
    try:
        url = C.LLM_BASE_URL.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {C.LLM_API_KEY}",
                   "Content-Type": "application/json"}
        body = {"model": C.LLM_MODEL, "messages": messages,
                "temperature": temperature, "max_tokens": max_tokens}
        resp = requests.post(url, json=body, headers=headers, timeout=40)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception:                                   # noqa: BLE001
        return None


# ================= SQL 生成与校验 =================

def nl_to_sql(question: str) -> str | None:
    """大模型将自然语言转为 SQL；失败返回 None"""
    messages = [
        {"role": "system", "content":
            "你是 MySQL 查询专家。根据表结构把用户问题转换为一条只读 SELECT 语句。"
            "要求：\n"
            "1. 只允许 SELECT，绝对禁止 INSERT/UPDATE/DELETE/DROP 等修改语句\n"
            "2. 只用表 light_records 及其列 id/device_address/illuminance/measure_time\n"
            "3. 中文列别名；时间类统计用 DATE_FORMAT(measure_time,'%Y-%m-%d %H:%i') 格式化\n"
            "4. 只输出 SQL 本身，不要解释、不要分号、不要 Markdown 代码块\n" + SCHEMA_HINT},
        {"role": "user", "content": question},
    ]
    sql = llm_chat(messages, temperature=0)
    if not sql:
        return None
    sql = sql.strip().strip("`")
    # 去掉可能的 markdown 代码块包裹
    sql = re.sub(r"^```(?:sql)?\s*|\s*```$", "", sql, flags=re.I).strip()
    return sql


def validate_sql(sql: str) -> bool:
    """白名单校验：只读单条 SELECT、合法表列、无危险关键字"""
    s = sql.strip().strip(";").strip()
    if not re.match(r"^select\b", s, re.I):
        return False
    if ";" in s:                                        # 禁止多语句
        return False
    low = s.lower()
    for kw in FORBIDDEN_KEYWORDS:
        if kw in low:
            return False
    if not re.search(r"light_records", low):            # 必须查本表
        return False
    # 列名白名单：去掉字符串字面量与反引号后按 token 检查
    s_clean = re.sub(r"`", "", s)
    s_clean = re.sub(r"'[^']*'", "", s_clean)          # 去除格式串/字面量
    for token in re.findall(r"[a-z_]+", s_clean.lower()):
        if len(token) <= 1:                             # 忽略单字母别名
            continue
        if token in ALLOWED_TABLES or token in ALLOWED_COLUMNS:
            continue
        if token in {"select", "from", "where", "group", "by", "order", "and",
                     "or", "not", "as", "asc", "desc", "limit", "between",
                     "join", "on", "min", "max", "avg", "count", "sum", "round",
                     "date_format", "now", "current_date", "curdate", "distinct",
                     "ifnull", "coalesce", "cast", "concat", "left", "right",
                     "hour", "minute", "second", "day", "month", "year", "date",
                     "date_add", "date_sub", "interval", "having", "like",
                     "in", "is", "null", "true", "false", "div", "mod"}:
            continue
        return False                                    # 出现未授权标识符
    return True


# ================= 查询执行 =================

def run_sql(sql: str) -> pd.DataFrame:
    conn = pymysql.connect(**C.MYSQL)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            cols = [d[0] for d in cur.description]
            return pd.DataFrame(cur.fetchall(), columns=cols)
    finally:
        conn.close()


# ================= 规则引擎（无 Key 兜底） =================

def rule_query(question: str) -> dict:
    """内置规则解析常见问题，返回结构化查询结果"""
    q = question.lower()
    today = datetime.now()
    start = today.replace(hour=0, minute=0, second=0, microsecond=0)
    start_str = start.strftime("%Y-%m-%d %H:%M:%S")

    # 节点筛选（单表查询用无前缀，JOIN 查询用 t. 前缀）
    node_clause, node_clause_t, node_name = "", "", ""
    for addr, name in [(1, "1号"), (2, "2号"), (3, "3号"), (4, "4号")]:
        if str(addr) in q or name in q or f"0x0{addr}" in q:
            node_clause = f"AND device_address = {addr}"
            node_clause_t = f"AND t.device_address = {addr}"
            node_name = f"0x{addr:02X} "
            break

    # 统计 SQL（今日各节点）
    sql1 = (
        f"SELECT device_address AS 节点, "
        f"MIN(illuminance) AS 最低光照, MAX(illuminance) AS 最高光照, "
        f"ROUND(AVG(illuminance),1) AS 平均光照, COUNT(*) AS 记录数 "
        f"FROM light_records WHERE measure_time >= '{start_str}' {node_clause} "
        f"GROUP BY device_address ORDER BY device_address"
    )
    # 最低值出现时间 SQL
    sql2 = (
        f"SELECT t.device_address AS 节点, t.illuminance AS 最低光照, "
        f"DATE_FORMAT(t.measure_time,'%H:%i') AS 出现时间 "
        f"FROM light_records t JOIN ("
        f"  SELECT device_address, MIN(illuminance) AS mn FROM light_records "
        f"  WHERE measure_time >= '{start_str}' {node_clause} "
        f"  GROUP BY device_address) m ON t.device_address = m.device_address "
        f"  AND t.illuminance = m.mn "
        f"WHERE t.measure_time >= '{start_str}' {node_clause_t} "
        f"ORDER BY t.device_address"
    )

    df1 = run_sql(sql1)
    df2 = run_sql(sql2) if not df1.empty else pd.DataFrame()

    # 生成中文总结
    date_str = today.strftime("%Y-%m-%d")
    if df1.empty:
        summary = f"今日（{date_str}）暂无光照采集记录，请先在大屏启动采集。"
    else:
        lines = [f"今日（{date_str}）{node_name}光照统计："]
        for _, r in df1.iterrows():
            lines.append(
                f"节点0x{int(r['节点']):02X}：最低 {r['最低光照']} lux，"
                f"最高 {r['最高光照']} lux，平均 {r['平均光照']} lux，共 {int(r['记录数'])} 条")
        if not df2.empty:
            t = df2.iloc[0]
            lines.append(f"其中今日最低光照出现在 {t['出现时间']}"
                         f"（节点0x{int(t['节点']):02X}，{t['最低光照']} lux）")
        summary = "；".join(lines) + "。"

    return {
        "mode": "rule",
        "sql": sql1,
        "df": df1,
        "summary": summary,
        "note": "未配置大模型 Key，当前使用内置规则引擎（仅支持今日统计类问题）；配置 Key 后支持任意自然语言提问",
    }


# ================= AI 总结 =================

def summarize(question: str, sql: str, df: pd.DataFrame) -> str:
    messages = [
        {"role": "system", "content":
            "你是智慧养殖系统的数据助手。用户问了一个关于光照数据的问题，"
            "以下是执行的 SQL 和查询结果。请用简洁中文回答用户问题，"
            "直接给出结论和关键数字，不超过 120 字。"},
        {"role": "user", "content":
            f"问题：{question}\nSQL：{sql}\n查询结果：\n{df.to_string(index=False)}"},
    ]
    reply = llm_chat(messages)
    return reply or "（AI 总结生成失败，请查看上方查询结果）"


# ================= 对外入口 =================

def handle(question: str) -> dict:
    """主入口：自然语言问题 -> {sql, df, summary, mode, note}"""
    if not C.LLM_API_KEY:
        return rule_query(question)                     # 规则引擎兜底

    sql = nl_to_sql(question)
    if not sql or not validate_sql(sql):
        return {
            "mode": "ai", "sql": sql or "（生成失败）", "df": pd.DataFrame(),
            "summary": "大模型未能生成合法查询，请换个问法，或检查 API Key 配置。",
            "note": "",
        }
    try:
        df = run_sql(sql)
    except Exception as e:                              # noqa: BLE001
        return {
            "mode": "ai", "sql": sql, "df": pd.DataFrame(),
            "summary": f"SQL 执行失败：{e}", "note": "",
        }
    summary = summarize(question, sql, df)
    return {"mode": "ai", "sql": sql, "df": df, "summary": summary,
            "note": "AI 模式：大模型生成 SQL 并总结"}


if __name__ == "__main__":
    # 自测：python ai_query.py "今天几点光照最低？"
    import sys
    question = sys.argv[1] if len(sys.argv) > 1 else "今天各节点平均光照是多少？"
    res = handle(question)
    print("模式:", res["mode"])
    print("SQL :", res["sql"])
    print(res["df"].to_string(index=False))
    print("总结:", res["summary"])
