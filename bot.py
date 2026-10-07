#!/usr/bin/env python3
"""Concierge の常駐 bot（秘書）。#concierge で報告する。

  10分おき       受け皿 Gmail の郵便受けを覗き、未処理のメールを全部仕分ける（ラベル・預かりタスク）
                 今日・明日の締切や予定に関わるものは、すぐ知らせる
  毎朝 8:00      モーニングブリーフ（天気・タスク・メールの仕分け結果・片付いていないもの・昨日のふりかえり）

  !brief / !mail [時間] / !inbox / !check
  !vip / !mute / !except  add <パターン> [呼び名] | remove <パターン> | list
  !help

Recall（記録係）とは別アプリ・別プロセス。Recall の記録は Nextcloud から読むだけ。
メールの鍵（受け皿 Gmail のアプリパスワード）を持つのはこの bot だけ。
"""
import asyncio
import os
import sys
import traceback
from datetime import datetime, time as dtime

import discord
from discord.ext import tasks

import rules as rules_mod
import secretary as sec
from brief import build_brief, mail_list, inbox_list
from nc import JST, log, log_date

TOKEN = os.environ["CONCIERGE_DISCORD_TOKEN"]
CHANNEL_ID = int(os.environ["CONCIERGE_CHANNEL_ID"])
ALLOWED_USER_IDS = {
    int(x) for x in os.environ.get("CONCIERGE_ALLOWED_USER_IDS", "").split(",") if x.strip()
}
if not ALLOWED_USER_IDS:
    sys.exit("CONCIERGE_ALLOWED_USER_IDS が未設定です")
_h, _m = (os.environ.get("CONCIERGE_BRIEF_TIME", "08:00").split(":") + ["0"])[:2]
BRIEF_TIME = dtime(hour=int(_h), minute=int(_m), tzinfo=JST)
POLL_MINUTES = int(os.environ.get("CONCIERGE_POLL_MINUTES", "10"))
LIMIT = 1900
RULE_KINDS = {"!vip": ("vip", "大事な相手（自動アーカイブしない・最低でも確認推奨）"),
              "!mute": ("mute", "ミュート（今後アーカイブしてよい。保護対象には効かない）"),
              "!except": ("except", "保護の例外（大学宛て・VIP でも AI の判断に任せる）")}
HELP = ("🛎️ **Concierge**（秘書）\n"
        f"- {POLL_MINUTES}分おきに郵便受けを見て、大学・就活・大事な相手からのメールはすぐ、それ以外は{sec.BATCH_WAIT_MIN}分ごとにまとめて仕分けます。"
        f"毎朝 {BRIEF_TIME:%H:%M} にブリーフ。今日・明日の締切や予定はすぐお知らせします\n"
        "- `!brief` ブリーフ / `!mail [時間]` 仕分けたメールの一覧 / `!inbox` 片付いていないもの / `!check` 今すぐ仕分ける / `!usage [日数]` Codex の使用量\n"
        "- `!vip` `!mute` `!except` + `add <アドレスや名前の一部> [呼び名]` / `remove <…>` / `list`\n"
        f"- 自動アーカイブ: {'オン' if sec.AUTO_ARCHIVE else 'オフ（ラベルだけ付けています）'}")

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
_lock = asyncio.Lock()   # IMAP と Codex は1件ずつ
_poll_failures = 0


def split_message(text, limit=LIMIT):
    chunks, cur = [], ""
    for ln in text.splitlines(keepends=True):
        if len(cur) + len(ln) > limit and cur:
            chunks.append(cur)
            cur = ""
        while len(ln) > limit:
            chunks.append(ln[:limit])
            ln = ln[limit:]
        cur += ln
    if cur.strip():
        chunks.append(cur)
    return chunks or ["（空の応答）"]


async def channel():
    return client.get_channel(CHANNEL_ID) or await client.fetch_channel(CHANNEL_ID)


async def send_long(text, reply_to=None):
    ch = await channel()
    for i, chunk in enumerate(split_message(text)):
        if reply_to is not None and i == 0:
            await reply_to.reply(chunk, mention_author=False)
        else:
            await ch.send(chunk)


async def react(message, emoji, remove=False):
    try:
        if remove:
            await message.remove_reaction(emoji, client.user)
        else:
            await message.add_reaction(emoji)
    except discord.HTTPException:
        pass


# ---- 郵便受け ----

async def check_mail(report_empty=False, force=False):
    global _poll_failures
    async with _lock:
        try:
            out = await asyncio.to_thread(sec.run_once, force)
            _poll_failures = 0
        except Exception as e:  # noqa: BLE001
            _poll_failures += 1
            log(f"NG poll ({_poll_failures}回連続): {e!r}\n{traceback.format_exc()}")
            if _poll_failures == 3 or report_empty:
                await send_long(f"⚠️ メールを確認できません（{_poll_failures}回連続）: `{str(e)[:200]}`")
            return
    for rec, reason in out["urgent"]:
        await send_long(f"⏰ **急ぎ**（{reason}）\n{sec.line(rec)}\n件名: {rec['subject']}")
    if out["failed"]:
        await send_long(f"❓ 仕分けできなかったメールが {out['failed']} 通あります（`Concierge/未分類` ラベル。受信トレイに残しています）")
    if report_empty:
        n = len(out["processed"])
        await send_long(f"📬 {n}通を仕分けました。" + (f"預かりタスク {len(out['held'])}件。" if out["held"] else "")
                        if n else "📭 新しいメールはありません")


