"""京都の今日の天気（Open-Meteo。APIキー不要）。"""
import requests

LAT, LON = 35.03, 135.78  # 京都大学（吉田キャンパス）のあたり
WMO = {0: "快晴", 1: "晴れ", 2: "晴れ時々くもり", 3: "くもり", 45: "霧", 48: "霧",
       51: "弱い霧雨", 53: "霧雨", 55: "強い霧雨", 61: "小雨", 63: "雨", 65: "強い雨",
       66: "着氷性の雨", 67: "強い着氷性の雨", 71: "小雪", 73: "雪", 75: "大雪", 77: "霧雪",
       80: "にわか雨", 81: "強いにわか雨", 82: "激しいにわか雨", 85: "にわか雪", 86: "強いにわか雪",
       95: "雷雨", 96: "雹を伴う雷雨", 99: "激しい雹を伴う雷雨"}


def weather_text(today):
    r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=15, params={
        "latitude": LAT, "longitude": LON, "timezone": "Asia/Tokyo",
        "start_date": today.isoformat(), "end_date": today.isoformat(),
        "daily": "weather_code,temperature_2m_max,temperature_2m_min",
        "hourly": "precipitation_probability",
    })
    r.raise_for_status()
    j = r.json()
    d = j["daily"]
    hours, probs = j["hourly"]["time"], j["hourly"]["precipitation_probability"]

    def block(h0, h1):
        vals = [p for t, p in zip(hours, probs) if p is not None and h0 <= int(t[11:13]) < h1]
        return max(vals) if vals else None

    return (f"京都（大学付近）: {WMO.get(d['weather_code'][0], '不明')}、"
            f"最高{d['temperature_2m_max'][0]:.0f}℃ / 最低{d['temperature_2m_min'][0]:.0f}℃、"
            f"降水確率 朝(6-12時){block(6, 12)}% 昼(12-18時){block(12, 18)}% 夜(18-24時){block(18, 24)}%")
