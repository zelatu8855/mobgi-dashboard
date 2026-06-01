#!/usr/bin/env python3
"""
Mobgi API Client - 创量广告投放平台

认证: loginInfo → loginModule → login (Set-Cookie: userId)
请求头: client-user / main-user-id / ff-request-id

用法:
  python3 mobgi_api.py api <path> [GET|POST] [json_body]
  python3 mobgi_api.py report [--date YYYY-MM-DD] [--page N] [--page-size N] [--sort cost|show_count|click] [--dim material|project|...]
"""
import asyncio, json, os, sys, time, hashlib, uuid

EMAIL = os.environ.get('MOBGI_EMAIL', '775355788@qq.com')
PASSWORD = os.environ.get('MOBGI_PASSWORD', 'Zh@Dj20260409')
MOBGI_HOST = 'https://cl.mobgi.com'
API_HOST = 'https://cli1.mobgi.com'
LOGIN_URL = f'{MOBGI_HOST}/login?cl_t={int(time.time())}'
DEVICE_FINGERPRINT = '3b4614f5f95dc49f97676ead8043f50c'

def md5(s): return hashlib.md5(s.encode()).hexdigest()
def _rid(): return f'{time.strftime("%Y%m%d%H%M%S")}{uuid.uuid4().hex}'[:44]
def _safe_json(text):
    try: return json.loads(text)
    except: return None

from typing import Any
_state: dict[str, Any] = dict(pw=None, browser=None, context=None, page=None, user=None, login_time=0)
LOGIN_TTL = 86400  # 24 小时

