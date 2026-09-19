# -*- coding: utf-8 -*-
"""
 —— Streamlit 智慧养殖光照监测与协同决策大屏
======================================================
功能：
  - 系统登录（st.session_state 鉴权）
  - 实时监控：st.metric 展示 4 节点光照，Plotly 实时曲线（颜色可选）
  - 程序控制：侧边栏启动/停止采集
  - 采样周期可调：2/4/6/8/10 秒五档
  - 历史查询：指定时间段筛选 + st.dataframe 展示 + CSV/Excel 导出
  - 继电器控制：补光灯手动开关 + 自动补光（低光照阈值联动）
  - 告警与补光动作日志（飞书协同待配置 Webhook 后启用）

运行：
  python -m streamlit run streamlit_app.py
"""
import re
import threading
import time
from datetime import datetime, timedelta
from io import BytesIO

import pandas as pd
import plotly.graph_objects as go
import pymysql
import streamlit as st
from pymodbus.client import ModbusTcpClient

import config as C
import feishu_alarm
import ai_query

st.set_page_config(page_title=" · 光照监测", page_icon="🐟", layout="wide")

# ================= 数据访问 =================

def db_connect():
    return pymysql.connect(**C.MYSQL)


def fetch_recent(limit=600):
    """最近 limit 条记录（时间升序，供曲线绘制）"""
    conn = db_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id, device_address, illuminance, measure_time "
                f"FROM {C.TABLE} ORDER BY id DESC LIMIT %s", (limit,)
            )
            df = pd.DataFrame(cur.fetchall(),
                              columns=["id", "device_address", "illuminance", "measure_time"])
    finally:
        conn.close()
    if not df.empty:
        df = df.sort_values("measure_time").reset_index(drop=True)
    return df


def fetch_latest_each():
    """各节点最新一条记录"""
    conn = db_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT t.device_address, t.illuminance, t.measure_time "
                f"FROM {C.TABLE} t "
                f"JOIN (SELECT device_address, MAX(id) mid FROM {C.TABLE} "
                f"      GROUP BY device_address) m ON t.id = m.mid "
                f"ORDER BY t.device_address"
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return {r[0]: {"lux": r[1], "time": str(r[2])} for r in rows}


def query_between(start_dt, end_dt, device=None):
    """按时间段查询历史记录，可指定传感器"""
    conn = db_connect()
    try:
        where = "measure_time BETWEEN %s AND %s"
        params = [start_dt.strftime("%Y-%m-%d %H:%M:%S"),
                  end_dt.strftime("%Y-%m-%d %H:%M:%S")]
        if device:
            where += " AND device_address = %s"
            params.append(device)
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id, device_address, illuminance, measure_time "
                f"FROM {C.TABLE} "
                f"WHERE {where} "
                f"ORDER BY measure_time DESC LIMIT 10000",
                params
            )
            df = pd.DataFrame(cur.fetchall(),
                              columns=["序号", "硬件地址", "光照值(lux)", "测量时间"])
    finally:
        conn.close()
    df["硬件地址"] = df["硬件地址"].map(lambda a: f"0x{a:02X}")
    return df


def db_total():
    conn = db_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {C.TABLE}")
            return cur.fetchone()[0]
    finally:
        conn.close()


# ================= 模拟倍速（Modbus HR[1]） =================

def read_modbus_speed():
    """读取从站当前模拟倍速（0x01 单元的 HR[1]），未连接返回 None"""
    client = ModbusTcpClient(C.MODBUS_HOST, port=C.MODBUS_PORT)
    if not client.connect():
        return None
    try:
        rr = client.read_holding_registers(1, count=1, slave=0x01)
        if rr.isError():
            return None
        return rr.registers[0]
    finally:
        client.close()


def write_modbus_speed(speed):
    """写入模拟倍速到从站 HR[1]，实时生效无需重启"""
    client = ModbusTcpClient(C.MODBUS_HOST, port=C.MODBUS_PORT)
    if not client.connect():
        st.error("从站连接失败，无法调整倍速")
        return False
    try:
        rr = client.write_register(1, int(speed), slave=0x01)
        return not rr.isError()
    finally:
        client.close()


# ================= 采集线程 =================

