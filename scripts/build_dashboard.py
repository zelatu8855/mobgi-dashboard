#!/usr/bin/env python3.13
"""
生成 Mobgi 数据可视化面板（自包含 HTML + ECharts）
支持多维度切换：全部 / 按广告主主体 / 按产品关键词
用法: python3.13 build_dashboard.py
"""
import sqlite3, json, os, re, sys
from collections import defaultdict
from datetime import date, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DB_PATH = os.path.join(PROJECT_DIR, "data", "mobgi.db")
OUTPUT_PATH = os.path.join(PROJECT_DIR, "dashboard.html")

# ─── 产品关键词映射规则 ──────────────────────────────────────────
# 素材名称开头匹配 → 产品标签
PRODUCT_RULES = [
    (r"^番茄小说", "番茄小说"),
    (r"^番茄短剧", "番茄短剧"),
    (r"^番茄", "番茄"),
    (r"^红果短剧", "红果短剧"),
    (r"^红果", "红果"),
    (r"^狸鸣红果", "狸鸣红果"),
    (r"^头条", "头条"),
    (r"^抖音", "抖音"),
    (r"^百度", "百度"),
    (r"^腾讯", "腾讯"),
    (r"^快手", "快手"),
]


def classify_product(material_name: str) -> str:
    for pattern, label in PRODUCT_RULES:
        if re.match(pattern, material_name):
            return label
    return "其他"


def query(conn, sql, params=()):
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.row_factory = None
    return [dict(r) for r in rows]


def pct_change(old, new):
    if not old:
        return (new, None, "up") if new else (0, None, "flat")
    diff = new - old
    pct = round(diff / abs(old) * 100, 2) if old else None
    direction = "up" if diff > 0 else ("down" if diff < 0 else "flat")
    return (round(diff, 2), pct, direction)


