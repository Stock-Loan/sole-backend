from fastapi import APIRouter

from app.resources.countries import COUNTRIES, SUBDIVISIONS
from app.resources.timezones import TIMEZONES

router = APIRouter(prefix="/meta", tags=["meta"])


@router.get("/timezones", summary="List supported timezones")
async def list_timezones() -> dict:
    return {"timezones": TIMEZONES}


@router.get("/countries", summary="List supported countries")
async def list_countries() -> dict:
    return {"countries": COUNTRIES}


@router.get("/countries/{country_code}/subdivisions", summary="List subdivisions/states for a country")
async def list_subdivisions(country_code: str) -> dict:
    country_code = country_code.upper()
    subdivisions = SUBDIVISIONS.get(country_code, [])
    return {"country": country_code, "subdivisions": subdivisions}
