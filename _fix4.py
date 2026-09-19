p = r'C:\Users\chuan\Doubao\chats\2026-09-19\new-chat-1\feishu_bot.py'
c = open(p, encoding='utf-8').read()
c = c.replace(', device, device', ', device')
open(p, 'w', encoding='utf-8').write(c)
print("done")
