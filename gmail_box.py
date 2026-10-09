"""受け皿 Gmail を IMAP で扱う。触るのはラベルの追加とスター（\\Flagged）の付け外しだけ。

既読にしない（本文は BODY.PEEK で読む）・アーカイブしない・削除しない・送信しない。
Gmail のラベル名は IMAP では modified UTF-7 で書く（日本語のラベルを付けるため）。
"""
import base64
import email
import imaplib
import os
import re
from email import policy
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime, getaddresses

from nc import JST

GMAIL_USER = os.environ["GMAIL_USER"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "")
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MAX_FULL_SIZE = 1_500_000   # これより大きいメール（重い添付）はヘッダーだけ読む
BODY_LIMIT = 900   # 仕分けにはこれで足りる（長いほど Codex の使用量が増える）


def imap_utf7(text):
    """IMAP の modified UTF-7（RFC 3501 5.1.3）"""
    out, buf = [], []

    def flush():
        if buf:
            b64 = base64.b64encode("".join(buf).encode("utf-16-be")).decode().rstrip("=")
            out.append("&" + b64.replace("/", ",") + "-")
            buf.clear()

    for ch in text:
        if 0x20 <= ord(ch) <= 0x7E:
            flush()
            out.append("&-" if ch == "&" else ch)
        else:
            buf.append(ch)
    flush()
    return "".join(out)


