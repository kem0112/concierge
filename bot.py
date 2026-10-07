#!/usr/bin/env python3
"""Concierge の常駐 bot。#concierge で、朝のブリーフやメールのまとめを届ける。

  毎朝 8:00（CONCIERGE_BRIEF_TIME）: モーニングブリーフ（天気・タスク・メール・昨日のふりかえり）
  !brief       いつでもブリーフを作る
  !mail [時間]  直近のメールのまとめ（既定 24 時間）
  !help        使い方

Recall（記録係）とは別アプリ・別プロセス。Recall の記録は Nextcloud から読むだけ。
メールの鍵（受け皿 Gmail のアプリパスワード）を持つのはこの bot だけ。
"""
import asyncio
import os
import sys
from datetime import datetime, time as dtime

import discord
from discord.ext import tasks

from brief import build_brief, build_mail_digest
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
LIMIT = 1900
HELP = ("🛎️ **Concierge**\n"
        "- `!brief` モーニングブリーフ（毎朝自動でも届きます）\n"
        "- `!mail` 直近24時間のメールのまとめ（`!mail 72` のように時間を指定可）\n"
        "- `!help` この説明")

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
_lock = asyncio.Lock()  # Codex は1件ずつ


def split_message(text, limit=LIMIT):
    """Discord の 2000 字制限に合わせて、行の切れ目で分ける。"""
    chunks, cur = [], ""
    for line in text.splitlines(keepends=True):
        if len(cur) + len(line) > limit and cur:
            chunks.append(cur)
            cur = ""
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        cur += line
    if cur.strip():
        chunks.append(cur)
    return chunks or ["（空の応答）"]


async def channel():
    return client.get_channel(CHANNEL_ID) or await client.fetch_channel(CHANNEL_ID)


async def send_long(target, text, reply_to=None):
    for i, chunk in enumerate(split_message(text)):
        if reply_to is not None and i == 0:
            await reply_to.reply(chunk, mention_author=False)
        else:
            await target.send(chunk)


async def react(message, emoji, remove=False):
    try:
        if remove:
            await message.remove_reaction(emoji, client.user)
        else:
            await message.add_reaction(emoji)
    except discord.HTTPException:
        pass


@client.event
async def on_message(message):
    if message.author.bot or message.channel.id != CHANNEL_ID:
        return
    if message.author.id not in ALLOWED_USER_IDS:
        return
    words = message.content.strip().split()
    cmd = words[0].lower() if words else ""
    if cmd not in ("!brief", "!mail"):
        await message.reply(HELP, mention_author=False)
        return
    await react(message, "⏳")
    try:
        async with _lock:
            if cmd == "!brief":
                text = await asyncio.to_thread(build_brief, log_date(datetime.now(JST)))
            else:
                hours = int(words[1]) if len(words) > 1 and words[1].isdigit() else 24
                text = await asyncio.to_thread(build_mail_digest, min(hours, 24 * 14))
        await send_long(message.channel, text, reply_to=message)
    except Exception as e:  # noqa: BLE001
        log(f"NG {cmd}: {e!r}")
        await message.reply(f"⚠️ 失敗しました: `{str(e)[:300]}`", mention_author=False)
    finally:
        await react(message, "⏳", remove=True)


@tasks.loop(time=BRIEF_TIME)
async def morning_brief():
    try:
        async with _lock:
            text = await asyncio.to_thread(build_brief, datetime.now(JST).date())
        await send_long(await channel(), text)
    except Exception as e:  # noqa: BLE001
        log(f"NG morning brief: {e!r}")
        try:
            await (await channel()).send(f"⚠️ モーニングブリーフを作れませんでした: `{str(e)[:300]}`")
        except Exception:  # noqa: BLE001
            pass


@morning_brief.before_loop
async def before_morning_brief():
    await client.wait_until_ready()


@client.event
async def on_ready():
    log(f"Concierge ready as {client.user} (channel={CHANNEL_ID}, brief={BRIEF_TIME:%H:%M})")
    if not morning_brief.is_running():
        morning_brief.start()


if __name__ == "__main__":
    client.run(TOKEN, log_handler=None)
