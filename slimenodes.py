#!/usr/bin/env python3
"""SlimeNodes Auto-Coin + Auto-Renew + Discord OAuth auto-relogin"""
import os, sys, re, json, time, base64, random, subprocess
from urllib.parse import unquote, urlencode
from datetime import datetime, timezone

BASE = "https://dash.slimenodes.com"
CPC = 12
WAIT = 16
def get_env_int(key, default):
    v = os.environ.get(key, "").strip()
    return int(v) if v.isdigit() else default

MAX = get_env_int("MAX_ADS", 20)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
TGT = os.environ.get("TG_BOT_TOKEN", "").strip()
TGC = os.environ.get("TG_CHAT_ID", "").strip()
PX = os.environ.get("SOCKS_PROXY", os.environ.get("HTTP_PROXY", "")).strip()
SESSION = os.environ.get("SLIME_SESSION", "").strip()
ACCOUNT_LABEL = os.environ.get("ACCOUNT_LABEL", "").strip() or "btpphlmb"
SERVER_ID = os.environ.get("SERVER_ID", "").strip() or "10102"
RENEW_THRESHOLD = get_env_int("RENEW_THRESHOLD", 50)
RENEW_HOURS = get_env_int("RENEW_HOURS", 48)  # 默认到期前48小时内执行续期

# Discord OAuth 自动登录配置
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
GH_TOKEN = os.environ.get("GH_TOKEN", "")
DISCORD_CLIENT_ID = "1267847469501513799"
OAUTH_REDIRECT_URI = "https://dash.slimenodes.com/callback"
OAUTH_SCOPE = "identify email guilds.join"
DISCORD_AUTH_API = "https://discord.com/api/v9/oauth2/authorize"
DISCORD_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"

# Exit codes
EXIT_OK = 0
EXIT_NO_SESSION = 1
EXIT_SESSION_EXPIRED = 2
EXIT_BALANCE_FAIL = 3
EXIT_EARN_FAIL = 4

def px(): return ["-x", PX] if PX else []
def log(m): print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {m}", flush=True)
def ok(m): log(f"✅ {m}")
def er(m): log(f"❌ {m}")

def run_curl(args, timeout=25):
    cmd = ["curl", "-s", "--connect-timeout", "20", "--max-time", str(timeout)] + px() + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout+10)
        return r.stdout
    except Exception as e:
        return f"ERR:{e}"

def send_tg(msg):
    if not TGT or not TGC: return
    run_curl(["-s", "-X", "POST", f"https://api.telegram.org/bot{TGT}/sendMessage",
              "-H", "Content-Type: application/json",
              "-d", json.dumps({"chat_id": TGC, "text": msg, "parse_mode": "HTML"})],
             timeout=15)

def ck(s): return f"connect.sid={s}"

# ─── Discord OAuth 自动登录 ──────────────────────────────────────

def discord_get_code():
    """用 DISCORD_TOKEN 调 authorize API，返回授权码 code"""
    if not DISCORD_TOKEN:
        er("未配置 DISCORD_TOKEN，无法自动登录")
        return None
    query = urlencode({
        "client_id": DISCORD_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": OAUTH_REDIRECT_URI,
        "scope": OAUTH_SCOPE,
    })
    authorize_url = f"{DISCORD_AUTH_API}?{query}"
    referer = ("https://discord.com/oauth2/authorize?" + query)

    headers = [
        "-H", f"authorization: {DISCORD_TOKEN}",
        "-H", "content-type: application/json",
        "-H", "origin: https://discord.com",
        "-H", f"referer: {referer}",
        "-H", f"user-agent: {DISCORD_UA}",
    ]
    body = json.dumps({
        "permissions": "0",
        "authorize": True,
        "integration_type": 0,
        "location_context": {"guild_id": "10000", "channel_id": "10000", "channel_type": 10000},
    })
    out = run_curl(headers + ["-d", body, authorize_url], timeout=25)
    try:
        resp = json.loads(out)
        loc = resp.get("location", "")
        m = re.search(r"[?&]code=([^&\s]+)", loc)
        if m:
            code = m.group(1)
            ok(f"Discord OAuth 获取授权码成功")
            return code
        er(f"Discord authorize 响应无 code: {loc[:120]}")
    except Exception as e:
        er(f"Discord authorize 解析失败: {e} | 响应: {out[:150]}")
    return None