async def _get_page():
    page = _state.get('page')
    if page and not page.is_closed(): return page
    from playwright.async_api import async_playwright
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
    ctx = await browser.new_context(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36', locale='zh-CN')
    _state.update(pw=pw, browser=browser, context=ctx)
    _state['page'] = page = await ctx.new_page()
    return page

async def _close():
    for k in ('page', 'context', 'browser', 'pw'):
        o = _state.get(k)
        if o:
            try: await (o.stop() if k == 'pw' else o.close())
            except: pass
        _state[k] = None

async def _xhr(method, path, data=None, headers=None):
    page = await _get_page()
    cfg = {'method': method, 'url': f'{API_HOST}{path}', 'body': data,
           'headers': {'Content-Type': 'application/json;charset=UTF-8', **(headers or {})}}
    await page.evaluate(f'window.__cfg = {json.dumps(cfg)}')
    raw = await page.evaluate("""
    (() => new Promise(r => {
        const c = window.__cfg, x = new XMLHttpRequest();
        x.open(c.method, c.url, true); x.withCredentials = true;
        for (const [k,v] of Object.entries(c.headers)) if (v != null) x.setRequestHeader(k, v);
        x.onload = () => r(x.responseText);
        x.onerror = () => r('{"_err":true}'); x.timeout = 30000; x.ontimeout = () => r('{"_timeout":true}');
        if (c.body != null) x.send(JSON.stringify(c.body)); else x.send();
    }))()""")
    result = _safe_json(raw)
    return result if result else {'code': -1, 'raw': raw[:200] if raw else ''}

async def login():
    """登录，返回 user dict"""
    page = await _get_page()
    pw_md5 = md5(PASSWORD)
    if 'cl.mobgi.com' not in page.url:
        await page.goto(LOGIN_URL, wait_until='domcontentloaded', timeout=20000)
        await page.wait_for_timeout(2000)
    # Step 1: loginInfo
    r1 = await _xhr('POST', '/User/AdminUser/loginInfo',
        {'email': EMAIL, 'password': pw_md5, 'device_fingerprint': DEVICE_FINGERPRINT, 'login_origin': 'web'})
    if r1.get('code') != 0: raise Exception(f'loginInfo 失败: {r1.get("message", "")}')
    u = r1['data']
    # Step 2: loginModule
    pv = u.get('product_version_data', [{}])[0].get('product_version', 0)
    r2 = await _xhr('POST', '/User/AdminUser/loginModule',
        {'email': EMAIL, 'password': pw_md5, 'product_version': pv, 'login_module': 'material',
         'device_fingerprint': DEVICE_FINGERPRINT, 'device_detail': None})
    if r2.get('code') != 0: raise Exception(f'loginModule 失败: {r2.get("message", "")}')
    # Step 3: login (Set-Cookie: userId)
    await _xhr('POST', '/User/AdminUser/login',
        {'email': EMAIL, 'password': pw_md5, 'product_version': pv, 'login_module': 'material',
         'device_fingerprint': DEVICE_FINGERPRINT, 'device_detail': None, 'login_origin': 'web'})
    # 刷新页面让 Cookie 在 Playwright context 中生效
    await page.reload(wait_until='domcontentloaded', timeout=15000)
    await page.wait_for_timeout(1000)
    user = {'user_id': str(u['user_id']), 'main_user_id': str(u['main_user_id']),
            'user_name': u.get('user_name', ''), 'product_version': pv}
    _state.update(user=user, login_time=time.time())
    return user

async def _ensure_login():
    u = _state.get('user')
    if u and (time.time() - _state.get('login_time', 0)) < LOGIN_TTL:
        # 超过半天后用轻量 API 验证 token 是否仍有效（防止服务端提前回收）
        if (time.time() - _state.get('login_time', 0)) > LOGIN_TTL // 2:
            try:
                check = await _xhr('GET', '/User/AdminUser/getMyOptimizeUsers',
                    headers={'client-user': u['user_id'], 'main-user-id': u['main_user_id'],
                             'Content-Type': 'application/json;charset=UTF-8', 'ff-request-id': _rid()})
                if check and check.get('code') == 0:
                    return u
            except Exception:
                pass
        else:
            return u
    return await login()

async def api_get(path, params=None):
    if params: path += '?' + '&'.join(f'{k}={v}' for k, v in params.items())
    return await _api_call('GET', path)

async def api_post(path, data=None):
    return await _api_call('POST', path, data or {})

async def _api_call(method, path, data=None):
    user = await _ensure_login()
    headers = {'client-user': user['user_id'], 'main-user-id': user['main_user_id'],
               'Content-Type': 'application/json;charset=UTF-8', 'ff-request-id': _rid()}
    r = await _xhr(method, path, data, headers)
    # 失效重试
    if r.get('code') == -10002:
        _state.update(user=None, login_time=0)
        user = await login()
        headers.update({'client-user': user['user_id'], 'main-user-id': user['main_user_id'], 'ff-request-id': _rid()})
        r = await _xhr(method, path, data, headers)
    return r

async def get_material_report(time_dim="days", data_dim="material", start_date=None, end_date=None,
                              sort_field="cost", sort_direction="desc", page=1, page_size=20):
    """查询素材消耗排行榜"""
    today = time.strftime("%Y-%m-%d")
    if not start_date: start_date = today
    if not end_date: end_date = today
    kpis = ["cost", "conversion_num", "show_count", "click", "cpm", "cpc", "ctr",
            "convert_rate", "convert_cost", "active", "active_cost", "active_rate",
            "download_start", "download_finish", "install_finish"]
    payload = {
        "time_dim": time_dim,
        "media_type": "aggregate",
        "data_type": "list",
        "data_dim": data_dim,
        "conditions": {
            "search_type": "name"
        },
        "sort_field": sort_field,
        "sort_direction": sort_direction,
        "kpis": kpis,
        "relate_dims": [],
        "start_date": start_date,
        "end_date": end_date,
        "db_type": "doris",
        "page": page,
        "page_size": page_size
    }
    return await _api_call('POST', '/ReportV23/MaterialReport/getReport', payload)

async def main():
    if len(sys.argv) < 2:
        print((__doc__ or '').strip())
        print()
        print("示例:")
        print("  python3 mobgi_api.py api /User/AdminUser/getMyOptimizeUsers")
        print('  python3 mobgi_api.py api /Material/Manage/lists POST \'{"page":1,"page_size":20}\'')
        print("  python3 mobgi_api.py report")
        print("  python3 mobgi_api.py report --date 2026-06-01 --page-size 10 --sort cost")
        return
    cmd = sys.argv[1]
    try:
        if cmd == 'report':
            import argparse
            p = argparse.ArgumentParser()
            p.add_argument('--date', default=None)
            p.add_argument('--page', type=int, default=1)
            p.add_argument('--page-size', type=int, default=20)
            p.add_argument('--sort', default='cost')
            p.add_argument('--dim', default='material')
            args = p.parse_args(sys.argv[2:])
            r = await get_material_report(
                start_date=args.date, end_date=args.date,
                sort_field=args.sort, page=args.page, page_size=args.page_size,
                data_dim=args.dim)
            print(json.dumps(r, indent=2, ensure_ascii=False)[:5000])
        else:
            path = sys.argv[2] if len(sys.argv) > 2 else '/User/AdminUser/getMyOptimizeUsers'
            method = sys.argv[3].upper() if len(sys.argv) > 3 else 'GET'
            data = json.loads(sys.argv[4]) if len(sys.argv) > 4 else None
            r = await (api_post(path, data) if method == 'POST' else api_get(path))
            print(json.dumps(r, indent=2, ensure_ascii=False)[:3000])
    except Exception as e:
        print(f'❌ {e}')
    finally:
        await _close()

if __name__ == '__main__':
    asyncio.run(main())
