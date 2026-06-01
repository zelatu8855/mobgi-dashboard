#!/usr/bin/env python3.13
"""
Mobgi 数据采集脚本 v2
登录一次提取 Cookie，后续用 aiohttp 并发拉取，速度提升 10x+

用法:
  python3.13 collect.py                    # 增量：拉取昨日
  python3.13 collect.py --full             # 全量：拉取近 30 天
  python3.13 collect.py --date 2026-05-31  # 指定日期
"""
import asyncio, json, os, sys, sqlite3, time, uuid, hashlib
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

try:
    import aiohttp
except ImportError:
    aiohttp = None  # type: ignore

# ─── 配置 ────────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DB_PATH = os.path.join(PROJECT_DIR, "data", "mobgi.db")
SESSION_PATH = os.path.join(PROJECT_DIR, "data", ".session.json")
MOBGI_API_DIR = "/root/.hermes/skills/mobgi-api-client/scripts"

API_HOST = "https://cli1.mobgi.com"
MOBGI_HOST = "https://cl.mobgi.com"
EMAIL = os.environ.get("MOBGI_EMAIL", "775355788@qq.com")
PASSWORD = os.environ.get("MOBGI_PASSWORD", "Zh@Dj20260409")
DEVICE_FP = "3b4614f5f95dc49f97676ead8043f50c"
LOGIN_URL = f"{MOBGI_HOST}/login?cl_t={int(time.time())}"
CONCURRENT_PAGES = 10   # 并发拉取页数

sys.path.insert(0, MOBGI_API_DIR)
os.environ.setdefault("PYTHONUNBUFFERED", "1")


