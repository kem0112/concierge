"""モーニング/イブニングブリーフ（1日の総合ブリーフ）。材料を集めて Codex に1通の読み物にしてもらう。
メールは秘書が仕分けたときの1行要約を渡し、新着全体を文章でまとめてもらう（生の本文は渡さない）。
天気の行は Python が作り、そのまま載せてもらう。
Codex が失敗した日も、材料をそのまま並べた版を返す（届かない日を作らない）。"""
import re
from collections import Counter

import secretary as sec
from ai import run_codex, load_prompt
from nc import JST, WEEKDAYS, log
from recall_data import open_tasks_with_due, tasks_by_window, yesterday_summary, random_wish
from weather import weather_text
from gcal import calendar_text


def _safe(fn, *a, default=None):
    try:
        return fn(*a)
    except Exception as e:  # noqa: BLE001
        log(f"NG material {getattr(fn, '__name__', fn)}: {e!r}")
        return default


def _lst(xs):
    return "\n".join(f"- {x}" for x in xs) if xs else "- なし"


def _cal_section(title, day):
    """カレンダー未設定なら空（見出しごと出さない）。取得に失敗したらそう書く"""
    from gcal import enabled
    if not enabled():
        return ""
    return f"## {title}\n{_safe(calendar_text, day) or '（取得できず）'}\n\n"


MAIL_MATERIAL_MAX = 60   # まとめの材料に渡すメールの上限（多すぎるときは新しい順に）


def _sender(r):
    return r["from"].split("<")[0].strip().strip('"')[:24] or r["from"][:24]


def mail_materials():
    """ブリーフのメール欄の材料。新着全体を AI が文章でまとめる元（1通ずつは並べない）と、⭐ の件数"""
    st = sec.load_state()
    new = sorted(sec.since_last_brief(st), key=lambda r: r.get("date") or "")
    ads = [r for r in new if r["category"] == "広告・通知"]
    body = [r for r in new if r["category"] != "広告・通知"][-MAIL_MATERIAL_MAX:]
    lines = [f"新着（前回のブリーフ以降）: {len(new)}通（うち広告・通知 {len(ads)}通）"]
    for r in body:
        star = "⭐ " if r.get("star") else ""
        kind = "就活サイト" if r["category"] == "就活サイト" else r["account"]
        lines.append(f"- {star}[{kind}] {_sender(r)}：{r.get('summary') or r['subject'][:60]}")
    if ads:
        senders = Counter(_sender(r)[:16] for r in ads).most_common(4)
        lines.append("広告・通知の主な差出人: " + "、".join(f"{n}({c})" for n, c in senders))
    todo = _safe(sec.starred_items, default=None)
    if todo is None:
        lines.append("\n⭐（スター付き・未対応）: （確認できませんでした）")
    else:
        lines.append(f"\n⭐（スター付き・未対応）: {len(todo)}件。中身はブリーフの最後に一覧で付く")
    return "\n".join(lines)


def _shift_materials(day):
    """その日のバイト（カレンダーの数字だけの予定）。天気と予定欄で使う"""
    from gcal import shifts_on
    return _safe(shifts_on, day, default=[]) or []


def _tonight(today):
    """イブニング用: 今日このあと始まるバイト（夜勤など）の南区の雨。なければ空"""
    from datetime import datetime
    from weather import shift_lines
    now = datetime.now(JST)
    later = [(s, e) for s, e, _ in _shift_materials(today) if s > now]
    if not later:
        return ""
    return f"## 今夜のバイトの天気（この行はそのまま載せる）\n{chr(10).join(_safe(shift_lines, later, default=[]) or ['（取得できず）'])}\n\n"


def _weather(day):
    shifts = _shift_materials(day)
    return _safe(weather_text, day, [(s, e) for s, e, _ in shifts]) or "（取得できず）"