def discord_get_session():
    """完整 Discord OAuth 流程，返回新的 connect.sid（无则 None）"""
    log("🔄 尝试 Discord OAuth 自动登录...")
    code = discord_get_code()
    if not code:
        return None

    # 1. 访问 /callback?code=... (JS 重定向页)
    cb = run_curl(["-H", f"User-Agent: {UA}", f"{BASE}/callback?code={code}"], timeout=20)
    m = re.search(r"submitlogin\?code=([^'\"]+)", cb)
    if not m:
        er(f"callback 未找到 submitlogin 跳转: {cb[:120]}")
        return None
    sub_code = m.group(1)

    # 2. 访问 /submitlogin?code=... → set-cookie connect.sid
    hdr = "/tmp/slime_sub.txt"
    cmd = ["curl", "-s", "-D", hdr, "--connect-timeout", "20", "--max-time", "20"] + px()
    cmd += ["-H", f"User-Agent: {UA}", f"{BASE}/submitlogin?code={sub_code}"]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=25)
        with open(hdr) as f:
            for line in f:
                if line.lower().startswith("set-cookie:"):
                    m2 = re.search(r"connect\.sid=([^;]+)", line)
                    if m2:
                        new_sid = m2.group(1)
                        ok(f"Discord OAuth 登录成功，获得新 session")
                        return new_sid
    except Exception as e:
        er(f"submitlogin 异常: {e}")
    er("Discord OAuth 登录后未获得 session cookie")
    return None


def update_github_secret(secret_name, new_value):
    """用 gh secret set 自动更新 GitHub Actions secret"""
    if not GH_TOKEN:
        er("未配置 GH_TOKEN，无法自动更新 secret")
        return False
    if not new_value:
        er(f"跳过更新 {secret_name}：新值为空")
        return False
    masked = new_value[:4] + "..." + new_value[-4:] if len(new_value) > 8 else "***"
    log(f"🔄 更新 Secret: {secret_name} (新值: {masked})")
    try:
        env = os.environ.copy()
        env["GH_TOKEN"] = GH_TOKEN
        proc = subprocess.run(
            ["gh", "secret", "set", secret_name, "--body", new_value],
            capture_output=True, text=True, timeout=30, check=False, env=env
        )
        if proc.returncode == 0:
            ok(f"{secret_name} 更新成功")
            return True
        else:
            er(f"更新失败: {proc.stderr.strip()}")
            return False
    except FileNotFoundError:
        er("gh CLI 未安装，无法自动更新 secret")
        return False
    except Exception as e:
        er(f"更新异常: {e}")
        return False

# ─── 刷币/续期逻辑 ──────────────────────────────────────

def cooldown(s):
    body = run_curl(["-H", f"User-Agent: {UA}", "-H", f"Cookie: {ck(s)}",
                     f"{BASE}/api/lvcooldown"])
    try: return json.loads(body)
    except: return {"error": body[:80]}

def gen(s):
    hdr = "/tmp/sng.txt"
    cmd = ["curl", "-s", "-D", hdr, "--connect-timeout", "20", "--max-time", "20"] + px()
    cmd += ["-H", f"User-Agent: {UA}", "-H", f"Cookie: {ck(s)}",
            "-H", f"Referer: {BASE}/lv", f"{BASE}/lv/gen"]
    try:
        body = subprocess.run(cmd, capture_output=True, text=True, timeout=25).stdout
        loc = ""
        with open(hdr) as f:
            for line in f:
                if line.lower().startswith("location:"):
                    loc = line.split(":",1)[1].strip(); break
        if not loc:
            m = re.search(r'href="(https://link-to\.net/[^"]+)"', body)
            if m: loc = m.group(1)
        if not loc:
            if "daily" in body.lower() and "limit" in body.lower(): return "DAILY_LIMIT"
            if "/login" in (loc or body): return "SESSION_EXPIRED"
            er(f"Gen fail: {body[:120]}"); return None
        rm = re.search(r'[?&]r=([^&\s]+)', loc)
        if not rm: er(f"No r: {loc[:60]}"); return None
        rp = unquote(rm.group(1)).replace("-","+").replace("_","/")
        rp += "=" * ((4-len(rp)%4)%4)
        try: return base64.b64decode(rp).decode()
        except: er("b64 fail"); return None
    except Exception as e:
        er(f"Gen err: {e}"); return None

