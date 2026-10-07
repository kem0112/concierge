#!/usr/bin/env python3
"""token.json で受け皿の Gmail を読めるか確認する。直近のメールの日時・宛先・差出人・件名を表示するだけ。

  python tools/gmail_test.py [token.json] [件数]
"""
import sys

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "token.json"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    creds = Credentials.from_authorized_user_file(path, SCOPES)
    if not creds.valid:
        creds.refresh(Request())
    svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
    profile = svc.users().getProfile(userId="me").execute()
    print(f"アカウント: {profile['emailAddress']}（メール総数 {profile['messagesTotal']}）\n")
    res = svc.users().messages().list(userId="me", maxResults=n).execute()
    for m in res.get("messages", []):
        msg = svc.users().messages().get(
            userId="me", id=m["id"], format="metadata",
            metadataHeaders=["Date", "To", "From", "Subject"]).execute()
        h = {x["name"]: x["value"] for x in msg["payload"]["headers"]}
        print(f"{h.get('Date', '')}\n  To: {h.get('To', '')}\n  From: {h.get('From', '')}\n  件名: {h.get('Subject', '（件名なし）')}\n")


if __name__ == "__main__":
    main()
