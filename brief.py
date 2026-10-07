"""モーニングブリーフと、メールのまとめ。材料を集めて Codex に1通の読み物にしてもらう。
Codex が失敗した日も、材料をそのまま並べた版を返す（届かない日を作らない）。"""
import re
from datetime import timedelta

from ai import run_codex, load_prompt
from mail import fetch_recent, mails_text
from nc import WEEKDAYS, log
from recall_data import open_tasks_with_due, tasks_by_window, yesterday_summary, random_wish
from weather import weather_text


def _safe(fn, *a, default=None):
    try:
        return fn(*a)
    except Exception as e:  # noqa: BLE001
        log(f"NG material {fn.__name__}: {e!r}")
        return default


def _lst(xs):
    return "\n".join(f"- {x}" for x in xs) if xs else "- なし"


def _strip_fence(text):
    return re.sub(r"^```(?:markdown|md)?\s*\n|\n```\s*$", "", text.strip())


def build_brief(today, mail_hours=24):
    tasks = tasks_by_window(today, _safe(open_tasks_with_due, default=[]))
    mails = _safe(fetch_recent, mail_hours, default=None)
    materials = (
        f"## 日付\n{today:%Y-%m-%d}（{WEEKDAYS[today.weekday()]}）\n\n"
        f"## 天気\n{_safe(weather_text, today) or '（取得できず）'}\n\n"
        f"## 期日を過ぎたタスク\n{_lst(tasks['overdue'])}\n\n## 今日が期日のタスク\n{_lst(tasks['today'])}\n\n"
        f"## 明日が期日のタスク\n{_lst(tasks['tomorrow'])}\n\n## 今週中（2〜7日後）が期日のタスク\n{_lst(tasks['week'])}\n\n"
        f"## 昨日の日記の要約\n{_safe(yesterday_summary, today) or '（昨日の日記はまだありません）'}\n\n"
        f"## やりたいことリストから1つ\n- {_safe(random_wish) or '（まだありません）'}\n\n"
        f"## 直近{mail_hours}時間のメール\n"
        f"{mails_text(mails) if mails is not None else '（メールを取得できませんでした）'}\n"
    )
    try:
        text = _strip_fence(run_codex(load_prompt("brief.md").replace("{{MATERIALS}}", materials), timeout=400))
        if text:
            return text
    except Exception as e:  # noqa: BLE001
        log(f"NG brief codex: {e!r}")
    n_mail = "?" if mails is None else len(mails)
    return ("☀️ **おはようございます**（AIのまとめは失敗したので材料だけ）\n"
            f"**今日やること**\n{_lst(tasks['overdue'] + tasks['today'])}\n"
            f"**明日**\n{_lst(tasks['tomorrow'])}\n📬 直近{mail_hours}時間のメール: {n_mail}通（`!mail` で一覧）")


def build_mail_digest(hours=24):
    mails = fetch_recent(hours)
    if not mails:
        return f"📭 直近{hours}時間のメールはありません"
    try:
        prompt = (load_prompt("mail.md").replace("{{HOURS}}", str(hours))
                  .replace("{{MAILS}}", mails_text(mails)))
        text = _strip_fence(run_codex(prompt, timeout=400))
        if text:
            return text
    except Exception as e:  # noqa: BLE001
        log(f"NG mail codex: {e!r}")
    lines = [f"📬 直近{hours}時間のメール {len(mails)}通（AIのまとめは失敗したので一覧だけ）"]
    lines += [f"- [{m['account']}] {m['date']} {m['from'][:30]}：{m['subject']}" for m in mails]
    return "\n".join(lines)
