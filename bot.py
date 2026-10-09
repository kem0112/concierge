#!/usr/bin/env python3
"""Concierge の常駐 bot（秘書）。#concierge で報告する。

  10分おき       受け皿 Gmail の郵便受けを覗き、未処理のメールを全部仕分ける（ラベル・預かりタスク）
                 今日・明日の締切や予定に関わるものは、すぐ知らせる
  毎朝 8:00      モーニングブリーフ（天気・タスク・メールの仕分け結果・片付いていないもの・昨日のふりかえり）
  毎夕 18:00     イブニングブリーフ（朝以降のメール・まだ片付いていないこと・明日の準備）

  !brief / !mail [時間] / !todo（!inbox も同じ） / !check / !retriage
  未対応の🔴 ＝ 受け皿でスターが付いているメール。ブリーフの最後（と !todo）に番号つきの一覧で出る。
  番号のリアクション（1️⃣ など）でスターを外す（外すと付け直す）。⏰急ぎ の 🔴 は ✅ で同じ
  詳しい要約: !read 番号（いちばん新しい一覧の番号）/ ⏰ に 📖
  !vip / !mute / !except / !site  add <パターン> [呼び名] | remove <パターン> | list
  !help

Recall（記録係）とは別アプリ・別プロセス。Recall の記録は Nextcloud から読むだけ。
メールの鍵（受け皿 Gmail のアプリパスワード）を持つのはこの bot だけ。
"""
import asyncio
import os
import re
import sys
import traceback
from datetime import datetime, time as dtime

import discord
from discord.ext import tasks

import rules as rules_mod
import secretary as sec
from brief import build_brief, build_evening, mail_list
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
_eh, _em = (os.environ.get("CONCIERGE_EVENING_TIME", "18:00").split(":") + ["0"])[:2]
EVENING_TIME = dtime(hour=int(_eh), minute=int(_em), tzinfo=JST)
POLL_MINUTES = int(os.environ.get("CONCIERGE_POLL_MINUTES", "10"))
LIMIT = 1900
DONE_EMOJI = "✅"
READ_EMOJI = "📖"
NUM_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟",
              "🇦", "🇧", "🇨", "🇩", "🇪", "🇫", "🇬", "🇭", "🇮", "🇯"]   # 1通に付けられるのは20種類まで
RULE_KINDS = {"!vip": ("vip", "大事な相手（アーカイブ候補にしない・最低でも確認推奨）"),
              "!mute": ("mute", "ミュート（アーカイブ候補にしてよい。保護対象には効かない）"),
              "!except": ("except", "保護の例外（大学宛て・VIP でも AI の判断に任せる）"),
              "!site": ("site", "就活サイトのドメイン（「就活サイト」ラベルだけ付ける）")}
HELP = ("🛎️ **Concierge**（秘書）\n"
        f"- {POLL_MINUTES}分おきに郵便受けを見て、大学宛て・大事な相手からのメールはすぐ、それ以外は{sec.BATCH_WAIT_MIN}分ごとにまとめて仕分けます。"
        f"毎朝 {BRIEF_TIME:%H:%M} にモーニング、毎夕 {EVENING_TIME:%H:%M} にイブニングブリーフ。今日・明日の締切や予定はすぐお知らせします\n"
        "- 受け皿の Gmail には付箋だけ: 🔴→⭐スター / 参考・広告・通知→「アーカイブ候補」/ 就活サイト→「就活サイト」（既読・アーカイブはしません）\n"
        "- 未対応の🔴（スター付き）はブリーフの最後に番号つきで出ます。片付いたら番号のリアクション（1️⃣ など）でスターを外します。Gmail で外しても同じ\n"
        "- `!todo` 未対応の一覧 / `!brief` ブリーフ（15時より前は朝の形） / `!mail [時間]` 仕分けたメール / `!check` 今すぐ仕分ける / `!usage [日数]` Codex の使用量\n"
        "- `!read 3` 一覧の3番を詳しく要約（⏰ には 📖 リアクションでも）。一度作った要約は保存して使い回します\n"
        "- `!retriage` 記録にあるメールを今の決まりで仕分け直す\n"
        "- `!vip` `!mute` `!except` `!site` + `add <アドレスや名前の一部> [呼び名]` / `remove <…>` / `list`")

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


# ---- 未対応（スター付き）の一覧と ⏰。リアクションでスターを外す ----

def card_text(rec, head="", done=False):
    body = f"{sec.line(rec, with_age=True)}\n件名: {rec['subject'][:200]}"
    if head:
        body = head + "\n" + body
    if done:
        return "✅ スターを外しました\n" + "\n".join(f"~~{ln}~~" for ln in body.split("\n"))
    return body


