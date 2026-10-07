"""受け皿 Gmail を IMAP（読み取り専用）で読み、直近のメールを取り出す。

転送されたメールは To などに元のアドレスが残るので、どのアカウント宛てかを判定できる。
メール本文は外から来た文章（信用できないデータ）。Codex には「データとして扱え」と指示して渡す。
"""
import email
import imaplib
import os
import re
from datetime import datetime, timedelta
from email import policy
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime, getaddresses

from nc import JST

GMAIL_USER = os.environ["GMAIL_USER"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "")
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MAX_FULL_SIZE = 1_500_000   # これより大きいメール（重い添付）はヘッダーだけ読む
BODY_LIMIT = 1500


def _accounts():
    """MAIL_ACCOUNTS="outlook.jp=メイン,st.kyoto-u.ac.jp=大学,..."（アドレスの一部=呼び名）"""
    out = []
    for part in os.environ.get("MAIL_ACCOUNTS", "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            out.append((k.strip().lower(), v.strip()))
    return out


ACCOUNTS = _accounts()


def _dec(value):
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:  # noqa: BLE001
        return value or ""


def _account_of(msg):
    addrs = " ".join(a.lower() for _, a in getaddresses(
        msg.get_all("To", []) + msg.get_all("Cc", []) + msg.get_all("Delivered-To", [])))
    for key, label in ACCOUNTS:
        if key in addrs:
            return label
    return "その他"


def _body_text(msg):
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
        if part is None:
            return ""
        text = part.get_content()
        if part.get_content_type() == "text/html":
            text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
            text = re.sub(r"(?s)<[^>]+>", " ", text)
            text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"[ \t　]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n", text).strip()
        return text[:BODY_LIMIT] + ("…" if len(text) > BODY_LIMIT else "")
    except Exception:  # noqa: BLE001
        return ""


def fetch_recent(hours=24, limit=40):
    """直近 hours 時間のメール（新しい順）。各要素は dict(date, account, from, subject, body)。"""
    since = datetime.now(JST) - timedelta(hours=hours)
    since_str = f"{since.day:02d}-{MONTHS[since.month - 1]}-{since.year}"
    items = []
    with imaplib.IMAP4_SSL("imap.gmail.com", 993) as imap:
        imap.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        imap.select("INBOX", readonly=True)  # 読み取り専用。既読にもしない
        _, data = imap.search(None, f"(SINCE {since_str})")
        for mid in reversed(data[0].split()):
            if len(items) >= limit:
                break
            _, meta = imap.fetch(mid, "(RFC822.SIZE BODY.PEEK[HEADER])")
            size = int(re.search(rb"RFC822\.SIZE (\d+)", meta[0][0]).group(1))
            header_only = size > MAX_FULL_SIZE
            if header_only:
                raw = meta[0][1]
            else:
                _, full = imap.fetch(mid, "(BODY.PEEK[])")
                raw = full[0][1]
            msg = email.message_from_bytes(raw, policy=policy.default)
            try:
                dt = parsedate_to_datetime(msg["Date"]).astimezone(JST)
            except Exception:  # noqa: BLE001
                dt = None
            if dt is not None and dt < since:
                continue
            items.append({
                "date": f"{dt:%m/%d %H:%M}" if dt else "?",
                "account": _account_of(msg),
                "from": _dec(msg.get("From")),
                "subject": _dec(msg.get("Subject")) or "（件名なし）",
                "body": "（大きい添付付きのため本文は省略）" if header_only else _body_text(msg),
            })
    return items


def mails_text(items):
    if not items:
        return "（該当するメールはありません）"
    blocks = []
    for i, m in enumerate(items, 1):
        blocks.append(f"### メール{i}\n日時: {m['date']} / 宛先アカウント: {m['account']}\n"
                      f"差出人: {m['from']}\n件名: {m['subject']}\n本文:\n{m['body']}")
    return "\n\n".join(blocks)
