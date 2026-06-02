#!/usr/bin/env python3.13
"""
Mobgi BI 看板生成器 v2
深色玻璃态 + 全模块 + 多维度筛选
"""
import sqlite3, json, os, re, sys
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DB_PATH = os.path.join(PROJECT_DIR, "data", "mobgi.db")
OUTPUT_PATH = os.path.join(PROJECT_DIR, "dashboard.html")

# ─── 产品规则 ────────────────────────────────────────────────────
PRODUCT_RULES = [
    (r"^红果短剧", "红果短剧"),
    (r"^红果",     "红果"),
    (r"^番茄小说", "番茄小说"),
    (r"^番茄短剧", "番茄短剧"),
    (r"^番茄",     "番茄"),
    (r"^狸鸣红果", "狸鸣红果"),
    (r"^狸鸣",     "狸鸣"),
]

def classify_product(name):
    for pattern, label in PRODUCT_RULES:
        if re.match(pattern, name):
            return label
    return "其他"

def query(conn, sql, params=()):
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.row_factory = None
    return [dict(r) for r in rows]

def pct(old, new):
    if not old: return (round(new,2), None, "flat")
    d = new - old
    p = round(d/abs(old)*100, 2) if old else None
    return (round(d,2), p, "up" if d>0 else ("down" if d<0 else "flat"))