async def send_card(rec, head=""):
    hint = "（片付いたら ✅ / 詳しくは 📖）" if rec.get("star") else "（詳しくは 📖）"
    msg = await (await channel()).send(card_text(rec, head) + "\n" + hint)
    if rec.get("gmid"):
        await asyncio.to_thread(sec.remember_message, msg.id, rec["gmid"])
        if rec.get("star"):
            await react(msg, DONE_EMOJI)   # 押す場所の目印（bot 自身のリアクションは無視する）
        await react(msg, READ_EMOJI)
    return msg


async def send_detail(gmid, reply_to=None):
    """詳しい要約を返す（初回は AI、2回目からは保存したもの）"""
    try:
        async with _lock:
            text = await asyncio.to_thread(sec.detail, gmid)
    except Exception as e:  # noqa: BLE001
        log(f"NG detail {gmid}: {e!r}")
        text = f"⚠️ 詳しい要約を作れませんでした: `{str(e)[:200]}`"
    await send_long(text or "🤔 そのメールが見つかりません（削除されたかもしれません）", reply_to=reply_to)


BOARD_HEAD = "⭐ **未対応のメール**（片付いたら番号のリアクションでスターを外す。詳しく読むなら `!read 番号`）"


async def send_board(empty_text=None):
    """スター付きを番号つきの一覧1通で送り、番号のリアクションを付ける（古い順・20件まで）"""
    try:
        items = await asyncio.to_thread(sec.starred_items)
    except Exception as e:  # noqa: BLE001
        log(f"NG starred: {e!r}")
        await send_long(f"⚠️ スター付きのメールを確認できませんでした: `{str(e)[:200]}`")
        return
    if not items:
        if empty_text:
            await send_long(empty_text)
        return
    recs = items[:len(NUM_EMOJIS)]
    lines = [BOARD_HEAD] + [f"{emo} {sec.line(r, with_age=True)[2:]}" for emo, r in zip(NUM_EMOJIS, recs)]
    text = "\n".join(lines)[:LIMIT]
    if len(items) > len(recs):
        text += f"\n…ほか {len(items) - len(recs)} 件（減らしてから `!todo`）"
    msg = await (await channel()).send(text)
    await asyncio.to_thread(sec.remember_board, msg.id, [r["gmid"] for r in recs])
    for emo in NUM_EMOJIS[:len(recs)]:
        await react(msg, emo)


def _toggle_line(content, idx, done):
    """一覧の idx 番目（0始まり）の行に取り消し線を付ける／外す"""
    lines = content.split("\n")
    i = idx + 1
    if i < len(lines):
        ln = re.sub(r"^~~(.*)~~ ✅$", r"\1", lines[i])
        lines[i] = f"~~{ln}~~ ✅" if done else ln
    return "\n".join(lines)


async def on_board_reaction(payload, gmids, done):
    emo = str(payload.emoji)
    if emo not in NUM_EMOJIS or NUM_EMOJIS.index(emo) >= len(gmids):
        return
    idx = NUM_EMOJIS.index(emo)
    try:
        ok = await asyncio.to_thread(sec.set_star, gmids[idx], not done)
    except Exception as e:  # noqa: BLE001
        log(f"NG star {gmids[idx]}: {e!r}")
        await send_long(f"⚠️ スターを{'外せ' if done else '付けられ'}ませんでした: `{str(e)[:200]}`")
        return
    log(f"{'UNSTAR' if done else 'STAR'} gmid={gmids[idx]}（一覧）{'' if ok else ' 見つからず'}")
    try:
        msg = await (await channel()).fetch_message(payload.message_id)
        await msg.edit(content=_toggle_line(msg.content, idx, done))
    except discord.HTTPException:
        pass


async def on_card_reaction(payload, done):
    if payload.channel_id != CHANNEL_ID or payload.user_id not in ALLOWED_USER_IDS:
        return
    gmids = await asyncio.to_thread(sec.board_for_message, payload.message_id)
    if gmids:
        await on_board_reaction(payload, gmids, done)
        return
    if str(payload.emoji) not in (DONE_EMOJI, READ_EMOJI):
        return
    gmid = await asyncio.to_thread(sec.gmid_for_message, payload.message_id)
    if gmid is None:
        return
    if str(payload.emoji) == READ_EMOJI:
        if done:   # 付けたときだけ（外したときは何もしない）
            msg = await (await channel()).fetch_message(payload.message_id)
            await send_detail(gmid, reply_to=msg)
        return
    try:
        await asyncio.to_thread(sec.set_star, gmid, not done)
    except Exception as e:  # noqa: BLE001
        log(f"NG star {gmid}: {e!r}")
        return
    log(f"{'UNSTAR' if done else 'STAR'} gmid={gmid}（⏰）")
    try:
        msg = await (await channel()).fetch_message(payload.message_id)
        rec = (await asyncio.to_thread(sec.find_by_gmid, [gmid]))[0]
        if rec:
            m = re.search(r"⏰ \*\*急ぎ\*\*（[^）]*）", msg.content or "")
            await msg.edit(content=card_text(rec, m.group(0) if m else "", done)
                           + ("" if done else "\n（片付いたら ✅）"))
    except discord.HTTPException:
        pass


