#!/usr/bin/env python3.13
"""
Mobgi 每日数据采集 + 面板生成
由 cron 定时调用
"""
import asyncio, os, sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
COLLECT_SCRIPT = os.path.join(SCRIPT_DIR, "collect.py")
BUILD_SCRIPT = os.path.join(SCRIPT_DIR, "build_dashboard.py")
DEPLOY_PATH = "/www/wwwroot/mobgi.skykiah.eu.org/dashboard.html"


async def main():
    import subprocess

    print("=" * 50, flush=True)
    print(f"🚀 Mobgi 数据采集 + 面板生成", flush=True)
    print(f"📅 {os.popen('date').read().strip()}", flush=True)
    print("=" * 50, flush=True)

    # Step 1: 采集昨日数据
    print("\n[1/3] 采集数据...", flush=True)
    r = subprocess.run(
        [sys.executable, COLLECT_SCRIPT],
        capture_output=True, text=True, timeout=300
    )
    print(r.stdout, flush=True)
    if r.returncode != 0:
        print(f"⚠️ 采集返回码: {r.returncode}", flush=True)
        print(r.stderr[:500] if r.stderr else "", flush=True)

    # Step 2: 生成面板
    print("\n[2/3] 生成面板...", flush=True)
    r = subprocess.run(
        [sys.executable, BUILD_SCRIPT],
        capture_output=True, text=True, timeout=30
    )
    print(r.stdout, flush=True)

    # Step 3: 部署
    print("\n[3/3] 部署...", flush=True)
    dashboard_path = os.path.join(PROJECT_DIR, "dashboard.html")
    if os.path.exists(dashboard_path):
        import shutil
        shutil.copy2(dashboard_path, DEPLOY_PATH)
        print(f"  ✅ 已部署到 {DEPLOY_PATH}", flush=True)
    else:
        print(f"  ❌ dashboard.html 不存在", flush=True)

    print("\n✅ 全部完成", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