# ─── SQLite ──────────────────────────────────────────────────────

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS daily_summary (
            date TEXT PRIMARY KEY,
            total_cost REAL DEFAULT 0,
            total_conversion INTEGER DEFAULT 0,
            total_show INTEGER DEFAULT 0,
            total_click INTEGER DEFAULT 0,
            cpm REAL DEFAULT 0, cpc REAL DEFAULT 0, ctr REAL DEFAULT 0,
            convert_rate REAL DEFAULT 0, convert_cost REAL DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS material_cost (
            material_id TEXT, date TEXT, material_name TEXT,
            cost REAL DEFAULT 0, conversion_num INTEGER DEFAULT 0,
            show_count INTEGER DEFAULT 0, click INTEGER DEFAULT 0,
            cpm REAL DEFAULT 0, cpc REAL DEFAULT 0, ctr REAL DEFAULT 0,
            convert_rate REAL DEFAULT 0, convert_cost REAL DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (material_id, date)
        );
        CREATE TABLE IF NOT EXISTS balance_snapshot (
            date TEXT PRIMARY KEY, balance REAL,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_mc_date ON material_cost(date);
    """)
    conn.commit()
    return conn


def save_daily(conn, dt: str, s: dict):
    conn.execute("""
        INSERT INTO daily_summary (date,total_cost,total_conversion,total_show,total_click,cpm,cpc,ctr,convert_rate,convert_cost)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(date) DO UPDATE SET
            total_cost=excluded.total_cost, total_conversion=excluded.total_conversion,
            total_show=excluded.total_show, total_click=excluded.total_click,
            cpm=excluded.cpm, cpc=excluded.cpc, ctr=excluded.ctr,
            convert_rate=excluded.convert_rate, convert_cost=excluded.convert_cost,
            updated_at=datetime('now')
    """, (dt, s["cost"], s["conversion_num"], s["show_count"], s["click"],
          s.get("cpm", 0), s.get("cpc", 0), s.get("ctr", 0),
          s.get("convert_rate", 0), s.get("convert_cost", 0)))


def save_materials(conn, dt: str, items: list):
    for item in items:
        mid = str(item.get("material_id", ""))
        if not mid:
            continue
        conn.execute("""
            INSERT INTO material_cost (material_id,date,material_name,cost,conversion_num,show_count,click,cpm,cpc,ctr,convert_rate,convert_cost)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(material_id,date) DO UPDATE SET
                material_name=excluded.material_name, cost=excluded.cost,
                conversion_num=excluded.conversion_num, show_count=excluded.show_count,
                click=excluded.click, cpm=excluded.cpm, cpc=excluded.cpc,
                ctr=excluded.ctr, convert_rate=excluded.convert_rate,
                convert_cost=excluded.convert_cost, updated_at=datetime('now')
        """, (mid, dt, item.get("material_name", ""), item.get("cost", 0),
              item.get("conversion_num", 0), item.get("show_count", 0),
              item.get("click", 0), item.get("cpm", 0), item.get("cpc", 0),
              item.get("ctr", 0), item.get("convert_rate", 0), item.get("convert_cost", 0)))


def save_balance(conn, dt: str, balance: float):
    conn.execute("""
        INSERT INTO balance_snapshot (date,balance) VALUES (?,?)
        ON CONFLICT(date) DO UPDATE SET balance=excluded.balance, updated_at=datetime('now')
    """, (dt, balance))


# ─── 登录 & Session 管理 ─────────────────────────────────────────

def _rid() -> str:
    return f"{time.strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex}"[:44]


def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


async def login_and_save_session():
    """Playwright 登录一次，把 Cookie + user 信息保存到文件"""
    from mobgi_api import _get_page, _close, _xhr, _state, login

    print("🔑 登录 Mobgi...", flush=True)
    page = await _get_page()

    # 导航用 commit 避免超时
    await page.goto(f"https://cl.mobgi.com/login?cl_t={int(time.time())}",
                    wait_until="commit", timeout=15000)
    await page.wait_for_timeout(1000)

    # 复用 mobgi_api 的登录逻辑，但用 commit
    from mobgi_api import EMAIL, PASSWORD, DEVICE_FINGERPRINT, md5, _rid
    pw_md5 = md5(PASSWORD)

    r1 = await _xhr('POST', '/User/AdminUser/loginInfo',
        {'email': EMAIL, 'password': pw_md5, 'device_fingerprint': DEVICE_FP, 'login_origin': 'web'})
    if r1.get('code') != 0:
        raise RuntimeError(f"loginInfo 失败: {r1.get('message', '')}")
    u = r1['data']
    pv = u.get('product_version_data', [{}])[0].get('product_version', 0)

    await _xhr('POST', '/User/AdminUser/loginModule',
        {'email': EMAIL, 'password': pw_md5, 'product_version': pv, 'login_module': 'material',
         'device_fingerprint': DEVICE_FP, 'device_detail': None})
    await _xhr('POST', '/User/AdminUser/login',
        {'email': EMAIL, 'password': pw_md5, 'product_version': pv, 'login_module': 'material',
         'device_fingerprint': DEVICE_FP, 'device_detail': None, 'login_origin': 'web'})

    await page.reload(wait_until='commit', timeout=15000)
    await page.wait_for_timeout(1000)

    # 提取 Cookie
    ctx = _state.get('context')
    cookies = await ctx.cookies() if ctx else []

    session = {
        "user_id": str(u['user_id']),
        "main_user_id": str(u['main_user_id']),
        "user_name": u.get('user_name', ''),
        "product_version": pv,
        "cookies": cookies,
        "login_time": time.time()
    }
    os.makedirs(os.path.dirname(SESSION_PATH), exist_ok=True)
    with open(SESSION_PATH, "w") as f:
        json.dump(session, f, indent=2)

    await _close()
    print(f"  ✅ 登录成功 (user_id={u['user_id']})，session 已保存", flush=True)
    return session


def load_session() -> Optional[dict]:
    if not os.path.exists(SESSION_PATH):
        return None
    try:
        with open(SESSION_PATH) as f:
            session = json.load(f)
        # 检查是否过期 (24h)
        if time.time() - session.get("login_time", 0) > 86400:
            return None
        return session
    except:
        return None


async def ensure_session() -> dict:
    """获取有效 session，过期则重新登录"""
    session = load_session()
    if session:
        return session
    return await login_and_save_session()


# ─── aiohttp 并发拉取 ────────────────────────────────────────────

KPIS = ["cost","conversion_num","show_count","click","cpm","cpc","ctr",
        "convert_rate","convert_cost","active","active_cost","active_rate",
        "download_start","download_finish","install_finish"]

CONCURRENT_PAGES = 3   # 并发拉取页数（避免 API 限流）
PAGE_TIMEOUT = 60      # 单页超时秒

BASE_PAYLOAD = {
    "time_dim": "days", "media_type": "aggregate",
    "data_type": "list", "data_dim": "material",
    "conditions": {"search_type": "name"},
    "sort_field": "cost", "sort_direction": "desc",
    "kpis": KPIS, "relate_dims": [], "db_type": "doris",
}


async def _fetch_page_aiohttp(session_data: dict, aio_session, target_date: str, page: int) -> list:
    """用 aiohttp 拉单页数据，带超时"""
    payload = {**BASE_PAYLOAD,
               "start_date": target_date, "end_date": target_date,
               "page": page, "page_size": 100}

    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in session_data.get("cookies", []))
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "client-user": session_data["user_id"],
        "main-user-id": session_data["main_user_id"],
        "ff-request-id": _rid(),
        "Cookie": cookie_str,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    url = f"{API_HOST}/ReportV23/MaterialReport/getReport"
    try:
        async with aio_session.post(url, json=payload, headers=headers,
                                     timeout=aiohttp.ClientTimeout(total=PAGE_TIMEOUT)) as resp:
            data = await resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"API code={data.get('code')} msg={data.get('message','')}")
            return data.get("data", {}).get("list", [])
    except asyncio.TimeoutError:
        raise RuntimeError(f"第 {page} 页超时 ({PAGE_TIMEOUT}s)")


async def _fetch_page_with_retry(session_data: dict, aio_session, target_date: str, page: int, max_retries: int = 3) -> list:
    """带重试的单页拉取"""
    for attempt in range(max_retries):
        try:
            return await _fetch_page_aiohttp(session_data, aio_session, target_date, page)
        except Exception as e:
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"    ↺ 第 {page} 页重试 ({attempt+1}/{max_retries}) 等待 {wait}s: {e}", flush=True)
                await asyncio.sleep(wait)
            else:
                raise


async def fetch_date_concurrent(target_date: str, session_data: dict) -> dict:
    """并发拉取指定日期的全部 + 返回汇总"""

    connector = aiohttp.TCPConnector(limit=CONCURRENT_PAGES)
    async with aiohttp.ClientSession(connector=connector) as aio_session:
        # 先拉第 1 页获取 total_count
        print(f"  📡 拉取 {target_date}...", flush=True)
        first_items = await _fetch_page_with_retry(session_data, aio_session, target_date, 1)

        # 获取 total_count
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in session_data.get("cookies", []))
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "client-user": session_data["user_id"],
            "main-user-id": session_data["main_user_id"],
            "ff-request-id": _rid(),
            "Cookie": cookie_str,
        }
        first_payload = {**BASE_PAYLOAD,
                         "start_date": target_date, "end_date": target_date,
                         "page": 1, "page_size": 100}
        url = f"{API_HOST}/ReportV23/MaterialReport/getReport"
        async with aio_session.post(url, json=first_payload, headers=headers,
                                     timeout=aiohttp.ClientTimeout(total=PAGE_TIMEOUT)) as resp:
            first_data = (await resp.json()).get("data", {})
            total_count = first_data.get("page_info", {}).get("total_count", 0)

        total_pages = (total_count + 99) // 100
        print(f"    共 {total_count} 条, {total_pages} 页, 并发 {CONCURRENT_PAGES} 路", flush=True)

        all_items = list(first_items)

        # 分批并发拉剩余页
        if total_pages > 1:
            remaining = list(range(2, total_pages + 1))
            for batch_start in range(0, len(remaining), CONCURRENT_PAGES):
                batch = remaining[batch_start:batch_start + CONCURRENT_PAGES]
                tasks = [_fetch_page_with_retry(session_data, aio_session, target_date, p) for p in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for r in results:
                    if isinstance(r, BaseException):
                        print(f"    ⚠️ 分页错误: {r}", flush=True)
                    else:
                        all_items.extend(r)
                print(f"    进度: {len(all_items)}/{total_count}", flush=True)

        print(f"    拉取完成: {len(all_items)} 条", flush=True)

    # 汇总
    total_cost = sum(float(i.get("cost", 0) or 0) for i in all_items)
    total_conversion = sum(int(i.get("conversion_num", 0) or 0) for i in all_items)
    total_show = sum(int(i.get("show_count", 0) or 0) for i in all_items)
    total_click = sum(int(i.get("click", 0) or 0) for i in all_items)

    cpm = round(total_cost / total_show * 1000, 4) if total_show else 0
    cpc = round(total_cost / total_click, 4) if total_click else 0
    ctr = round(total_click / total_show * 100, 4) if total_show else 0
    convert_rate = round(total_conversion / total_click * 100, 4) if total_click else 0
    convert_cost = round(total_cost / total_conversion, 4) if total_conversion else 0

    return {
        "summary": {
            "cost": round(total_cost, 2), "conversion_num": total_conversion,
            "show_count": total_show, "click": total_click,
            "cpm": cpm, "cpc": cpc, "ctr": ctr,
            "convert_rate": convert_rate, "convert_cost": convert_cost,
        },
        "items": all_items
    }


# ─── 主流程 ──────────────────────────────────────────────────────

async def main():
    import argparse
    p = argparse.ArgumentParser(description="Mobgi 数据采集 v2")
    p.add_argument("--full", action="store_true", help="全量：近 30 天")
    p.add_argument("--date", default=None, help="指定日期 YYYY-MM-DD")
    p.add_argument("--re-login", action="store_true", help="强制重新登录")
    args = p.parse_args()

    conn = init_db()

    # 获取 session
    if args.re_login:
        session_data = await login_and_save_session()
    else:
        session_data = await ensure_session()

    # 确定日期列表
    if args.date:
        dates = [args.date]
    elif args.full:
        today = date.today()
        dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(30, 0, -1)]
    else:
        dates = [(date.today() - timedelta(days=1)).strftime("%Y-%m-%d")]

    mode = f"全量 {len(dates)} 天" if len(dates) > 1 else f"增量 {dates[0]}"
    print(f"🚀 采集模式: {mode}", flush=True)
    print(f"📁 数据库: {DB_PATH}", flush=True)

    start = time.time()
    for dt in dates:
        print(f"\n📅 {dt}", flush=True)
        try:
            result = await fetch_date_concurrent(dt, session_data)
            save_daily(conn, dt, result["summary"])
            save_materials(conn, dt, result["items"])
            conn.commit()
            s = result["summary"]
            print(f"  ✅ {len(result['items'])} 条 | 花费 ¥{s['cost']:,.2f} | 转化 {s['conversion_num']} | CTR {s['ctr']}%", flush=True)
        except Exception as e:
            print(f"  ❌ 采集失败: {e}", flush=True)
            # 如果是认证错误，尝试重新登录一次
            if "cookie" in str(e).lower() or "code" in str(e).lower():
                print("  🔄 尝试重新登录...", flush=True)
                session_data = await login_and_save_session()

    elapsed = time.time() - start
    days_in_db = conn.execute("SELECT COUNT(DISTINCT date) FROM daily_summary").fetchone()[0]
    print(f"\n⏱ 完成，耗时 {elapsed:.1f}s | 数据库共 {days_in_db} 天数据", flush=True)
    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
