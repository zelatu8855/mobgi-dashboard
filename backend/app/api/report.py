from datetime import date
from fastapi import APIRouter, Query
from app.services.mobgi_client import get_material_report

router = APIRouter(prefix="/api/report", tags=["report"])

@router.get("/materials")
async def materials_report(
    start_date: str = Query(default=None, description="开始日期 YYYY-MM-DD"),
    end_date: str = Query(default=None, description="结束日期 YYYY-MM-DD"),
    sort_field: str = Query(default="cost", description="排序字段"),
    sort_direction: str = Query(default="desc", description="排序方向 asc/desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """素材消耗排行榜"""
    today = date.today().strftime("%Y-%m-%d")
    if not start_date:
        start_date = today
    if not end_date:
        end_date = today

    result = await get_material_report(
        start_date=start_date,
        end_date=end_date,
        sort_field=sort_field,
        sort_direction=sort_direction,
        page=page,
        page_size=page_size,
    )

    if result.get("code") != 0:
        return {"code": result.get("code", 1), "msg": result.get("message", "error"), "data": {"list": [], "page_info": {}}}

    return {"code": 0, "msg": "ok", "data": result["data"]}
