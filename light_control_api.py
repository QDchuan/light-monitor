# -*- coding: utf-8 -*-
"""
 —— 本地继电器控制端点
================================
为飞书卡片按钮提供跳转控制入口（标准库实现，无额外依赖）：
  GET /api/light?unit=1&on=1   -> 写线圈控制 0x01 补光灯
  GET /                         -> 控制面板说明页

演示闭环：飞书群收到告警卡片 → 点击「开启补光灯」→ 浏览器打开本端点
→ Python 写 Modbus 线圈 → 从站补光生效 → 大屏实时可见。

运行：python light_control_api.py --port 8502
"""
import argparse
import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

from pymodbus.client import ModbusTcpClient

import config as C

HOST = "127.0.0.1"
PORT = 8502

PAGE_CSS = """
body{font-family:'Microsoft YaHei',sans-serif;background:#0e1117;color:#e6e6e6;
     display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0}
.card{background:#1b2330;border-radius:14px;padding:40px 48px;max-width:480px;
      box-shadow:0 8px 30px rgba(0,0,0,.4);text-align:center}
h2{margin-top:0;color:#fff}
.status{font-size:22px;font-weight:700;margin:18px 0}
.ok{color:#22c55e}.err{color:#ef4444}
.info{color:#9ca3af;font-size:14px;line-height:1.8;margin:14px 0}
a{display:inline-block;margin-top:18px;padding:10px 26px;border-radius:8px;
  background:#2f6fed;color:#fff;text-decoration:none;font-weight:600}
.lux{font-size:16px;color:#f6c026;margin-top:8px}
"""


def read_lux(unit):
    client = ModbusTcpClient(C.MODBUS_HOST, port=C.MODBUS_PORT)
    if not client.connect():
        return None
    try:
        rr = client.read_holding_registers(0, count=1, slave=unit)
        return rr.registers[0] if not rr.isError() else None
    finally:
        client.close()


def write_coil(unit, on):
    client = ModbusTcpClient(C.MODBUS_HOST, port=C.MODBUS_PORT)
    if not client.connect():
        return False
    try:
        rr = client.write_coil(0, on, slave=unit)
        return not rr.isError()
    finally:
        client.close()


def page(title, status_cls, status_text, lux_text="", note=""):
    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>{title}</title><style>{PAGE_CSS}</style></head>
<body><div class="card">
<h2>🐟  · 继电器控制</h2>
<div class="status {status_cls}">{status_text}</div>
<div class="lux">{lux_text}</div>
<div class="info">{note}</div>
<a href="{C.MODBUS_HOST and 'http://localhost:8501'}">→ 打开监控大屏</a>
</div></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):          # 精简日志
        print("[控制API] %s" % (fmt % args), flush=True)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path in ("/", "/api"):
            html = page("继电器控制", "ok", "🟢 控制端点运行中",
                        note="用法：/api/light?unit=1&on=1（unit 为节点编号 1-4）")
            self._send(html)
            return

        if url.path == "/api/light":
            qs = urllib.parse.parse_qs(url.query)
            try:
                unit = int(qs.get("unit", ["1"])[0])
                on = qs.get("on", ["1"])[0].lower() in ("1", "true", "on", "yes")
            except ValueError:
                self._send(page("参数错误", "err", "❌ 参数无效",
                                note="unit 需为 1-4 的数字"), 400)
                return

            node_name = C.NODE_NAMES.get(unit, f"节点0x{unit:02X}")
            ok = write_coil(unit, on)
            if ok:
                lux = read_lux(unit)
                lux_text = f"当前 {node_name} 光照：{lux} lux" if lux is not None else ""
                action = "已开启" if on else "已关闭"
                html = page(
                    "控制成功", "ok", f"✅ {node_name} 补光灯{action}",
                    lux_text=lux_text,
                    note="返回监控大屏查看实时曲线变化")
            else:
                html = page(
                    "控制失败", "err", f"❌ {node_name} 补光灯控制失败",
                    note="请确认从站 (5020) 正在运行")
            self._send(html)
            return

        self._send(page("404", "err", "❌ 页面不存在"), 404)

    def _send(self, html, code=200):
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    parser = argparse.ArgumentParser(description="飞书卡片按钮控制端点")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    print(f"[控制API] 监听 {args.host}:{args.port}")
    print("[控制API] 用法: http://localhost:8502/api/light?unit=1&on=1")
    HTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