def build_evening(today):
    """イブニングブリーフ（夕方）: 朝以降のメール・片付いていないこと・明日の準備"""
    from datetime import timedelta
    tomorrow = today + timedelta(days=1)
    tasks = tasks_by_window(today, _safe(open_tasks_with_due, default=[]))
    materials = (
        f"## 日付\n{today:%Y-%m-%d}（{WEEKDAYS[today.weekday()]}）\n\n"
        f"## 朝以降に届いたメール（まとめの材料）\n{_safe(mail_materials) or '（取得できず）'}\n\n"
        f"## 期日を過ぎたタスク\n{_lst(tasks['overdue'])}\n\n## 今日が期日のタスク\n{_lst(tasks['today'])}\n\n"
        f"## 明日が期日のタスク\n{_lst(tasks['tomorrow'])}\n\n"
        f"{_tonight(today)}"
        f"## 明日の天気（この行はそのまま載せる）\n{_weather(tomorrow)}\n\n"
        f"{_cal_section('明日の予定（Googleカレンダー）', tomorrow)}"
    )
    text = None
    try:
        text = re.sub(r"^```(?:markdown|md)?\s*\n|\n```\s*$", "",
                      run_codex(load_prompt("evening.md").replace("{{MATERIALS}}", materials), timeout=400,
                                profile="main", task="evening").strip())
    except Exception as e:  # noqa: BLE001
        log(f"NG evening codex: {e!r}")
    if not text:
        text = "🌆 **おつかれさまです**（AIのまとめは失敗したので材料だけ）\n\n" + materials
    _safe(sec.mark_briefed)
    return text


def build_brief(today):
    tasks = tasks_by_window(today, _safe(open_tasks_with_due, default=[]))
    materials = (
        f"## 日付\n{today:%Y-%m-%d}（{WEEKDAYS[today.weekday()]}）\n\n"
        f"## 天気（この行はそのまま載せる）\n{_weather(today)}\n\n"
        f"{_cal_section('今日の予定（Googleカレンダー）', today)}"
        f"## 期日を過ぎたタスク\n{_lst(tasks['overdue'])}\n\n## 今日が期日のタスク\n{_lst(tasks['today'])}\n\n"
        f"## 明日が期日のタスク\n{_lst(tasks['tomorrow'])}\n\n## 今週中（2〜7日後）が期日のタスク\n{_lst(tasks['week'])}\n\n"
        f"## 前回のブリーフ以降に届いたメール（まとめの材料）\n{_safe(mail_materials) or '（取得できず）'}\n\n"
        f"## 昨日の日記の要約\n{_safe(yesterday_summary, today) or '（昨日の日記はまだありません）'}\n\n"
        f"## アイデアから1つ\n- {_safe(random_wish) or '（まだありません）'}\n"
    )
    text = None
    try:
        text = re.sub(r"^```(?:markdown|md)?\s*\n|\n```\s*$", "",
                      run_codex(load_prompt("brief.md").replace("{{MATERIALS}}", materials), timeout=400,
                                profile="main", task="brief").strip())
    except Exception as e:  # noqa: BLE001
        log(f"NG brief codex: {e!r}")
    if not text:
        text = "☀️ **おはようございます**（AIのまとめは失敗したので材料だけ）\n\n" + materials
    _safe(sec.mark_briefed)
    return text


def mail_list(hours=24):
    """!mail: 直近 hours 時間に仕分けたメールの一覧（Codex は使わない）"""
    from datetime import datetime, timedelta
    from nc import JST
    since = (datetime.now(JST) - timedelta(hours=hours)).isoformat()
    recs = sorted([r for r in sec.load_state()["records"].values() if r.get("processed_at", "") > since],
                  key=lambda r: list(sec.SHELF_ICON).index(r["category"]))
    if not recs:
        return f"📭 直近{hours}時間に仕分けたメールはありません"
    head = f"📬 直近{hours}時間のメール {len(recs)}通"
    ads = [r for r in recs if r["category"] == "広告・通知"]
    sites = [r for r in recs if r["category"] == "就活サイト"]
    body = [sec.line(r) for r in recs if r["category"] not in ("広告・通知", "就活サイト")]
    if ads:
        body.append(f"🗑 広告・通知 {len(ads)}通")
    if sites:
        body.append(f"💼 就活サイト {len(sites)}通")
    return head + "\n" + "\n".join(body)
