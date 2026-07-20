#!/usr/bin/env python3
"""Block self: 强制拦截 process_inbox.py 调用除非 URL 已在 notes.md"""
import sys
import os

NOTES = "/root/.local/share/mimocode/memory/sessions/ses_08c4f6069ffeNrx5mrwd6kJF0B/notes.md"
BLOCKED = []

def check_url_in_notes(url):
    if not os.path.exists(NOTES):
        return False
    with open(NOTES) as f:
        return url in f.read()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: block_self.py <url>")
        sys.exit(1)

    url = sys.argv[1]
    if not check_url_in_notes(url):
        print(f"BLOCKED: {url} not in notes.md", file=sys.stderr)
        sys.exit(1)
    print(f"ALLOWED: {url}")
