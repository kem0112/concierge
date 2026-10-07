"""Recall の Obsidian ノートから、ブリーフの材料を機械的に拾う（Codex は使わない）。"""
import random
import re
from datetime import date, timedelta

from nc import JOURNAL_DIR, TASKS_MD, HELD_TASKS_MD, WISHES_MD, WEEKDAYS, get_text

TASK_RE = re.compile(r"^\s*- \[ \] (.*)$")
DUE_RE = re.compile(r"📅\s*(\d{4}-\d{2}-\d{2})")


def _clean(body):
    body = DUE_RE.sub("", body)
    body = re.sub(r"（\[\[[^\]]*\]\]）", "", body)
    body = re.sub(r"%%[^%]*%%", "", body)
    body = re.sub(r"（📧 ([^：）]*)：[^）]*）", r"（📧 \1）", body)
    return re.sub(r"\s+", " ", body).strip()


def open_tasks_with_due(content=None):
    """自分のタスク（Recall）と預かりタスク（Concierge）の両方。預かりの方には 📧 が付く"""
    if content is None:
        content = (get_text(TASKS_MD) or "") + "\n" + (get_text(HELD_TASKS_MD) or "")
    out = []
    for line in content.splitlines():
        m = TASK_RE.match(line)
        d = DUE_RE.search(m.group(1)) if m else None
        if not d:
            continue
        try:
            out.append((date.fromisoformat(d.group(1)), _clean(m.group(1))))
        except ValueError:
            continue
    return sorted(out)


def tasks_by_window(today, tasks):
    return {
        "overdue": [f"{t}（期日 {d:%m/%d}）" for d, t in tasks if d < today],
        "today": [t for d, t in tasks if d == today],
        "tomorrow": [t for d, t in tasks if d == today + timedelta(days=1)],
        "week": [f"{t}（{d:%m/%d}・{WEEKDAYS[d.weekday()]}）" for d, t in tasks
                 if today + timedelta(days=1) < d <= today + timedelta(days=7)],
    }


def yesterday_summary(today):
    text = get_text(f"{JOURNAL_DIR}/{(today - timedelta(days=1)).isoformat()}.md")
    if not text:
        return None
    m = re.search(r"## 要約\s*\n(.*?)(?=\n## |\Z)", text, re.S)
    return (m.group(1) if m else text[:600]).strip()


def random_wish():
    text = get_text(WISHES_MD) or ""
    items = [_clean(ln[2:]) for ln in text.splitlines() if ln.startswith("- ")]
    return random.choice(items) if items else None
