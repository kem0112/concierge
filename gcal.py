"""Google カレンダーの予定（「iCal 形式の非公開URL」を読むだけ。OAuth 不要・読み取り専用）。

env の CONCIERGE_ICAL_URLS に URL を書く（複数のカレンダーは空白で区切る）。未設定なら何もしない。
URL は合鍵と同じなので、env 以外（コード・ログ・Discord）には出さない。
"""
import os
import re
from datetime import date, datetime, time, timedelta

import requests

from nc import JST

URLS = os.environ.get("CONCIERGE_ICAL_URLS", "").split()


# バイトのシフト: タイトルが数字だけの予定。終日で入っていたときはこの表で時間を決める（開始時, 勤務時間）
SHIFT_CODES = {"13": (13, 9), "17": (17, 5), "22": (22, 10)}
SHIFT_RE = re.compile(r"^\d{1,2}$")


def enabled():
    return bool(URLS)


def _together(ev):
    """予定のメモ欄の「一緒: …」の行（シフト取り込みのスクリプトが書く）"""
    m = re.search(r"^一緒: *(.+)$", str(ev.get("DESCRIPTION", "")), re.M)
    return m.group(1).strip() if m and m.group(1).strip() != "なし" else None


def _span(ev, title):
    """シフトの (開始, 終了)。分からなければ None"""
    s = ev.get("DTSTART").dt
    e = ev.get("DTEND").dt if ev.get("DTEND") else None
    if isinstance(s, datetime):
        s = s.astimezone(JST) if s.tzinfo else s.replace(tzinfo=JST)
        e = (e.astimezone(JST) if e.tzinfo else e.replace(tzinfo=JST)) if isinstance(e, datetime) else None
        if e is None and title in SHIFT_CODES:
            e = s + timedelta(hours=SHIFT_CODES[title][1])
        return (s, e) if e else None
    if title in SHIFT_CODES:   # 終日で入っている場合
        h, n = SHIFT_CODES[title]
        s = datetime.combine(s, time(h), tzinfo=JST)
        return s, s + timedelta(hours=n)
    return None


def shifts_on(day):
    """day に始まるバイトのシフト [(開始, 終了, 一緒の人 or None)]"""
    import recurring_ical_events
    if not enabled():
        return []
    start = datetime.combine(day, time(0), tzinfo=JST)
    out = []
    for url in URLS:
        for ev in recurring_ical_events.of(_fetch(url)).between(start, start + timedelta(days=1)):
            title = str(ev.get("SUMMARY", "")).strip()
            if not SHIFT_RE.match(title) or str(ev.get("STATUS", "")).upper() == "CANCELLED":
                continue
            span = _span(ev, title)
            if span and span[0].date() == day:
                out.append((span[0], span[1], _together(ev)))
    return sorted(out)


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
            if SHIFT_RE.match(title):   # バイトのシフト
                span = _span(ev, title)
                with_ = _together(ev)
                if span:
                    s, e = span
                    when = (f"{s:%H:%M}〜" if s.date() == day else "（前日から）〜") + (
                        f"{e:%H:%M}" if e.date() == day or s.date() < day else f"翌{e:%H:%M}")
                    out.append(((1, s.time() if s.date() == day else time(0)),
                                f"{when} バイト" + (f"（一緒: {with_}）" if with_ and s.date() == day else "")))
                    continue
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
