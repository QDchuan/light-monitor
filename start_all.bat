@echo off
REM ============================================================
REM   · 一键启动脚本
REM  1) Modbus 模拟从站 (127.0.0.1:5020, 4节点, 60倍速演示模式)
REM  2) Streamlit 监控大屏 (http://localhost:8501)
REM  3) 继电器控制端点 (http://localhost:8502, 飞书卡片按钮跳转)
REM  4) 飞书 AI 查询机器人 (WebSocket 长连接, 需配置 AppID/Secret)
REM  账号: admin  密码: 123456
REM  停止: 关闭弹出的窗口即可
REM ============================================================
cd /d "%~dp0"

echo [1/4] 启动 Modbus 模拟从站 (5020, 60倍速) ...
start "-Modbus从站" ".venv\Scripts\python.exe" modbus_slave_server.py --port 5020 --speed 60

timeout /t 3 /nobreak >nul

echo [2/4] 启动 Streamlit 监控大屏 (8501) ...
start "-监控大屏" ".venv\Scripts\python.exe" -m streamlit run streamlit_app.py --server.headless true --server.port 8501 --browser.gatherUsageStats false

timeout /t 3 /nobreak >nul

echo [3/4] 启动继电器控制端点 (8502) ...
start "-控制端点" ".venv\Scripts\python.exe" light_control_api.py --port 8502

timeout /t 3 /nobreak >nul

echo [4/4] 启动飞书 AI 查询机器人 ...
start "-飞书机器人" ".venv\Scripts\python.exe" feishu_bot.py

echo.
echo 大屏地址: http://localhost:8501
echo 账号: admin / 123456
echo 控制端点: http://localhost:8502
echo 飞书机器人: 配置 FEISHU_APP_ID / FEISHU_APP_SECRET 后可用
echo.
pause
