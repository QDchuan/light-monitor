p = r'C:\Users\chuan\Doubao\chats\2026-09-19\new-chat-1\streamlit_app.py'
c = open(p, encoding='utf-8').read()

# 在 AI 查询表单提交后，先检查是不是导出请求
old = '''    if submitted and question.strip():
        with st.spinner("AI 正在分析数据..."):
            res = ai_query.handle(question.strip())'''

new = '''    if submitted and question.strip():
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
            m = re.search(r"(\d{1,2})[:：点](\d{0,2})\s*[~至到\\-—]+\\s*(\d{1,2})[:：点](\d{0,2})", q)
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
                res = ai_query.handle(q)'''

c = c.replace(old, new)

# 把原来的 if res["mode"] 块包到 else 里
old2 = '''        if res["mode"] == "ai":
            st.caption("🟢 AI 模式：大模型生成 SQL → 白名单校验 → 执行 → 总结")
        else:
            st.caption(f"🟡 规则模式：{res['note']}")
        st.markdown("**自动生成的 SQL**")
        st.code(res["sql"], language="sql")
        if not res["df"].empty:
            st.dataframe(res["df"], use_container_width=True)
        st.markdown(f"**AI 总结**：{res['summary']}")
    st.caption("示例：今天几点光照最低？/ 各节点平均光照？/ 0x02 最近100条中超过3000lux的有几条？")'''

new2 = '''            if res["mode"] == "ai":
                st.caption("🟢 AI 模式：大模型生成 SQL → 白名单校验 → 执行 → 总结")
            else:
                st.caption(f"🟡 规则模式：{res['note']}")
            st.markdown("**自动生成的 SQL**")
            st.code(res["sql"], language="sql")
            if not res["df"].empty:
                st.dataframe(res["df"], use_container_width=True)
            st.markdown(f"**AI 总结**：{res['summary']}")
    st.caption("示例：今天几点光照最低？/ 导出今天2号传感器数据为Excel / 0x02 最近100条中超过3000lux的有几条？")'''

c = c.replace(old2, new2)

open(p, 'w', encoding='utf-8').write(c)
print("done")
