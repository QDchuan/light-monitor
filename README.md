# 智慧养殖光照监测与协同决策系统

基于 **Python + Streamlit + MySQL + 飞书开放平台** 的智慧养殖光照监测系统，零硬件依赖，纯软件模拟即可完成完整业务闭环演示。

## 功能特性

### 数据采集与实时监控
- 4 节点模拟光照数据采集（Modbus RTU 协议，pymodbus 模拟从站）
- Streamlit Web 监控大屏：实时数值、实时曲线、历史数据查询
- 采样周期可调：2s / 4s / 6s / 8s / 10s 五档
- 演示倍速调节：方便快速演示数据流动

### 数据持久化与导出
- MySQL 数据库存储（表 `light_records`）
- 历史记录按时间范围筛选查询
- 一键导出 CSV / Excel 文件

### 智能控制
- RS485 继电器手动控制（Streamlit 面板）
- 自动补光：光照低于阈值自动开启补光灯（带迟滞防抖）

### 飞书协同
- **Webhook 告警**：光照异常自动推送告警卡片到飞书群
- **AI 机器人**：群内 @机器人 即可
  - 自然语言查询（如"今天几点光照最低？"）
  - 导出数据（如"导出今天的数据"），CSV/Excel 自动发到群里

### AI 赋能
- 接入 DeepSeek 大语言模型
- 自然语言 → SQL → 查询结果 → AI 中文总结
- 大屏内置 AI 查询面板

## 技术栈

| 层级 | 技术 |
|------|------|
| 通信层 | Modbus RTU（pymodbus 3.x） |
| 数据层 | MySQL（pymysql / SQLAlchemy） |
| 应用层 | Streamlit |
| 协同层 | 飞书开放平台（Webhook + 自建应用 API） |
| AI 层 | DeepSeek API（OpenAI 兼容接口） |

## 快速启动

### 1. 环境准备

```bash
# 安装依赖
pip install -r requirements.txt
```

### 2. 配置环境变量

在系统环境变量中设置以下变量（不要硬编码到代码里）：

| 变量名 | 说明 |
|--------|------|
| `MYSQL_PASSWORD` | MySQL root 密码（默认 123456） |
| `FEISHU_WEBHOOK` | 飞书自定义机器人 Webhook 地址 |
| `FEISHU_APP_ID` | 飞书自建应用 App ID |
| `FEISHU_APP_SECRET` | 飞书自建应用 App Secret |
| `DEEPSEEK_API_KEY` | DeepSeek API Key |

### 3. 初始化数据库

```bash
# 先确保 MySQL 已启动，然后运行：
python seed_sim_data.py    # 生成模拟光照数据（4 节点 72 小时昼夜曲线）
```

### 4. 一键启动

```bash
start_all.bat
```

或手动启动各模块：

```bash
# 终端 1：Modbus 模拟从站
python modbus_slave_server.py --port 5020

# 终端 2：Modbus 采集器（轮询入库）
python modbus_collector.py

# 终端 3：Streamlit 大屏
streamlit run streamlit_app.py --server.port 8501

# 终端 4：飞书机器人（轮询模式）
python feishu_bot.py
```

### 5. 访问大屏

打开浏览器访问 http://localhost:8501

默认登录账号：`admin` / `123456`

## 项目结构

```
├── config.py              # 全局配置（敏感信息从环境变量读取）
├── streamlit_app.py       # Streamlit 监控大屏主程序
├── modbus_slave_server.py # Modbus 模拟从站（替代硬件传感器）
├── modbus_collector.py    # Modbus 主站轮询采集 + MySQL 入库
├── seed_sim_data.py       # 模拟数据生成脚本
├── feishu_alarm.py        # 飞书 Webhook 告警模块
├── feishu_bot.py          # 飞书 AI 机器人（轮询模式）
├── light_control_api.py   # 本地继电器控制端点
├── ai_query.py            # AI 自然语言查询模块
└── start_all.bat          # 一键启动脚本
```

## 飞书机器人配置说明

1. 在 [飞书开放平台](https://open.feishu.cn) 创建企业自建应用
2. 启用机器人能力
3. 开通权限：
   - `im:message:send_as_bot`（以机器人身份发消息）
   - `im:message.group_msg`（读取群消息）
   - `im:chat:readonly`（获取群信息）
   - `im:resource`（上传文件）
4. 将机器人拉入目标群
5. 设置环境变量 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET`
6. 运行 `python feishu_bot.py`

## License

MIT
