"""Google カレンダーの予定（「iCal 形式の非公開URL」を読むだけ。OAuth 不要・読み取り専用）。

env の CONCIERGE_ICAL_URLS に URL を書く（複数のカレンダーは空白で区切る）。未設定なら何もしない。
URL は合鍵と同じなので、env 以外（コード・ログ・Discord）には出さない。
"""
import os
from datetime import date, datetime, time, timedelta

import requests

from nc import JST

URLS = os.environ.get("CONCIERGE_ICAL_URLS", "").split()


def enabled():
    return bool(URLS)


def _fetch(url):
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
    except requests.RequestException as e:
        # 例外の文面に URL が入るので、そのまま出さない
        raise RuntimeError(f"カレンダーを取得できませんでした（{type(e).__name__}）") from None
    import icalendar   # ここで読む（未インストールでも bot 本体は起動できるように）
    return icalendar.Calendar.from_ical(r.content)


def events_on(day):
    """day（date）の予定を [(並べ替え用, 表示行)] で返す。終日→時刻順"""
    import recurring_ical_events
    start = datetime.combine(day, time(0), tzinfo=JST)
    end = start + timedelta(days=1)
    out = []
    for url in URLS:
        cal = _fetch(url)
        for ev in recurring_ical_events.of(cal).between(start, end):
            if str(ev.get("STATUS", "")).upper() == "CANCELLED":
                continue
            title = str(ev.get("SUMMARY", "（無題）")).strip()
            loc = str(ev.get("LOCATION", "")).strip()
            s = ev.get("DTSTART").dt
            e = ev.get("DTEND").dt if ev.get("DTEND") else None
            if isinstance(s, datetime):
                s = s.astimezone(JST) if s.tzinfo else s.replace(tzinfo=JST)
                e = (e.astimezone(JST) if e.tzinfo else e.replace(tzinfo=JST)) if isinstance(e, datetime) else None
                when = f"{s:%H:%M}" + (f"〜{e:%H:%M}" if e else "")
                if s.date() < day:
                    when = "（前日から）〜" + (f"{e:%H:%M}" if e else "")
                key = (1, s.time() if s.date() == day else time(0))
            else:   # 終日の予定（date）
                when, key = "終日", (0, time(0))
            out.append((key, f"{when} {title}" + (f"（{loc[:30]}）" if loc else "")))
    return sorted(out)


def calendar_text(day):
    """ブリーフの材料用。未設定なら None"""
    if not enabled():
        return None
    lines = [line for _, line in events_on(day)]
    return "\n".join(f"- {ln}" for ln in lines) if lines else "- 予定なし"
