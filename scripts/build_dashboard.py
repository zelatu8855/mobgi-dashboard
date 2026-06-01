#!/usr/bin/env python3.13
"""
生成 Mobgi 数据可视化面板（自包含 HTML + ECharts）
用法: python3.13 build_dashboard.py
"""
import sqlite3, json, os, sys
from datetime import date, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DB_PATH = os.path.join(PROJECT_DIR, "data", "mobgi.db")
OUTPUT_PATH = os.path.join(PROJECT_DIR, "dashboard.html")


def query(conn, sql, params=()):
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.row_factory = None
    return [dict(r) for r in rows]


def pct_change(old, new):
    """计算百分比变化，返回 (变化值, 百分比, 方向)"""
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

    # ─── 获取最近 30 天数据 ──────────────────────────────────────
    daily = query(conn, """
        SELECT * FROM daily_summary
        WHERE date >= date('now', '-30 days') AND total_cost > 0
        ORDER BY date ASC
    """)

    if not daily:
        print("⚠️ 没有数据，生成空面板", flush=True)
        daily = []

    # 今日/昨日对比
    today_data = daily[-1] if daily else {}
    yesterday_data = daily[-2] if len(daily) >= 2 else {}

    # KPI 对比
    kpis = [
        ("总花费", "total_cost", "¥", 2),
        ("转化数", "total_conversion", "", 0),
        ("展示数", "total_show", "", 0),
        ("点击数", "total_click", "", 0),
        ("CTR", "ctr", "%", 4),
        ("转化率", "convert_rate", "%", 4),
        ("CPM", "cpm", "¥", 4),
        ("CPC", "cpc", "¥", 4),
        ("转化成本", "convert_cost", "¥", 4),
    ]

    kpi_cards = []
    for label, key, unit, decimals in kpis:
        new_val = today_data.get(key, 0) or 0
        old_val = yesterday_data.get(key, 0) or 0
        diff, pct, direction = pct_change(old_val, new_val)
        kpi_cards.append({
            "label": label,
            "value": round(new_val, decimals),
            "unit": unit,
            "diff": diff,
            "pct": pct,
            "direction": direction,
            "decimals": decimals,
        })

    # TOP 20 素材（按花费）
    top_materials = query(conn, """
        SELECT material_name, cost, conversion_num, show_count, click, ctr, convert_rate, convert_cost
        FROM material_cost
        WHERE date = ?
        ORDER BY cost DESC
        LIMIT 20
    """, [today_data.get("date", "")])

    # 散点图数据（消耗 vs 转化）
    scatter_data = query(conn, """
        SELECT material_name, cost, conversion_num, ctr, convert_cost
        FROM material_cost
        WHERE date = ? AND cost > 0
        ORDER BY cost DESC
        LIMIT 100
    """, [today_data.get("date", "")])

    # 余额
    balance = query(conn, "SELECT * FROM balance_snapshot ORDER BY date DESC LIMIT 1")
    balance_val = balance[0]["balance"] if balance else None

    conn.close()

    # ─── 生成 HTML ────────────────────────────────────────────────
    dates = [d["date"] for d in daily]
    cost_series = [d["total_cost"] for d in daily]
    conv_series = [d["total_conversion"] for d in daily]
    show_series = [d["total_show"] for d in daily]
    click_series = [d["total_click"] for d in daily]
    ctr_series = [d.get("ctr", 0) for d in daily]

    scatter_json = json.dumps([[r["cost"], r["conversion_num"], r["ctr"], r["material_name"], r.get("convert_cost", 0)] for r in scatter_data], ensure_ascii=False)
    top_names = json.dumps([r["material_name"][:20] for r in top_materials], ensure_ascii=False)
    top_costs = json.dumps([r["cost"] for r in top_materials])
    top_convs = json.dumps([r["conversion_num"] for r in top_materials])
    top_ctrs = json.dumps([r.get("ctr", 0) for r in top_materials])
    kpi_json = json.dumps(kpi_cards, ensure_ascii=False)

    today_str = today_data.get("date", "无数据")
    yesterday_str = yesterday_data.get("date", "无")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Mobgi 素材数据面板</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: #0a0a0f; color: #e0e0e5; font-family: -apple-system, 'Segoe UI', sans-serif; padding: 20px; }}
  .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }}
  .header h1 {{ font-size: 20px; font-weight: 700; color: #fff; }}
  .header .date {{ font-size: 13px; color: #888; }}
  .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 20px; }}
  .kpi-card {{ background: #141418; border: 1px solid #2a2a35; border-radius: 10px; padding: 14px 16px; }}
  .kpi-label {{ font-size: 12px; color: #888; margin-bottom: 6px; }}
  .kpi-value {{ font-size: 22px; font-weight: 700; color: #fff; }}
  .kpi-value .unit {{ font-size: 14px; color: #aaa; font-weight: 400; }}
  .kpi-diff {{ font-size: 12px; margin-top: 4px; display: flex; align-items: center; gap: 4px; }}
  .kpi-diff.up {{ color: #ef4444; }}
  .kpi-diff.down {{ color: #22c55e; }}
  .kpi-diff.flat {{ color: #666; }}
  .kpi-diff .arrow {{ font-size: 14px; }}
  .charts-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }}
  .chart-box {{ background: #141418; border: 1px solid #2a2a35; border-radius: 10px; padding: 16px; }}
  .chart-box h3 {{ font-size: 14px; font-weight: 600; color: #ccc; margin-bottom: 12px; }}
  .chart {{ width: 100%; height: 300px; }}
  .chart-lg {{ height: 360px; }}
  .full-width {{ grid-column: 1 / -1; }}
  .balance-card {{ background: linear-gradient(135deg, #1e1b4b 0%, #312e81 100%); border: 1px solid #4338ca; border-radius: 10px; padding: 16px 20px; margin-bottom: 20px; display: flex; justify-content: space-between; align-items: center; }}
  .balance-label {{ font-size: 13px; color: #a5b4fc; }}
  .balance-value {{ font-size: 28px; font-weight: 700; color: #fff; }}
  .update-time {{ font-size: 11px; color: #555; text-align: right; margin-top: 16px; }}
  @media (max-width: 768px) {{ .charts-row {{ grid-template-columns: 1fr; }} .kpi-grid {{ grid-template-columns: repeat(2, 1fr); }} }}
</style>
</head>
<body>

<div class="header">
  <h1>📊 Mobgi 素材数据面板</h1>
  <div class="date">数据日期: {today_str} | 对比: {yesterday_str}</div>
</div>

{"<div class='balance-card'><div class='balance-label'>💰 账户余额</div><div class='balance-value'>¥" + f"{balance_val:,.2f}" + "</div></div>" if balance_val else ""}

<div class="kpi-grid" id="kpiGrid"></div>

<div class="charts-row">
  <div class="chart-box">
    <h3>📈 每日消耗趋势</h3>
    <div class="chart" id="trendChart"></div>
  </div>
  <div class="chart-box">
    <h3>📊 每日转化趋势</h3>
    <div class="chart" id="convChart"></div>
  </div>
</div>

<div class="charts-row">
  <div class="chart-box full-width">
    <h3>🏆 素材消耗排行 TOP 20</h3>
    <div class="chart chart-lg" id="rankChart"></div>
  </div>
</div>

<div class="charts-row">
  <div class="chart-box full-width">
    <h3>💡 消耗 vs 转化散点图（TOP 100）</h3>
    <div class="chart chart-lg" id="scatterChart"></div>
  </div>
</div>

<div class="update-time" id="updateTime"></div>

<script>
const dates = {json.dumps(dates)};
const costSeries = {json.dumps(cost_series)};
const convSeries = {json.dumps(conv_series)};
const ctrSeries = {json.dumps(ctr_series)};
const kpis = {kpi_json};
const topNames = {top_names};
const topCosts = {top_costs};
const topConvs = {top_convs};
const topCtrs = {top_ctrs};
const scatterData = {scatter_json};

// ─── KPI 卡片 ─────────────────────────────────────────────────
const kpiGrid = document.getElementById('kpiGrid');
kpiCards = kpis.map(k => {{
  const arrow = k.direction === 'up' ? '↑' : (k.direction === 'down' ? '↓' : '→');
  const sign = k.diff > 0 ? '+' : '';
  const pctStr = k.pct !== null ? ` (${{sign}}${{k.pct}}%)` : '';
  const diffColor = k.direction === 'up' ? '#ef4444' : (k.direction === 'down' ? '#22c55e' : '#666');
  return `
    <div class="kpi-card">
      <div class="kpi-label">${{k.label}}</div>
      <div class="kpi-value">${{k.unit}}${{k.value.toLocaleString(undefined, {{minimumFractionDigits: k.decimals, maximumFractionDigits: k.decimals}})}}</div>
      <div class="kpi-diff ${{k.direction}}">
        <span class="arrow">${{arrow}}</span>
        <span>${{sign}}${{Math.abs(k.diff).toLocaleString()}}${{pctStr}}</span>
      </div>
    </div>
  `;
}});
kpiGrid.innerHTML = kpiCards.join('');

// ─── 消耗趋势 ─────────────────────────────────────────────────
const trendChart = echarts.init(document.getElementById('trendChart'));
trendChart.setOption({{
  backgroundColor: 'transparent',
  tooltip: {{ trigger: 'axis', axisPointer: {{ type: 'cross' }} }},
  grid: {{ left: 50, right: 20, top: 10, bottom: 30 }},
  xAxis: {{ type: 'category', data: dates, axisLine: {{ lineStyle: {{ color: '#333' }} }}, axisLabel: {{ color: '#888', fontSize: 10 }} }},
  yAxis: [
    {{ type: 'value', name: '花费', axisLabel: {{ color: '#888', fontSize: 10, formatter: v => '¥' + (v >= 1000 ? (v/1000).toFixed(0) + 'k' : v) }}, splitLine: {{ lineStyle: {{ color: '#1a1a25' }} }} }},
    {{ type: 'value', name: 'CTR%', axisLabel: {{ color: '#888', fontSize: 10, formatter: v => v + '%' }}, splitLine: {{ show: false }} }}
  ],
  series: [
    {{ name: '花费', type: 'bar', data: costSeries, itemStyle: {{ color: '#6366f1' }}, barMaxWidth: 20 }},
    {{ name: 'CTR', type: 'line', yAxisIndex: 1, data: ctrSeries, itemStyle: {{ color: '#f59e0b' }}, lineStyle: {{ width: 2 }}, symbol: 'circle', symbolSize: 4 }}
  ]
}});

// ─── 转化趋势 ─────────────────────────────────────────────────
const convChart = echarts.init(document.getElementById('convChart'));
convChart.setOption({{
  backgroundColor: 'transparent',
  tooltip: {{ trigger: 'axis' }},
  grid: {{ left: 50, right: 20, top: 10, bottom: 30 }},
  xAxis: {{ type: 'category', data: dates, axisLine: {{ lineStyle: {{ color: '#333' }} }}, axisLabel: {{ color: '#888', fontSize: 10 }} }},
  yAxis: [
    {{ type: 'value', name: '转化数', axisLabel: {{ color: '#888', fontSize: 10 }}, splitLine: {{ lineStyle: {{ color: '#1a1a25' }} }} }},
    {{ type: 'value', name: '点击', axisLabel: {{ color: '#888', fontSize: 10 }}, splitLine: {{ show: false }} }}
  ],
  series: [
    {{ name: '转化数', type: 'bar', data: convSeries, itemStyle: {{ color: '#22c55e' }}, barMaxWidth: 20 }},
    {{ name: '点击', type: 'line', yAxisIndex: 1, data: {json.dumps(click_series)}, itemStyle: {{ color: '#3b82f6' }}, lineStyle: {{ width: 2 }}, symbol: 'circle', symbolSize: 4 }}
  ]
}});

// ─── 素材排行 ─────────────────────────────────────────────────
const rankChart = echarts.init(document.getElementById('rankChart'));
rankChart.setOption({{
  backgroundColor: 'transparent',
  tooltip: {{ trigger: 'axis', axisPointer: {{ type: 'shadow' }} }},
  legend: {{ data: ['花费', '转化数'], textStyle: {{ color: '#888' }}, top: 0 }},
  grid: {{ left: 20, right: 20, top: 30, bottom: 20, containLabel: true }},
  xAxis: {{ type: 'value', axisLabel: {{ color: '#888', fontSize: 10 }}, splitLine: {{ lineStyle: {{ color: '#1a1a25' }} }} }},
  yAxis: {{ type: 'category', data: topNames.reverse(), axisLabel: {{ color: '#ccc', fontSize: 11, width: 140, overflow: 'truncate' }} }},
  series: [
    {{ name: '花费', type: 'bar', data: topCosts.reverse(), itemStyle: {{ color: '#6366f1', borderRadius: [0, 4, 4, 0] }}, barMaxWidth: 16 }},
    {{ name: '转化数', type: 'bar', data: topConvs.reverse(), itemStyle: {{ color: '#22c55e', borderRadius: [0, 4, 4, 0] }}, barMaxWidth: 16 }}
  ]
}});

// ─── 散点图 ───────────────────────────────────────────────────
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
      color: d => {{
        const ctr = d[2];
        if (ctr > 8) return '#22c55e';
        if (ctr > 4) return '#f59e0b';
        return '#ef4444';
      }},
      opacity: 0.8
    }}
  }}]
}});

// 响应式
window.addEventListener('resize', () => {{
  trendChart.resize(); convChart.resize(); rankChart.resize(); scatterChart.resize();
}});

document.getElementById('updateTime').textContent = '更新时间: ' + new().toLocaleString('zh-CN');
</script>
</body>
</html>"""

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"✅ 面板已生成: {OUTPUT_PATH}", flush=True)
    print(f"   数据日期: {today_str}", flush=True)
    print(f"   素材数: {len(top_materials)}", flush=True)
    print(f"   天数: {len(daily)}", flush=True)


if __name__ == "__main__":
    build()