class Collector:
    """后台采集线程：轮询 Modbus 从站 -> 入库 -> 自动补光 -> 告警记录"""

    def __init__(self):
        self._stop = threading.Event()
        self.running = False
        self.interval = C.DEFAULT_INTERVAL
        self.auto_light = False
        self.threshold = C.AUTO_LIGHT_THRESHOLD
        self.latest = {}      # unit -> {"lux": int, "time": str}
        self.coil = {}        # unit -> bool  补光灯实际状态
        self.logs = []        # [(time, text), ...]
        self.lock = threading.Lock()
        self.thread = None
        self.last_low = {}    # unit -> 连续低光照轮数（告警防抖）
        self.alarm_cooldown = {}  # unit -> 上次飞书告警时间戳

    # ---------- 控制 ----------
    def start(self):
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()
        self.running = True
        self._log("程序启动：开始采集")

    def stop(self):
        self.running = False
        self._log("程序停止：暂停采集")

    def _log(self, text):
        with self.lock:
            self.logs.append((datetime.now().strftime("%H:%M:%S"), text))
            if len(self.logs) > 300:
                self.logs = self.logs[-300:]

    # ---------- 主循环 ----------
    def _loop(self):
        while not self._stop.is_set():
            if self.running:
                try:
                    self.poll_once()
                except Exception as e:                      # noqa: BLE001
                    self._log(f"采集异常: {e}")
                time.sleep(max(1, self.interval))
            else:
                time.sleep(0.5)

    # ---------- 一轮轮询 ----------
    def poll_once(self):
        client = ModbusTcpClient(C.MODBUS_HOST, port=C.MODBUS_PORT)
        if not client.connect():
            self._log("从站连接失败，本轮跳过")
            return
        rows = []
        try:
            for unit in C.NODES:
                rr = client.read_holding_registers(C.MODBUS_REG_ADDR,
                                                   count=1, slave=unit)
                if rr.isError():
                    self._log(f"0x{unit:02X} 读取异常，跳过")
                    continue
                lux = rr.registers[0]
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                rows.append((unit, lux, now))
                with self.lock:
                    self.latest[unit] = {"lux": lux, "time": now}
                self._auto_light(client, unit, lux)          # 自动补光联动
                self._check_alarm(unit, lux)                 # 飞书异常告警
        finally:
            client.close()
        if rows:
            self._save(rows)

    # ---------- 飞书异常告警 ----------
    def _check_alarm(self, unit, lux):
        """低光照持续 N 轮且超过冷却期 → 推送飞书告警；恢复后推送恢复通知"""
        if not C.FEISHU_WEBHOOK:
            return
        now_ts = time.time()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rounds = feishu_alarm.CONFIRM_ROUNDS
        if lux < self.threshold:
            self.last_low[unit] = self.last_low.get(unit, 0) + 1
            if self.last_low[unit] >= rounds:
                last = self.alarm_cooldown.get(unit, 0)
                if now_ts - last > feishu_alarm.ALARM_COOLDOWN_SECONDS:
                    self.alarm_cooldown[unit] = now_ts
                    ok = feishu_alarm.send_alarm(unit, lux, self.threshold,
                                                 now_str, C.FEISHU_WEBHOOK,
                                                 C.NODE_NAMES)
                    self._log(f"飞书告警 0x{unit:02X} 光照{lux}lux → "
                              f"{'已推送' if ok else '推送失败'}")
        else:
            if self.last_low.get(unit, 0) >= rounds:
                last = self.alarm_cooldown.get(unit, 0)
                if now_ts - last > feishu_alarm.ALARM_COOLDOWN_SECONDS:
                    self.alarm_cooldown[unit] = now_ts
                    ok = feishu_alarm.send_recovery(unit, lux, now_str,
                                                    C.FEISHU_WEBHOOK,
                                                    C.NODE_NAMES)
                    self._log(f"飞书恢复通知 0x{unit:02X} 光照{lux}lux → "
                              f"{'已推送' if ok else '推送失败'}")
            self.last_low[unit] = 0

    # ---------- 自动补光 ----------
    def _auto_light(self, client, unit, lux):
        if not self.auto_light:
            return
        on = self.coil.get(unit, False)
        if lux < self.threshold and not on:
            client.write_coil(C.MODBUS_COIL_ADDR, True, slave=unit)
            self.coil[unit] = True
            self._log(f"0x{unit:02X} 光照 {lux}lux < 阈值{self.threshold} → 自动开补光灯")
        elif lux > self.threshold + C.AUTO_LIGHT_HYSTERESIS and on:
            client.write_coil(C.MODBUS_COIL_ADDR, False, slave=unit)
            self.coil[unit] = False
            self._log(f"0x{unit:02X} 光照 {lux}lux 恢复 → 自动关补光灯")

    # ---------- 入库 ----------
    def _save(self, rows):
        conn = db_connect()
        try:
            with conn.cursor() as cur:
                cur.executemany(
                    f"INSERT INTO {C.TABLE} (device_address, illuminance, measure_time) "
                    f"VALUES (%s, %s, %s)", rows)
            conn.commit()
        finally:
            conn.close()


