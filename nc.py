"""Nextcloud（WebDAV）。Recall の記録は読むだけ。書くのは Concierge 専用のフォルダ（/ob/Concierge）だけ。"""
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

NC_BASE = os.environ.get("NC_BASE", "http://192.168.11.200:8081")
NC_USER = os.environ["NC_USER"]
NC_PASS = os.environ["NC_PASS"]
DAV = f"{NC_BASE}/remote.php/dav/files/{NC_USER}"

RECALL_ROOT = "/ob/Recall"
JOURNAL_DIR = f"{RECALL_ROOT}/日記"
# 自分のタスク（Recall が書く）。タスク.md は以前の分類のノート
TASK_FILES = [f"{RECALL_ROOT}/{n}.md" for n in ("短期タスク", "長期タスク", "開発タスク", "タスク")]
# ブリーフの「ひとつ思い出す」の元。やりたいこと.md は以前の分類のノート
WISH_FILES = [f"{RECALL_ROOT}/アイデア.md", f"{RECALL_ROOT}/長期タスク.md", f"{RECALL_ROOT}/やりたいこと.md"]
CONCIERGE_ROOT = "/ob/Concierge"

DAY_START_HOUR = 6   # Recall と同じ（朝6時より前は前日扱い）
JST = timezone(timedelta(hours=9))
WEEKDAYS = "月火水木金土日"

s = requests.Session()
s.auth = (NC_USER, NC_PASS)


def log(msg):
    print(f"{datetime.now(JST):%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def log_date(dt):
    return (dt - timedelta(hours=DAY_START_HOUR)).date()


def get_text(path):
    r = s.get(DAV + quote(path), timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.content.decode("utf-8")


def put_text(path, text):
    cur = ""
    for p in path.rsplit("/", 1)[0].strip("/").split("/"):
        cur += "/" + p
        r = s.request("MKCOL", DAV + quote(cur), timeout=30)
        if r.status_code not in (201, 405):
            r.raise_for_status()
    s.put(DAV + quote(path), data=text.encode("utf-8"), timeout=30).raise_for_status()