def red(s, url):
    hdr = "/tmp/snr.txt"
    cmd = ["curl", "-s", "-D", hdr, "--connect-timeout", "20", "--max-time", "20"] + px()
    cmd += ["-H", f"User-Agent: {UA}", "-H", f"Cookie: {ck(s)}",
            "-H", "Referer: https://linkvertise.com/", url]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
    body = result.stdout or ""
    loc = ""
    with open(hdr) as f:
        for line in f:
            if line.lower().startswith("location:"):
                loc = line.split(":",1)[1].strip(); break
    # linkvertise 加了 Cloudflare bot 拦截, 看到 "Attention Required" 直接放弃整个循环
    if "Attention Required" in body or "cf-mitigated" in body:
        return "BLOCKED"
    if "success=true" in loc: return "OK"
    if "LVBYPASSERROR" in loc: return "BYPASS"
    if "/login" in loc: return "SESSION_EXPIRED"
    if "daily" in loc.lower(): return "DAILY_LIMIT"
    return f"UNK:{loc[:40]}"

def bal(s):
    """获取余额，并尝试提取当前登录用户名"""
    body = run_curl(["-L", "-H", f"User-Agent: {UA}", "-H", f"Cookie: {ck(s)}",
                     f"{BASE}/dashboard"])
    m = re.search(r'balance\.textContent\s*=\s*Math\.floor\((\d+)\s*\*\s*100\)', body)
    balance = int(m.group(1)) if m else None
    
    # 提取用户名
    username = None
    u_match = re.search(r'navbar-profile-name[^>]*>([^<]+)<', body)
    if u_match and u_match.group(1).strip():
        username = u_match.group(1).strip()
        
    return balance, username

def get_user_server_ids(s):
    """从面板获取当前账号名下的真实服务器 ID 列表"""
    try:
        body = run_curl(["-L", "-H", f"User-Agent: {UA}", "-H", f"Cookie: {ck(s)}",
                         f"{BASE}/servers"])
        ids = list(dict.fromkeys(re.findall(r'/(?:servers/edit|delete)\?id=(\d+)', body)))
        return ids
    except Exception as e:
        log(f"获取服务器列表异常: {e}")
        return []

def get_server_hours_left(s, server_id):
    """查询指定服务器剩余到期小时数"""
    cd_body = run_curl(["-H", f"User-Agent: {UA}", "-H", f"Cookie: {ck(s)}",
                        f"{BASE}/lastrenew?id={server_id}"])
    try:
        last_renew_ms = json.loads(cd_body).get("lastrenew", 0)
        if not last_renew_ms:
            return None
        now_ms = time.time() * 1000
        return (last_renew_ms - now_ms) / (1000 * 60 * 60)
    except Exception:
        return None

