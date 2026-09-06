# SlimeNodes Auto Coin + Renew 🟢

自动刷币 + 自动续期 [SlimeNodes](https://dash.slimenodes.com) 服务器。使用 `connect.sid` session cookie 认证，**session 失效时通过 Discord token 纯 HTTP 自动重登录**（免浏览器，绕开 hCaptcha），全程无人值守。

## 功能

- 🪙 **自动刷币** — 通过 Linkvertise 广告赚取金币（每次 +240 币）
- 🔄 **自动续期** — 服务器到期前 <24 小时自动续期
- 🔐 **Session 自动恢复** — SID 失效/无法获取余额时，自动用 Discord token 走 OAuth 换新 session、更新 secret 并重试
- 📱 **TG 通知** — 每次运行后发送详细通知到 Telegram
- 🚦 **真实退出码** — 失败时以非零码退出，GitHub Actions 如实反映状态并告警

## 工作原理

纯 HTTP 方式，无需浏览器：

1. 使用 Session Cookie 登录
2. 调用 `/lv/gen` 获取 Linkvertise 链接
3. 解码 `r` 参数（Base64）得到 redeem URL
4. 等待 16-20 秒（服务端计时验证）
5. Redeem URL → +12 币/次
6. 每次运行 20 次广告 = +240 币
7. **自动重登录**：若 `无法获取余额`（session 完全失效）或请求返回过期，自动走 Discord OAuth（`DISCORD_TOKEN` + client_id）换新 `connect.sid` → 用 `GH_TOKEN` 更新 `SLIME_SESSION` secret → 重新刷币

> ⚠️ 曾修复 bug：session 完全过期时 `bal()` 返回 `BALANCE_FAIL`(3) 而非 `SESSION_EXPIRED`(2)，导致旧逻辑不触发自动重登录直接失败。现已将两种失效都纳入自动重登录触发条件。

## 定时任务

GitHub Actions 每天自动运行两次：

- ⏰ **00:30 UTC**（北京时间 08:30）
- ⏰ **12:30 UTC**（北京时间 20:30）

也可手动触发 `workflow_dispatch`。

## GitHub Secrets 配置

| Secret | 说明 | 必填 |
|--------|------|------|
| `SLIME_SESSION` | SlimeNodes session cookie (`connect.sid`) | ✅ |
| `DISCORD_TOKEN` | Discord 账号 token（纯 HTTP OAuth 换 session 用） | ⚠️ 需自动续期时 |
| `GH_TOKEN` | GitHub PAT（自动更新 SLIME_SESSION secret） | ⚠️ 需自动续期时 |
| `TG_BOT_TOKEN` | Telegram Bot Token | ❌ |
| `TG_CHAT_ID` | Telegram Chat ID | ❌ |
| `SERVER_ID` | 服务器 ID（默认 10102） | ❌ |
| `ACCOUNT_LABEL` | 通知显示的账号名（默认 btpphlmb） | ❌ |
| `SOCKS_PROXY` | 代理（可选，纯 HTTP 刷币用） | ❌ |
| `MAX_ADS` | 最大广告数（默认 20） | ❌ |
| `RENEW_HOURS` | 续期阈值小时数（默认 24） | ❌ |
| `RENEW_THRESHOLD` | 续期最低余额（默认 50 币） | ❌ |

> 🔑 `DISCORD_TOKEN` 获取：浏览器登录 Discord → DevTools → Network → 任一个 `discord.com/api` 请求 → Request Headers → `authorization` 字段的值
> ⚠️ `GH_TOKEN` 需为带 `repo` + `workflow` 权限的 PAT（GITHUB_TOKEN 无权写仓库 secrets）

## TG 通知格式

```
🟢 SlimeNodes 刷币通知
━━━━━━━━━━━━━━━━
👤 账号: btpphlmb@outlook.com
━━━━━━━━━━━━━━━━
💰 刷币: ✅ +240币
🏦 余额: 1400币
⏰ 到期: 161小时后 (6.7天)
🔄 续期: ⏭️ 暂不需要 (>24h)
━━━━━━━━━━━━━━━━
```

session 失效且自动重登录成功时，通知会标注 `🔄 (本次已自动刷新 session 并重试)`。

## 续期逻辑

- 通过 `/lastrenew?id={SERVER_ID}` API 获取精确到期时间
- 仅当剩余 < `RENEW_HOURS`（默认 24h）且余额 ≥ `RENEW_THRESHOLD`（默认 50 币）时自动续期
- 续期费用：约 115 币/次

## 文件结构

```
├── slimenodes.py          # 主脚本（刷币 + 续期 + Discord OAuth 自动重登录 + 通知）
├── .github/workflows/
│   └── coin.yml           # GitHub Actions 工作流
└── README.md
```

## 参考

- [jpus/SlimeNodes-AutoCoin](https://github.com/jpus/SlimeNodes-AutoCoin) — 原始参考脚本
