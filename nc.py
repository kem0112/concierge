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
TASKS_MD = f"{RECALL_ROOT}/タスク.md"
WISHES_MD = f"{RECALL_ROOT}/やりたいこと.md"
CONCIERGE_ROOT = "/ob/Concierge"
HELD_TASKS_MD = f"{CONCIERGE_ROOT}/預かりタスク.md"   # 秘書が預かったタスク（メール由来）

DAY_START_HOUR = 4
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