def renew(s, server_id):
    """Renew server. Returns (success: bool, msg: str)"""
    log(f"Renewing server {server_id}...")
    
    # 记录续期前到期时间戳
    before_hours = get_server_hours_left(s, server_id)

    # Use -w to capture final URL after redirects
    cmd = ["curl", "-s", "-L", "-w", "\n%{url_effective}", "--connect-timeout", "20", "--max-time", "20"]
    cmd += ["-H", f"User-Agent: {UA}", "-H", f"Cookie: {ck(s)}",
            f"{BASE}/renew?id={server_id}"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
        output = result.stdout
        lines = output.strip().split("\n")
        final_url = lines[-1] if lines else ""
        body = "\n".join(lines[:-1]) if len(lines) > 1 else ""
        log(f"Renew URL: {final_url[:80]}")
        
        # 1. 成功重定向判断
        if "success=RENEWED" in final_url or "RENEWED" in final_url:
            ok(f"Server {server_id} renewed!")
            return True, "已续期"
            
        # 2. 对比时间戳判断（若时间增加说明面板已续期但未带 RENEWED 参数）
        time.sleep(1)
        after_hours = get_server_hours_left(s, server_id)
        if before_hours is not None and after_hours is not None and after_hours > before_hours:
            ok(f"Server {server_id} 时间戳已延长 (+{after_hours - before_hours:.1f}h)!")
            return True, "已续期 (时间已延长)"

        if "/login" in final_url:
            er("Session expired during renew")
            return False, "Session过期"
            
        # 提取报错原因
        clean_msg = ""
        if "err=" in final_url:
            clean_msg = final_url.split("err=")[-1].split("&")[0]
        elif body.strip():
            clean_body = re.sub(r'<[^>]+>', ' ', body).strip()
            clean_msg = ' '.join(clean_body.split())[:100]
            
        er(f"Renew failed: {final_url[:80]} | 返回信息: {clean_msg or '未知'}")
        return False, clean_msg or "续期失败"
    except Exception as e:
        er(f"Renew error: {e}")
        return False, str(e)

def process(session_id, label="acct"):
    """
    Earn coins and optionally renew. Returns:
      (earned, daily_limit_hit, balance_after, renew_summary, exit_code)
    """
    log(f"\n{'='*40}\n账号: {label}\n{'='*40}")
    b0, detected_user = bal(session_id)
    if b0 is not None:
        log(f"余额: {b0}币")
    else:
        er("无法获取余额 - SID可能已过期或网络问题")
        return 0, False, None, {"status": "FAIL", "msg": "无法获取余额"}, EXIT_BALANCE_FAIL

    # Earn coins
    earned = 0; bypass = 0; daily = False; session_died = False
    for i in range(MAX):
        cd = cooldown(session_id)
        if cd.get("dailyLimit"):
            log("每日上限"); daily = True; break
        ru = gen(session_id)
        if ru == "DAILY_LIMIT": daily = True; break
        if ru == "SESSION_EXPIRED":
            er("Session过期"); session_died = True; break
        if not ru: er("gen失败"); break
        w = WAIT + random.randint(1, 4)
        log(f"广告{i+1}/{MAX}: 等{w}s...")
        time.sleep(w)
        r = red(session_id, ru)
        if r == "OK":
            earned += CPC; bypass = 0
            ok(f"+{CPC}币 (累计+{earned})")
        elif r == "BLOCKED":
            er("linkvertise 拦了 (CF bot challenge) - 跳过剩余")
            break
        elif r == "BYPASS":
            bypass += 1; er("BYPASS")
            if bypass >= 3: break
            time.sleep(10)
        elif r == "SESSION_EXPIRED":
            er("Session过期"); session_died = True; break
        elif r == "DAILY_LIMIT": daily = True; break
        else: er(r)
        time.sleep(random.randint(3, 6))

    b1, _ = bal(session_id)
    actual = max(b1 - b0, 0) if b0 is not None and b1 is not None else earned
    log(f"刷币完成: {b1}币 (本次+{actual})")

    # 确定要续期的服务器列表
    target_servers = []
    if SERVER_ID and SERVER_ID != "10102":
        target_servers = [s.strip() for s in SERVER_ID.split(",") if s.strip()]
        
    detected_servers = get_user_server_ids(session_id)
    if detected_servers:
        log(f"面板检测到名下服务器: {detected_servers}")
        valid_targets = [s for s in target_servers if s in detected_servers]
        if not valid_targets:
            target_servers = detected_servers
            log(f"已自动切换为实际服务器: {target_servers}")
    elif not target_servers:
        target_servers = ["10102"]

    # 循环检查与执行续期
    renew_summary = {
        "status": "SKIP",
        "msg": "",
        "hours_left": None,
        "details": []
    }
    
    for s_id in target_servers:
        hl = get_server_hours_left(session_id, s_id)
        if hl is not None:
            log(f"服务器 {s_id} 剩余: {hl:.0f}小时")
            renew_summary["hours_left"] = hl
            
        if b1 is not None and b1 >= RENEW_THRESHOLD:
            if hl is not None and hl > RENEW_HOURS:
                log(f"服务器 {s_id} 离到期还有 {hl:.0f}小时，暂不续期 (>{RENEW_HOURS}h)")
                renew_summary["details"].append(f"Server {s_id}: 剩余 {hl:.0f}h 暂不续期")
            else:
                success, r_msg = renew(session_id, s_id)
                if success:
                    renew_summary["status"] = "SUCCESS"
                    renew_summary["msg"] = r_msg
                    renew_summary["details"].append(f"Server {s_id}: {r_msg}")
                    b1, _ = bal(session_id)
                    log(f"续期后余额: {b1}")
                else:
                    if renew_summary["status"] != "SUCCESS":
                        renew_summary["status"] = "FAIL"
                        renew_summary["msg"] = r_msg
                    renew_summary["details"].append(f"Server {s_id}: 失败 ({r_msg})")
        elif b1 is not None:
            log(f"余额不足续期 (需要{RENEW_THRESHOLD}币, 当前{b1}币)")
            renew_summary["status"] = "LOW_BALANCE"
            renew_summary["details"].append(f"Server {s_id}: 余额不足({b1}币)")

    # Decide exit code
    if session_died:
        return actual, daily, b1, renew_summary, EXIT_SESSION_EXPIRED
    if earned == 0 and not daily and b1 is not None and b1 == b0:
        return actual, daily, b1, renew_summary, EXIT_EARN_FAIL
    return actual, daily, b1, renew_summary, EXIT_OK


def main():
    global SESSION
    if not SESSION:
        er("SLIME_SESSION未设置!")
        if DISCORD_TOKEN:
            log("尝试 Discord OAuth 自动登录获取新 session...")
            SESSION = discord_get_session()
            if SESSION:
                update_github_secret("SLIME_SESSION", SESSION)
                log(f"✅ 已通过 Discord 自动登录获得 session")
            else:
                sys.exit(EXIT_NO_SESSION)
        else:
            sys.exit(EXIT_NO_SESSION)

    # 尝试读取面板真实用户名
    _, detected_username = bal(SESSION)
    label = detected_username if (detected_username and ACCOUNT_LABEL == "btpphlmb") else ACCOUNT_LABEL

    c, d, b1, renew_summary, exit_code = process(SESSION, label)

    # 若 session 过期，尝试 Discord 自动登录重试一次
    if exit_code in (EXIT_SESSION_EXPIRED, EXIT_BALANCE_FAIL) and DISCORD_TOKEN:
        er("Session 已过期，尝试 Discord OAuth 自动重新登录...")
        new_session = discord_get_session()
        if new_session:
            ok("Discord 自动登录成功，更新 secret 并重试")
            update_github_secret("SLIME_SESSION", new_session)
            SESSION = new_session
            _, detected_username = bal(SESSION)
            label = detected_username if (detected_username and ACCOUNT_LABEL == "btpphlmb") else ACCOUNT_LABEL
            c, d, b1, renew_summary, exit_code = process(SESSION, label)
    
    # 获取最新的到期时间
    hours_left = renew_summary.get("hours_left")
    
    # Build detailed TG message
    lines = ["<b>🟢 SlimeNodes 刷币通知</b>"]
    lines.append(f"━━━━━━━━━━━━━━━━")
    lines.append(f"👤 账号: {label}")
    lines.append(f"━━━━━━━━━━━━━━━━")
    
    s = "✅" if c > 0 else "❌"
    dl = " (已达每日上限)" if d else ""
    if exit_code == EXIT_SESSION_EXPIRED:
        lines.append(f"💰 刷币: ❌ SID已过期 (请更新 SLIME_SESSION secret)")
    else:
        lines.append(f"💰 刷币: {s} +{c}币{dl}")
    
    if b1 is not None:
        lines.append(f"🏦 余额: {b1}币")
    
    if hours_left is not None:
        days = hours_left / 24
        lines.append(f"⏰ 到期: {hours_left:.0f}小时后 ({days:.1f}天)")
    
    r_status = renew_summary.get("status")
    r_msg = renew_summary.get("msg", "")
    if r_status == "SUCCESS":
        lines.append(f"🔄 续期: ✅ {r_msg or '已续期'}")
    elif r_status == "FAIL":
        lines.append(f"🔄 续期: ❌ 续期失败 ({r_msg or '请检查ServerID'})")
    elif r_status == "LOW_BALANCE":
        lines.append(f"🔄 续期: ⚠️ 余额不足 (需{RENEW_THRESHOLD}币)")
    elif hours_left is not None and hours_left > RENEW_HOURS:
        lines.append(f"🔄 续期: ⏭️ 暂不需要 (>{RENEW_HOURS}h)")
    elif renew_summary.get("details"):
        lines.append(f"🔄 续期: {'; '.join(renew_summary['details'])}")
    
    lines.append(f"━━━━━━━━━━━━━━━━")
    send_tg("\n".join(lines))
    log("完成!")
    
    # Exit with proper code so GitHub Actions shows real status
    if exit_code != EXIT_OK:
        er(f"退出码: {exit_code}")
    sys.exit(exit_code)

if __name__ == "__main__":
    main()
