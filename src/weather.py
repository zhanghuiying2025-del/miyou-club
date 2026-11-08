"""Weather forecast adapters with human-friendly planning advice."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import re
from typing import Any

import httpx

from src.models import WeatherForecast, WeatherForecastDay

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_SECONDS = 8

WEATHER_CODES = {
    0: "晴", 1: "晴间多云", 2: "多云", 3: "阴",
    45: "雾", 48: "冻雾",
    51: "小毛毛雨", 53: "毛毛雨", 55: "较强毛毛雨",
    56: "冻毛毛雨", 57: "较强冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "强冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "小阵雨", 81: "阵雨", 82: "强阵雨",
    85: "小阵雪", 86: "强阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "强雷暴伴冰雹",
}
RAIN_CODES = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99}
SNOW_CODES = {71, 73, 75, 77, 85, 86}
STORM_CODES = {95, 96, 99}


class WeatherError(RuntimeError):
    """A user-facing weather service failure."""


def _get_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        response = httpx.get(url, params=params, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise WeatherError("天气服务暂时不可用，请稍后刷新。") from exc


def _clean_place(value: str) -> str:
    value = re.sub(r"(近郊|周边|市区|市内|城区)$", "", value.strip())
    return value.strip() or value.strip()


def _location_query(destination: str, city: str | None) -> list[str]:
    candidates = [destination, city or ""]
    for value in (destination, city or ""):
        cleaned = _clean_place(value)
        if cleaned and cleaned != value:
            candidates.append(cleaned)
    return list(dict.fromkeys(x.strip() for x in candidates if x.strip()))


def _geocode(place: str) -> tuple[float, float, str]:
    payload = _get_json(GEOCODING_URL, {
        "name": place, "count": 1, "language": "zh", "format": "json",
    })
    results = payload.get("results") or []
    if not results:
        raise WeatherError(f"暂时找不到“{place}”的天气位置，请换一种地点写法。")
    item = results[0]
    label = "、".join(str(item.get(key, "")).strip() for key in ("name", "admin1", "country") if item.get(key))
    return float(item["latitude"]), float(item["longitude"]), label or place


def _first(values: list[Any], index: int) -> Any:
    return values[index] if index < len(values) else None


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def advice_for(*, code: int, temp_min: float | None, temp_max: float | None,
               precipitation_probability: float | None, wind_speed: float | None) -> tuple[list[str], list[str], str]:
    clothing: list[str] = []
    tips: list[str] = []
    risk = "low"

    high = temp_max if temp_max is not None else temp_min
    low = temp_min if temp_min is not None else temp_max
    if high is not None and high >= 30:
        clothing.append("穿透气、速干的夏装，优先浅色衣物")
        tips.append("高温时段减少暴晒，准备遮阳帽、防晒用品和充足饮水")
        risk = "medium"
    elif high is not None and high >= 22:
        clothing.append("穿轻薄透气的长袖或短袖，带一件防晒薄外套")
    elif high is not None and high >= 15:
        clothing.append("穿长袖上衣或薄外套，方便早晚增减")
    elif high is not None and high >= 8:
        clothing.append("穿毛衣搭配防风外套，早晚注意保暖")
    elif high is not None:
        clothing.append("穿保暖内搭、厚外套或羽绒服，注意手脚防寒")
        risk = "medium"
    if low is not None and high is not None and high - low >= 10:
        clothing.append("昼夜温差较大，建议叠穿并随身带一件外套")

    precipitation = precipitation_probability or 0
    if code in RAIN_CODES or precipitation >= 50:
        clothing.append("选择防滑、耐水的鞋子")
        tips.append("随身带折叠伞，预留室内备选并为交通多留时间")
        risk = "medium" if risk == "low" else risk
    if code in SNOW_CODES:
        clothing.append("穿防水防滑鞋并增加保暖层")
        tips.append("雪天放慢交通节奏，优先选择室内或短距离活动")
        risk = "high"
    if code in STORM_CODES:
        tips.append("雷雨时远离空旷地带、树木和高处，必要时立即转为室内")
        risk = "high"
    if code in {45, 48}:
        tips.append("雾天能见度较低，交通时间要放宽并听从现场安排")
        risk = "medium" if risk == "low" else risk
    if (wind_speed or 0) >= 6:
        clothing.append("外层选择防风面料，避免过于宽松的衣物")
        tips.append("风力较大，户外高处、临水和轻型道具活动要谨慎")
        risk = "medium" if risk == "low" else risk
    if code <= 2 and (temp_max or 0) >= 22:
        tips.append("天气较好，户外活动注意补水和适时休息")
    if not tips:
        tips.append("按活动强度准备饮水，听从领队和现场安全提示")
    return clothing, list(dict.fromkeys(tips)), risk


def fetch_weather(destination: str, event_date: date, city: str | None = None,
                  end_date: date | None = None) -> WeatherForecast:
    if event_date < date.today():
        raise WeatherError("只能查询今天及之后的天气。")
    end_date = end_date or event_date
    if end_date < event_date:
        raise WeatherError("结束日期不能早于出发日期。")
    if (end_date - event_date).days >= 16:
        raise WeatherError("一次最多查询 16 天的天气预报，请分段查看。")
    if end_date > date.today() + timedelta(days=15):
        raise WeatherError("天气预报通常只能查询未来16天，请临近出发时再次查看。")
    geocode_error: WeatherError | None = None
    for place in _location_query(destination, city):
        try:
            latitude, longitude, label = _geocode(place)
            break
        except WeatherError as exc:
            geocode_error = exc
    else:
        raise geocode_error or WeatherError("请先填写可识别的活动地点。")

    payload = _get_json(FORECAST_URL, {
        "latitude": latitude,
        "longitude": longitude,
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max",
        "timezone": "auto",
        "wind_speed_unit": "ms",
        "start_date": event_date.isoformat(),
        "end_date": end_date.isoformat(),
    })
    daily = payload.get("daily") or {}
    times = daily.get("time") or []
    wanted = [(event_date.toordinal() + offset) for offset in range((end_date - event_date).days + 1)]
    wanted_dates = [date.fromordinal(value).isoformat() for value in wanted]
    if any(day not in times for day in wanted_dates):
        raise WeatherError("活动日期超出可查询的天气预报范围，请临近出发时再次查看。")
    days: list[WeatherForecastDay] = []
    clothing: list[str] = []
    tips: list[str] = []
    risk_order = {"low": 0, "medium": 1, "high": 2}
    overall_risk = "low"
    for target in wanted_dates:
        index = times.index(target)
        code = int(_first(daily.get("weather_code") or [], index) or 0)
        temp_min = _number(_first(daily.get("temperature_2m_min") or [], index))
        temp_max = _number(_first(daily.get("temperature_2m_max") or [], index))
        precipitation = _number(_first(daily.get("precipitation_probability_max") or [], index))
        wind = _number(_first(daily.get("wind_speed_10m_max") or [], index))
        day_clothing, day_tips, day_risk = advice_for(
            code=code, temp_min=temp_min, temp_max=temp_max,
            precipitation_probability=precipitation, wind_speed=wind,
        )
        clothing.extend(day_clothing)
        tips.extend(day_tips)
        if risk_order[day_risk] > risk_order[overall_risk]:
            overall_risk = day_risk
        days.append(WeatherForecastDay(
            date=target, condition=WEATHER_CODES.get(code, "天气变化中"), weather_code=code,
            temperature_min_c=temp_min, temperature_max_c=temp_max,
            precipitation_probability=precipitation, wind_speed_max=wind, risk_level=day_risk,
        ))
    return WeatherForecast(
        location=label,
        destination=destination.strip() or city or label,
        event_date=event_date.isoformat(),
        end_date=end_date.isoformat(),
        days=days,
        condition="；".join(dict.fromkeys(day.condition for day in days)),
        weather_code=days[0].weather_code,
        temperature_min_c=min((day.temperature_min_c for day in days if day.temperature_min_c is not None), default=None),
        temperature_max_c=max((day.temperature_max_c for day in days if day.temperature_max_c is not None), default=None),
        precipitation_probability=max((day.precipitation_probability for day in days if day.precipitation_probability is not None), default=None),
        wind_speed_max=max((day.wind_speed_max for day in days if day.wind_speed_max is not None), default=None),
        clothing_advice=list(dict.fromkeys(clothing)),
        tips=list(dict.fromkeys(tips)),
        risk_level=overall_risk,
        fetched_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        source="Open-Meteo",
    )
