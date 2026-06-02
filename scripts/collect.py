#!/usr/bin/env python3.13
"""
Mobgi BI 数据采集脚本 v3
支持多维度：全量 / 按广告主 / 按产品 / 按平台
"""
import asyncio, json, os, re, sqlite3, sys, time, uuid
from collections import defaultdict
from datetime import date, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DB_PATH = os.path.join(PROJECT_DIR, "data", "mobgi.db")
SESSION_PATH = os.path.join(PROJECT_DIR, "data", ".session.json")
MOBGI_API_DIR = "/root/.hermes/skills/mobgi-api-client/scripts"

sys.path.insert(0, MOBGI_API_DIR)
os.environ.setdefault("PYTHONUNBUFFERED", "1")

CONCURRENT = 3
PAGE_TIMEOUT = 60

import aiohttp

# ─── 产品 & 平台识别规则 ────────────────────────────────────────

# 素材名称 → 产品标签
PRODUCT_RULES = [
    (r"^红果短剧", "红果短剧"),
    (r"^红果",     "红果"),
    (r"^番茄小说", "番茄小说"),
    (r"^番茄短剧", "番茄短剧"),
    (r"^番茄",     "番茄"),
    (r"^狸鸣红果", "狸鸣红果"),
    (r"^狸鸣",     "狸鸣"),
]

# advertiser_id → 平台（通过素材名称中的平台关键词推断）
PLATFORM_RULES = [
    (r"巨量|头条|抖音|douyin|toutiao", "巨量引擎"),
    (r"腾讯|广点通|qq|微信",           "广点通"),
    (r"百度|baidu",                     "百度信息流"),
]


def classify_product(name: str) -> str:
    for pattern, label in PRODUCT_RULES:
        if re.search(pattern, name, re.IGNORECASE):
            return label
    return "其他"


def classify_platform(name: str) -> str:
    for pattern, label in PLATFORM_RULES:
        if re.search(pattern, name, re.IGNORECASE):
            return label
    return "其他"


