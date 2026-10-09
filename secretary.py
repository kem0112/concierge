"""秘書の仕事: 郵便受けを覗いて、未処理のメールを全部仕分ける。

  - 未処理は UID で管理（state/mail.json）。pk-206 が止まっていても、再開時に溜まった分から全部処理する
  - 受け皿の Gmail にするのは「付箋」だけ（既読・アーカイブ・削除はしない）:
      🔴 要対応 → ⭐ スター / ⚪ 参考・🗑 広告・通知 → 「アーカイブ候補」/ 就活サイト → 「就活サイト」（AI は使わない）
      全部のメールに 宛先/<アカウント>
  - 未対応の 🔴 ＝ 受け皿でスターが付いているメール（自分で付けたものも含む）。スターを外せば片付いたことになる
  - 今日・明日の締切/予定は即時通知の候補として返す
  - 「やること」があるメールは /ob/Concierge/預かりタスク.md に預かる
"""
import json
import os
import threading
from datetime import datetime, timedelta

import rules as rules_mod
import triage
from gmail_box import Mailbox
from nc import JST, HELD_TASKS_MD, get_text, put_text, log

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(HERE, "state", "mail.json")
# どの Discord メッセージがどのメールか（リアクションでスターを外すため）。仕分けの処理とぶつからないよう別ファイル
DONE_PATH = os.path.join(HERE, "state", "done.json")
_done_lock = threading.Lock()
KEEP_DAYS = 60
FIRST_RUN_DAYS = 3
# Codex に渡すのは、まとめて（呼び出し回数を減らす）。ただし大学宛て・VIP のメールが来ていたら、すぐ仕分ける
BATCH_WAIT_MIN = int(os.environ.get("CONCIERGE_BATCH_WAIT_MIN", "60"))
BATCH_MAX = 10
SHELF_ICON = {"要対応": "🔴", "確認推奨": "🟡", "参考": "⚪", "広告・通知": "🗑", "就活サイト": "💼", "未分類": "❓"}
OLD_LABELS = [f"Concierge/{c}" for c in ("要対応", "確認推奨", "参考", "広告・通知", "未分類")]   # 以前のラベル（仕分け直しで外す）


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
    st.setdefault("pending_since", {})   # 見つけたがまだ仕分けていないメール: uid -> 初めて見た時刻
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

def _apply(mb, st, mail, d, ai, now, out):
    """決定 d を受け皿の Gmail（ラベル・スター）と記録に反映する"""
    rec = {k: mail[k] for k in ("uid", "gmid", "msgid", "date", "account", "from", "subject")}
    rec.update({"category": d["category"], "protected": d["protected"], "vip": d["vip"], "star": d["star"],
                "reason": d["reason"], "summary": (ai or {}).get("summary", ""),
                "deadline": (ai or {}).get("deadline"), "event": (ai or {}).get("event"),
                "processed_at": now.isoformat()})
    try:
        mb.add_labels(mail["uid"], [f"宛先/{mail['account']}"] + d["labels"])
        if d["star"]:
            mb.star(mail["uid"])
    except Exception as e:  # noqa: BLE001
        log(f"NG label/star uid={mail['uid']}: {e!r}")
    if d["category"] == "未分類":
        out["failed"] += 1
    st["records"][str(mail["uid"])] = rec
    st["pending_since"].pop(str(mail["uid"]), None)
    out["processed"].append(rec)
    return rec


def retriage():
    """記録にある（受信トレイに残っている）メールを、今の決まりで仕分け直す。以前のラベルは外す。⏰ は出さない"""
    st = load_state()
    rules = rules_mod.load()
    now = datetime.now(JST)
    out = {"processed": [], "urgent": [], "held": [], "failed": 0, "waiting": 0}
    with Mailbox() as mb:
        if mb.uidvalidity != st["uidvalidity"]:
            return out
        old = dict(st["records"])
        mails = [m for m in (mb.fetch(int(u)) for u in sorted(old, key=int)) if m]
        for m in mails:
            try:
                mb.remove_labels(m["uid"], OLD_LABELS)
            except Exception as e:  # noqa: BLE001
                log(f"NG remove old labels uid={m['uid']}: {e!r}")
        others = []
        for m in mails:
            d = triage.decide_site(m, rules)
            if d:
                _apply(mb, st, m, d, None, now, out)
            else:
                others.append(m)
        ais = triage.ask_ai(others, now) if others else []
        for m, ai in zip(others, ais):
            _apply(mb, st, m, triage.decide(m, ai, rules), ai, now, out)
        for rec in out["processed"]:   # 仕分けた日時は元のままにする（ブリーフの「新着」に混ぜない）
            prev = old.get(str(rec["uid"])) if old else None
            if prev and prev.get("processed_at"):
                rec["processed_at"] = prev["processed_at"]
        save_state(st)
    log(f"仕分け直し {len(out['processed'])}通（⭐ {sum(r['star'] for r in out['processed'])}）")
    return out

