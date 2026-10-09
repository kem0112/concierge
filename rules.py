"""秘書が覚えている決まりごと（大事な相手・ミュート・保護の例外）。pk-206 の state/rules.json に保存する。

照合は「差出人（名前とアドレス）・宛先・CC・List-Id」の文字列に、パターンが含まれるか（大文字小文字は無視）。
  vip     大事な相手。自動アーカイブしない。棚は最低でも「確認推奨」
  except  保護の例外。大学宛て・VIP に当たっても保護せず、AI の判断に任せる（例: 高頻度の事務連絡）
  mute    広告・通知として扱ってよい相手（アーカイブ候補。保護対象には効かない）
  site    就活サイトのドメイン。差出人のアドレスがこのドメイン（サブドメイン含む）なら「就活サイト」ラベルだけ付ける（AI は使わない）
"""
import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
RULES_PATH = os.path.join(HERE, "state", "rules.json")
DEFAULT_PATH = os.path.join(HERE, "config", "rules_default.json")
KINDS = ("vip", "except", "mute", "site")


def load():
    if not os.path.exists(RULES_PATH):
        os.makedirs(os.path.dirname(RULES_PATH), exist_ok=True)
        shutil.copy(DEFAULT_PATH, RULES_PATH)
    with open(RULES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    if any(k not in data for k in KINDS):
        # 後から増えた種類（site など）は初期値から補って保存する
        with open(DEFAULT_PATH, encoding="utf-8") as f:
            default = json.load(f)
        for k in KINDS:
            data.setdefault(k, default.get(k, []))
        save({k: data[k] for k in KINDS})
    return {k: data.get(k, []) for k in KINDS}


def site_match(rules, from_addr):
    """差出人アドレスのドメインが就活サイトなら、そのサイト名"""
    domain = (from_addr or "").rsplit("@", 1)[-1].lower()
    for r in rules["site"]:
        p = r["pattern"].lower().lstrip("@.")
        if domain == p or domain.endswith("." + p):
            return r.get("name") or p
    return None


def save(rules):
    tmp = RULES_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False, indent=2)
    os.replace(tmp, RULES_PATH)


def match(rules, kind, haystack):
    """最初に当たったルールの name（なければ None）"""
    h = haystack.lower()
    for r in rules[kind]:
        if r["pattern"].lower() in h:
            return r.get("name") or r["pattern"]
    return None


def add(kind, pattern, name=""):
    rules = load()
    rules[kind] = [r for r in rules[kind] if r["pattern"].lower() != pattern.lower()]
    rules[kind].append({"pattern": pattern, "name": name or pattern})
    save(rules)


def remove(kind, pattern):
    rules = load()
    before = len(rules[kind])
    rules[kind] = [r for r in rules[kind] if r["pattern"].lower() != pattern.lower()]
    save(rules)
    return before != len(rules[kind])