def build():
    if not os.path.exists(DB_PATH):
        print(f"❌ 数据库不存在: {DB_PATH}", flush=True)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)

    # ─── 最近 30 天每日汇总 ──────────────────────────────────────
    daily = query(conn, """
        SELECT * FROM daily_summary
        WHERE date >= date('now', '-30 days') AND total_cost > 0
        ORDER BY date ASC
    """)

    # ─── 按广告主主体汇总（最近一天）────────────────────────────
    latest_date = daily[-1]["date"] if daily else ""
    by_advertiser = query(conn, """
        SELECT advertiser_id,
               SUM(cost) as total_cost,
               SUM(conversion_num) as total_conversion,
               SUM(show_count) as total_show,
               SUM(click) as total_click,
               COUNT(*) as material_count
        FROM material_cost
        WHERE date = ? AND advertiser_id != ''
        GROUP BY advertiser_id
        ORDER BY total_cost DESC
    """, [latest_date])

    # ─── 按产品关键词汇总（最近一天）────────────────────────────
    all_materials = query(conn, """
        SELECT material_name, cost, conversion_num, show_count, click, ctr, convert_rate, convert_cost, advertiser_id
        FROM material_cost
        WHERE date = ?
        ORDER BY cost DESC
    """, [latest_date])

    # 按产品分组
    product_groups = defaultdict(lambda: {"cost": 0, "conversion": 0, "show": 0, "click": 0, "count": 0})
    for m in all_materials:
        product = classify_product(m["material_name"])
        g = product_groups[product]
        g["cost"] += float(m.get("cost", 0) or 0)
        g["conversion"] += int(m.get("conversion_num", 0) or 0)
        g["show"] += int(m.get("show_count", 0) or 0)
        g["click"] += int(m.get("click", 0) or 0)
        g["count"] += 1

    by_product = []
    for name, g in sorted(product_groups.items(), key=lambda x: -x[1]["cost"]):
        ctr = round(g["click"] / g["show"] * 100, 4) if g["show"] else 0
        conv_rate = round(g["conversion"] / g["click"] * 100, 4) if g["click"] else 0
        cpm = round(g["cost"] / g["show"] * 1000, 2) if g["show"] else 0
        cpc = round(g["cost"] / g["click"], 2) if g["click"] else 0
        conv_cost = round(g["cost"] / g["conversion"], 2) if g["conversion"] else 0
        by_product.append({
            "product": name, "cost": round(g["cost"], 2),
            "conversion": g["conversion"], "show": g["show"], "click": g["click"],
            "ctr": ctr, "conv_rate": conv_rate, "cpm": cpm, "cpc": cpc, "conv_cost": conv_cost,
            "count": g["count"]
        })

    # ─── TOP 20 素材 ─────────────────────────────────────────────
    top_materials = all_materials[:20]

    # ─── 散点图数据 ──────────────────────────────────────────────
    scatter_data = all_materials[:100]

    # ─── 余额 ────────────────────────────────────────────────────
    balance = query(conn, "SELECT * FROM balance_snapshot ORDER BY date DESC LIMIT 1")
    balance_val = balance[0]["balance"] if balance else None

    conn.close()

    # ─── 生成 HTML ───────────────────────────────────────────────
    dates = [d["date"] for d in daily]
    cost_series = [d["total_cost"] for d in daily]
    conv_series = [d["total_conversion"] for d in daily]
    click_series = [d["total_click"] for d in daily]
    ctr_series = [d.get("ctr", 0) for d in daily]

    today_str = latest_date or "无数据"

    # JSON 数据注入
    by_adv_json = json.dumps(by_advertiser, ensure_ascii=False)
    by_prod_json = json.dumps(by_product, ensure_ascii=False)
    top_names = json.dumps([m["material_name"][:25] for m in top_materials], ensure_ascii=False)
    top_costs = json.dumps([m["cost"] for m in top_materials])
    top_convs = json.dumps([m.get("conversion_num", 0) for m in top_materials])
    scatter_json = json.dumps([
        [m["cost"], m.get("conversion_num", 0), m.get("ctr", 0),
         m["material_name"][:30], m.get("convert_cost", 0)]
        for m in scatter_data
    ], ensure_ascii=False)

    balance_html = ""
    if balance_val:
        balance_html = f"""
        <div class="balance-card">
            <div class="balance-label">💰 账户余额</div>
            <div class="balance-value">¥{balance_val:,.2f}</div>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Mobgi 素材数据面板</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ background: #0a0a0f; color: #e0e0e5; font-family: -apple-system, 'Segoe UI', sans-serif; padding: 16px; }}
.header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; flex-wrap: wrap; gap: 8px; }}
.header h1 {{ font-size: 18px; font-weight: 700; color: #fff; }}
.header .date {{ font-size: 12px; color: #888; }}
.balance-card {{ background: linear-gradient(135deg, #1e1b4b, #312e81); border: 1px solid #4338ca; border-radius: 10px; padding: 14px 18px; margin-bottom: 16px; display: flex; justify-content: space-between; align-items: center; }}
.balance-label {{ font-size: 13px; color: #a5b4fc; }}
.balance-value {{ font-size: 24px; font-weight: 700; color: #fff; }}
.section {{ margin-bottom: 16px; }}
.section-title {{ font-size: 14px; font-weight: 600; color: #ccc; margin-bottom: 10px; display: flex; align-items: center; gap: 8px; }}
.tab-group {{ display: flex; gap: 4px; margin-left: auto; }}
.tab {{ padding: 4px 12px; border-radius: 6px; border: 1px solid #333; background: #1a1a25; color: #888; font-size: 12px; cursor: pointer; transition: all .2s; }}
.tab:hover {{ border-color: #555; color: #ccc; }}
.tab.active {{ background: #6366f1; border-color: #6366f1; color: #fff; }}
.charts-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
.chart-box {{ background: #141418; border: 1px solid #2a2a35; border-radius: 10px; padding: 12px; }}
.chart-box h3 {{ font-size: 13px; font-weight: 600; color: #aaa; margin-bottom: 8px; }}
.chart {{ width: 100%; height: 280px; }}
.chart-lg {{ height: 340px; }}
.full-width {{ grid-column: 1 / -1; }}
.table-wrap {{ overflow-x: auto; }}
table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
th {{ background: #1a1a25; color: #888; padding: 8px 10px; text-align: left; font-weight: 600; white-space: nowrap; }}
td {{ padding: 7px 10px; border-bottom: 1px solid #1a1a25; color: #ccc; white-space: nowrap; }}
tr:hover td {{ background: #1a1a25; }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
.money {{ color: #f59e0b; }}
.up {{ color: #ef4444; }}
.down {{ color: #22c55e; }}
.badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
.badge-tomato {{ background: #7f1d1d; color: #fca5a5; }}
.badge-redfruit {{ background: #14532d; color: #86efac; }}
.badge-other {{ background: #1e1b4b; color: #a5b4fc; }}
.update-time {{ font-size: 11px; color: #555; text-align: right; margin-top: 12px; }}
@media (max-width: 768px) {{ .charts-row {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>

<div class="header">
    <h1>📊 Mobgi 素材数据面板</h1>
    <div class="date">数据日期: {today_str}</div>
</div>

{balance_html}

<!-- ─── 趋势图 ─────────────────────────────────────────────── -->
<div class="section">
    <div class="section-title">📈 每日趋势</div>
    <div class="charts-row">
        <div class="chart-box">
            <h3>消耗趋势</h3>
            <div class="chart" id="trendChart"></div>
        </div>
        <div class="chart-box">
            <h3>转化趋势</h3>
            <div class="chart" id="convChart"></div>
        </div>
    </div>
</div>

<!-- ─── 主体维度 ───────────────────────────────────────────── -->
<div class="section">
    <div class="section-title">
        🏢 按广告主体
        <div class="tab-group">
            <div class="tab active" onclick="switchAdvTab(this, 'cost')">按花费</div>
            <div class="tab" onclick="switchAdvTab(this, 'conv')">按转化</div>
        </div>
    </div>
    <div class="chart-box">
        <div class="chart" id="advChart"></div>
    </div>
    <div class="table-wrap" style="margin-top:10px">
        <table>
            <thead><tr><th>广告主ID</th><th>素材数</th><th class="num">花费</th><th class="num">转化</th><th class="num">展示</th><th class="num">点击</th><th class="num">CTR</th><th class="num">转化率</th><th class="num">CPM</th><th class="num">CPC</th></tr></thead>
            <tbody id="advTable"></tbody>
        </table>
    </div>
</div>

<!-- ─── 产品维度 ───────────────────────────────────────────── -->
<div class="section">
    <div class="section-title">🎯 按产品</div>
    <div class="charts-row">
        <div class="chart-box">
            <h3>各产品花费占比</h3>
            <div class="chart" id="prodPieChart"></div>
        </div>
        <div class="chart-box">
            <h3>各产品消耗对比</h3>
            <div class="chart" id="prodBarChart"></div>
        </div>
    </div>
    <div class="table-wrap" style="margin-top:10px">
        <table>
            <thead><tr><th>产品</th><th>素材数</th><th class="num">花费</th><th class="num">转化</th><th class="num">展示</th><th class="num">点击</th><th class="num">CTR</th><th class="num">转化率</th><th class="num">CPM</th><th class="num">CPC</th></tr></thead>
            <tbody id="prodTable"></tbody>
        </table>
    </div>
</div>

<!-- ─── 素材排行 ───────────────────────────────────────────── -->
<div class="section">
    <div class="section-title">🏆 素材消耗排行 TOP 20</div>
    <div class="chart-box full-width">
        <div class="chart chart-lg" id="rankChart"></div>
    </div>
</div>

<!-- ─── 散点图 ─────────────────────────────────────────────── -->
<div class="section">
    <div class="section-title">💡 消耗 vs 转化（TOP 100）</div>
    <div class="chart-box full-width">
        <div class="chart chart-lg" id="scatterChart"></div>
    </div>
</div>

<div class="update-time" id="updateTime"></div>

<script>
const dates = {json.dumps(dates)};
const costSeries = {json.dumps(cost_series)};
const convSeries = {json.dumps(conv_series)};
const clickSeries = {json.dumps(click_series)};
const ctrSeries = {json.dumps(ctr_series)};
const byAdv = {by_adv_json};
const byProd = {by_prod_json};
const topNames = {top_names};
const topCosts = {top_costs};
const topConvs = {top_convs};
const scatterData = {scatter_json};

// ─── 趋势图 ─────────────────────────────────────────────────
const trendChart = echarts.init(document.getElementById('trendChart'));
trendChart.setOption({{
    backgroundColor: 'transparent',
    tooltip: {{ trigger: 'axis' }},
    grid: {{ left: 55, right: 15, top: 10, bottom: 30 }},
    xAxis: {{ type: 'category', data: dates, axisLabel: {{ color: '#888', fontSize: 10 }}, axisLine: {{ lineStyle: {{ color: '#333' }} }} }},
    yAxis: [
        {{ type: 'value', name: '花费', axisLabel: {{ color: '#888', fontSize: 10, formatter: v => '¥' + (v >= 1000 ? (v/1000).toFixed(0)+'k' : v) }}, splitLine: {{ lineStyle: {{ color: '#1a1a25' }} }} }},
        {{ type: 'value', name: 'CTR%', axisLabel: {{ color: '#888', fontSize: 10, formatter: v => v + '%' }}, splitLine: {{ show: false }} }}
    ],
    series: [
        {{ name: '花费', type: 'bar', data: costSeries, itemStyle: {{ color: '#6366f1' }}, barMaxWidth: 18 }},
        {{ name: 'CTR', type: 'line', yAxisIndex: 1, data: ctrSeries, itemStyle: {{ color: '#f59e0b' }}, lineStyle: {{ width: 2 }}, symbol: 'circle', symbolSize: 3 }}
    ]
}});

const convChart = echarts.init(document.getElementById('convChart'));
convChart.setOption({{
    backgroundColor: 'transparent',
    tooltip: {{ trigger: 'axis' }},
    grid: {{ left: 55, right: 15, top: 10, bottom: 30 }},
    xAxis: {{ type: 'category', data: dates, axisLabel: {{ color: '#888', fontSize: 10 }}, axisLine: {{ lineStyle: {{ color: '#333' }} }} }},
    yAxis: [
        {{ type: 'value', name: '转化', axisLabel: {{ color: '#888', fontSize: 10 }}, splitLine: {{ lineStyle: {{ color: '#1a1a25' }} }} }},
        {{ type: 'value', name: '点击', axisLabel: {{ color: '#888', fontSize: 10 }}, splitLine: {{ show: false }} }}
    ],
    series: [
        {{ name: '转化', type: 'bar', data: convSeries, itemStyle: {{ color: '#22c55e' }}, barMaxWidth: 18 }},
        {{ name: '点击', type: 'line', yAxisIndex: 1, data: clickSeries, itemStyle: {{ color: '#3b82f6' }}, lineStyle: {{ width: 2 }}, symbol: 'circle', symbolSize: 3 }}
    ]
}});

// ─── 广告主体图表 ──────────────────────────────────────────
let advSortBy = 'cost';
function renderAdv() {{
    const sorted = [...byAdv].sort((a, b) => b['total_' + advSortBy] - a['total_' + advSortBy]);
    const ids = sorted.map(a => a.advertiser_id);
    const costs = sorted.map(a => a.total_cost);
    const convs = sorted.map(a => a.total_conversion);

    const advChart = echarts.init(document.getElementById('advChart'));
    advChart.setOption({{
        backgroundColor: 'transparent',
        tooltip: {{ trigger: 'axis', axisPointer: {{ type: 'shadow' }} }},
        legend: {{ data: ['花费', '转化'], textStyle: {{ color: '#888' }}, top: 0 }},
        grid: {{ left: 20, right: 20, top: 30, bottom: 20, containLabel: true }},
        xAxis: {{ type: 'value', axisLabel: {{ color: '#888', fontSize: 10 }}, splitLine: {{ lineStyle: {{ color: '#1a1a25' }} }} }},
        yAxis: {{ type: 'category', data: ids, axisLabel: {{ color: '#ccc', fontSize: 10, width: 120, overflow: 'truncate' }} }},
        series: [
            {{ name: '花费', type: 'bar', data: costs, itemStyle: {{ color: '#6366f1', borderRadius: [0, 4, 4, 0] }}, barMaxWidth: 14 }},
            {{ name: '转化', type: 'bar', data: convs, itemStyle: {{ color: '#22c55e', borderRadius: [0, 4, 4, 0] }}, barMaxWidth: 14 }}
        ]
    }});

    // 表格
    const tbody = document.getElementById('advTable');
    tbody.innerHTML = sorted.map(a => {{
        const ctr = a.total_show > 0 ? (a.total_click / a.total_show * 100).toFixed(2) : '-';
        const convRate = a.total_click > 0 ? (a.total_conversion / a.total_click * 100).toFixed(2) : '-';
        const cpm = a.total_show > 0 ? (a.total_cost / a.total_show * 1000).toFixed(2) : '-';
        const cpc = a.total_click > 0 ? (a.total_cost / a.total_click).toFixed(2) : '-';
        return `<tr>
            <td>${{a.advertiser_id}}</td>
            <td class="num">${{a.material_count}}</td>
            <td class="num money">¥${{a.total_cost.toLocaleString(undefined,{{minimumFractionDigits:2}})}}</td>
            <td class="num">${{a.total_conversion.toLocaleString()}}</td>
            <td class="num">${{a.total_show.toLocaleString()}}</td>
            <td class="num">${{a.total_click.toLocaleString()}}</td>
            <td class="num">${{ctr}}%</td>
            <td class="num">${{convRate}}%</td>
            <td class="num">¥${{cpm}}</td>
            <td class="num">¥${{cpc}}</td>
        </tr>`;
    }}).join('');
}}
function switchAdvTab(el, sortBy) {{
    document.querySelectorAll('.tab-group .tab').forEach(t => t.classList.remove('active'));
    el.classList.add('active');
    advSortBy = sortBy;
    renderAdv();
}}
renderAdv();

// ─── 产品维度图表 ───────────────────────────────────────────
function renderProd() {{
    const names = byProd.map(p => p.product);
    const costs = byProd.map(p => p.cost);
    const convs = byProd.map(p => p.conversion);

    // 饼图
    const pieChart = echarts.init(document.getElementById('prodPieChart'));
    pieChart.setOption({{
        backgroundColor: 'transparent',
        tooltip: {{ trigger: 'item', formatter: '{{b}}: ¥{{c}} ({{d}}%)' }},
        legend: {{ orient: 'vertical', right: 0, top: 'center', textStyle: {{ color: '#888', fontSize: 11 }} }},
        series: [{{
            type: 'pie',
            radius: ['40%', '70%'],
            center: ['40%', '50%'],
            data: byProd.map(p => ({{ name: p.product, value: Math.round(p.cost * 100) / 100 }})),
            label: {{ color: '#ccc', fontSize: 11 }},
            itemStyle: {{ borderRadius: 4, borderColor: '#141418', borderWidth: 2 }}
        }}]
    }});

    // 柱图
    const barChart = echarts.init(document.getElementById('prodBarChart'));
    barChart.setOption({{
        backgroundColor: 'transparent',
        tooltip: {{ trigger: 'axis', axisPointer: {{ type: 'shadow' }} }},
        legend: {{ data: ['花费', '转化'], textStyle: {{ color: '#888' }}, top: 0 }},
        grid: {{ left: 20, right: 20, top: 30, bottom: 20, containLabel: true }},
        xAxis: {{ type: 'value', axisLabel: {{ color: '#888', fontSize: 10 }}, splitLine: {{ lineStyle: {{ color: '#1a1a25' }} }} }},
        yAxis: {{ type: 'category', data: names, axisLabel: {{ color: '#ccc', fontSize: 11 }} }},
        series: [
            {{ name: '花费', type: 'bar', data: costs, itemStyle: {{ color: '#6366f1', borderRadius: [0, 4, 4, 0] }}, barMaxWidth: 14 }},
            {{ name: '转化', type: 'bar', data: convs, itemStyle: {{ color: '#22c55e', borderRadius: [0, 4, 4, 0] }}, barMaxWidth: 14 }}
        ]
    }});

    // 表格
    const tbody = document.getElementById('prodTable');
    tbody.innerHTML = byProd.map(p => {{
        const badgeClass = p.product.includes('番茄') ? 'badge-tomato' : (p.product.includes('红果') ? 'badge-redfruit' : 'badge-other');
        return `<tr>
            <td><span class="badge ${{badgeClass}}">${{p.product}}</span></td>
            <td class="num">${{p.count}}</td>
            <td class="num money">¥${{p.cost.toLocaleString(undefined,{{minimumFractionDigits:2}})}}</td>
            <td class="num">${{p.conversion.toLocaleString()}}</td>
            <td class="num">${{p.show.toLocaleString()}}</td>
            <td class="num">${{p.click.toLocaleString()}}</td>
            <td class="num">${{p.ctr}}%</td>
            <td class="num">${{p.conv_rate}}%</td>
            <td class="num">¥${{p.cpm}}</td>
            <td class="num">¥${{p.cpc}}</td>
        </tr>`;
    }}).join('');
}}
renderProd();

// ─── 素材排行 ───────────────────────────────────────────────
const rankChart = echarts.init(document.getElementById('rankChart'));
rankChart.setOption({{
    backgroundColor: 'transparent',
    tooltip: {{ trigger: 'axis', axisPointer: {{ type: 'shadow' }} }},
    legend: {{ data: ['花费', '转化数'], textStyle: {{ color: '#888' }}, top: 0 }},
    grid: {{ left: 20, right: 20, top: 30, bottom: 20, containLabel: true }},
    xAxis: {{ type: 'value', axisLabel: {{ color: '#888', fontSize: 10 }}, splitLine: {{ lineStyle: {{ color: '#1a1a25' }} }} }},
    yAxis: {{ type: 'category', data: [...topNames].reverse(), axisLabel: {{ color: '#ccc', fontSize: 11, width: 160, overflow: 'truncate' }} }},
    series: [
        {{ name: '花费', type: 'bar', data: [...topCosts].reverse(), itemStyle: {{ color: '#6366f1', borderRadius: [0, 4, 4, 0] }}, barMaxWidth: 12 }},
        {{ name: '转化数', type: 'bar', data: [...topConvs].reverse(), itemStyle: {{ color: '#22c55e', borderRadius: [0, 4, 4, 0] }}, barMaxWidth: 12 }}
    ]
}});

// ─── 散点图 ─────────────────────────────────────────────────
const scatterChart = echarts.init(document.getElementById('scatterChart'));
scatterChart.setOption({{
    backgroundColor: 'transparent',
    tooltip: {{
        trigger: 'item',
        formatter: p => `<b>${{p.data[3]}}</b><br/>花费: ¥${{p.data[0].toLocaleString()}}<br/>转化: ${{p.data[1]}}<br/>CTR: ${{p.data[2]}}%<br/>转化成本: ¥${{p.data[4].toFixed(2)}}`
    }},
    grid: {{ left: 60, right: 20, top: 10, bottom: 30 }},
    xAxis: {{ name: '花费', nameTextStyle: {{ color: '#888' }}, axisLabel: {{ color: '#888', fontSize: 10, formatter: v => '¥' + (v >= 1000 ? (v/1000).toFixed(0)+'k' : v) }}, splitLine: {{ lineStyle: {{ color: '#1a1a25' }} }} }},
    yAxis: {{ name: '转化数', nameTextStyle: {{ color: '#888' }}, axisLabel: {{ color: '#888', fontSize: 10 }}, splitLine: {{ lineStyle: {{ color: '#1a1a25' }} }} }},
    series: [{{
        type: 'scatter',
        data: scatterData,
        symbolSize: d => Math.max(6, Math.min(30, d[0] / 50)),
        itemStyle: {{
            color: d => d[2] > 8 ? '#22c55e' : (d[2] > 4 ? '#f59e0b' : '#ef4444'),
            opacity: 0.8
        }}
    }}]
}});

window.addEventListener('resize', () => {{
    trendChart.resize(); convChart.resize(); rankChart.resize(); scatterChart.resize();
}});

document.getElementById('updateTime').textContent = '更新时间: ' + new Date().toLocaleString('zh-CN');
</script>
</body>
</html>"""

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"✅ 面板已生成: {OUTPUT_PATH}", flush=True)
    print(f"   数据日期: {today_str}", flush=True)
    print(f"   广告主数: {len(by_advertiser)}", flush=True)
    print(f"   产品数: {len(by_product)}", flush=True)
    print(f"   素材数: {len(top_materials)}", flush=True)


if __name__ == "__main__":
    build()