@client.event
async def on_raw_reaction_add(payload):
    await on_card_reaction(payload, True)


@client.event
async def on_raw_reaction_remove(payload):
    await on_card_reaction(payload, False)


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
        await send_card(rec, head=f"⏰ **急ぎ**（{reason}）")
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
        await send_board()
    except Exception as e:  # noqa: BLE001
        log(f"NG morning brief: {e!r}")
        try:
            await send_long(f"⚠️ モーニングブリーフを作れませんでした: `{str(e)[:300]}`")
        except Exception:  # noqa: BLE001
            pass


@morning_brief.before_loop
async def before_brief():
    await client.wait_until_ready()


@tasks.loop(time=EVENING_TIME)
async def evening_brief():
    await check_mail(force=True)
    try:
        async with _lock:
            text = await asyncio.to_thread(build_evening, datetime.now(JST).date())
        await send_long(text)
        await send_board()
    except Exception as e:  # noqa: BLE001
        log(f"NG evening brief: {e!r}")
        try:
            await send_long(f"⚠️ イブニングブリーフを作れませんでした: `{str(e)[:300]}`")
        except Exception:  # noqa: BLE001
            pass


@evening_brief.before_loop
async def before_evening():
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
        elif cmd == "!read":
            gmids = await asyncio.to_thread(sec.last_board)
            n = int(words[1]) if len(words) > 1 and words[1].isdigit() else 0
            if not 1 <= n <= len(gmids):
                await message.reply("番号を書いてください（例: `!read 3`。いちばん新しい未対応の一覧の番号）", mention_author=False)
                return
            await react(message, "⏳")
            await send_detail(gmids[n - 1], reply_to=message)
            await react(message, "⏳", remove=True)
        elif cmd in ("!todo", "!inbox"):
            await send_board(empty_text="✨ スター付き（未対応）のメールはありません")
        elif cmd == "!retriage":
            await react(message, "⏳")
            async with _lock:
                out = await asyncio.to_thread(sec.retriage)
            n = len(out["processed"])
            by = {}
            for r in out["processed"]:
                by[r["category"]] = by.get(r["category"], 0) + 1
            await message.reply(f"🔁 {n}通を仕分け直しました（以前の Concierge/… ラベルは外しました）\n"
                                + " / ".join(f"{sec.SHELF_ICON.get(c, '')}{c} {k}" for c, k in by.items()),
                                mention_author=False)
            await react(message, "⏳", remove=True)
        elif cmd == "!usage":
            days = int(words[1]) if len(words) > 1 and words[1].isdigit() else 1
            await message.reply(await asyncio.to_thread(usage_summary, days), mention_author=False)
        elif cmd == "!check":
            await react(message, "⏳")
            await check_mail(report_empty=True, force=True)
            await react(message, "⏳", remove=True)
        elif cmd in ("!brief", "!mail"):
            await react(message, "⏳")
            if cmd == "!brief":
                await check_mail(force=True)
                now = datetime.now(JST)
                async with _lock:
                    if 4 <= now.hour < 15:
                        text = await asyncio.to_thread(build_brief, log_date(now))
                    else:
                        text = await asyncio.to_thread(build_evening, log_date(now))
                await send_long(text, reply_to=message)
                await send_board()
                await react(message, "⏳", remove=True)
                return
            hours = int(words[1]) if len(words) > 1 and words[1].isdigit() else 24
            text = await asyncio.to_thread(mail_list, min(hours, 24 * 30))
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
    log(f"Concierge ready as {client.user} (channel={CHANNEL_ID}, brief={BRIEF_TIME:%H:%M}/{EVENING_TIME:%H:%M}, "
        f"poll={POLL_MINUTES}min)")
    if not poll_mail.is_running():
        poll_mail.start()
    if not morning_brief.is_running():
        morning_brief.start()
    if not evening_brief.is_running():
        evening_brief.start()


if __name__ == "__main__":
    client.run(TOKEN, log_handler=None)
