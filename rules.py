"""秘書が覚えている決まりごと（大事な相手・ミュート・保護の例外）。pk-206 の state/rules.json に保存する。

照合は「差出人（名前とアドレス）・宛先・CC・List-Id」の文字列に、パターンが含まれるか（大文字小文字は無視）。
  vip     大事な相手。自動アーカイブしない。棚は最低でも「確認推奨」
  except  保護の例外。大学宛て・VIP に当たっても保護せず、AI の判断に任せる（例: 高頻度の事務連絡）
  mute    今後アーカイブしてよい相手（保護対象には効かない）
"""
import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
RULES_PATH = os.path.join(HERE, "state", "rules.json")
DEFAULT_PATH = os.path.join(HERE, "config", "rules_default.json")
KINDS = ("vip", "except", "mute")


def load():
    if not os.path.exists(RULES_PATH):
        os.makedirs(os.path.dirname(RULES_PATH), exist_ok=True)
        shutil.copy(DEFAULT_PATH, RULES_PATH)
    with open(RULES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return {k: data.get(k, []) for k in KINDS}


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
