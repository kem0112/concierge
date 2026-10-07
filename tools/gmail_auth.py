#!/usr/bin/env python3
"""Mac で1回だけ実行する: ブラウザで受け皿の Gmail にログインし、読み取り専用の token.json を作る。

  python tools/gmail_auth.py <client_secret_xxx.json> [token.json]

できた token.json は pk-206 の ~/.config/concierge/ にコピーする（中身は鍵なので、チャット等には貼らない）。
"""
import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    client_file = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "token.json"
    flow = InstalledAppFlow.from_client_secrets_file(client_file, SCOPES)
    # prompt=consent: 毎回同意画面を出して、確実に refresh_token を受け取る
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    with open(out, "w") as f:
        f.write(creds.to_json())
    os.chmod(out, 0o600)
    print(f"保存しました: {out}（refresh_token: {'あり' if creds.refresh_token else 'なし ← やり直しが必要'}）")


if __name__ == "__main__":
    main()
