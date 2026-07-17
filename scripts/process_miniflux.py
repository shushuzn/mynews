#!/usr/bin/env python3
"""
process_miniflux.py
周期性从 Miniflux 获取新 entries，写入 _inbox 供 process_inbox.py 处理
基于 URL 追踪已处理条目，状态存在本地 JSON 文件
"""
import subprocess
import time
import json
import urllib.request
import os
import base64
import hashlib
import fcntl

MINIFLUX_URL = "http://127.0.0.1:8080"
AUTH = ("admin", "admin123")
STATE_FILE = "/root/mynews/data/processed_urls.json"
PROCESSING_FILE = "/root/mynews/data/processing_urls.json"
data_dir = os.path.dirname(PROCESSING_FILE)
if data_dir and not os.path.exists(data_dir):
    os.makedirs(data_dir, exist_ok=True)
POLL_INTERVAL = 300  # 5分钟
BATCH_SIZE = 100
PROCESSING_TIMEOUT = 600  # 10分钟
LOCK_FILE = "/tmp/miniflux_processor.lock"
BASE_DIR = "/root/mynews"
INBOX_DIR = os.path.join(BASE_DIR, "_inbox")
SEEN_FILE = os.path.join(INBOX_DIR, ".seen_ids.json")
INBOX_MAX_FILES = 10000

GITHUB_REPOS = [
    "leanprover-community/mathlib4",
]


def write_to_inbox(entry):
    url = entry.get("url", "")
    title = entry.get("title", "untitled")
    content = entry.get("content", "")
    feed = entry.get("feed", {})
    feed_title = feed.get("title", "") if feed else ""
    entry_id = entry.get("id", "")

    is_github = "/commit/" in url and "github.com" in url

    if is_github:
        sha = str(entry_id).replace("gh_", "") if str(entry_id).startswith("gh_") else hashlib.md5(url.encode()).hexdigest()[:12]
        filename = f"gh_{sha}.md"
    else:
        filename = f"mf_{entry_id}.md"

    filepath = os.path.join(INBOX_DIR, filename)

    lines = [
        f"# SOURCE_URL\n{url}",
        f"# SOURCE_TYPE\n{'github_commit' if is_github else 'rss_entry'}",
        f"# FEED\n{feed_title}",
        f"# ENTRY_ID\n{entry_id}",
        "",
        "---",
        "",
        content if content else f"# TITLE\n{title}",
    ]

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return filepath


def get_json(path):
    url = f"{MINIFLUX_URL}{path}"
    creds = base64.b64encode(f"{AUTH[0]}:{AUTH[1]}".encode()).decode()
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Basic {creds}")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"API error {path}: {e}")
        return None


def load_processed():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return set(json.load(f).get("processed_urls", []))
    return set()


def save_processed(urls):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    existing = load_processed()
    updated = existing | set(urls)
    with open(STATE_FILE, "w") as f:
        json.dump({"processed_urls": sorted(list(updated))}, f, indent=2)


def load_processing():
    if os.path.exists(PROCESSING_FILE):
        with open(PROCESSING_FILE) as f:
            return json.load(f).get("processing_urls", {})
    return {}


def save_processing_urls(processing_urls):
    os.makedirs(os.path.dirname(PROCESSING_FILE), exist_ok=True)
    with open(PROCESSING_FILE, "w") as f:
        json.dump({"processing_urls": processing_urls}, f, indent=2)


def remove_processing(url):
    processing = load_processing()
    processing.pop(url, None)
    save_processing_urls(processing)


def cleanup_stale_processing():
    processing = load_processing()
    now = time.time()
    stale = [url for url, t in processing.items() if now - t > PROCESSING_TIMEOUT]
    for url in stale:
        processing.pop(url, None)
    if stale:
        save_processing_urls(processing)
        print(f"  Cleaned up {len(stale)} stale processing URLs")
    return len(stale)


def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen(urls):
    os.makedirs(os.path.dirname(SEEN_FILE), exist_ok=True)
    existing = load_seen()
    updated = existing | set(urls)
    with open(SEEN_FILE, "w") as f:
        json.dump(sorted(list(updated)), f, indent=2)


