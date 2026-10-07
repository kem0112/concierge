"""Nextcloud（WebDAV）から Recall の記録を読む。Concierge は読むだけで、ob には書かない。"""
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
