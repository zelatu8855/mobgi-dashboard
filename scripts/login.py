#!/usr/bin/env python3.13
"""
Mobgi 登录脚本 - 手动运行一次，保存 session 到 data/.session.json
用法: python3.13 login.py
"""
import asyncio, json, os, sys, time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
SESSION_PATH = os.path.join(PROJECT_DIR, "data", ".session.json")
MOBGI_API_DIR = "/root/.hermes/skills/mobgi-api-client/scripts"

sys.path.insert(0, MOBGI_API_DIR)
os.environ.setdefault("PYTHONUNBUFFERED", "1")


async def main():
    import mobgi_api

    print("🔑 登录 Mobgi...", flush=True)
    page = await mobgi_api._get_page()

    # 导航（用 commit 避免 domcontentloaded 超时）
    await page.goto(
        f"https://cl.mobgi.com/login?cl_t={int(time.time())}",
        wait_until="commit", timeout=30000
    )
    await page.wait_for_timeout(2000)

    pw_md5 = mobgi_api.md5(mobgi_api.PASSWORD)

    # Step 1: loginInfo
    print("  loginInfo...", flush=True)
    r1 = await mobgi_api._xhr('POST', '/User/AdminUser/loginInfo',
        {'email': mobgi_api.EMAIL, 'password': pw_md5,
         'device_fingerprint': mobgi_api.DEVICE_FINGERPRINT, 'login_origin': 'web'})
    if r1.get('code') != 0:
        print(f"  ❌ 失败: {r1.get('message', '')}", flush=True)
        await mobgi_api._close()
        return
    u = r1['data']
    pv = u.get('product_version_data', [{}])[0].get('product_version', 0)
    print(f"  user_id={u['user_id']} main_user_id={u['main_user_id']}", flush=True)

    # Step 2: loginModule
    print("  loginModule...", flush=True)
    await mobgi_api._xhr('POST', '/User/AdminUser/loginModule',
        {'email': mobgi_api.EMAIL, 'password': pw_md5, 'product_version': pv,
         'login_module': 'material', 'device_fingerprint': mobgi_api.DEVICE_FINGERPRINT,
         'device_detail': None})

    # Step 3: login
    print("  login...", flush=True)
    await mobgi_api._xhr('POST', '/User/AdminUser/login',
        {'email': mobgi_api.EMAIL, 'password': pw_md5, 'product_version': pv,
         'login_module': 'material', 'device_fingerprint': mobgi_api.DEVICE_FINGERPRINT,
         'device_detail': None, 'login_origin': 'web'})

    # 刷新让 Cookie 生效
    await page.reload(wait_until='commit', timeout=30000)
    await page.wait_for_timeout(1000)

    # 验证
    chk = await mobgi_api._xhr('GET', '/User/AdminUser/getMyOptimizeUsers',
        headers={'client-user': str(u['user_id']), 'main-user-id': str(u['main_user_id']),
                 'Content-Type': 'application/json;charset=UTF-8', 'ff-request-id': mobgi_api._rid()})
    if chk.get('code') == 0:
        print("  ✅ 登录验证通过", flush=True)
    else:
        print(f"  ⚠️ 验证返回 code={chk.get('code')}", flush=True)

    # 提取 Cookie
    ctx = mobgi_api._state.get('context')
    cookies = await ctx.cookies() if ctx else []

    os.makedirs(os.path.dirname(SESSION_PATH), exist_ok=True)
    session = {
        "user_id": str(u['user_id']),
        "main_user_id": str(u['main_user_id']),
        "user_name": u.get('user_name', ''),
        "product_version": pv,
        "cookies": cookies,
        "login_time": time.time()
    }
    with open(SESSION_PATH, "w") as f:
        json.dump(session, f, indent=2)

    await mobgi_api._close()
    print(f"✅ Session 已保存到 {SESSION_PATH}", flush=True)
    print(f"   Cookie 数量: {len(cookies)}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
