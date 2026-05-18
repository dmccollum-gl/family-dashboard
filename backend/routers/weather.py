from fastapi import APIRouter, HTTPException
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict
import json, httpx

router = APIRouter()

CONFIG_PATH = Path(__file__).parent.parent / "dashboard_config.json"


def _read_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    return json.loads(CONFIG_PATH.read_text())


@router.get("/current")
async def get_current_weather():
    cfg = _read_config()
    api_key  = cfg.get("owm_api_key")
    location = cfg.get("owm_location")
    units    = cfg.get("owm_units", "imperial")
    if not api_key or not location:
        raise HTTPException(status_code=404, detail="Weather not configured")
    async with httpx.AsyncClient(timeout=10) as client:
        res = await client.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"q": location, "appid": api_key, "units": units},
        )
    if res.status_code != 200:
        raise HTTPException(status_code=res.status_code, detail="OWM API error")
    data = res.json()
    unit_symbol = "°F" if units == "imperial" else "°C"
    return {
        "temp":        round(data["main"]["temp"]),
        "feels_like":  round(data["main"]["feels_like"]),
        "humidity":    data["main"]["humidity"],
        "description": data["weather"][0]["description"].title(),
        "icon":        data["weather"][0]["icon"],
        "city":        data["name"],
        "unit_symbol": unit_symbol,
        "wind_speed":  round(data["wind"]["speed"]),
        "wind_unit":   "mph" if units == "imperial" else "m/s",
        "sunrise":     data["sys"]["sunrise"],
        "sunset":      data["sys"]["sunset"],
        "temp_min":    round(data["main"]["temp_min"]),
        "temp_max":    round(data["main"]["temp_max"]),
    }


@router.get("/forecast")
async def get_forecast():
    cfg = _read_config()
    api_key  = cfg.get("owm_api_key")
    location = cfg.get("owm_location")
    units    = cfg.get("owm_units", "imperial")
    if not api_key or not location:
        raise HTTPException(status_code=404, detail="Weather not configured")

    async with httpx.AsyncClient(timeout=10) as client:
        res = await client.get(
            "https://api.openweathermap.org/data/2.5/forecast",
            params={"q": location, "appid": api_key, "units": units, "cnt": 40},
        )
    if res.status_code != 200:
        raise HTTPException(status_code=res.status_code, detail="OWM API error")

    data       = res.json()
    tz_offset  = data.get("city", {}).get("timezone", 0)  # seconds from UTC
    unit_symbol = "°F" if units == "imperial" else "°C"

    days = defaultdict(list)
    for entry in data["list"]:
        local_dt = datetime.fromtimestamp(entry["dt"] + tz_offset, tz=timezone.utc)
        days[local_dt.strftime("%Y-%m-%d")].append(entry)

    result = []
    for day_key in sorted(days.keys())[:5]:
        entries = days[day_key]
        temps   = [e["main"]["temp"] for e in entries]
        # Prefer midday entry for the representative icon
        rep = next(
            (e for e in entries if "12:00:00" in e["dt_txt"] or "15:00:00" in e["dt_txt"]),
            entries[len(entries) // 2],
        )
        result.append({
            "date":        day_key,
            "high":        round(max(temps)),
            "low":         round(min(temps)),
            "icon":        rep["weather"][0]["icon"],
            "description": rep["weather"][0]["description"].title(),
        })

    return {"days": result, "unit_symbol": unit_symbol}