# ================= 会话初始化 =================

def init_collector():
    if "collector" not in st.session_state:
        st.session_state["collector"] = Collector()
    return st.session_state["collector"]


def manual_light(collector, unit, on):
    """手动写线圈控制补光灯"""
    client = ModbusTcpClient(C.MODBUS_HOST, port=C.MODBUS_PORT)
    if not client.connect():
        st.error("从站连接失败，无法控制继电器")
        return
    try:
        client.write_coil(C.MODBUS_COIL_ADDR, on, slave=unit)
        collector.coil[unit] = on
        collector._log(f"0x{unit:02X} 手动{'开启' if on else '关闭'}补光灯")
        st.success(f"0x{unit:02X} 补光灯已{'开启' if on else '关闭'}")
    except Exception as e:                                  # noqa: BLE001
        st.error(f"控制失败: {e}")
    finally:
        client.close()


# ================= 登录 =================

def login_page():
    st.title("🐟  · 智慧养殖光照监测与协同决策系统")
    st.caption("账号登录后才能进入监控大屏")
    col1, col2, col3 = st.columns([1, 1.4, 1])
    with col2:
        st.markdown("### 用户登录")
        user = st.text_input("账号", key="login_user")
        pwd = st.text_input("密码", type="password", key="login_pwd")
        if st.button("登  录", use_container_width=True, type="primary"):
            if C.ACCOUNTS.get(user) == pwd:
                st.session_state["auth"] = True
                st.session_state["user"] = user
                st.rerun()
            else:
                st.error("账号或密码错误，请重试")
        st.caption(f"演示账号：{list(C.ACCOUNTS.keys())[0]} / {list(C.ACCOUNTS.values())[0]}")
    st.stop()


# ================= 实时面板（自动刷新） =================

@st.fragment(run_every="2s")
def realtime_panel(collector, colors):
    """实时数值卡片 + 实时曲线 + 补光状态（每 2 秒自动刷新）"""
    # 1) 状态行
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("采集状态", "🟢 运行中" if collector.running else "⚪ 已停止")
    c2.metric("采样周期", f"{collector.interval} s")
    c3.metric("数据库记录", f"{db_total()} 条")
    c4.metric("当前时间", datetime.now().strftime("%H:%M:%S"))

    # 2) 4 节点实时数值
    st.markdown("### 📊 实时光照")
    latest = collector.latest if collector.latest else fetch_latest_each()
    cols = st.columns(4)
    for i, unit in enumerate(C.NODES):
        v = latest.get(unit)
        lux = v["lux"] if v else None
        t = v["time"] if v else "—"
        cols[i].metric(
            f"{C.NODE_NAMES[unit]} · 0x{unit:02X}",
            f"{lux} lux" if lux is not None else "—",
            help=f"采集时间：{t}")

    # 3) 实时曲线（颜色可选）
    st.markdown("### 📈 实时趋势（最近 600 条）")
    df = fetch_recent(600)
    if df.empty:
        st.info("暂无数据，请在左侧启动采集")
    else:
        fig = go.Figure()
        for unit in C.NODES:
            sub = df[df["device_address"] == unit]
            if sub.empty:
                continue
            fig.add_scatter(
                x=sub["measure_time"], y=sub["illuminance"],
                mode="lines", name=f"{C.NODE_NAMES[unit]} 0x{unit:02X}",
                line=dict(width=2,
                          color=C.COLOR_OPTIONS.get(colors.get(unit), "#2f6fed")))
        fig.update_layout(
            template="plotly_dark", height=420,
            margin=dict(l=10, r=10, t=30, b=10),
            legend=dict(orientation="h", yanchor="bottom", y=1.01),
            xaxis_title="时间", yaxis_title="光照 (lux)",
            hovermode="x unified")
        st.plotly_chart(fig, use_container_width=True)

    # 4) 补光灯状态
    st.markdown("### 💡 补光灯状态（继电器）")
    scols = st.columns(4)
    for i, unit in enumerate(C.NODES):
        on = collector.coil.get(unit, False)
        scols[i].markdown(
            f"**{C.NODE_NAMES[unit]}**<br>"
            f"<span style='font-size:20px'>{'🟡 补光中' if on else '⚪ 关闭'}</span>",
            unsafe_allow_html=True)