def usage_summary(days=1):
    """!usage: Codex の使用量（state/codex_usage.jsonl）"""
    import json as _json
    from collections import defaultdict
    from datetime import timedelta
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "codex_usage.jsonl")
    since = (datetime.now(JST) - timedelta(days=days)).isoformat()
    agg = defaultdict(lambda: [0, 0, 0])   # 回数, トークン, トークン不明の回数
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                r = _json.loads(ln)
                if r["time"] < since:
                    continue
                a = agg[f"{r['task']}（{r['model']}）"]
                a[0] += 1
                if r.get("tokens") is None:
                    a[2] += 1
                else:
                    a[1] += r["tokens"]
    except FileNotFoundError:
        pass
    if not agg:
        return f"📊 直近{days}日の Codex 使用はありません（Concierge 分）"
    lines = [f"📊 **Codex 使用量**（直近{days}日・Concierge 分）"]
    for k, (n, t, unknown) in sorted(agg.items()):
        lines.append(f"- {k}: {n}回 / {t:,} トークン" + (f"（{unknown}回は不明）" if unknown else ""))
    return "\n".join(lines)


@tasks.loop(minutes=POLL_MINUTES)
async def poll_mail():
    await check_mail()


@poll_mail.before_loop
async def before_poll():
    await client.wait_until_ready()


# ---- 毎朝 ----

@tasks.loop(time=BRIEF_TIME)
async def morning_brief():
    await check_mail(force=True)   # 出す前に郵便受けを見て、溜まっている分も仕分けておく
    try:
        async with _lock:
            text = await asyncio.to_thread(build_brief, datetime.now(JST).date())
        await send_long(text)
    except Exception as e:  # noqa: BLE001
        log(f"NG morning brief: {e!r}")
        try:
            await send_long(f"⚠️ モーニングブリーフを作れませんでした: `{str(e)[:300]}`")
        except Exception:  # noqa: BLE001
            pass


@morning_brief.before_loop
async def before_brief():
    await client.wait_until_ready()


# ---- コマンド ----

def rule_command(words):
    kind, label = RULE_KINDS[words[0]]
    sub = words[1].lower() if len(words) > 1 else "list"
    if sub == "add" and len(words) >= 3:
        rules_mod.add(kind, words[2], " ".join(words[3:]))
        return f"✅ {label} に `{words[2]}` を追加しました"
    if sub == "remove" and len(words) >= 3:
        ok = rules_mod.remove(kind, words[2])
        return f"🗑 `{words[2]}` を外しました" if ok else f"🤔 `{words[2]}` は登録されていません"
    items = rules_mod.load()[kind]
    body = "\n".join(f"- `{r['pattern']}` {r.get('name', '')}" for r in items) or "- なし"
    return f"📋 **{label}**\n{body}"


@client.event
async def on_message(message):
    if message.author.bot or message.channel.id != CHANNEL_ID:
        return
    if message.author.id not in ALLOWED_USER_IDS:
        return
    words = message.content.strip().split()
    cmd = words[0].lower() if words else ""
    try:
        if cmd in RULE_KINDS:
            words[0] = cmd
            await message.reply(await asyncio.to_thread(rule_command, words), mention_author=False)
        elif cmd == "!usage":
            days = int(words[1]) if len(words) > 1 and words[1].isdigit() else 1
            await message.reply(await asyncio.to_thread(usage_summary, days), mention_author=False)
        elif cmd == "!check":
            await react(message, "⏳")
            await check_mail(report_empty=True, force=True)
            await react(message, "⏳", remove=True)
        elif cmd in ("!brief", "!mail", "!inbox"):
            await react(message, "⏳")
            if cmd == "!brief":
                await check_mail(force=True)
                async with _lock:
                    text = await asyncio.to_thread(build_brief, log_date(datetime.now(JST)))
            elif cmd == "!mail":
                hours = int(words[1]) if len(words) > 1 and words[1].isdigit() else 24
                text = await asyncio.to_thread(mail_list, min(hours, 24 * 30))
            else:
                async with _lock:
                    text = await asyncio.to_thread(inbox_list)
            await send_long(text, reply_to=message)
            await react(message, "⏳", remove=True)
        else:
            await message.reply(HELP, mention_author=False)
    except Exception as e:  # noqa: BLE001
        log(f"NG {cmd}: {e!r}\n{traceback.format_exc()}")
        await message.reply(f"⚠️ 失敗しました: `{str(e)[:300]}`", mention_author=False)
        await react(message, "⏳", remove=True)


@client.event
async def on_ready():
    log(f"Concierge ready as {client.user} (channel={CHANNEL_ID}, brief={BRIEF_TIME:%H:%M}, "
        f"poll={POLL_MINUTES}min, auto_archive={sec.AUTO_ARCHIVE})")
    if not poll_mail.is_running():
        poll_mail.start()
    if not morning_brief.is_running():
        morning_brief.start()


if __name__ == "__main__":
    client.run(TOKEN, log_handler=None)