def build():
    if not os.path.exists(DB_PATH):
        print(f"❌ 数据库不存在"); sys.exit(1)
    conn = sqlite3.connect(DB_PATH)

    # ── KPI：最新一天 ──────────────────────────────────────────
    latest = query(conn, "SELECT * FROM daily_summary WHERE total_cost>0 ORDER BY date DESC LIMIT 1")
    prev    = query(conn, "SELECT * FROM daily_summary WHERE total_cost>0 ORDER BY date DESC LIMIT 1 OFFSET 1")
    today   = latest[0]  if latest  else {}
    yesterday= prev[0]   if prev    else {}
    today_date   = today.get("date","-")
    yesterday_date= yesterday.get("date","-")

    # ── 近30天趋势 ────────────────────────────────────────────
    trend = query(conn, "SELECT * FROM daily_summary WHERE total_cost>0 ORDER BY date ASC LIMIT 30")
    dates = [r["date"] for r in trend]

    # ── 广告主维度（最新一天，TOP20）──────────────────────────
    adv = query(conn, """
        SELECT COALESCE(an.name, da.advertiser_id) as name, da.advertiser_id,
               da.total_cost, da.total_conversion, da.total_show, da.total_click,
               da.material_count,
               ROUND(da.total_cost/NULLIF(da.total_show,0)*1000,2) as cpm,
               ROUND(da.total_cost/NULLIF(da.total_click,0),2) as cpc,
               ROUND(da.total_click/NULLIF(da.total_show,0)*100,4) as ctr,
               ROUND(da.total_conversion/NULLIF(da.total_click,0)*100,4) as conv_rate,
               ROUND(da.total_cost/NULLIF(da.total_conversion,0),2) as conv_cost
        FROM daily_by_advertiser da
        LEFT JOIN advertiser_names an ON da.advertiser_id = an.advertiser_id
        WHERE da.date = ?
        ORDER BY da.total_cost DESC LIMIT 20
    """, [today_date])

    # TOP 5 广告主近7天趋势
    top5_adv_ids = [r["advertiser_id"] for r in adv[:5]]
    adv_trend = {}
    if top5_adv_ids:
        placeholders = ",".join("?"*len(top5_adv_ids))
        for r in query(conn, f"""
            SELECT advertiser_id, date, total_cost, total_conversion, total_click
            FROM daily_by_advertiser
            WHERE advertiser_id IN ({placeholders}) AND date >= date('now','-7 days')
            ORDER BY date ASC
        """, top5_adv_ids):
            adv_trend.setdefault(r["advertiser_id"], []).append(r)

    # ── 产品维度 ──────────────────────────────────────────────
    prods = query(conn, """
        SELECT product, SUM(total_cost) as cost, SUM(total_conversion) as conversion,
               SUM(total_show) as show, SUM(total_click) as click,
               SUM(material_count) as cnt
        FROM daily_by_product
        WHERE date = ?
        GROUP BY product
        ORDER BY cost DESC
    """, [today_date])

    # 产品近7天趋势
    prod_trend = {}
    for prod in [p["product"] for p in prods]:
        rows = query(conn, "SELECT date, total_cost, total_conversion FROM daily_by_product WHERE product=? AND date>=date('now','-7 days') ORDER BY date ASC", [prod])
        prod_trend[prod] = rows

    # ── 素材排行 TOP30 ────────────────────────────────────────
    mats = query(conn, """
        SELECT material_name, advertiser_id, product, cost, conversion_num,
               show_count, click,
               ROUND(cost/NULLIF(click,0),2) as cpc,
               ROUND(click/NULLIF(show_count,0)*100,4) as ctr,
               ROUND(conversion_num/NULLIF(click,0)*100,4) as conv_rate,
               ROUND(cost/NULLIF(conversion_num,0),2) as conv_cost
        FROM material_cost
        WHERE date = ?
        ORDER BY cost DESC LIMIT 30
    """, [today_date])

    # ── 余额 ──────────────────────────────────────────────────
    bal = query(conn, "SELECT balance FROM balance_snapshot ORDER BY date DESC LIMIT 1")
    balance = bal[0]["balance"] if bal else None

    # ── 所有广告主列表（用于筛选）────────────────────────────
    all_adv = query(conn, "SELECT DISTINCT advertiser_id FROM daily_by_advertiser WHERE date=? ORDER BY total_cost DESC", [today_date])
    all_prods = [p["product"] for p in prods]

    conn.close()

    # ── 注入 JSON ─────────────────────────────────────────────
    kpi_json = json.dumps({
        "today": today, "yesterday": yesterday,
        "today_date": today_date, "yesterday_date": yesterday_date
    }, ensure_ascii=False)
    trend_json = json.dumps(trend, ensure_ascii=False)
    adv_json = json.dumps(adv, ensure_ascii=False)
    adv_trend_json = json.dumps(adv_trend, ensure_ascii=False)
    prod_json = json.dumps(prods, ensure_ascii=False)
    prod_trend_json = json.dumps(prod_trend, ensure_ascii=False)
    mats_json = json.dumps(mats, ensure_ascii=False)
    dates_json = json.dumps(dates, ensure_ascii=False)
    all_adv_json = json.dumps([a["advertiser_id"] for a in all_adv], ensure_ascii=False)
    all_prods_json = json.dumps(all_prods, ensure_ascii=False)

    balance_html = f"""
    <div class="balance-pill">
        <span class="balance-label">💰 余额</span>
        <span class="balance-val">¥{balance:,.2f}</span>
    </div>""" if balance else ""

    # ── HTML ──────────────────────────────────────────────────
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Mobgi BI 看板</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
<style>
:root {{
  --bg: #0B1220;
  --card: rgba(15,23,42,0.6);
  --card-border: rgba(99,102,241,0.15);
  --text: #e2e8f0;
  --text-dim: #64748b;
  --accent: #6366f1;
  --accent2: #8b5cf6;
  --green: #22c55e;
  --red: #ef4444;
  --yellow: #f59e0b;
  --glass: rgba(255,255,255,0.03);
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, 'Segoe UI', 'PingFang SC', sans-serif;
  font-size: 13px;
  min-height: 100vh;
  background-image: radial-gradient(ellipse at 20% 0%, rgba(99,102,241,0.08) 0%, transparent 50%),
                    radial-gradient(ellipse at 80% 100%, rgba(139,92,246,0.06) 0%, transparent 50%);
}}