# ================= 主页面 =================

def main_page(collector):
    # ---------- 侧边栏 ----------
    with st.sidebar:
        st.markdown(f"**当前用户**：{st.session_state.get('user', '')}")
        if st.button("退出登录", use_container_width=True):
            collector.stop()
            st.session_state["auth"] = False
            st.rerun()
        st.divider()

        st.markdown("### 程序控制")
        c1, c2 = st.columns(2)
        c1.button("▶ 启动", use_container_width=True,
                  disabled=collector.running,
                  on_click=collector.start)
        c2.button("⏹ 停止", use_container_width=True,
                  disabled=not collector.running,
                  on_click=collector.stop)
        st.caption(f"当前：{'🟢 采集中' if collector.running else '⚪ 已停止'}")

        st.divider()
        st.markdown("### 采样周期（单号进阶）")
        iv = st.select_slider("周期", options=C.INTERVAL_OPTIONS,
                              value=collector.interval)
        if iv != collector.interval:
            collector.interval = iv
            collector._log(f"采样周期调整为 {iv}s")
        st.caption("采集线程按该周期轮询从站并入库")

        st.divider()
        st.markdown("### 模拟倍速（演示用）")
        cur_speed = read_modbus_speed()
        if cur_speed is None:
            st.caption("⚠ 从站未连接，无法调整倍速")
        else:
            speed_opts = [1, 10, 30, 60, 120, 240]
            idx = speed_opts.index(cur_speed) if cur_speed in speed_opts else 3
            new_speed = st.select_slider(
                "时间加速", options=speed_opts, value=cur_speed,
                help="模拟时间加速倍数：60 倍 = 1 分钟模拟 1 小时")
            if new_speed != cur_speed:
                if write_modbus_speed(new_speed):
                    collector._log(f"模拟倍速调整为 {new_speed}x")
                    st.success(f"已切换为 {new_speed} 倍速，曲线将按新节奏流动")
                else:
                    st.error("倍速写入失败")
            st.caption(f"当前倍速：**{cur_speed}x**")
            st.caption("备注：用于演示昼夜循环——60 倍约 24 分钟跑完一天"
                       "（10 倍=2.4 小时/天，240 倍=6 分钟/天）；"
                       "实时写入从站 HR[1] 生效，无需重启")

        st.divider()
        st.markdown("### 曲线颜色（双号进阶）")
        colors = {}
        defaults = {0x01: "蓝", 0x02: "红", 0x03: "绿", 0x04: "黄"}
        for unit in C.NODES:
            colors[unit] = st.selectbox(
                f"0x{unit:02X} 曲线",
                list(C.COLOR_OPTIONS),
                index=list(C.COLOR_OPTIONS).index(
                    st.session_state.get(f"color_{unit}", defaults[unit])),
                key=f"color_{unit}")
        st.caption("可选：红 / 绿 / 蓝 / 黄 / 白 / 橙 / 紫")

    # ---------- 主区标题 ----------
    st.title("🐟  · 智慧养殖光照监测与协同决策系统")
    st.caption(f"Modbus 从站 127.0.0.1:{C.MODBUS_PORT} · 数据库 {C.DB_NAME}.{C.TABLE} · "
               f"登录用户 {st.session_state.get('user', '')}")

    # ---------- 实时面板 ----------
    realtime_panel(collector, colors)

    # ---------- 补光灯控制 ----------
    st.divider()
    st.markdown("### 🎛 继电器控制面板")
    auto = st.toggle("开启自动补光（光照低于阈值自动闭合继电器）",
                     value=collector.auto_light)
    if auto != collector.auto_light:
        collector.auto_light = auto
        collector._log(f"自动补光{'开启' if auto else '关闭'}")
    th = st.number_input("低光照阈值 (lux)",
                         min_value=0, max_value=5000,
                         value=int(collector.threshold), step=50)
    collector.threshold = float(th)
    st.caption(f"自动逻辑：光照 < {collector.threshold} lux → 开启补光灯；"
               f"光照 > {collector.threshold + C.AUTO_LIGHT_HYSTERESIS} lux → 关闭补光灯")

    mcols = st.columns(4)
    for i, unit in enumerate(C.NODES):
        with mcols[i]:
            on = collector.coil.get(unit, False)
            st.markdown(f"**{C.NODE_NAMES[unit]}** 0x{unit:02X}")
            st.markdown("🟡 补光中" if on else "⚪ 已关闭")
            btn = st.button("关闭补光灯" if on else "开启补光灯",
                            key=f"manual_{unit}", use_container_width=True)
            if btn:
                manual_light(collector, unit, not on)
                st.rerun()

    # ---------- 飞书协同告警 ----------
    st.divider()
    st.markdown("### 📨 飞书协同告警")
    if C.FEISHU_WEBHOOK:
        st.success("✅ Webhook 已配置：低光照持续 2 轮将自动推送告警卡片到飞书群")
        if st.button("📨 发送测试告警卡片（验证连通性）"):
            ok = feishu_alarm.send_test_card(C.FEISHU_WEBHOOK)
            if ok:
                st.success("测试卡片已发送，请到飞书群查看")
            else:
                st.error("发送失败，请检查 Webhook 地址")
    else:
        st.warning("Webhook 未配置，告警暂不推送（仍记录在系统日志）")
        st.caption("配置方法：飞书群 → 设置 → 群机器人 → 添加机器人 → 自定义机器人"
                   " → 复制 Webhook 地址 → 填入 config.py 的 FEISHU_WEBHOOK")
    st.caption("闭环演示：群内收到告警卡片 → 点「💡 开启补光灯」→ 跳转控制端点(8502)"
               " → 写继电器线圈 → 大屏实时可见光照恢复")

    # ---------- AI 自然语言查询 ----------
    st.divider()
    st.markdown("### 🤖 AI 自然语言查询")
    if C.LLM_API_KEY:
        st.caption(f"模式：🟢 **AI 模式**（{C.LLM_MODEL} 生成 SQL 并总结）")
    else:
        st.caption("模式：🟡 **规则引擎**（未配置 Key，仅支持今日统计类问题）"
                   "；配置 DEEPSEEK_API_KEY 环境变量后升级为任意自然语言提问")
    with st.form("ai_query_form"):
        question = st.text_input(
            "输入问题",
            placeholder="例如：今天几点光照最低？2号节点平均光照多少？"
                        "最近2小时采集了多少条？",
        )
        submitted = st.form_submit_button("🔍 查询并生成 AI 总结", type="primary")
    if submitted and question.strip():
        q = question.strip()
        # 导出意图识别
        if any(k in q for k in ("导出", "下载", "excel", "csv", "表格")):
            # 解析导出范围
            from datetime import timedelta as _td
            now = datetime.now()
            start = now - _td(days=7)
            end = now
            dev = None
            label = "最近7天"
            fmt = "both"
            if re.search(r"excel|xlsx|电子表格", q, re.I):
                fmt = "xlsx"
            elif re.search(r"csv|文本", q, re.I):
                fmt = "csv"
            if "今天" in q or "今日" in q:
                start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                label = "今天"
            if "全部" in q or "所有" in q:
                start = datetime(2020,1,1)
                label = "全部"
            m = re.search(r"([1-4])\s*号\s*(传感器|节点)?", q)
            if m:
                dev = int(m.group(1))
                label = f"{dev}号传感器 {label}"
            m = re.search(r"(\d{1,2})[:：点](\d{0,2})\s*[~至到\-—]+\s*(\d{1,2})[:：点](\d{0,2})", q)
            if m:
                h1,m1,h2,m2 = int(m.group(1)),int(m.group(2) or 0),int(m.group(3)),int(m.group(4) or 0)
                start = now.replace(hour=h1, minute=m1, second=0, microsecond=0)
                end = now.replace(hour=h2, minute=m2, second=0, microsecond=0)
                label = f"{h1:02d}:{m1:02d}~{h2:02d}:{m2:02d}"
            df = query_between(start, end, dev)
            if df.empty:
                st.warning(f"⚠️ {label}暂无数据")
            else:
                if "测量时间" in df.columns:
                    df = df.copy()
                    df["测量时间"] = pd.to_datetime(df["测量时间"]).dt.strftime("%Y-%m-%d %H:%M:%S")
                st.success(f"📤 已生成 {label} 数据（{len(df)} 条）")
                st.dataframe(df, use_container_width=True)
                csv_b = df.to_csv(index=False).encode("utf-8-sig")
                buf = BytesIO()
                df.to_excel(buf, index=False, sheet_name="数据")
                fc1, fc2 = st.columns(2)
                if fmt in ("csv","both"):
                    fc1.download_button("⬇ CSV", csv_b, file_name=f"数据_{start:%Y%m%d_%H%M}.csv", mime="text/csv")
                if fmt in ("xlsx","both"):
                    fc2.download_button("⬇ Excel", buf.getvalue(), file_name=f"数据_{start:%Y%m%d_%H%M}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        else:
            with st.spinner("AI 正在分析数据..."):
                res = ai_query.handle(q)
            if res["mode"] == "ai":
                st.caption("🟢 AI 模式：大模型生成 SQL → 白名单校验 → 执行 → 总结")
            else:
                st.caption(f"🟡 规则模式：{res['note']}")
            st.markdown("**自动生成的 SQL**")
            st.code(res["sql"], language="sql")
            if not res["df"].empty:
                st.dataframe(res["df"], use_container_width=True)
            st.markdown(f"**AI 总结**：{res['summary']}")
    st.caption("示例：今天几点光照最低？/ 导出今天2号传感器数据为Excel / 0x02 最近100条中超过3000lux的有几条？")

    # ---------- 历史查询 ----------
    st.divider()
    st.markdown("### 📚 历史记录查询")
    c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
    today = datetime.now().date()
    d1 = c1.date_input("起始日期", value=today - timedelta(days=7))
    d2 = c2.date_input("结束日期", value=today)
    t1 = c3.time_input("起始时间", value=datetime(2000, 1, 1, 0, 0).time())
    t2 = c4.time_input("结束时间", value=datetime(2000, 1, 1, 23, 59).time())
    sensor_opt = st.selectbox("传感器", ["全部", "1号(0x01)", "2号(0x02)", "3号(0x03)", "4号(0x04)"])
    sensor_map = {"全部": None, "1号(0x01)": 1, "2号(0x02)": 2, "3号(0x03)": 3, "4号(0x04)": 4}
    sel_device = sensor_map[sensor_opt]

    if st.button("🔍 查询历史记录", type="primary"):
        start_dt = datetime.combine(d1, t1)
        end_dt = datetime.combine(d2, t2)
        if start_dt > end_dt:
            st.error("起始时间不能晚于结束时间")
        else:
            st.session_state["query_df"] = query_between(start_dt, end_dt, sel_device)
            st.session_state["query_ok"] = True
            dev_label = f"{sensor_opt} · " if sel_device else ""
            st.session_state["query_range"] = f"{dev_label}{start_dt:%Y-%m-%d %H:%M} ~ {end_dt:%Y-%m-%d %H:%M}"

    if st.session_state.get("query_ok"):
        qdf = st.session_state["query_df"]
        st.markdown(f"**查询范围**：{st.session_state.get('query_range', '')} · "
                    f"共 **{len(qdf)}** 条")
        st.dataframe(qdf, use_container_width=True, height=320)
        if "测量时间" in qdf.columns:
            qdf = qdf.copy()
            qdf["测量时间"] = pd.to_datetime(qdf["测量时间"]).dt.strftime("%Y-%m-%d %H:%M:%S")
        csv_bytes = qdf.to_csv(index=False).encode("utf-8-sig")
        xlsx_buf = BytesIO()
        qdf.to_excel(xlsx_buf, index=False, sheet_name="光照记录")
        e1, e2, e3 = st.columns(3)
        e1.download_button(
            "⬇ CSV", csv_bytes,
            file_name=f"光照记录_{d1}_{d2}.csv", mime="text/csv",
            use_container_width=True)
        e2.download_button(
            "⬇ Excel", xlsx_buf.getvalue(),
            file_name=f"光照记录_{d1}_{d2}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True)
        e3.download_button(
            "⬇ CSV+Excel(打包)", csv_bytes,
            file_name=f"光照记录_{d1}_{d2}.csv", mime="text/csv",
            use_container_width=True,
            help="同时导出 CSV 和 Excel，分别点上面两个按钮即可")

    # ---------- 日志 ----------
    with st.expander("📜 系统日志（采集 / 补光 / 告警）"):
        logs = list(reversed(collector.logs[-50:]))
        for ts, text in logs:
            st.caption(f"`{ts}`  {text}")
        if not logs:
            st.caption("暂无日志")

    # ---------- 待接入 ----------
    st.divider()
    st.caption("🚧 飞书协同告警与 AI 自然语言查询：配置 Webhook 与 API Key 后启用")


# ================= 入口 =================

if not st.session_state.get("auth"):
    login_page()

collector = init_collector()
main_page(collector)