# ─── SQLite ──────────────────────────────────────────────────────

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        -- 每日总汇总
        CREATE TABLE IF NOT EXISTS daily_summary (
            date TEXT PRIMARY KEY,
            total_cost REAL DEFAULT 0, total_conversion INTEGER DEFAULT 0,
            total_show INTEGER DEFAULT 0, total_click INTEGER DEFAULT 0,
            cpm REAL DEFAULT 0, cpc REAL DEFAULT 0, ctr REAL DEFAULT 0,
            convert_rate REAL DEFAULT 0, convert_cost REAL DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        -- 素材级明细（核心表）
        CREATE TABLE IF NOT EXISTS material_cost (
            material_id TEXT, date TEXT,
            material_name TEXT, advertiser_id TEXT DEFAULT '',
            product TEXT DEFAULT '', platform TEXT DEFAULT '',
            cost REAL DEFAULT 0, conversion_num INTEGER DEFAULT 0,
            show_count INTEGER DEFAULT 0, click INTEGER DEFAULT 0,
            cpm REAL DEFAULT 0, cpc REAL DEFAULT 0, ctr REAL DEFAULT 0,
            convert_rate REAL DEFAULT 0, convert_cost REAL DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (material_id, date)
        );
 CREATE INDEX IF NOT EXISTS idx_mc_date ON material_cost(date);
        CREATE INDEX IF NOT EXISTS idx_mc_adv ON material_cost(advertiser_id);
        CREATE INDEX IF NOT EXISTS idx_mc_prod ON material_cost(product);
        CREATE INDEX IF NOT EXISTS idx_mc_platform ON material_cost(platform);

        -- 按广告主每日汇总
        CREATE TABLE IF NOT EXISTS daily_by_advertiser (
            date TEXT, advertiser_id TEXT,
            total_cost REAL DEFAULT 0, total_conversion INTEGER DEFAULT 0,
            total_show INTEGER DEFAULT 0, total_click INTEGER DEFAULT 0,
            material_count INTEGER DEFAULT 0,
            cpm REAL DEFAULT 0, cpc REAL DEFAULT 0, ctr REAL DEFAULT 0,
            convert_rate REAL DEFAULT 0, convert_cost REAL DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (date, advertiser_id)
        );

        -- 按产品每日汇总
        CREATE TABLE IF NOT EXISTS daily_by_product (
            date TEXT, product TEXT,
            total_cost REAL DEFAULT 0, total_conversion INTEGER DEFAULT 0,
            total_show INTEGER DEFAULT 0, total_click INTEGER DEFAULT 0,
            material_count INTEGER DEFAULT 0,
            cpm REAL DEFAULT 0, cpc REAL DEFAULT 0, ctr REAL DEFAULT 0,
            convert_rate REAL DEFAULT 0, convert_cost REAL DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (date, product)
        );

        -- 按平台每日汇总
        CREATE TABLE IF NOT EXISTS daily_by_platform (
            date TEXT, platform TEXT,
            total_cost REAL DEFAULT 0, total_conversion INTEGER DEFAULT 0,
            total_show INTEGER DEFAULT 0, total_click INTEGER DEFAULT 0,
            material_count INTEGER DEFAULT 0,
            cpm REAL DEFAULT 0, cpc REAL DEFAULT 0, ctr REAL DEFAULT 0,
            convert_rate REAL DEFAULT 0, convert_cost REAL DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (date, platform)
        );

        -- 广告主名称映射（手动维护）
        CREATE TABLE IF NOT EXISTS advertiser_names (
            advertiser_id TEXT PRIMARY KEY,
            name TEXT DEFAULT '',
            platform TEXT DEFAULT '',
            updated_at TEXT DEFAULT (datetime('now'))
        );

        -- 余额快照
        CREATE TABLE IF NOT EXISTS balance_snapshot (
            date TEXT PRIMARY KEY, balance REAL,
            updated_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    return conn


def upsert_daily(conn, dt, s):
    conn.execute("""
        INSERT INTO daily_summary (date,total_cost,total_conversion,total_show,total_click,cpm,cpc,ctr,convert_rate,convert_cost)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(date) DO UPDATE SET total_cost=excluded.total_cost, total_conversion=excluded.total_conversion,
            total_show=excluded.total_show, total_click=excluded.total_click,
            cpm=excluded.cpm, cpc=excluded.cpc, ctr=excluded.ctr,
            convert_rate=excluded.convert_rate, convert_cost=excluded.convert_cost,
            updated_at=datetime('now')
    """, (dt, s["cost"], s["conversion_num"], s["show_count"], s["click"],
          s.get("cpm",0), s.get("cpc",0), s.get("ctr",0), s.get("convert_rate",0), s.get("convert_cost",0)))


def upsert_materials(conn, dt, items):
    for item in items:
        mid = str(item.get("material_id", ""))
        if not mid:
            continue
        name = item.get("material_name", "")
        adv  = item.get("advertiser_id", "")
        prod = classify_product(name)
        plat = classify_platform(name)
        conn.execute("""
            INSERT INTO material_cost
                (material_id,date,material_name,advertiser_id,product,platform,
                 cost,conversion_num,show_count,click,cpm,cpc,ctr,convert_rate,convert_cost)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(material_id,date) DO UPDATE SET
                material_name=excluded.material_name, advertiser_id=excluded.advertiser_id,
                product=excluded.product, platform=excluded.platform,
                cost=excluded.cost, conversion_num=excluded.conversion_num,
                show_count=excluded.show_count, click=excluded.click,
                cpm=excluded.cpm, cpc=excluded.cpc, ctr=excluded.ctr,
                convert_rate=excluded.convert_rate, convert_cost=excluded.convert_cost,
                updated_at=datetime('now')
        """, (mid, dt, name, adv, prod, plat,
              item.get("cost",0), item.get("conversion_num",0),
              item.get("show_count",0), item.get("click",0),
              item.get("cpm",0), item.get("cpc",0),
              item.get("ctr",0), item.get("convert_rate",0), item.get("convert_cost",0)))


def upsert_daily_by_dim(conn, table, dt, key, items):
    """按维度聚合写入 daily_by_advertiser / daily_by_product / daily_by_platform"""
    groups = defaultdict(lambda: {"cost":0.0,"conversion":0,"show":0,"click":0,"count":0})
    for item in items:
        k = item.get(key, "")
        if not k:
            continue
        g = groups[k]
        g["cost"]      += float(item.get("cost", 0) or 0)
        g["conversion"]+= int(item.get("conversion_num", 0) or 0)
        g["show"]      += int(item.get("show_count", 0) or 0)
        g["click"]     += int(item.get("click", 0) or 0)
        g["count"]     += 1

    for k, g in groups.items():
        show = g["show"]
        click = g["click"]
        conv = g["conversion"]
        cost = g["cost"]
        cpm  = round(cost / show * 1000, 4) if show else 0
        cpc  = round(cost / click, 4)    if click else 0
        ctr  = round(click / show * 100, 4) if show else 0
        conv_rate = round(conv / click * 100, 4) if click else 0
        conv_cost = round(cost / conv, 4) if conv else 0

        conn.execute(f"""
            INSERT INTO {table} (date,{key},total_cost,total_conversion,total_show,total_click,
                                 material_count,cpm,cpc,ctr,convert_rate,convert_cost)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(date,{key}) DO UPDATE SET
                total_cost=excluded.total_cost, total_conversion=excluded.total_conversion,
                total_show=excluded.total_show, total_click=excluded.total_click,
                material_count=excluded.material_count,
                cpm=excluded.cpm, cpc=excluded.cpc, ctr=excluded.ctr,
                convert_rate=excluded.convert_rate, convert_cost=excluded.convert_cost,
                updated_at=datetime('now')
        """, (dt, k, round(cost,2), conv, show, click, g["count"],
              cpm, cpc, ctr, conv_rate, conv_cost))


def save_balance(conn, dt, balance):
    conn.execute("""
        INSERT INTO balance_snapshot (date,balance) VALUES (?,?)
        ON CONFLICT(date) DO UPDATE SET balance=excluded.balance, updated_at=datetime('now')
    """, (dt, balance))


# ─── aiohttp 并发拉取 ────────────────────────────────────────────

def _rid():
    return f"{time.strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex}"[:44]


def _md5(s):
    import hashlib
    return hashlib.md5(s.encode()).hexdigest()


async def _fetch_page(session_data, aio_session, target_date, page):
    payload = {
        "time_dim": "days", "media_type": "aggregate",
        "data_type": "list", "data_dim": "material",
        "conditions": {"search_type": "name"},
        "sort_field": "cost", "sort_direction": "desc",
        "kpis": ["cost","conversion_num","show_count","click",
                 "cpm","cpc","ctr","convert_rate","convert_cost"],
        "relate_dims": [], "db_type": "doris",
        "start_date": target_date, "end_date": target_date,
        "page": page, "page_size": 100
    }
    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in session_data.get("cookies", []))
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "client-user": session_data["user_id"],
        "main-user-id": session_data["main_user_id"],
        "ff-request-id": _rid(),
        "Cookie": cookie_str,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    url = "https://cli1.mobgi.com/ReportV23/MaterialReport/getReport"
    async with aio_session.post(url, json=payload, headers=headers,
                                 timeout=aiohttp.ClientTimeout(total=PAGE_TIMEOUT)) as resp:
        data = (await resp.json()).get("data", {})
        if data.get("code", 0) != 0:
            raise RuntimeError(f"API code={data.get('code')} msg={data.get('message','')}")
        return data.get("list", []), data.get("page_info", {}).get("total_count", 0)


async def _fetch_page_retry(session_data, aio_session, target_date, page):
    for attempt in range(3):
        try:
            return await _fetch_page(session_data, aio_session, target_date, page)
        except Exception as e:
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
            else:
                raise


async def fetch_date(session_data, target_date):
    connector = aiohttp.TCPConnector(limit=CONCURRENT)
    async with aiohttp.ClientSession(connector=connector) as s:
        items, total = await _fetch_page_retry(session_data, s, target_date, 1)
        total_pages = (total + 99) // 100
        print(f"  📡 {target_date}: {total} 条 / {total_pages} 页", flush=True)

        if total_pages > 1:
            remaining = list(range(2, total_pages + 1))
            for i in range(0, len(remaining), CONCURRENT):
                batch = remaining[i:i+CONCURRENT]
                results = await asyncio.gather(
                    *[_fetch_page_retry(session_data, s, target_date, p) for p in batch],
                    return_exceptions=True
                )
                for r in results:
                    if isinstance(r, Exception):
                        print(f"    ⚠️ {r}", flush=True)
                    else:
                        batch_items, _ = r
                        items.extend(batch_items)
                print(f"    进度: {len(items)}/{total}", flush=True)

        return items


# ─── 登录 ────────────────────────────────────────────────────────

async def login_and_save():
    import mobgi_api
    print("🔑 登录 Mobgi...", flush=True)
    page = await mobgi_api._get_page()
    await page.goto(f"https://cl.mobgi.com/login?cl_t={int(time.time())}",
                    wait_until="commit", timeout=15000)
    await page.wait_for_timeout(2000)

    pw_md5 = _md5(mobgi_api.PASSWORD)
    r1 = await mobgi_api._xhr('POST', '/User/AdminUser/loginInfo',
        {'email': mobgi_api.EMAIL, 'password': pw_md5,
         'device_fingerprint': mobgi_api.DEVICE_FINGERPRINT, 'login_origin': 'web'})
    if r1.get('code') != 0:
        raise RuntimeError(f"loginInfo 失败: {r1.get('message','')}")
    u = r1['data']
    pv = u.get('product_version_data', [{}])[0].get('product_version', 0)

    await mobgi_api._xhr('POST', '/User/AdminUser/loginModule',
        {'email': mobgi_api.EMAIL, 'password': pw_md5, 'product_version': pv,
         'login_module': 'material', 'device_fingerprint': mobgi_api.DEVICE_FINGERPRINT,
         'device_detail': None})
    await mobgi_api._xhr('POST', '/User/AdminUser/login',
        {'email': mobgi_api.EMAIL, 'password': pw_md5, 'product_version': pv,
         'login_module': 'material', 'device_fingerprint': mobgi_api.DEVICE_FINGERPRINT,
         'device_detail': None, 'login_origin': 'web'})
    await page.reload(wait_until='commit', timeout=15000)
    await page.wait_for_timeout(1000)

    ctx = mobgi_api._state.get('context')
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
    await mobgi_api._close()
    print(f"  ✅ user_id={u['user_id']}, cookies={len(cookies)}", flush=True)
    return session


def load_session():
    if not os.path.exists(SESSION_PATH):
        return None
    try:
        with open(SESSION_PATH) as f:
            s = json.load(f)
        if time.time() - s.get("login_time", 0) > 86400:
            return None
        return s
    except:
        return None


async def ensure_session():
    s = load_session()
    if s:
        return s
    return await login_and_save()


# ─── 主流程 ──────────────────────────────────────────────────────

async def collect_date(conn, dt, session_data):
    items = await fetch_date(session_data, dt)

    # 补充 product / platform 字段
    for item in items:
        item["product"]    = classify_product(item.get("material_name", ""))
        item["platform"]   = classify_platform(item.get("material_name", ""))

    # 写入素材明细
    upsert_materials(conn, dt, items)

    # 汇总
    cost = sum(float(i.get("cost",0) or 0) for i in items)
    conv = sum(int(i.get("conversion_num",0) or 0) for i in items)
    show = sum(int(i.get("show_count",0) or 0) for i in items)
    clk  = sum(int(i.get("click",0) or 0) for i in items)
    cpm  = round(cost / show * 1000, 4) if show else 0
    cpc  = round(cost / clk, 4)      if clk  else 0
    ctr  = round(clk / show * 100, 4) if show else 0
    conv_rate = round(conv / clk * 100, 4) if clk else 0
    conv_cost = round(cost / conv, 4)      if conv else 0

    s = {"cost": round(cost,2), "conversion_num": conv, "show_count": show, "click": clk,
         "cpm": cpm, "cpc": cpc, "ctr": ctr, "convert_rate": conv_rate, "convert_cost": conv_cost}
    upsert_daily(conn, dt, s)

    # 按维度聚合
    upsert_daily_by_dim(conn, "daily_by_advertiser", dt, "advertiser_id", items)
    upsert_daily_by_dim(conn, "daily_by_product",    dt, "product",       items)
    upsert_daily_by_dim(conn, "daily_by_platform",   dt, "platform",      items)

    conn.commit()
    print(f"  ✅ {len(items)} 条 | 花费 ¥{s['cost']:,.2f} | 转化 {conv} | CTR {ctr}%", flush=True)


async def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--full",  action="store_true", help="近30天")
    p.add_argument("--date",  default=None,        help="指定日期")
    p.add_argument("--re-login", action="store_true")
    args = p.parse_args()

    conn = init_db()
    session_data = await login_and_save() if args.re_login else await ensure_session()

    if args.date:
        dates = [args.date]
    elif args.full:
        today = date.today()
        dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(30, 0, -1)]
    else:
        dates = [(date.today() - timedelta(days=1)).strftime("%Y-%m-%d")]

    print(f"🚀 采集 {len(dates)} 天 | DB: {DB_PATH}", flush=True)
    t0 = time.time()
    for dt in dates:
        print(f"\n📅 {dt}", flush=True)
        try:
            await collect_date(conn, dt, session_data)
        except Exception as e:
            print(f"  ❌ {e}", flush=True)

    elapsed = time.time() - t0
    days = conn.execute("SELECT COUNT(*) FROM daily_summary WHERE total_cost>0").fetchone()[0]
    print(f"\n⏱ {elapsed:.1f}s | 共 {days} 天数据", flush=True)
    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