/* ── Header ── */
.header {{
  display: flex; justify-content: space-between; align-items: center;
  padding: 16px 24px; border-bottom: 1px solid rgba(99,102,241,0.1);
  backdrop-filter: blur(12px); position: sticky; top: 0; z-index: 100;
  background: rgba(11,18,32,0.8);
}}
.header h1 {{ font-size: 18px; font-weight: 700; background: linear-gradient(135deg, #6366f1, #a78bfa); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
.header-right {{ display: flex; align-items: center; gap: 12px; }}
.date-badge {{ background: rgba(99,102,241,0.15); border: 1px solid rgba(99,102,241,0.3); border-radius: 8px; padding: 4px 12px; font-size: 12px; color: #a5b4fc; }}
.balance-pill {{ background: linear-gradient(135deg, rgba(99,102,241,0.2), rgba(139,92,246,0.2)); border: 1px solid rgba(99,102,241,0.3); border-radius: 8px; padding: 6px 14px; display: flex; align-items: center; gap: 8px; }}
.balance-label {{ font-size: 11px; color: #a5b4fc; }}
.balance-val {{ font-size: 16px; font-weight: 700; color: #fff; }}

/* ── Filters ── */
.filters {{
  display: flex; gap: 8px; padding: 12px 24px; flex-wrap: wrap;
  border-bottom: 1px solid rgba(99,102,241,0.08);
}}
.filter-select {{
  background: rgba(15,23,42,0.8); border: 1px solid rgba(99,102,241,0.2); border-radius: 8px;
  color: var(--text); padding: 6px 12px; font-size: 12px; outline: none; cursor: pointer;
  appearance: none; min-width: 120px;
}}
.filter-select:focus {{ border-color: var(--accent); }}
.filter-select option {{ background: #0f172a; }}

/* ── KPI Cards ── */
.kpi-grid {{
  display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px; padding: 16px 24px;
}}
.kpi-card {{
  background: var(--card); border: 1px solid var(--card-border); border-radius: 12px;
  padding: 14px 16px; backdrop-filter: blur(12px);
  position: relative; overflow: hidden;
}}
.kpi-card::before {{
  content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
  background: linear-gradient(90deg, var(--accent), var(--accent2));
  opacity: 0.5;
}}
.kpi-label {{ font-size: 11px; color: var(--text-dim); margin-bottom: 6px; }}
.kpi-value {{ font-size: 22px; font-weight: 700; color: #fff; letter-spacing: -0.5px; }}
.kpi-value .unit {{ font-size: 13px; color: var(--text-dim); font-weight: 400; }}
.kpi-diff {{ font-size: 11px; margin-top: 4px; display: flex; align-items: center; gap: 3px; }}
.kpi-diff.up {{ color: var(--red); }}
.kpi-diff.down {{ color: var(--green); }}
.kpi-diff.flat {{ color: var(--text-dim); }}

/* ── Section ── */
.section {{ padding: 0 24px 20px; }}
.section-title {{
  font-size: 14px; font-weight: 600; color: #cbd5e1; margin-bottom: 12px;
  display: flex; align-items: center; gap: 8px;
}}
.section-title::before {{ content: ''; width: 3px; height: 14px; background: linear-gradient(180deg, var(--accent), var(--accent2)); border-radius: 2px; }}

/* ── Charts ── */
.charts-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 12px; }}
.chart-box {{
  background: var(--card); border: 1px solid var(--card-border); border-radius: 12px;
  padding: 14px; backdrop-filter: blur(12px);
}}
.chart-box h4 {{ font-size: 12px; font-weight: 600; color: var(--text-dim); margin-bottom: 8px; }}
.chart {{ width: 100%; height: 260px; }}
.chart-lg {{ height: 340px; }}
.chart-xl {{ height: 420px; }}
.full-width {{ grid-column: 1 / -1; }}

/* ── Table ── */
.table-wrap {{ overflow-x: auto; }}
table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
th {{ background: rgba(99,102,241,0.08); color: var(--text-dim); padding: 8px 10px; text-align: left; font-weight: 600; white-space: nowrap; position: sticky; top: 0; }}
td {{ padding: 7px 10px; border-bottom: 1px solid rgba(255,255,255,0.03); color: #cbd5e1; white-space: nowrap; }}
tr:hover td {{ background: rgba(99,102,241,0.05); }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
.money {{ color: var(--yellow); }}
.green {{ color: var(--green); }}
.red {{ color: var(--red); }}
.badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
.badge-tomato {{ background: rgba(239,68,68,0.15); color: #fca5a5; }}
.badge-redfruit {{ background: rgba(34,197,94,0.15); color: #86efac; }}
.badge-other {{ background: rgba(99,102,241,0.15); color: #a5b4fc; }}

/* ── Platform Table ── */
.platform-grid {{
  display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 10px; margin-bottom: 12px;
}}
.platform-card {{
  background: var(--card); border: 1px solid var(--card-border); border-radius: 12px;
  padding: 14px; text-align: center;
}}
.platform-name {{ font-size: 13px; font-weight: 600; color: #a5b4fc; margin-bottom: 8px; }}
.platform-cost {{ font-size: 20px; font-weight: 700; color: #fff; }}
.platform-ctr {{ font-size: 12px; color: var(--text-dim); margin-top: 4px; }}

/* ── Responsive ── */
@media (max-width: 768px) {{
  .charts-row {{ grid-template-columns: 1fr; }}
  .kpi-grid {{ grid-template-columns: repeat(3, 1fr); }}
  .header {{ padding: 12px 16px; }}
  .section {{ padding: 0 16px 16px; }}
  .filters {{ padding: 10px 16px; }}
}}
</style>
</head>
<body>

<!-- ── Header ── -->
<div class="header">
  <h1>📊 Mobgi BI 看板</h1>
  <div class="header-right">
    {balance_html}
    <div class="date-badge" id="dateBadge">加载中...</div>
  </div>
</div>

<!-- ── Filters ── -->
<div class="filters">
  <select class="filter-select" id="filterTime" onchange="applyFilters()">
    <option value="today">今天</option>
    <option value="yesterday">昨天</option>
    <option value="7d" selected>近7天</option>
    <option value="30d">近30天</option>
  </select>
  <select class="filter-select" id="filterProduct" onchange="applyFilters()">
    <option value="">全部产品</option>
  </select>
  <select class="filter-select" id="filterAdvertiser" onchange="applyFilters()">
    <option value="">全部账户</option>
  </select>
</div>

<!-- ── KPI 区 ── -->
<div class="kpi-grid" id="kpiGrid"></div>

<!-- ── 趋势图 ── -->
<div class="section">
  <div class="section-title">📈 消耗 & 转化趋势</div>
  <div class="charts-row">
    <div class="chart-box">
      <h4>每日消耗</h4>
      <div class="chart" id="trendCost"></div>
    </div>
    <div class="chart-box">
      <h4>每日转化</h4>
      <div class="chart" id="trendConv"></div>
    </div>
  </div>
</div>

<!-- ── 产品维度 ── -->
<div class="section">
  <div class="section-title">🎯 产品维度分析</div>
  <div class="charts-row">
    <div class="chart-box">
      <h4>产品消耗占比</h4>
      <div class="chart" id="prodPie"></div>
    </div>
    <div class="chart-box">
      <h4>产品消耗 & 转化</h4>
      <div class="chart" id="prodBar"></div>
    </div>
  </div>
  <div class="table-wrap">
    <table>
      <thead><tr><th>产品</th><th class="num">素材数</th><th class="num">消耗</th><th class="num">转化</th><th class="num">展示</th><th class="num">点击</th><th class="num">CTR</th><th class="num">转化率</th><th class="num">CPM</th><th class="num">CPC</th></tr></thead>
      <tbody id="prodTable"></tbody>
    </table>
  </div>
</div>

<!-- ── 广告主维度 ── -->
<div class="section">
  <div class="section-title">🏢 广告主维度（TOP 20）</div>
  <div class="chart-box" style="margin-bottom:12px">
    <h4>TOP 5 广告主消耗趋势</h4>
    <div class="chart" id="advTrendChart"></div>
  </div>
  <div class="table-wrap">
    <table>
      <thead><tr><th>广告主</th><th class="num">素材数</th><th class="num">消耗</th><th class="num">转化</th><th class="num">展示</th><th class="num">点击</th><th class="num">CTR</th><th class="num">转化率</th><th class="num">CPM</th><th class="num">CPC</th></tr></thead>
      <tbody id="advTable"></tbody>
    </table>
  </div>
</div>

<!-- ── 素材排行 ── -->
<div class="section">
  <div class="section-title">🏆 素材消耗排行 TOP 30</div>
  <div class="chart-box" style="margin-bottom:12px">
    <h4>TOP 20 素材</h4>
    <div class="chart chart-xl" id="rankChart"></div>
  </div>
  <div class="table-wrap">
    <table>
      <thead><tr><th>#</th><th>素材名称</th><th>产品</th><th class="num">消耗</th><th class="num">转化</th><th class="num">展示</th><th class="num">点击</th><th class="num">CTR</th><th class="num">转化率</th><th class="num">CPC</th></tr></thead>
      <tbody id="matTable"></tbody>
    </table>
  </div>
</div>

<!-- ── 散点图 ── -->
<div class="section">
  <div class="section-title">💡 消耗 vs 转化（TOP 100）</div>
  <div class="chart-box">
    <div class="chart chart-xl" id="scatterChart"></div>
  </div>
</div>

<div style="text-align:center; padding:20px; color:#333; font-size:11px;" id="updateTime"></div>

<script>
const KPI = {kpi_json};
const TREND = {trend_json};
const ADV = {adv_json};
const ADV_TREND = {adv_trend_json};
const PROD = {prod_json};
const PROD_TREND = {prod_trend_json};
const MATS = {mats_json};
const DATES = {dates_json};
const ALL_ADV = {all_adv_json};
const ALL_PRODS = {all_prods_json};

// ── 工具函数 ──
function fmt(n, d=2) {{ return Number(n||0).toLocaleString('zh-CN',{{minimumFractionDigits:d,maximumFractionDigits:d}}); }}
function fmtPct(n) {{ return (n||0).toFixed(2)+'%'; }}
function arrow(d) {{ return d>0?'↑':(d<0?'↓':'→'); }}
function cls(d) {{ return d>0?'up':(d<0?'down':'flat'); }}

// ── 填充筛选器 ──
function fillFilters() {{
  const pSel = document.getElementById('filterProduct');
  ALL_PRODS.forEach(p => {{ pSel.innerHTML += `<option value="${{p}}">${{p}}</option>`; }});
  const aSel = document.getElementById('filterAdvertiser');
  ALL_ADV.forEach(a => {{ aSel.innerHTML += `<option value="${{a}}">${{a}}</option>`; }});
}}
fillFilters();

// ── KPI 卡片 ──
function renderKPI() {{
  const t = KPI.today, y = KPI.yesterday;
  document.getElementById('dateBadge').textContent = `数据: ${{KPI.today_date}} | 对比: ${{KPI.yesterday_date}}`;
  const kpis = [
    ['💰 消耗','total_cost','¥',2],
    ['📊 展示','total_show','',0],
    ['👆 点击','total_click','',0],
    ['📈 CTR','ctr','%',4],
    ['🎯 转化','total_conversion','',0],
    ['💵 CPA','convert_cost','¥',4],
    ['📉 CPC','cpc','¥',4],
    ['📊 CPM','cpm','¥',2],
  ];
  document.getElementById('kpiGrid').innerHTML = kpis.map(([label,key,unit,dec]) => {{
    const nv = t[key]||0, ov = y[key]||0;
    const [diff,pct,dir] = (ov !== undefined) ? [nv-ov, ov!==0?((nv-ov)/Math.abs(ov)*100).toFixed(1):null, nv>ov?'up':(nv<ov?'down':'flat')] : [0,null,'flat'];
    const pctStr = pct!==null ? ` (${{diff>0?'+'':''}}${{pct}}%)` : '';
    return `<div class="kpi-card">
      <div class="kpi-label">${{label}}</div>
      <div class="kpi-value">${{unit}}${{fmt(nv,dec)}}</div>
      <div class="kpi-diff ${{cls(diff)}}">${{arrow(diff)}} ${{fmt(Math.abs(diff),dec)}}${{pctStr}}</div>
    </div>`;
  }}).join('');
}}
renderKPI();

// ── 趋势图 ──
const commonOpt = {{
  backgroundColor:'transparent',
  tooltip:{{trigger:'axis'}},
  grid:{{left:55,right:15,top:10,bottom:25}},
  xAxis:{{type:'category',axisLabel:{{color:'#64748b',fontSize:10}},axisLine:{{lineStyle:{{color:'#1e293b'}}}}}},
  yAxis:{{type:'value',axisLabel:{{color:'#64748b',fontSize:10}},splitLine:{{lineStyle:{{color:'#0f172a'}}}}}},
}};

const trendCost = echarts.init(document.getElementById('trendCost'));
trendCost.setOption({{
  ...commonOpt,
  xAxis:{{...commonOpt.xAxis,data:DATES}},
  yAxis:{{...commonOpt.yAxis,axisLabel:{{...commonOpt.yAxis.axisLabel,formatter:v=>'¥'+(v>=1000?(v/1000).toFixed(0)+'k':v)}}}},
  series:[{{type:'bar',data:TREND.map(r=>r.total_cost),itemStyle:{{color:'#6366f1',borderRadius:[3,3,0,0]}},barMaxWidth:16}}]
}});

const trendConv = echarts.init(document.getElementById('trendConv'));
trendConv.setOption({{
  ...commonOpt,
  xAxis:{{...commonOpt.xAxis,data:DATES}},
  series:[{{type:'bar',data:TREND.map(r=>r.total_conversion),itemStyle:{{color:'#22c55e',borderRadius:[3,3,0,0]}},barMaxWidth:16}}]
}});

// ── 产品维度 ──
function renderProd() {{
  // 饼图
  const pie = echarts.init(document.getElementById('prodPie'));
  pie.setOption({{
    backgroundColor:'transparent',
    tooltip:{{trigger:'item',formatter:'{{b}}: ¥{{c}} ({{d}}%)'}},
    legend:{{orient:'vertical',right:0,top:'center',textStyle:{{color:'#64748b',fontSize:11}}}},
    series:[{{
      type:'pie',radius:['40%','70%'],center:['38%','50%'],
      data:PROD.map(p=>{{return {{name:p.product,value:Math.round(p.cost*100)/100}}}}),
      label:{{color:'#cbd5e1',fontSize:11}},
      itemStyle:{{borderRadius:4,borderColor:'rgba(15,23,42,0.8)',borderWidth:2}}
    }}]
  }});

  // 柱图
  const bar = echarts.init(document.getElementById('prodBar'));
  bar.setOption({{
    backgroundColor:'transparent',
    tooltip:{{trigger:'axis',axisPointer:{{type:'shadow'}}}},
    legend:{{data:['消耗','转化'],textStyle:{{color:'#64748b'}},top:0}},
    grid:{{left:20,right:20,top:30,bottom:20,containLabel:true}},
    xAxis:{{type:'value',axisLabel:{{color:'#64748b',fontSize:10}},splitLine:{{lineStyle:{{color:'#0f172a'}}}}}},
    yAxis:{{type:'category',data:PROD.map(p=>p.product),axisLabel:{{color:'#cbd5e1',fontSize:11}}}},
    series:[
      {{name:'消耗',type:'bar',data:PROD.map(p=>p.cost),itemStyle:{{color:'#6366f1',borderRadius:[0,4,4,0]}},barMaxWidth:14}},
      {{name:'转化',type:'bar',data:PROD.map(p=>p.conversion),itemStyle:{{color:'#22c55e',borderRadius:[0,4,4,0]}},barMaxWidth:14}}
    ]
  }});

  // 表格
  document.getElementById('prodTable').innerHTML = PROD.map(p => {{
    const ctr = p.show>0?(p.click/p.show*100).toFixed(2):'-';
    const cr  = p.click>0?(p.conversion/p.click*100).toFixed(2):'-';
    const cpm = p.show>0?(p.cost/p.show*1000).toFixed(2):'-';
    const cpc = p.click>0?(p.cost/p.click).toFixed(2):'-';
    const bc  = p.product.includes('番茄')?'badge-tomato':(p.product.includes('红果')?'badge-redfruit':'badge-other');
    return `<tr>
      <td><span class="badge ${{bc}}">${{p.product}}</span></td>
      <td class="num">${{p.cnt}}</td>
      <td class="num money">¥${{fmt(p.cost)}}</td>
      <td class="num">${{fmt(p.conversion,0)}}</td>
      <td class="num">${{fmt(p.show,0)}}</td>
      <td class="num">${{fmt(p.click,0)}}</td>
      <td class="num">${{ctr}}%</td>
      <td class="num">${{cr}}%</td>
      <td class="num">¥${{cpm}}</td>
      <td class="num">¥${{cpc}}</td>
    </tr>`;
  }}).join('');
}}
renderProd();

// ── 广告主维度 ──
function renderAdv() {{
  // TOP5 趋势
  const colors = ['#6366f1','#22c55e','#f59e0b','#ef4444','#3b82f6'];
  const advTrendChart = echarts.init(document.getElementById('advTrendChart'));
  const top5 = ADV.slice(0,5);
  advTrendChart.setOption({{
    backgroundColor:'transparent',
    tooltip:{{trigger:'axis'}},
    legend:{{data:top5.map(a=>a.name),textStyle:{{color:'#64748b',fontSize:10}},top:0}},
    grid:{{left:55,right:15,top:30,bottom:25}},
    xAxis:{{type:'category',data:DATES,axisLabel:{{color:'#64748b',fontSize:10}}}},
    yAxis:{{type:'value',axisLabel:{{color:'#64748b',fontSize:10,formatter:v=>'¥'+(v>=1000?(v/1000).toFixed(0)+'k':v)}},splitLine:{{lineStyle:{{color:'#0f172a'}}}}}},
    series:top5.map((a,i)=>({{
      name:a.name, type:'line',
      data:(ADV_TREND[a.advertiser_id]||[]).map(r=>r.total_cost),
      lineStyle:{{width:2,color:colors[i]}},
      symbol:'circle',symbolSize:4,
      smooth:true
    }}))
  }});

  // 表格
  document.getElementById('advTable').innerHTML = ADV.map(a => {{
    const ctr = a.ctr||0, cr=a.conv_rate||0, cpm=a.cpm||0, cpc=a.cpc||0;
    return `<tr>
      <td style="max-width:120px;overflow:hidden;text-overflow:ellipsis">${{a.name}}</td>
      <td class="num">${{a.material_count}}</td>
      <td class="num money">¥${{fmt(a.total_cost)}}</td>
      <td class="num">${{fmt(a.total_conversion,0)}}</td>
      <td class="num">${{fmt(a.total_show,0)}}</td>
      <td class="num">${{fmt(a.total_click,0)}}</td>
      <td class="num">${{fmtPct(ctr)}}</td>
      <td class="num">${{fmtPct(cr)}}</td>
      <td class="num">¥${{fmt(cpm)}}</td>
      <td class="num">¥${{fmt(cpc)}}</td>
    </tr>`;
  }}).join('');
}}
renderAdv();

// ── 素材排行 ──
function renderMats() {{
  const top20 = MATS.slice(0,20);
  const rankChart = echarts.init(document.getElementById('rankChart'));
  rankChart.setOption({{
    backgroundColor:'transparent',
    tooltip:{{trigger:'axis',axisPointer:{{type:'shadow'}}}},
    legend:{{data:['花费','转化'],textStyle:{{color:'#64748b'}},top:0}},
    grid:{{left:20,right:20,top:30,bottom:20,containLabel:true}},
    xAxis:{{type:'value',axisLabel:{{color:'#64748b',fontSize:10}},splitLine:{{lineStyle:{{color:'#0f172a'}}}}}},
    yAxis:{{type:'category',data:[...top20].reverse().map(m=>m.material_name.substring(0,25)),axisLabel:{{color:'#cbd5e1',fontSize:10,width:150,overflow:'truncate'}}}},
    series:[
      {{name:'花费',type:'bar',data:[...top20].reverse().map(m=>m.cost),itemStyle:{{color:'#6366f1',borderRadius:[0,4,4,0]}},barMaxWidth:10}},
      {{name:'转化',type:'bar',data:[...top20].reverse().map(m=>m.conversion_num),itemStyle:{{color:'#22c55e',borderRadius:[0,4,4,0]}},barMaxWidth:10}}
    ]
  }});

  document.getElementById('matTable').innerHTML = MATS.map((m,i) => {{
    const bc = m.product.includes('番茄')?'badge-tomato':(m.product.includes('红果')?'badge-redfruit':'badge-other');
    return `<tr>
      <td class="num">${{i+1}}</td>
      <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis" title="${{m.material_name}}">${{m.material_name}}</td>
      <td><span class="badge ${{bc}}">${{m.product||'其他'}}</span></td>
      <td class="num money">¥${{fmt(m.cost)}}</td>
      <td class="num">${{fmt(m.conversion_num,0)}}</td>
      <td class="num">${{fmt(m.show_count,0)}}</td>
      <td class="num">${{fmt(m.click,0)}}</td>
      <td class="num">${{fmtPct(m.ctr)}}</td>
      <td class="num">${{fmtPct(m.conv_rate)}}</td>
      <td class="num">¥${{fmt(m.cpc)}}</td>
    </tr>`;
  }}).join('');
}}
renderMats();

// ── 散点图 ──
const scatter = echarts.init(document.getElementById('scatterChart'));
scatter.setOption({{
  backgroundColor:'transparent',
  tooltip:{{trigger:'item',formatter:p=>`<b>${{p.data[3]}}</b><br/>花费:¥${{fmt(p.data[0])}}<br/>转化:${{p.data[1]}}<br/>CTR:${{p.data[2]}}%<br/>CPA:¥${{p.data[4].toFixed(2)}}`}},
  grid:{{left:60,right:20,top:10,bottom:25}},
  xAxis:{{name:'花费',nameTextStyle:{{color:'#64748b'}},axisLabel:{{color:'#64748b',fontSize:10,formatter:v=>'¥'+(v>=1000?(v/1000).toFixed(0)+'k':v)}},splitLine:{{lineStyle:{{color:'#0f172a'}}}}}},
  yAxis:{{name:'转化',nameTextStyle:{{color:'#64748b'}},axisLabel:{{color:'#64748b',fontSize:10}},splitLine:{{lineStyle:{{color:'#0f172a'}}}}}},
  series:[{{
    type:'scatter',
    data:MATS.slice(0,100).map(m=>[m.cost,m.conversion_num,m.ctr,m.material_name.substring(0,30),m.conv_cost||0]),
    symbolSize:d=>Math.max(6,Math.min(35,d[0]/40)),
    itemStyle:{{color:d=>d[2]>8?'#22c55e':(d[2]>4?'#f59e0b':'#ef4444'),opacity:0.8}}
  }}]
}});

// ── 筛选 ──
function applyFilters() {{
  // 当前只是 UI 占位，实际筛选需要后端支持或前端 JS 过滤
  console.log('filter changed');
}}

// ── 响应式 ──
window.addEventListener('resize',()=>{{
  trendCost.resize();trendConv.resize();scatter.resize();
}});

document.getElementById('updateTime').textContent = '更新时间: '+new Date().toLocaleString('zh-CN');
</script>
</body>
</html>"""

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"✅ BI 看板已生成: {OUTPUT_PATH}", flush=True)
    print(f"   日期: {today_date} | 广告主: {len(adv)} | 产品: {len(prods)} | 素材: {len(mats)}", flush=True)

if __name__ == "__main__":
    build()