def _accounts():
    out = []
    for part in os.environ.get("MAIL_ACCOUNTS", "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            out.append((k.strip().lower(), v.strip()))
    return out


ACCOUNTS = _accounts()


def _is_own(addr):
    a = (addr or "").lower()
    return a == GMAIL_USER.lower() or any(key in a for key, _ in ACCOUNTS)


def dec(value):
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:  # noqa: BLE001
        return value or ""


def _body_text(msg, limit=BODY_LIMIT, keep_links=False):
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
        if part is None:
            return ""
        text = part.get_content()
        if part.get_content_type() == "text/html":
            text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
            if keep_links:   # 詳しい要約用: リンク先を残す（<a href="URL">文字</a> → 文字 (URL)）
                text = re.sub(r'(?is)<a\s[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', r"\2 (\1)", text)
            text = re.sub(r"(?s)<[^>]+>", " ", text)
            text = text.replace("&nbsp;", " ")
        text = re.sub(r"[ \t　]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n", text).strip()
        return text[:limit] + ("…" if len(text) > limit else "")
    except Exception:  # noqa: BLE001
        return ""


def _addr(value):
    addrs = getaddresses([value or ""])
    return (addrs[0][1] if addrs else "").lower()


class Mailbox:
    """with Mailbox() で受信トレイ、with Mailbox(all_mail=True) で「すべてのメール」（スター付きを探す・外す）"""

    def __init__(self, all_mail=False):
        self.all_mail = all_mail

    def _all_mail_name(self):
        typ, data = self.imap.list()
        for line in data or []:
            line = line.decode() if isinstance(line, bytes) else str(line)
            if "\\All" in line:
                m = re.search(r'"([^"]+)"\s*$', line) or re.search(r"(\S+)\s*$", line)
                return m.group(1)
        raise RuntimeError("「すべてのメール」フォルダが見つかりません")

    def __enter__(self):
        self.imap = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        self.imap.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        box = f'"{self._all_mail_name()}"' if self.all_mail else "INBOX"
        typ, _ = self.imap.select(box)  # 書き込み可（ラベル・スターのため）。既読はつけない
        if typ != "OK":
            raise RuntimeError(f"{box} を開けませんでした")
        uv = self.imap.response("UIDVALIDITY")[1]
        self.uidvalidity = int(uv[0]) if uv and uv[0] else 0
        return self

    def __exit__(self, *a):
        try:
            self.imap.logout()
        except Exception:  # noqa: BLE001
            pass

    def _uids(self, typ_data):
        typ, data = typ_data
        if typ != "OK" or not data or not data[0]:
            return []
        return [int(x) for x in data[0].split()]

    def uids_after(self, last_uid):
        return [u for u in self._uids(self.imap.uid("SEARCH", None, f"UID {last_uid + 1}:*")) if u > last_uid]

    def uids_since(self, since_date):
        d = f"{since_date.day:02d}-{MONTHS[since_date.month - 1]}-{since_date.year}"
        return self._uids(self.imap.uid("SEARCH", None, f"(SINCE {d})"))

    def max_uid(self):
        uids = self._uids(self.imap.uid("SEARCH", None, "ALL"))
        return max(uids) if uids else 0

    def in_inbox(self, uids):
        """まだ受信トレイにある（アーカイブされていない）UID"""
        if not uids:
            return set()
        typ, data = self.imap.uid("FETCH", ",".join(str(u) for u in uids), "(FLAGS)")
        found = set()
        for item in data or []:
            line = item[0] if isinstance(item, tuple) else item
            m = re.search(rb"UID (\d+)", line or b"")
            if m:
                found.add(int(m.group(1)))
        return found

    def fetch(self, uid, body_limit=BODY_LIMIT, keep_links=False):
        typ, meta = self.imap.uid("FETCH", str(uid), "(RFC822.SIZE X-GM-MSGID BODY.PEEK[HEADER])")
        if typ != "OK" or not meta or not isinstance(meta[0], tuple):
            return None
        size = int(re.search(rb"RFC822\.SIZE (\d+)", meta[0][0]).group(1))
        gm = re.search(rb"X-GM-MSGID (\d+)", meta[0][0])
        header_only = size > MAX_FULL_SIZE
        raw = meta[0][1]
        if not header_only:
            typ, full = self.imap.uid("FETCH", str(uid), "(BODY.PEEK[])")
            raw = full[0][1]
        msg = email.message_from_bytes(raw, policy=policy.default)
        try:
            dt = parsedate_to_datetime(msg["Date"]).astimezone(JST)
        except Exception:  # noqa: BLE001
            dt = None
        rcpt = getaddresses(msg.get_all("To", []) + msg.get_all("Cc", []) + msg.get_all("Delivered-To", []))
        rcpt_text = " ".join(f"{n} {a}" for n, a in rcpt)
        account = "その他"
        for key, label in ACCOUNTS:
            if key in rcpt_text.lower():
                account = label
                break
        sender = dec(msg.get("From"))
        return {
            "uid": uid,
            "gmid": gm.group(1).decode() if gm else None,   # Gmail のメールID（フォルダが変わっても同じ）
            "from_addr": _addr(msg.get("From")),
            "msgid": (msg.get("Message-ID") or f"uid-{uid}").strip(),
            "date": dt.isoformat() if dt else None,
            "account": account,
            "from": sender,
            "subject": dec(msg.get("Subject")) or "（件名なし）",
            "body": "（大きい添付付きのため本文は省略）" if header_only else _body_text(msg, body_limit, keep_links),
            # ルール照合用: 差出人・List-Id と、自分以外の宛先/CC（メーリスのアドレスなど）
            # 自分のアドレスは外す（でないと「kyoto-u.ac.jp」が大学宛ての全メールに当たってしまう）
            "match_text": f"{sender} {msg.get('List-Id', '')} " + " ".join(
                f"{n} {a}" for n, a in rcpt if not _is_own(a)),
        }

    def add_labels(self, uid, labels):
        if labels:
            arg = "(" + " ".join(f'"{imap_utf7(x)}"' for x in labels) + ")"
            typ, _ = self.imap.uid("STORE", str(uid), "+X-GM-LABELS", arg)
            if typ != "OK":
                raise RuntimeError(f"ラベルを付けられませんでした: uid={uid}")

    def star(self, uid, on=True):
        typ, _ = self.imap.uid("STORE", str(uid), "+FLAGS" if on else "-FLAGS", r"(\Flagged)")
        if typ != "OK":
            raise RuntimeError(f"スターを{'付け' if on else '外せ'}ませんでした: uid={uid}")

    def remove_labels(self, uid, labels):
        if labels:
            arg = "(" + " ".join(f'"{imap_utf7(x)}"' for x in labels) + ")"
            self.imap.uid("STORE", str(uid), "-X-GM-LABELS", arg)

    # ---- ここから下は Mailbox(all_mail=True) で使う ----

    def starred(self):
        """スター付きのメール（どのフォルダにあっても）。[{gmid, uid, date, from, subject, account}]"""
        uids = self._uids(self.imap.uid("SEARCH", "X-GM-RAW", '"is:starred"'))
        out = []
        for uid in uids:
            typ, data = self.imap.uid("FETCH", str(uid),
                                      "(X-GM-MSGID BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE TO CC DELIVERED-TO)])")
            if typ != "OK" or not data or not isinstance(data[0], tuple):
                continue
            gm = re.search(rb"X-GM-MSGID (\d+)", data[0][0])
            msg = email.message_from_bytes(data[0][1], policy=policy.default)
            try:
                dt = parsedate_to_datetime(msg["Date"]).astimezone(JST).isoformat()
            except Exception:  # noqa: BLE001
                dt = None
            rcpt = getaddresses(msg.get_all("To", []) + msg.get_all("Cc", []) + msg.get_all("Delivered-To", []))
            rcpt_text = " ".join(f"{n} {a}" for n, a in rcpt).lower()
            account = next((label for key, label in ACCOUNTS if key in rcpt_text), "その他")
            out.append({"gmid": gm.group(1).decode() if gm else None, "uid": uid, "date": dt,
                        "from": dec(msg.get("From")), "subject": dec(msg.get("Subject")) or "（件名なし）",
                        "account": account})
        return out

    def fetch_gmid(self, gmid, body_limit=BODY_LIMIT, keep_links=False):
        """Gmail のメールIDで1通読む（既読にしない）。見つからなければ None"""
        uids = self._uids(self.imap.uid("SEARCH", "X-GM-MSGID", str(gmid)))
        return self.fetch(uids[0], body_limit, keep_links) if uids else None

    def star_gmid(self, gmid, on=True):
        """Gmail のメールIDでスターを付け外し。見つからなければ False"""
        uids = self._uids(self.imap.uid("SEARCH", "X-GM-MSGID", str(gmid)))
        for uid in uids:
            self.star(uid, on)
        return bool(uids)

    def archive(self, uid):
        """受信トレイから外す（Gmail の「アーカイブ」）。メール自体は「すべてのメール」に残る"""
        typ, _ = self.imap.uid("STORE", str(uid), "-X-GM-LABELS", r"(\Inbox)")
        if typ != "OK":
            raise RuntimeError(f"アーカイブできませんでした: uid={uid}")
