import pathlib
p = pathlib.Path(r'C:\Users\chuan\Doubao\chats\2026-09-19\new-chat-1\streamlit_app.py')
c = p.read_text(encoding='utf-8')
old = '        csv_bytes = qdf.to_csv(index=False).encode("utf-8-sig")'
new = '''        if "测量时间" in qdf.columns:
            qdf = qdf.copy()
            qdf["测量时间"] = pd.to_datetime(qdf["测量时间"]).dt.strftime("%Y-%m-%d %H:%M:%S")
        csv_bytes = qdf.to_csv(index=False).encode("utf-8-sig")'''
assert old in c, "old not found"
c = c.replace(old, new, 1)
p.write_text(c, encoding='utf-8')
print("done")
