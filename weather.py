"""天気（Open-Meteo。APIキー不要。日本では気象庁のモデルが使われる）。

雨が降るかどうかに絞って、ブリーフにそのまま載せる行を作る（AI には書き換えさせない）。
  - 左京区: その日の 6〜24 時
  - バイトの日: 勤務時間（前後1時間の行き帰りを含む）の南区
「雨に注意」は、1時間ごとの降水確率が RAIN_PROB % 以上か、降水量が RAIN_MM mm 以上の時間。
"""
from datetime import datetime, timedelta

import requests

from nc import JST

PLACES = {"左京区": (35.04, 135.78), "南区": (34.98, 135.76)}   # 南区はバイト先（京都駅の南側）のあたり
RAIN_PROB = 30
RAIN_MM = 0.3
WMO = {0: "快晴", 1: "晴れ", 2: "晴れ時々くもり", 3: "くもり", 45: "霧", 48: "霧",
       51: "弱い霧雨", 53: "霧雨", 55: "強い霧雨", 61: "小雨", 63: "雨", 65: "強い雨",
       66: "着氷性の雨", 67: "強い着氷性の雨", 71: "小雪", 73: "雪", 75: "大雪", 77: "霧雪",
       80: "にわか雨", 81: "強いにわか雨", 82: "激しいにわか雨", 85: "にわか雪", 86: "強いにわか雪",
       95: "雷雨", 96: "雹を伴う雷雨", 99: "激しい雹を伴う雷雨"}


def _forecast(place, start_day, days):
    lat, lon = PLACES[place]
    r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=15, params={
        "latitude": lat, "longitude": lon, "timezone": "Asia/Tokyo",
        "start_date": start_day.isoformat(), "end_date": (start_day + timedelta(days=days - 1)).isoformat(),
        "daily": "weather_code,temperature_2m_max,temperature_2m_min",
        "hourly": "precipitation_probability,precipitation",
    })
    r.raise_for_status()
    return r.json()


def _hours(j):
    """[(datetime, 降水確率, 降水量)]"""
    h = j["hourly"]
    return [(datetime.fromisoformat(t).replace(tzinfo=JST), p or 0, mm or 0)
            for t, p, mm in zip(h["time"], h["precipitation_probability"], h["precipitation"])]


def _rainy(p, mm):
    return p >= RAIN_PROB or mm >= RAIN_MM


def _ranges(hours):
    """雨に注意の時間を「15時〜19時」のようにまとめる。戻り値: (文字列のリスト, 最大降水確率)"""
    out, cur, top = [], None, 0
    for t, p, mm in hours:
        if _rainy(p, mm):
            top = max(top, p)
            cur = [cur[0], t] if cur else [t, t]
        elif cur:
            out.append(cur)
            cur = None
    if cur:
        out.append(cur)

    def hh(t, base):
        return f"{t.hour}時" if t.date() == base else f"翌{t.hour}時"
    base = hours[0][0].date() if hours else None
    return [f"{hh(a, base)}〜{hh(b + timedelta(hours=1), base)}" for a, b in out], top


def weather_text(day, shifts=()):
    """ブリーフ用の行。shifts: [(開始 datetime, 終了 datetime)]（その日のバイト）"""
    j = _forecast("左京区", day, 2)
    d = j["daily"]
    head = (f"{WMO.get(d['weather_code'][0], '不明')}、最高{d['temperature_2m_max'][0]:.0f}℃ / "
            f"最低{d['temperature_2m_min'][0]:.0f}℃（左京区）")
    start = datetime(day.year, day.month, day.day, 6, tzinfo=JST)
    hours = [h for h in _hours(j) if start <= h[0] < start + timedelta(hours=18)]
    rng, top = _ranges(hours)
    lines = [head, f"☔ {'・'.join(rng)}ごろ雨に注意（左京区、最大{top}%）" if rng else "☀️ 左京区は雨の心配なし"]
    lines += shift_lines(shifts)
    return "\n".join(lines)


def shift_lines(shifts):
    """バイトの勤務時間（前後1時間の行き帰りを含む）の南区の雨"""
    lines = []
    for s, e in shifts:
        js = _forecast("南区", s.date(), 2)
        win = [h for h in _hours(js) if s - timedelta(hours=1) <= h[0] < e + timedelta(hours=1)]
        rng, top = _ranges(win)
        when = f"バイト {s:%H:%M}〜" + (f"翌{e.hour}:{e:%M}" if e.date() > s.date() else f"{e:%H:%M}")
        lines.append(f"☔ {when}の南区も雨に注意：{'・'.join(rng)}ごろ（最大{top}%）。傘があると安心" if rng
                     else f"☀️ {when}の南区は雨の心配なし")
    return lines
