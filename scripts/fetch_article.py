import re, sys, html as html_mod

try:
    data = sys.stdin.buffer.read()
    text = data.decode('utf-8', errors='replace')
except:
    text = sys.stdin.read()

# Page length
print("Page length:", len(text))

# var msg_title
m = re.search(r'var msg_title\s*=\s*"([^"]+)"', text)
if m: print("Title:", m.group(1))

# var msg_desc
m = re.search(r'var msg_desc\s*=\s*"([^"]+)"', text)
if m: print("Desc:", m.group(1)[:300])

# var msg_cdn_url
m = re.search(r'var msg_cdn_url\s*=\s*"([^"]+)"', text)
if m: print("CDN:", m.group(1))

# Try to find rich media content
m = re.search(r'id="js_content"[^>]*>(.*?)</div>\s*<script', text, re.DOTALL)
if m:
    content = re.sub(r'<[^>]+>', '', m.group(1))
    content = re.sub(r'\s+', ' ', content).strip()
    print("Rich content:", content[:2000])

# Try noscript
m = re.search(r'<noscript>(.*?)</noscript>', text, re.DOTALL)
if m:
    content = re.sub(r'<[^>]+>', '', m.group(1))
    content = re.sub(r'\s+', ' ', content).strip()
    print("Noscript:", content[:1000])

# Try any text in page body
m = re.search(r'<div[^>]*class="rich_media_content[^"]*"[^>]*>(.*?)</div>', text, re.DOTALL)
if m:
    content = re.sub(r'<[^>]+>', '', m.group(1))
    content = re.sub(r'\s+', ' ', content).strip()
    print("Rich media content:", content[:2000])

# Print first 3000 chars of text to see what's there
print("\n---RAW HTML SNIPPET---")
print(text[:3000])
