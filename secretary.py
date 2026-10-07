"""秘書の仕事: 郵便受けを覗いて、未処理のメールを全部仕分ける。

  - 未処理は UID で管理（state/mail.json）。pk-206 が止まっていても、再開時に溜まった分から全部処理する
  - ラベル: Concierge/<棚>、宛先/<アカウント>
  - 自動アーカイブは CONCIERGE_AUTO_ARCHIVE=1 のときだけ（最初の1週間はラベルだけで精度を見る）
  - 今日・明日の締切/予定は即時通知の候補として返す
  - 「やること」があるメールは /ob/Concierge/預かりタスク.md に預かる
"""
import json
import os
from datetime import datetime, timedelta

import rules as rules_mod
import triage
from gmail_box import Mailbox
from nc import JST, HELD_TASKS_MD, get_text, put_text, log

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(HERE, "state", "mail.json")
KEEP_DAYS = 60
FIRST_RUN_DAYS = 3
AUTO_ARCHIVE = os.environ.get("CONCIERGE_AUTO_ARCHIVE", "0") == "1"
SHELF_ICON = {"要対応": "🔴", "確認推奨": "🟡", "参考": "⚪", "広告・通知": "🗑", "未分類": "❓"}


# ---- 状態 ----

def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {}
    st.setdefault("uidvalidity", None)
    st.setdefault("last_uid", None)
    st.setdefault("last_brief", None)
    st.setdefault("records", {})
    return st


def save_state(st):
    cutoff = (datetime.now(JST) - timedelta(days=KEEP_DAYS)).isoformat()
    st["records"] = {k: v for k, v in st["records"].items() if v.get("processed_at", "") >= cutoff}
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_PATH)


# ---- 預かりタスク ----

def hold_tasks(items):
    """items: [(record, ai)] のうち task があるものを 預かりタスク.md に足す。足した行を返す"""
    lines = []
    for rec, ai in items:
        if not ai or not ai.get("task") or rec["category"] not in ("要対応", "確認推奨"):
            continue
        due = f" 📅 {ai['deadline']}" if ai.get("deadline") else ""
        sender = rec["from"].split("<")[0].strip().strip('"')[:20] or rec["from"][:20]
        lines.append(f"- [ ] {ai['task']}{due} #{rec['account']} （📧 {sender}：{rec['subject'][:40]}）"
                     f" %%mail:{rec['msgid']}%%")
    if not lines:
        return []
    content = get_text(HELD_TASKS_MD) or "# 預かりタスク\n\nConcierge がメールから預かったタスク。引き受けたら自分のタスクへ移すか、完了にする。\n\n"
    new = [ln for ln in lines if ln.split("%%")[1] not in content]
    if new:
        put_text(HELD_TASKS_MD, content.rstrip("\n") + "\n" + "\n".join(new) + "\n")
    return new


# ---- 急ぎ ----

def urgent_reason(rec, ai, today):
    if not ai or rec["category"] in ("広告・通知", "未分類"):
        return None
    if not rec["protected"] and ai["confidence"] != "high":
        return None
    soon = {today.isoformat(), (today + timedelta(days=1)).isoformat()}
    if ai.get("deadline") in soon:
        return f"締切 {ai['deadline'][5:].replace('-', '/')}"
    ev = ai.get("event")
    if ev and ev["date"] in soon:
        return f"予定{ev['change']} {ev['date'][5:].replace('-', '/')} {ev['what']}"
    return None


# ---- 1回分の処理 ----