def run_once(force=False):
    """未処理のメールを仕分ける。戻り値: dict(processed=[record], urgent=[(record, reason)], held=[line], failed=n, waiting=n)

    force=False のときは、次のどれかに当たるまで Codex を呼ばずに待つ（郵便受けを見るだけなら Codex は使わない）:
      - 大学宛て・VIP のメールがある（急ぎの可能性）
      - いちばん古い未処理を見つけてから BATCH_WAIT_MIN 分たった
      - 未処理が BATCH_MAX 通たまった
    """
    st = load_state()
    rules = rules_mod.load()
    now = datetime.now(JST)
    out = {"processed": [], "urgent": [], "held": [], "failed": 0, "waiting": 0}
    with Mailbox() as mb:
        if st["uidvalidity"] != mb.uidvalidity or st["last_uid"] is None:
            # 初回、または受信トレイの番号が振り直されたとき: 直近数日分だけ処理する
            uids = mb.uids_since((now - timedelta(days=FIRST_RUN_DAYS)).date())
            st["uidvalidity"] = mb.uidvalidity
            st["records"], st["pending_since"] = {}, {}
            # まだ仕分けていない分は last_uid より後ろに残しておく（待っている間に取りこぼさないため）
            st["last_uid"] = (min(uids) - 1) if uids else mb.max_uid()
            log(f"初期化: 直近{FIRST_RUN_DAYS}日の {len(uids)} 通を処理")
        else:
            uids = mb.uids_after(st["last_uid"])
        uids = [u for u in uids if str(u) not in st["records"]]
        if not uids:
            save_state(st)
            return out

        mails = [m for m in (mb.fetch(u) for u in uids) if m]
        # 待っている間に受信トレイから消えたもの（自分でアーカイブした等）は待つのをやめる
        alive = {m["uid"] for m in mails}
        st["pending_since"] = {k: v for k, v in st["pending_since"].items() if int(k) in alive}
        pairs = []
        # 就活サイトは差出人で決まるので、待たずに AI なしで処理する
        sites = {m["uid"]: triage.decide_site(m, rules) for m in mails}
        for mail in [m for m in mails if sites[m["uid"]]]:
            _apply(mb, st, mail, sites[mail["uid"]], None, now, out)
        mails = [m for m in mails if not sites[m["uid"]]]
        for m in mails:
            st["pending_since"].setdefault(str(m["uid"]), now.isoformat())
        oldest = min((datetime.fromisoformat(st["pending_since"][str(m["uid"])]) for m in mails), default=now)
        urgent_possible = any(m["account"] in triage.FAST_ACCOUNTS
                              or triage.decide(m, {"category": "参考", "confidence": "high"}, rules)["protected"]
                              for m in mails)
        if mails and not (force or urgent_possible or len(mails) >= BATCH_MAX
                          or now - oldest >= timedelta(minutes=BATCH_WAIT_MIN)):
            out["waiting"] = len(mails)
            mails = []
        ais = triage.ask_ai(mails, now) if mails else []
        for mail, ai in zip(mails, ais):
            rec = _apply(mb, st, mail, triage.decide(mail, ai, rules), ai, now, out)
            pairs.append((rec, ai))
            reason = urgent_reason(rec, ai, now.date())
            if reason:
                out["urgent"].append((rec, reason))
        # まだ仕分けていないメールより後ろへは進めない（待っている間に取りこぼさないため）
        pending = [int(u) for u in st["pending_since"]]
        done_max = max([st["last_uid"] or 0] + [r["uid"] for r in out["processed"]])
        st["last_uid"] = min(done_max, min(pending) - 1) if pending else done_max
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


# ---- 未対応 ＝ スター付き ----

def _load_links():
    try:
        with open(DONE_PATH, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    d.setdefault("messages", {})   # Discord のメッセージID -> gmid（⏰ 1通に1件）
    d.setdefault("boards", {})     # Discord のメッセージID -> [gmid, ...]（未対応の一覧。番号のリアクションと対応）
    return d


def _save_links(d):
    d = {"messages": dict(list(d["messages"].items())[-2000:]), "boards": dict(list(d["boards"].items())[-200:])}
    os.makedirs(os.path.dirname(DONE_PATH), exist_ok=True)
    tmp = DONE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(tmp, DONE_PATH)


def remember_message(message_id, gmid):
    with _done_lock:
        d = _load_links()
        d["messages"][str(message_id)] = gmid
        _save_links(d)


def remember_board(message_id, gmids):
    with _done_lock:
        d = _load_links()
        d["boards"][str(message_id)] = list(gmids)
        _save_links(d)


def board_for_message(message_id):
    return _load_links()["boards"].get(str(message_id))


def gmid_for_message(message_id):
    return _load_links()["messages"].get(str(message_id))


def starred_items():
    """未対応の 🔴: 受け皿でスターが付いているメール（自分で付けたものも含む）。古い順"""
    st = load_state()
    by_gmid = {r["gmid"]: r for r in st["records"].values() if r.get("gmid")}
    with Mailbox(all_mail=True) as mb:
        found = mb.starred()
    items = []
    for it in found:
        rec = by_gmid.get(it["gmid"])
        items.append(dict(rec) if rec else dict(it, category="要対応", summary="", deadline=None))
    return sorted(items, key=lambda r: r.get("date") or "")


def set_star(gmid, on):
    """スターを付け外し（Discord のリアクションから）"""
    with Mailbox(all_mail=True) as mb:
        return mb.star_gmid(gmid, on)


def find_by_gmid(gmids):
    st = load_state()
    by_gmid = {r["gmid"]: r for r in st["records"].values() if r.get("gmid")}
    return [by_gmid.get(g) for g in gmids]


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
