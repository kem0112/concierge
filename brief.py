"""モーニングブリーフ。材料を集めて Codex に1通の読み物にしてもらう。
メールは秘書がすでに仕分けた結果（棚・要約）を渡す。生の本文は渡さない。
Codex が失敗した日も、材料をそのまま並べた版を返す（届かない日を作らない）。"""
import re
from collections import Counter
from datetime import datetime

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


def mail_materials():
    """ブリーフのメール欄の材料（機械的に作る）"""
    st = sec.load_state()
    new = sec.since_last_brief(st)
    by = {c: [r for r in new if r["category"] == c] for c in sec.SHELF_ICON}
    ads = by["広告・通知"]
    senders = Counter(r["from"].split("<")[0].strip().strip('"')[:16] for r in ads).most_common(4)
    lines = [f"新着（前回のブリーフ以降）: 🔴要対応 {len(by['要対応'])} / 🟡確認推奨 {len(by['確認推奨'])} / "
             f"⚪参考 {len(by['参考'])} / 🗑広告・通知 {len(ads)} / 💼就活サイト {len(by['就活サイト'])}"
             + (f" / ❓未分類 {len(by['未分類'])}" if by["未分類"] else "")]
    # 🔴 は bot がブリーフの最後に一覧（リアクションでスターを外せる）を付けるので、ここでは件数だけ渡す
    lines += [sec.line(r) for r in by["確認推奨"]]
    if ads:
        lines.append("広告・通知の主な差出人: " + "、".join(f"{n}({c})" for n, c in senders)
                     + "（受け皿の Gmail で「アーカイブ候補」ラベル）")
    todo = _safe(sec.starred_items, default=None)
    if todo is None:
        lines.append("\n未対応の🔴要対応（スター付き）: （確認できませんでした）")
    else:
        new_g = {r.get("gmid") for r in new}
        oldest = max(((datetime.now(JST).date() - datetime.fromisoformat(r["date"]).date()).days
                      for r in todo if r.get("date")), default=0)
        lines.append(f"\n未対応の🔴要対応（スター付き）: {len(todo)}件（うち新着 {sum(r.get('gmid') in new_g for r in todo)}件）"
                     + (f"、いちばん古いものは{oldest}日目" if oldest >= 2 else "")
                     + "。中身はブリーフの最後に一覧で付く")
    events = [r for r in st["records"].values() if r.get("event")]
    lines.append("\nメールにあった予定（直近）:")
    lines += [f"- {r['event']['date']} {r['event']['what']}（{r['event']['change']}・{r['account']}）"
              for r in sorted(events, key=lambda r: r["event"]["date"])[-6:]] or ["- なし"]
    return "\n".join(lines)


def build_evening(today):
    """イブニングブリーフ（夕方）: 朝以降のメール・片付いていないこと・明日の準備"""
    from datetime import timedelta
    tomorrow = today + timedelta(days=1)
    tasks = tasks_by_window(today, _safe(open_tasks_with_due, default=[]))
    materials = (
        f"## 日付\n{today:%Y-%m-%d}（{WEEKDAYS[today.weekday()]}）\n\n"
        f"## メール（秘書の仕分け結果）\n{_safe(mail_materials) or '（取得できず）'}\n\n"
        f"## 期日を過ぎたタスク\n{_lst(tasks['overdue'])}\n\n## 今日が期日のタスク\n{_lst(tasks['today'])}\n\n"
        f"## 明日が期日のタスク\n{_lst(tasks['tomorrow'])}\n\n"
        f"## 明日の天気\n{_safe(weather_text, tomorrow) or '（取得できず）'}\n\n"
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
        f"## 天気\n{_safe(weather_text, today) or '（取得できず）'}\n\n"
        f"{_cal_section('今日の予定（Googleカレンダー）', today)}"
        f"## 期日を過ぎたタスク\n{_lst(tasks['overdue'])}\n\n## 今日が期日のタスク\n{_lst(tasks['today'])}\n\n"
        f"## 明日が期日のタスク\n{_lst(tasks['tomorrow'])}\n\n## 今週中（2〜7日後）が期日のタスク\n{_lst(tasks['week'])}\n\n"
        f"## メール（秘書の仕分け結果）\n{_safe(mail_materials) or '（取得できず）'}\n\n"
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