def fetch_github_commits(repo, limit=20):
    try:
        import urllib.request
        url = f"https://api.github.com/repos/{repo}/commits?per_page={limit}"
        req = urllib.request.Request(url)
        req.add_header("Accept", "application/vnd.github.v3+json")
        req.add_header("User-Agent", "mynews-processor")
        with urllib.request.urlopen(req, timeout=30) as r:
            commits = json.loads(r.read())
            results = []
            for c in commits:
                sha = c["sha"]
                message = c["commit"]["message"]
                author = c["commit"]["author"]["name"]
                date = c["commit"]["author"]["date"]
                commit_url = c["html_url"]
                diff_url = f"https://github.com/{repo}/commit/{sha}.diff"

                diff_content = ""
                try:
                    diff_req = urllib.request.Request(diff_url)
                    diff_req.add_header("Accept", "text/plain")
                    diff_req.add_header("User-Agent", "mynews-processor")
                    with urllib.request.urlopen(diff_req, timeout=15) as diff_r:
                        diff_content = diff_r.read().decode("utf-8", errors="replace")[:8000]
                except:
                    pass

                files_changed = c.get("stats", {}).get("total", 0)
                additions = c.get("stats", {}).get("additions", 0)
                deletions = c.get("stats", {}).get("deletions", 0)

                content = f"""GitHub Commit

Repo: {repo}
SHA: {sha}
Author: {author}
Date: {date}
Files changed: {files_changed} | +{additions} -{deletions}

Message:
{message}

Diff URL: {diff_url}

Diff (truncated):
{diff_content}
"""

                results.append({
                    "id": f"gh_{sha}",
                    "url": commit_url,
                    "title": f"[{repo}] {message.split(chr(10))[0][:80]}",
                    "content": content,
                    "feed": {
                        "id": 0,
                        "title": f"GitHub: {repo}",
                        "feed_url": f"https://github.com/{repo}",
                        "site_url": f"https://github.com/{repo}"
                    }
                })
            return results
    except Exception as e:
        print(f"  GitHub API error for {repo}: {e}")
        return []


def get_new_entries():
    cleanup_stale_processing()

    github_all = []
    for repo in GITHUB_REPOS:
        github_entries = fetch_github_commits(repo, limit=20)
        github_all.extend(github_entries)

    all_entries = list(github_all)
    min_entries = BATCH_SIZE * 200
    offset = 0

    while len(all_entries) < min_entries:
        batch_size = min(500, min_entries - len(all_entries))
        data = get_json(f"/v1/entries?limit={batch_size}&offset={offset}&direction=desc")
        if not data or not data.get("entries"):
            break
        all_entries.extend(data["entries"])
        if len(data["entries"]) < 500:
            break
        offset += 500

    if not all_entries:
        return []
    seen = load_seen()
    processing = load_processing()
    entries = [e for e in all_entries if e.get("url") and e["url"] not in seen and e["url"] not in processing]
    return entries[:BATCH_SIZE]


def process_entry(entry):
    url = entry.get("url")
    if not url or url.startswith("javascript:"):
        return False
    title = entry.get("title", "untitled")
    entry_id = entry.get("id", "")

    lock_fd = os.open(PROCESSING_FILE + ".lock", os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        processing = load_processing()
        if url in processing:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            print(f"  [{entry_id}] {title[:60]}")
            print(f"     URL: {url}")
            print(f"     Being processed by another instance, skipping")
            return True

        seen = load_seen()
        if url in seen:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            print(f"  [{entry_id}] {title[:60]}")
            print(f"     URL: {url}")
            print(f"     Already seen, skipping")
            return True

        processing[url] = time.time()
        save_processing_urls(processing)
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
    except Exception:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        raise

    os.makedirs(INBOX_DIR, exist_ok=True)
    filepath = write_to_inbox(entry)

    lock_fd2 = os.open(PROCESSING_FILE + ".lock", os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(lock_fd2, fcntl.LOCK_EX)
        remove_processing(url)
        save_seen([url])
        fcntl.flock(lock_fd2, fcntl.LOCK_UN)
    finally:
        os.close(lock_fd2)

    print(f"  [{entry_id}] {title[:60]}")
    print(f"     URL: {url}")
    print(f"     Written to inbox: {os.path.basename(filepath)}")
    return True


def is_process_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def trim_inbox():
    files = sorted(
        (os.path.join(INBOX_DIR, f) for f in os.listdir(INBOX_DIR) if f.endswith(".md")),
        key=os.path.getmtime
    )
    if len(files) > INBOX_MAX_FILES:
        to_delete = files[:len(files) - INBOX_MAX_FILES]
        for f in to_delete:
            os.remove(f)
        print(f"  Trimmed {len(to_delete)} oldest inbox files (limit {INBOX_MAX_FILES})")


def main(daemon=False):
    lock_fd = open(LOCK_FILE, 'w')
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Another instance is running, exiting")
        lock_fd.close()
        return

    seen_count = len(load_seen())
    print(f"Miniflux processor starting, {seen_count} URLs already seen")
    if daemon:
        print(f"Daemon mode, poll interval: {POLL_INTERVAL}s")

    try:
        while True:
            entries = get_new_entries()
            if entries:
                ts = time.strftime("%H:%M:%S")
                print(f"\n[{ts}] Found {len(entries)} new entries to process")
                for e in entries:
                    process_entry(e)
            else:
                print(f"[{time.strftime('%H:%M:%S')}] No new entries")

            trim_inbox()

            if not daemon:
                break
            time.sleep(POLL_INTERVAL)
    finally:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)


if __name__ == "__main__":
    import sys
    daemon = "--daemon" in sys.argv
    main(daemon=daemon)
