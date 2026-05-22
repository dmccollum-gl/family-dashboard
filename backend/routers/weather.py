from fastapi import APIRouter, HTTPException
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json, httpx, time

router = APIRouter()

CONFIG_PATH = Path(__file__).parent.parent / "dashboard_config.json"

# 5-minute in-memory cache — one combined Open-Meteo call serves both endpoints.
_cache: dict = {"data": None, "ts": 0.0, "ttl": 300.0}

# WMO weather code → OWM-style icon code (always daytime variant).
_WMO_ICON: dict[int, str] = {
    0: "01d", 1: "02d", 2: "03d", 3: "04d",
    45: "50d", 48: "50d",
    51: "09d", 53: "09d", 55: "09d", 56: "09d", 57: "09d",
    61: "10d", 63: "10d", 65: "10d", 66: "10d", 67: "10d",
    71: "13d", 73: "13d", 75: "13d", 77: "13d",
    80: "09d", 81: "09d", 82: "09d",
    85: "13d", 86: "13d",
    95: "11d", 96: "11d", 99: "11d",
}

_WMO_DESC: dict[int, str] = {
    0: "Clear Sky",       1: "Mainly Clear",         2: "Partly Cloudy",    3: "Overcast",
    45: "Fog",            48: "Icy Fog",
    51: "Light Drizzle",  53: "Drizzle",              55: "Heavy Drizzle",
    56: "Freezing Drizzle", 57: "Heavy Freezing Drizzle",
    61: "Light Rain",     63: "Rain",                 65: "Heavy Rain",
    66: "Freezing Rain",  67: "Heavy Freezing Rain",
    71: "Light Snow",     73: "Snow",                 75: "Heavy Snow",      77: "Snow Grains",
    80: "Showers",        81: "Heavy Showers",        82: "Violent Showers",
    85: "Snow Showers",   86: "Heavy Snow Showers",
    95: "Thunderstorm",   96: "Thunderstorm w/ Hail", 99: "Severe Thunderstorm",
}


def _read_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    return json.loads(CONFIG_PATH.read_text())


def _write_config(patch: dict) -> None:
    cfg = _read_config()
    cfg.update(patch)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


async def _geocode(address: str) -> tuple[float, float]:
    """Return (lat, lon) via Open-Meteo geocoding; result cached in config."""
    cfg = _read_config()
    if (cfg.get("_geo_for") == address
            and cfg.get("_geo_lat") is not None
            and cfg.get("_geo_lon") is not None):
        return float(cfg["_geo_lat"]), float(cfg["_geo_lon"])

    async with httpx.AsyncClient(timeout=10, verify=False) as client:
        res = await client.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": address, "count": "1", "language": "en", "format": "json"},
        )
    results = res.json().get("results") if res.status_code == 200 else None
    if not results:
        raise HTTPException(status_code=404, detail=f"Could not geocode: {address!r}")

    lat, lon = float(results[0]["latitude"]), float(results[0]["longitude"])
    _write_config({"_geo_lat": lat, "_geo_lon": lon, "_geo_for": address})
    return lat, lon


def _iso_to_ts(s: str, utc_offset_sec: int) -> int:
    """Convert an Open-Meteo naive ISO datetime string to a UTC Unix timestamp."""
    try:
        tz  = timezone(timedelta(seconds=utc_offset_sec))
        return int(datetime.fromisoformat(s).replace(tzinfo=tz).timestamp())
    except Exception:
        return 0


async def _fetch_om(cfg: dict) -> dict:
    """Single Open-Meteo call for current conditions + 6-day daily forecast."""
    now = time.monotonic()
    if _cache["data"] and now - _cache["ts"] < _cache["ttl"]:
        return _cache["data"]

    address = cfg.get("owm_location", "").strip()
    if not address:
        raise HTTPException(status_code=404, detail="Weather location not configured")

    lat, lon = await _geocode(address)
    units    = cfg.get("owm_units", "imperial")
    temp_u   = "fahrenheit" if units == "imperial" else "celsius"
    wind_u   = "mph"        if units == "imperial" else "kmh"

    async with httpx.AsyncClient(timeout=15, verify=False) as client:
        res = await client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude":         lat,
                "longitude":        lon,
                "current":          ",".join([
                    "temperature_2m", "relative_humidity_2m",
                    "apparent_temperature", "weather_code", "wind_speed_10m",
                ]),
                "daily":            ",".join([
                    "temperature_2m_max", "temperature_2m_min",
                    "weather_code", "sunrise", "sunset",
                ]),
                "temperature_unit": temp_u,
                "wind_speed_unit":  wind_u,
                "timezone":         "auto",
                "forecast_days":    "6",
            },
        )
    if res.status_code != 200:
        raise HTTPException(status_code=502, detail="Open-Meteo API error")

    data = res.json()
    _cache["data"] = data
    _cache["ts"]   = now
    return data


@router.get("/current")
async def get_current_weather():
    cfg  = _read_config()
    data = await _fetch_om(cfg)

    cur      = data["current"]
    daily    = data["daily"]
    units    = cfg.get("owm_units", "imperial")
    unit_sym = "°F" if units == "imperial" else "°C"
    wind_lbl = "mph" if units == "imperial" else "km/h"
    tz_off   = data.get("utc_offset_seconds", 0)
    code     = int(cur.get("weather_code", 0))

    sr = _iso_to_ts(daily["sunrise"][0], tz_off) if daily.get("sunrise") else 0
    ss = _iso_to_ts(daily["sunset"][0],  tz_off) if daily.get("sunset")  else 0

    hi = round(daily["temperature_2m_max"][0]) if daily.get("temperature_2m_max") else ""
    lo = round(daily["temperature_2m_min"][0]) if daily.get("temperature_2m_min") else ""

    return {
        "temp":           round(cur["temperature_2m"]),
        "feels_like":     round(cur["apparent_temperature"]),
        "humidity":       int(cur["relative_humidity_2m"]),
        "description":    _WMO_DESC.get(code, "Unknown"),
        "icon":           _WMO_ICON.get(code, "01d"),
        "location_label": cfg.get("owm_location", ""),
        "unit_symbol":    unit_sym,
        "wind_speed":     round(cur["wind_speed_10m"]),
        "wind_unit":      wind_lbl,
        "sunrise":        sr,
        "sunset":         ss,
        "temp_min":       lo,
        "temp_max":       hi,
    }


@router.get("/forecast")
async def get_forecast():
    cfg  = _read_config()
    data = await _fetch_om(cfg)

    units    = cfg.get("owm_units", "imperial")
    unit_sym = "°F" if units == "imperial" else "°C"
    daily    = data["daily"]

    dates  = daily.get("time", [])
    highs  = daily.get("temperature_2m_max", [])
    lows   = daily.get("temperature_2m_min", [])
    codes  = daily.get("weather_code", [])

    result = []
    for i, day_date in enumerate(dates[:6]):
        code = int(codes[i]) if i < len(codes) else 0
        result.append({
            "date":        day_date,
            "high":        round(highs[i]) if i < len(highs) else "",
            "low":         round(lows[i])  if i < len(lows)  else "",
            "icon":        _WMO_ICON.get(code, "01d"),
            "description": _WMO_DESC.get(code, "Unknown"),
        })

    return {"days": result, "unit_symbol": unit_sym}
