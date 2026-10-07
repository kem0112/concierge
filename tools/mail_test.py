#!/usr/bin/env python3
"""受け皿 Gmail を IMAP で読めるか確認する。直近のメールの日時・宛先・差出人・件名を表示するだけ。

  python3 tools/mail_test.py [件数]

~/.config/concierge/env の GMAIL_USER / GMAIL_APP_PASSWORD を使う。
受信トレイは読み取り専用（EXAMINE）で開き、本文も取らないので、既読にもならない。
"""
import email
import imaplib
import os
import sys
from email.header import decode_header, make_header

ENV_PATH = os.path.expanduser("~/.config/concierge/env")


def load_env(path):
    env = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def dec(value):
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:  # noqa: BLE001
        return value or ""


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    env = load_env(ENV_PATH)
    user = env["GMAIL_USER"]
    password = env["GMAIL_APP_PASSWORD"].replace(" ", "")
    with imaplib.IMAP4_SSL("imap.gmail.com", 993) as imap:
        imap.login(user, password)
        imap.select("INBOX", readonly=True)
        _, data = imap.search(None, "ALL")
        ids = data[0].split()
        print(f"アカウント: {user}（受信トレイ {len(ids)} 通）\n")
        for mid in reversed(ids[-n:]):
            _, msg_data = imap.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (DATE TO FROM SUBJECT)])")
            msg = email.message_from_bytes(msg_data[0][1])
            print(f"{msg.get('Date', '')}\n  To: {dec(msg.get('To'))}\n"
                  f"  From: {dec(msg.get('From'))}\n  件名: {dec(msg.get('Subject')) or '（件名なし）'}\n")


if __name__ == "__main__":
    main()
