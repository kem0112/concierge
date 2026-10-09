"""メールの仕分け。Codex に棚を決めてもらい、Python が保護ルールで必ず補正する。

AI の返事は信用しすぎない: 決められた選択肢以外は捨て、保護対象は AI が何と言っても「アーカイブ候補」にしない。
"""
import json
import re
from datetime import date

import rules as rules_mod
from ai import run_codex, load_prompt
from nc import WEEKDAYS, log

CATEGORIES = ["要対応", "確認推奨", "参考", "広告・通知"]
RANK = {c: i for i, c in enumerate(CATEGORIES)}  # 小さいほど重い
PROTECTED_ACCOUNTS = {"大学"}   # 就活宛ては保護しない（募集案内が多いので AI の判断に任せる。大事な相手は VIP で守る）
FAST_ACCOUNTS = {"大学"}   # 届いたら待たずにすぐ仕分ける宛先（就活宛ては他と同じくまとめて仕分ける）
BATCH = 10


def _glossary():
    try:
        return load_prompt("glossary.txt").strip()
    except FileNotFoundError:
        return ""


def _mail_block(i, m):
    return (f"### メール{i}\n日時: {m['date'] or '?'} / 宛先アカウント: {m['account']}\n"
            f"差出人: {m['from']}\n件名: {m['subject']}\n本文:\n{m['body']}")


def _parse_date(v):
    if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v.strip()):
        try:
            return date.fromisoformat(v.strip()).isoformat()
        except ValueError:
            return None
    return None


def _clean(item):
    """AI の1件分を検査して整える。壊れていれば None"""
    if not isinstance(item, dict) or item.get("category") not in CATEGORIES:
        return None
    ev = item.get("event")
    event = None
    if isinstance(ev, dict) and _parse_date(ev.get("date")):
        event = {"date": _parse_date(ev.get("date")),
                 "what": str(ev.get("what") or "")[:60],
                 "change": ev.get("change") if ev.get("change") in ("新規", "変更", "中止") else "新規"}
    task = item.get("task")
    return {
        "category": item["category"],
        "summary": str(item.get("summary") or "")[:80].replace("\n", " "),
        "deadline": _parse_date(item.get("deadline")),
        "event": event,
        "task": str(task)[:60].replace("\n", " ") if task else None,
        "confidence": "high" if item.get("confidence") == "high" else "low",
    }


def ask_ai(mails, now):
    """mails を BATCH ずつ Codex に渡す。戻り値は mails と同じ長さのリスト（失敗した分は None）"""
    results = [None] * len(mails)
    for start in range(0, len(mails), BATCH):
        chunk = mails[start:start + BATCH]
        prompt = (load_prompt("triage.md")
                  .replace("{{NOW}}", f"{now:%Y-%m-%d}（{WEEKDAYS[now.weekday()]}）{now:%H:%M}")
                  .replace("{{GLOSSARY}}", _glossary())
                  .replace("{{MAILS}}", "\n\n".join(_mail_block(i + 1, m) for i, m in enumerate(chunk))))
        try:
            out = run_codex(prompt, timeout=600, profile="light", task="mail_triage")
            m = re.search(r"\[.*\]", out, re.S)
            data = json.loads(m.group(0)) if m else []
        except Exception as e:  # noqa: BLE001
            log(f"NG triage codex: {e!r}")
            continue
        for item in data if isinstance(data, list) else []:
            try:
                idx = int(item.get("id")) - 1
            except (TypeError, ValueError, AttributeError):
                continue
            if 0 <= idx < len(chunk):
                results[start + idx] = _clean(item)
    return results


SITE_LABEL = "就活サイト"
CANDIDATE_LABEL = "アーカイブ候補"


def decide_site(mail, rules):
    """就活サイトからのメールなら、その決定（AI は使わない）。違えば None"""
    site = rules_mod.site_match(rules, mail.get("from_addr"))
    if not site:
        return None
    return {"category": "就活サイト", "protected": False, "vip": None, "star": False,
            "labels": [SITE_LABEL], "reason": f"就活サイト: {site}"}


def decide(mail, ai, rules):
    """AI の判断と決まりごとから、最終的な棚・スター・ラベルを決める。

    🔴 要対応 → スター / ⚪ 参考・🗑 広告・通知 → 「アーカイブ候補」ラベル（保護対象には付けない） / 🟡 → 何もしない
    """
    text = mail["match_text"]
    excepted = rules_mod.match(rules, "except", text)
    vip = rules_mod.match(rules, "vip", text)
    muted = rules_mod.match(rules, "mute", text)
    protected = not excepted and (mail["account"] in PROTECTED_ACCOUNTS or vip is not None)

    if ai is None:
        return {"category": "未分類", "protected": protected, "vip": vip, "star": False, "labels": [],
                "reason": "AIの仕分けに失敗"}
    cat = ai["category"]
    reason = ""
    if muted and not protected:
        cat, reason = "広告・通知", f"ミュート: {muted}"
    if protected and RANK[cat] > RANK["確認推奨"]:
        cat, reason = "確認推奨", f"保護: {vip or mail['account']}"
    labels = [CANDIDATE_LABEL] if cat in ("参考", "広告・通知") and not protected else []
    return {"category": cat, "protected": protected, "vip": vip, "star": cat == "要対応",
            "labels": labels, "reason": reason}