def run_once():
    """未処理のメールを全部仕分ける。戻り値: dict(processed=[record], urgent=[(record, reason)], held=[line], failed=n)"""
    st = load_state()
    rules = rules_mod.load()
    now = datetime.now(JST)
    out = {"processed": [], "urgent": [], "held": [], "failed": 0}
    with Mailbox() as mb:
        if st["uidvalidity"] != mb.uidvalidity or st["last_uid"] is None:
            # 初回、または受信トレイの番号が振り直されたとき: 直近数日分だけ処理する
            uids = mb.uids_since((now - timedelta(days=FIRST_RUN_DAYS)).date())
            st["uidvalidity"] = mb.uidvalidity
            st["last_uid"] = mb.max_uid()
            log(f"初期化: 直近{FIRST_RUN_DAYS}日の {len(uids)} 通を処理")
        else:
            uids = mb.uids_after(st["last_uid"])
        uids = [u for u in uids if str(u) not in st["records"]]
        if not uids:
            save_state(st)
            return out

        mails = [m for m in (mb.fetch(u) for u in uids) if m]
        ais = triage.ask_ai(mails, now)
        pairs = []
        for mail, ai in zip(mails, ais):
            d = triage.decide(mail, ai, rules)
            rec = {k: mail[k] for k in ("uid", "msgid", "date", "account", "from", "subject")}
            rec.update({"category": d["category"], "protected": d["protected"], "vip": d["vip"],
                        "reason": d["reason"], "summary": (ai or {}).get("summary", ""),
                        "deadline": (ai or {}).get("deadline"), "event": (ai or {}).get("event"),
                        "archived": False, "processed_at": now.isoformat()})
            labels = [f"Concierge/{d['category']}", f"宛先/{mail['account']}"]
            try:
                mb.add_labels(mail["uid"], labels)
                if d["archive"] and AUTO_ARCHIVE:
                    mb.archive(mail["uid"])
                    rec["archived"] = True
            except Exception as e:  # noqa: BLE001
                log(f"NG label/archive uid={mail['uid']}: {e!r}")
            if d["category"] == "未分類":
                out["failed"] += 1
            st["records"][str(mail["uid"])] = rec
            st["last_uid"] = max(st["last_uid"] or 0, mail["uid"])
            out["processed"].append(rec)
            pairs.append((rec, ai))
            reason = urgent_reason(rec, ai, now.date())
            if reason:
                out["urgent"].append((rec, reason))
        try:
            out["held"] = hold_tasks(pairs)
        except Exception as e:  # noqa: BLE001
            log(f"NG hold tasks: {e!r}")
        save_state(st)
    log(f"仕分け {len(out['processed'])}通（急ぎ {len(out['urgent'])} / 預かり {len(out['held'])} / 未分類 {out['failed']}）")
    return out


# ---- ブリーフ・一覧用 ----

def since_last_brief(st):
    since = st.get("last_brief") or (datetime.now(JST) - timedelta(days=1)).isoformat()
    return [r for r in st["records"].values() if r.get("processed_at", "") > since]


def open_items(categories=("要対応", "確認推奨")):
    """まだ受信トレイにある（片付いていない）指定の棚のメール。古い順"""
    st = load_state()
    cands = [r for r in st["records"].values() if r["category"] in categories]
    if not cands:
        return []
    with Mailbox() as mb:
        if mb.uidvalidity != st["uidvalidity"]:
            return []
        alive = mb.in_inbox([r["uid"] for r in cands])
    return sorted([r for r in cands if r["uid"] in alive], key=lambda r: r.get("date") or "")


def mark_briefed():
    st = load_state()
    st["last_brief"] = datetime.now(JST).isoformat()
    save_state(st)


def line(rec, with_age=False):
    sender = rec["from"].split("<")[0].strip().strip('"')[:24] or rec["from"][:24]
    s = f"{SHELF_ICON.get(rec['category'], '')} [{rec['account']}] {sender}：{rec['summary'] or rec['subject']}"
    if rec.get("deadline"):
        s += f"（〆 {rec['deadline'][5:].replace('-', '/')}）"
    if with_age and rec.get("date"):
        days = (datetime.now(JST).date() - datetime.fromisoformat(rec["date"]).date()).days
        if days >= 1:
            s += f" — {days}日目"
    return s
