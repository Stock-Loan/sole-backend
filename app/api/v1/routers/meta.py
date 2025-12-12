from fastapi import APIRouter

from app.resources.timezones import TIMEZONES

router = APIRouter(prefix="/meta", tags=["meta"])


@router.get("/timezones", summary="List supported timezones")
async def list_timezones() -> dict:
    return {"timezones": TIMEZONES}
