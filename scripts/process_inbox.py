#!/usr/bin/env python3
"""
mynews inbox 处理器
- 从 _inbox/ 读取条目（由 process_miniflux.py 生成）
- 本地关键词分类 + MCP flomo 搜索去重
- 成功后从 _inbox/ 移动到 _inbox_done/
- 失败后从 _inbox/ 移动到 _inbox_failed/
"""
import os
import sys
import json
import subprocess
import time
import argparse
import shutil
import re
import urllib.request
import urllib.parse
from pathlib import Path

from mynews_utils import (
    get_base_dir, get_opencode_bin, get_temp_dir, CrossPlatformLock,
    is_wechat_url, fetch_wechat_article
)

BASE_DIR = get_base_dir()
INBOX_DIR = BASE_DIR / "_inbox"
DONE_DIR = BASE_DIR / "_inbox_done"
FAILED_DIR = BASE_DIR / "_inbox_failed"
PROCESSED_FILE = BASE_DIR / "data" / "processed_urls.json"
PROCESSING_FILE = BASE_DIR / "data" / "processing_urls.json"
SEEN_FILE = INBOX_DIR / ".seen_ids.json"
STALE_TIMEOUT = 1800
LOCK_FILE = get_temp_dir() / "inbox_processor.lock"
KIMI_TIMEOUT = 300  # 5 分钟
PYTHON_BIN = sys.executable if sys.executable else ("python" if os.name == "nt" else "python3")
FLOMO_API_URL = "https://flomoapp.com/mcp"
FLOMO_TOKEN = "fmcp_tQUCgZl19bcH5slSicw2CotCJgw8V_1qdrHWs3w0Q8s"

# 确保所有目录存在
for d in [INBOX_DIR, DONE_DIR, FAILED_DIR,
          PROCESSED_FILE.parent,
          PROCESSING_FILE.parent]:
    d.mkdir(parents=True, exist_ok=True)


def load_processed():
    if PROCESSED_FILE.exists():
        with PROCESSED_FILE.open(encoding="utf-8") as f:
            return set(json.load(f).get("processed_urls", []))
    return set()


def save_processed(urls):
    existing = load_processed()
    updated = existing | set(urls)
    PROCESSED_FILE.parent.mkdir(parents=True, exist_ok=True)
    with PROCESSED_FILE.open("w", encoding="utf-8") as f:
        json.dump({"processed_urls": sorted(list(updated))}, f, indent=2)


def load_processing():
    if PROCESSING_FILE.exists():
        with PROCESSING_FILE.open(encoding="utf-8") as f:
            return json.load(f).get("processing_urls", {})
    return {}


def save_processing_urls(processing_urls):
    PROCESSING_FILE.parent.mkdir(parents=True, exist_ok=True)
    with PROCESSING_FILE.open("w", encoding="utf-8") as f:
        json.dump({"processing_urls": processing_urls}, f, indent=2)


def remove_processing(url):
    processing = load_processing()
    processing.pop(url, None)
    save_processing_urls(processing)


def extract_source_info(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.read().split("\n")

    source_url = ""
    source_type = "rss_entry"
    feed_title = ""
    entry_id = ""
    content_start = 0

    for i, line in enumerate(lines):
        if line.startswith("# SOURCE_URL") and i + 1 < len(lines):
            source_url = lines[i + 1].strip()
        elif line.startswith("# SOURCE_TYPE") and i + 1 < len(lines):
            source_type = lines[i + 1].strip()
        elif line.startswith("# FEED_TITLE") and i + 1 < len(lines):
            feed_title = lines[i + 1].strip()
        elif line.startswith("# ENTRY_ID") and i + 1 < len(lines):
            entry_id = lines[i + 1].strip()
        elif line.startswith("---"):
            content_start = i + 1
            break

    actual_content = "\n".join(lines[content_start:]).strip()
    return source_url, source_type, feed_title, entry_id, actual_content


def extract_wechat_if_needed(source_url, content, feed_title):
    """
    如果是微信公众号 URL 且内容为空或过短，尝试重新抓取
    返回: (content, feed_title, article_title)
    article_title 是从文章内容中提取的实际标题（不同于 feed_title 频道名）
    """
    if not is_wechat_url(source_url):
        return content, feed_title, None

    article_title = None

    # 如果已有有效内容（超过500字符），尝试从内容中提取真实标题
    if len(content) > 500:
        print(f"  [wechat] 已有足够内容 ({len(content)} 字符)，提取文章标题...")
        lines = [l.strip() for l in content.split("\n") if l.strip()]
        if lines:
            # 取第一行或第一段作为标题
            article_title = lines[0][:80]
        return content, feed_title, article_title

    print(f"  [wechat] 检测到微信公众号 URL，尝试重新抓取...")
    print(f"    URL: {source_url[:60]}...")

    text, source, error, wx_title = fetch_wechat_article(source_url, use_cache=True)

    if text:
        print(f"  [wechat] 抓取成功 ({source})，内容长度: {len(text)} 字符")
        # 优先使用从 HTML 提取的真实标题
        article_title = wx_title if wx_title else None
        if not article_title:
            # 降级：从正文第一段提取
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            for line in lines:
                if len(line) > 10 and not re.match(r'^[⇅\s\xa0~]+$', line):
                    article_title = line[:80]
                    break
        return text, feed_title or source, article_title
    else:
        print(f"  [wechat] 抓取失败: {error}")
        # 返回原始内容，让后续流程处理
        return content, feed_title, None


def move_to_failed(filepath, reason):
    basename = Path(filepath).name
    failed_path = FAILED_DIR / basename
    if Path(filepath).exists():
        shutil.move(filepath, failed_path)
    print(f"  [{basename}] Moved to failed: {reason}")


def move_to_done(filepath):
    basename = Path(filepath).name
    done_path = DONE_DIR / basename
    if Path(filepath).exists():
        shutil.move(filepath, done_path)


# ------------------------------------------------------------
# 域名/二级领域分类器（基于关键词）
# ------------------------------------------------------------
DOMAIN_KEYWORDS = {
    "技术": {
        "AI芯片": ["芯片", "CPU", "GPU", "AI芯片", "处理器", "算力", "真武", "T-Head", "NPU", "HBM", "半导体"],
        "大模型": ["大模型", "LLM", "GPT", "ChatGPT", "模型训练", "参数", "AGI", "多模态", "推理", "RAG", "Embedding"],
        "软件开发": ["软件", "代码", "开源", "框架", "API", "SDK", "算法", "编程", "GitHub", "开发", "程序员"],
        "互联网": ["互联网", "平台", "电商", "SaaS", "云计算", "数据中心", "服务器", "CDN", "运营商"],
        "AI应用": ["AI", "人工智能", "机器学习", "深度学习", "NLP", "CV", "计算机视觉", "语音", "AIGC", "Copilot"],
    },
    "社会科学": {
        "军事历史": ["红军", "长征", "军事", "战争", "军队", "国防", "战役", "士兵", "革命"],
        "社会治理": ["社会", "治理", "社区", "基层", "民生", "公共服务", "治理"],
        "政治学": ["政治", "政府", "政党", "政策", "外交", "国际关系", "主权", "政治学"],
        "经济学": ["经济", "市场", "金融", "投资", "贸易", "货币", "银行", "GDP", "通胀"],
        "教育": ["教育", "学校", "教学", "学生", "教师", "课程", "高等教育", "义务教育"],
        "法律": ["法律", "法规", "法案", "司法", "法院", "律师", "判决", "立法", "合规"],
        "哲学": ["哲学", "存在", "意识", "形而上学", "认识论", "本体论", "伦理学", "斯多葛", "塞涅卡", "西西弗斯", "生命之短暂", "焦虑", "精神困境"],
        "心理学": ["心理", "焦虑", "抑郁", "精神", "认知", "情感", "压力", "创伤", "潜意识"],
    },
    "自然科学": {
        "物理": ["物理", "量子", "相对论", "粒子", "天体", "宇宙", "黑洞", "引力", "磁场"],
        "化学": ["化学", "分子", "反应", "元素", "化合物", "材料"],
        "生物": ["生物", "基因", "细胞", "进化", "生态", "蛋白质", "DNA", "RNA"],
        "环境科学": ["环境", "气候", "碳排放", "污染", "生态", "能源", "可持续发展"],
    },
    "政治": {
        "外交": ["外交", "国际", "双边", "多边", "峰会", "外交关系", "使领馆"],
        "国际关系": ["国际关系", "地缘政治", "大国关系", "联盟", "制裁", "核武"],
        "国防": ["国防", "军队现代化", "军工", "武器装备", "军事演习"],
    },
    "医学": {
        "临床医学": ["临床", "诊断", "治疗", "手术", "药物", "医疗器械", "医院"],
        "药物学": ["药物", "靶点", "临床试验", "化合物", "生物制药", "疫苗"],
        "公共卫生": ["公共卫生", "流行病", "疫情防控", "疫苗接种", "CDC"],
    },
    "经济": {
        "产业": ["产业", "制造业", "供应链", "产业链", "工业", "实体经济"],
        "企业": ["企业", "公司", "创业", "融资", "上市", "商业模式"],
        "市场": ["市场", "消费", "零售", "房地产", "股市", "资本市场"],
    },
    "管理": {
        "企业战略": ["战略", "商业模式", "竞争", "增长", "转型", "并购"],
        "组织管理": ["组织", "管理", "领导力", "人才", "团队", "绩效"],
    },
    "教育科学": {
        "教育政策": ["教育政策", "双减", "素质教育", "新课改", "高考改革"],
        "教育技术": ["教育技术", "智慧教育", "在线教育", "EdTech"],
    },
    "安全": {
        "网络安全": ["网络", "安全", "漏洞", "数据泄露", "黑客", "加密", "隐私"],
        "信息安全": ["信息", "安全", "认证", "权限", "风控"],
    },
    "游戏": {
        "游戏产业": ["游戏", "电竞", "手游", "端游", "游戏开发", "VR", "AR", "元宇宙"],
    },
    "法律": {
        "法律研究": ["法律", "法学", "判例", "法律解释", "司法实践"],
    },
}


def classify_content(title: str, content: str) -> tuple:
    """基于关键词分类 content，返回 (domain, subdomain, knowledge_slug)。
    如果无法分类，返回 (None, None, None)。"""
    text = (title + " " + content[:3000]).lower()

    best_domain = None
    best_subdomain = None
    best_score = 0

    for domain, subdomains in DOMAIN_KEYWORDS.items():
        for subdomain, keywords in subdomains.items():
            score = sum(1 for kw in keywords if kw.lower() in text)
            if score > best_score:
                best_score = score
                best_domain = domain
                best_subdomain = subdomain

    if best_score == 0:
        # 默认归入社会科学-其他
        return "社会科学", "其他", slugify(title)[:20]

    knowledge = slugify(title)[:20]
    return best_domain, best_subdomain, knowledge


def generate_flomo_content(title: str, content: str, domain: str, subdomain: str, knowledge: str, source_title: str = None) -> str:
    """从原始内容生成 flomo 格式字符串（自动添加高亮和下划线）。"""
    import re

    # 清理原始内容中的 HTML 标签
    clean = re.sub(r'<[^>]+>', '', content)
    clean = re.sub(r'\s+', ' ', clean).strip()

    # 取前 500 字作为正文摘要
    body_preview = clean[:500]
    if len(clean) > 500:
        body_preview += "……"

    # 提取概念：取第一句完整的话
    first_para = clean.split('。')[0] if '。' in clean else clean[:200]
    if len(first_para) > 200:
        first_para = first_para[:197] + "……"
    if not first_para.strip():
        first_para = title[:80]  # 内容为空时用标题兜底

    # 为概念添加 <mark> 高亮：提取核心关键词（名词短语）
    # 策略：取第一句中首个中文名词短语（2-6字）
    concept_text = first_para[:300]
    core_term = _extract_core_term(concept_text)
    if core_term and core_term in concept_text:
        concept_text = concept_text.replace(core_term, f"<mark>{core_term}</mark>", 1)

    # 生成子概念：从正文中提取关键句子，并为每句首个关键词加 <u>
    sentences = re.split(r'[。\n]', clean)
    bullets = []
    for s in sentences:
        s = s.strip()
        if len(s) > 10 and len(s) < 150 and len(bullets) < 5:
            if not re.match(r'^[\d一二三四五六七八九十]+[.、:：]', s):
                # 提取句中首个关键词并加 <u>
                bullet_term = _extract_core_term(s)
                if bullet_term:
                    s = s.replace(bullet_term, f"<u>{bullet_term}</u>", 1)
                bullets.append(f"- {s[:120]}")

    subconcept_block = ""
    if bullets:
        subconcept_block = "\n\n**子概念**：\n\n" + "\n".join(bullets)
    else:
        subconcept_block = "\n\n**子概念**：\n\n- " + concept_text[:80]

    return f"""#信号笔记 #{domain} #{subdomain}

**{domain}_{subdomain}_{knowledge}**

**来源**：{source_title if source_title else "网络"}

**概念**：{concept_text}{subconcept_block}
"""


def _extract_core_term(text: str) -> str:
    """从文本中提取核心关键词（首个中文名词短语，2-6字）。"""
    # 匹配首个 2-6 字的中文词组（包含常见名词后缀）
    patterns = [
        r'[\u4e00-\u9fff]{2,4}法则',
        r'[\u4e00-\u9fff]{2,6}效应',
        r'[\u4e00-\u9fff]{2,6}理论',
        r'[\u4e00-\u9fff]{2,6}原则',
        r'[\u4e00-\u9fff]{2,6}模型',
        r'[\u4e00-\u9fff]{2,6}规律',
        r'[\u4e00-\u9fff]{2,6}机制',
        r'[\u4e00-\u9fff]{2,6}方法',
        r'[\u4e00-\u9fff]{2,6}方案',
        r'[\u4e00-\u9fff]{2,6}策略',
        r'[\u4e00-\u9fff]{2,6}规律',
        r'[\u4e00-\u9fff]{2,6}定律',
        r'[\u4e00-\u9fff]{2,6}现象',
        r'[\u4e00-\u9fff]{2,6}问题',
        r'[\u4e00-\u9fff]{2,6}原因',
        r'[\u4e00-\u9fff]{2,6}结果',
        r'[\u4e00-\u9fff]{3,6}',
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m.group(0)
    # 降级：取前2-4个连续中文
    m = re.search(r'[\u4e00-\u9fff]{2,4}', text)
    return m.group(0) if m else ""


def process_without_kimi(source_url: str, source_type: str, content: str, feed_title: str, filepath: str, args, article_title: str = None) -> tuple:
    """本地处理：分类 → 生成 → 验证 → 上传。返回 (success, created_file)。"""
    # 优先使用从文章内容提取的真实标题，其次使用 feed_title（频道名）
    title = article_title or feed_title or "未命名"
    if not content or len(content) < 50:
        return False, None

    # 1. 分类
    domain, subdomain, knowledge = classify_content(title, content)
    if not domain:
        print(f"    [error] 无法分类内容，请手动处理")
        return False, None

    print(f"    [classify] {domain} / {subdomain} / {knowledge}")

    # 3. 生成 flomo 内容
    flomo_content = generate_flomo_content(title, content, domain, subdomain, knowledge)

    # 4. 创建本地文件（验证用）
    filename = f"{domain}_{subdomain}_{knowledge}.md"
    # 修正段数
    name_part = filename[:-3]
    parts = name_part.split('_')
    while len(parts) > 3:
        parts[2] = parts[2] + '_' + parts[3]
        parts.pop(3)
    filename = '_'.join(parts) + ".md"

    full_path = BASE_DIR / "answers" / domain / subdomain / filename
    full_path.parent.mkdir(parents=True, exist_ok=True)
    # 删除旧文件（同一会话重复运行时会碰到）
    if full_path.exists():
        full_path.unlink()
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(flomo_content)
    print(f"    [file] 已创建: {full_path.relative_to(BASE_DIR)}")

    # 5. hook 验证
    subprocess.run(["git", "add", "-f", str(full_path.relative_to(BASE_DIR))], cwd=str(BASE_DIR))
    hook_result = subprocess.run(
        [PYTHON_BIN, str(BASE_DIR / "hooks" / "pre-commit"), "--staged"],
        cwd=str(BASE_DIR),
        capture_output=True, text=True
    )
    if hook_result.returncode != 0:
        print(f"    [error] 格式验证失败:\n{hook_result.stdout[:300]}")
        subprocess.run(["git", "reset", "HEAD", "--", str(full_path.relative_to(BASE_DIR))], cwd=str(BASE_DIR))
        return False, None

    # 6. 查重并更新已有笔记
    dup_result = search_flomo(knowledge)
    dup_memos = dup_result if dup_result and isinstance(dup_result, list) else []
    if dup_memos:
        old_id = dup_memos[0].get("id") if isinstance(dup_memos[0], dict) else None
        if old_id:
            print(f"    [flomo] 检测到相似笔记 id={old_id}，自动更新...")
            ok = update_flomo(old_id, flomo_content)
            if ok:
                print(f"    [flomo] 更新成功 id={old_id}")
                subprocess.run(["git", "reset", "HEAD", "--", str(full_path.relative_to(BASE_DIR))], cwd=str(BASE_DIR))
                full_path.unlink()
                return True, None
            else:
                print(f"    [flomo] 更新失败，继续新建")

    # 7. 上传到 flomo
    flomo_id = upload_flomo(flomo_content)
    if flomo_id:
        print(f"    [flomo] 上传成功 id={flomo_id}")
        subprocess.run(["git", "reset", "HEAD", "--", str(full_path.relative_to(BASE_DIR))], cwd=str(BASE_DIR))
        full_path.unlink()
        print(f"    [cleanup] 已删除本地文件")
        return True, str(full_path.relative_to(BASE_DIR))
    else:
        print(f"    [flomo] 上传失败（文件保留在: {full_path}）")
        return False, None


def call_kimi(prompt, timeout=KIMI_TIMEOUT):
    """调用 kimi 处理任务（已废弃，保留仅用于兼容）"""
    cmd = ["kimi", "-p", prompt]
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(BASE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = proc.communicate(timeout=timeout)
        if proc.returncode == 0:
            return stdout.strip(), stderr.strip()
        return "", f"kimi error: {stderr[:500]}"
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return "", "kimi timeout"
    except Exception as e:
        return "", f"kimi exception: {e}"


def search_flomo(keyword):
    """搜索 flomo"""
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "memo_search",
            "arguments": {"keywords": keyword}
        }
    }).encode("utf-8")

    req = urllib.request.Request(
        FLOMO_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {FLOMO_TOKEN}"
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read().decode("utf-8")
            # Parse SSE format
            for line in data.split("\n"):
                if line.startswith("data:"):
                    json_str = line[5:].strip()
                    if json_str:
                        result = json.loads(json_str)
                        if "result" in result:
                            return result["result"]
            return None
    except Exception as e:
        print(f"    [flomo search] error: {e}")
        return None


def upload_flomo(content):
    """上传到 flomo"""
    # 转义 content 中的下划线
    def escape_underscore_in_bold(match):
        return "**" + match.group(1).replace("_", "\\_") + "**"
    content_escaped = re.sub(
        r'^\*\*([^*]+)\*\*$', escape_underscore_in_bold, content, flags=re.MULTILINE
    )

    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "memo_create",
            "arguments": {"content": content_escaped}
        }
    }).encode("utf-8")

    req = urllib.request.Request(
        FLOMO_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {FLOMO_TOKEN}"
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read().decode("utf-8")
            # Parse SSE format
            for line in data.split("\n"):
                if line.startswith("data:"):
                    json_str = line[5:].strip()
                    if json_str:
                        result = json.loads(json_str)
                        if "result" in result:
                            memo = result["result"]
                            if "id" in memo:
                                return memo["id"]
                            # Check structuredContent
                            if "structuredContent" in memo and "id" in memo["structuredContent"]:
                                return memo["structuredContent"]["id"]
            return None
    except Exception as e:
        print(f"    [flomo upload] error: {e}")
        return None


def update_flomo(memo_id, content):
    """更新 flomo 已有笔记"""
    def escape_underscore_in_bold(match):
        return "**" + match.group(1).replace("_", "\\_") + "**"
    content_escaped = re.sub(
        r'^\*\*([^*]+)\*\*$', escape_underscore_in_bold, content, flags=re.MULTILINE
    )

    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "memo_update",
            "arguments": {"id": memo_id, "content": content_escaped}
        }
    }).encode("utf-8")

    req = urllib.request.Request(
        FLOMO_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {FLOMO_TOKEN}"
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read().decode("utf-8")
            for line in data.split("\n"):
                if line.startswith("data:"):
                    json_str = line[5:].strip()
                    if json_str:
                        result = json.loads(json_str)
                        if "result" in result:
                            return True
            return False
    except Exception as e:
        print(f"    [flomo update] error: {e}")
        return False


def get_topic_keywords(source_type, content, feed_title):
    """提取主题关键词用于查重"""
    # 简单实现：从标题和内容提取关键词
    lines = content.strip().split("\n")
    title = ""
    for line in lines:
        if line.startswith("#"):
            title = line.strip("# ").strip()
            break
    
    if not title:
        # 尝试从第一行获取
        title = lines[0][:50] if lines else "unknown"
    
    return title, feed_title


def build_prompt(source_type, source_url, content, feed_title, filepath):
    """构建 kimi prompt（已废弃）"""
    topic_title, topic_source = get_topic_keywords(source_type, content, feed_title)
    
    return f"""**mynews 任务**：处理以下信息，生成 flomo 格式笔记并创建本地文件。

**信息内容**：
SOURCE_URL: {source_url}
来源: {feed_title or topic_source}
类型: {source_type}

{content[:2000]}

**步骤**：
1. 先用 MCP 工具 `memo_search` 搜索类似内容查重（搜索主题：{topic_title}）
2. 根据内容确定领域、二级领域、知识点（三段式）
3. 运行 `python3 scripts/check_dir.py <领域> <二级领域>` 确认目录存在
4. 运行 `python3 scripts/title_to_path.py "<标题>"` 获取完整路径
5. 创建 flomo 格式文件（见下方格式要求）
6. 在文件开头加上 `# SOURCE_URL {source_url}` 行
7. 运行 `python3 scripts/validate_flomo.py <文件路径>` 验证格式
8. 打印创建的文件路径

**⚠️ flomo 格式要求**：
```
#信号笔记 #领域 #二级领域

**领域_二级领域_知识点**

**来源**：{feed_title or topic_source}

正文内容...
```

**重要**：
- 第一行必须有 ≥3 个标签（含 #信号笔记）
- 标题必须是三段式 `领域_二级领域_知识点`
- 用 `**加粗**` 而不是 `#` 标题
- 禁止：链接、图片、表格、代码块
- 文件路径必须是 `answers/领域/二级领域/xxx.md`
- 验证通过后打印：`CREATED_FILE: <文件路径>`
"""


def get_next_file(source_type_filter="all"):
    """获取下一个待处理文件"""
    processed = load_processed()
    processing = load_processing()

    files = sorted([str(p) for p in INBOX_DIR.glob("*.md")])

    for f in files:
        source_url, source_type, _, _, _ = extract_source_info(f)
        if not source_url:
            continue
        if source_url in processed:
            continue
        if source_type_filter != "all" and source_type != source_type_filter:
            continue
        entry_time = processing.get(source_url)
        if entry_time is None:
            return f
        if isinstance(entry_time, (int, float)) and time.time() - entry_time > STALE_TIMEOUT:
            return f
    return None


def process_file(filepath, args):
    """处理单个文件"""
    basename = Path(filepath).name
    source_url, source_type, feed_title, entry_id, content = extract_source_info(filepath)

    # 微信公众号特殊处理：重新抓取正文，并提取真实文章标题
    content, feed_title, article_title = extract_wechat_if_needed(source_url, content, feed_title)

    if not source_url:
        print(f"  No SOURCE_URL found, moving to failed")
        move_to_failed(filepath, "no_source_url")
        return False

    if source_url in load_processed():
        print(f"  [{basename}] Already processed, removing")
        move_to_done(filepath)
        return True

    processing = load_processing()
    if source_url in processing:
        entry_time = processing[source_url]
        if isinstance(entry_time, (int, float)) and time.time() - entry_time > STALE_TIMEOUT:
            processing.pop(source_url, None)
            save_processing_urls(processing)
        else:
            print(f"  [{basename}] Being processed, skipping")
            return True

    processing[source_url] = time.time()
    save_processing_urls(processing)

    print(f"  Processing: {basename}")
    print(f"  Type: {source_type}, URL: {source_url[:60]}")

    # 构建 prompt 并调用 kimi（已废弃，改用本地分类）
    # prompt = build_prompt(source_type, source_url, content, feed_title, filepath)
    # stdout, stderr = call_kimi(prompt, timeout=KIMI_TIMEOUT)

    # 使用本地处理：分类 → 生成 → 验证 → 上传
    success, created_file = process_without_kimi(
        source_url, source_type, content, feed_title, filepath, args, article_title
    )

    if not success:
        if not created_file:
            remove_processing(source_url)
            move_to_failed(filepath, "local_processing_failed")
        return False

    # 移动到 done
    remove_processing(source_url)
    save_processed([source_url])
    move_to_done(filepath)

    print(f"    [done] 处理完成")
    return True


ALLOWED_TOP_DIRS = {
    "医学", "安全", "技术", "政治", "教育科学", "法律",
    "游戏", "社会科学", "管理", "经济", "自然科学"
}


def slugify(text: str) -> str:
    """Convert text to a safe filename segment (keep Chinese, alphanumeric, underscore others).
    Truncates to 50 chars. Only allows: Chinese chars, letters, digits, underscores, parentheses."""
    import re
    # Replace non-alphanumeric with underscore (keep Chinese, letters, digits)
    text = re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '_', text)
    text = re.sub(r'_+', '_', text).strip('_')
    return text[:50]


def ask_domain() -> str:
    """Interactive domain selection."""
    print("\n可选领域:")
    sorted_dirs = sorted(ALLOWED_TOP_DIRS)
    for i, d in enumerate(sorted_dirs, 1):
        print(f"  {i}. {d}")
    while True:
        choice = input("选择领域编号（或直接输入领域名）: ").strip()
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(sorted_dirs):
                return sorted_dirs[idx]
        # Try direct name match
        if choice in ALLOWED_TOP_DIRS:
            return choice
        print(f"  无效选择，请重试")


def ask_subdomain() -> str:
    """Interactive subdomain input."""
    while True:
        d = input("二级领域（如：AI芯片, 外交, 军事历史）: ").strip()
        if d and len(d) >= 2:
            return d
        print("  二级领域至少2个字符，请重试")


def ask_tags() -> list:
    """Interactive tag input."""
    print("信号类型标签（五选一）: #趋势信号 #知识基座 #信号笔记 #分析框架 #知识载体")
    while True:
        tag = input("输入信号类型标签（直接回车=信号笔记）: ").strip() or "#信号笔记"
        if tag.startswith('#') or tag.startswith('@'):
            break
        print("  标签必须以 # 或 @ 开头，请重试")
    extra = input("额外标签（如：#AI #开源，输入空格分隔，回车跳过）: ").strip()
    tags = [tag]
    if extra:
        tags.extend([t.strip() for t in extra.split() if t.strip()])
    return tags


def process_url(url: str, args):
    """处理单个 URL，直接完成 fetch → 构建 → 验证 → 上传全流程。"""
    from mynews_utils import is_wechat_url, fetch_wechat_article
    import urllib.request
    import urllib.error

    print(f"\n[URL 模式] {url[:60]}{'...' if len(url) > 60 else ''}")

    # 1. 抓取内容
    if is_wechat_url(url):
        print("  [wechat] 抓取中...")
        text, source, error, wx_title = fetch_wechat_article(url, use_cache=True)
        if not text:
            print(f"  [error] 抓取失败: {error}")
            return False
        print(f"  [ok] 抓取成功 ({source})，{len(text)} 字符")
    else:
        print("  [http] 抓取中...")
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                }
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                html = resp.read().decode("utf-8", errors="replace")
            # 简单提取 <title>
            title_match = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
            wx_title = title_match.group(1).strip() if title_match else ""
            # 简单提取正文（取 <body> 内的文字）
            body_match = re.search(r'<body[^>]*>(.*)</body>', html, re.IGNORECASE | re.DOTALL)
            body = body_match.group(1) if body_match else html
            # 移除标签
            text = re.sub(r'<[^>]+>', ' ', body)
            text = re.sub(r'\s+', ' ', text).strip()
            print(f"  [ok] 抓取成功，{len(text)} 字符")
        except Exception as e:
            print(f"  [error] 抓取失败: {e}")
            return False

    # 2. 提取标题
    # WeChat: 用 wx_title（HTML <title>，真实标题）；其他: 取内容第一行
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    if is_wechat_url(url) and wx_title:
        title = wx_title
    else:
        title = lines[0][:80] if lines else "未命名"
        if len(title) > 60:
            title = title[:57] + "..."

    # 3. 交互收集元信息
    interactive = not (hasattr(args, 'domain') and args.domain)
    print()
    if interactive:
        domain = ask_domain()
        subdomain = ask_subdomain()
        tags = ask_tags()
        print(f"\n  文章标题: {title}")
        print("  文件名格式: 领域_二级领域_知识点.md（三段式，知识点为第三段）")
        while True:
            knowledge = input("知识点名称（第三段，如：WAIC2026新产品发布）: ").strip()
            if not knowledge:
                print("  不能为空，请重新输入")
                continue
            if '-' in knowledge:
                print("  禁止使用 '-'")
                continue
            break
    else:
        domain = args.domain
        subdomain = args.subdomain if args.subdomain else ask_subdomain()
        tags = args.tags.split() if (hasattr(args, 'tags') and args.tags) else ["#信号笔记"]
        if args.title:
            knowledge = args.title
            if '-' in knowledge:
                print("  [error] --title 禁止使用 '-'")
                return False
        else:
            # 禁用自动生成，要求必须指定 --title
            print("  [error] --url 模式必须指定 --title 知识点名称")
            print("  示例: --title 'WAIC2026新品发布'")
            return False

    # 4. 交互收集正文内容
    if interactive:
        print(f"\n请输入正文内容（flomo 格式，用空行分隔段落，输入单独的 '.' 结束）:")
        print("  格式提示: **概念**：<mark>核心定义</mark>...  |  **子概念**： |  - 要点列表")
        print("  (输入 '.' 回车结束输入)\n")
        content_lines = []
        while True:
            try:
                line = input()
            except EOFError:
                break
            if line.strip() == '.':
                break
            content_lines.append(line)
        body_text = '\n'.join(content_lines).strip()
    else:
        if hasattr(args, 'content') and args.content:
            # --content 是原材料，需要 AI 理解后生成概念和子概念
            # --ai-content 是已生成的 AI 内容（直接传入，跳过交互）
            if hasattr(args, 'ai_content') and args.ai_content:
                body_text = args.ai_content
                print(f"  [ai] 使用 --ai-content 内容（{len(body_text)} 字符）")
            else:
                print(f"\n{'='*60}")
                print("【AI 生成阶段】请理解下方原材料，自己生成概念和子概念：")
                print(f"{'='*60}")
                raw = args.content
                print(raw[:2000])
                if len(raw) > 2000:
                    print(f"...（共 {len(raw)} 字符）")
                print(f"{'='*60}")
                print("请粘贴你生成的 **概念** 和 **子概念**（直接粘贴，不要加额外说明）：")
                print("格式：\n**概念**：<mark>核心关键词</mark>...（核心词用<mark>，关键概念用<u>）\n\n**子概念**：\n- <u>关键概念1</u>：说明...\n- <u>关键概念2</u>：说明...\n（每个要点至少一个<u>关键词</u>）")
                content_lines = []
                while True:
                    try:
                        line = input()
                    except EOFError:
                        break
                    if line.strip() == '.':
                        break
                    content_lines.append(line)
                body_text = '\n'.join(content_lines).strip()
                if not body_text:
                    print("  [error] 正文内容为空")
                    return False
        else:
            print(f"\n请输入正文内容（flomo 格式，用空行分隔，输入 '.' 结束）:")
            print("  格式提示: **概念**：<mark>核心定义</mark>...  |  **子概念**： |  - 要点列表\n")
            content_lines = []
            while True:
                try:
                    line = input()
                except EOFError:
                    break
                if line.strip() == '.':
                    break
                content_lines.append(line)
            body_text = '\n'.join(content_lines).strip()

    if not body_text:
        print("  [error] 正文内容为空")
        return False

    # 5. 验证 domain 是否在有效领域列表中
    valid_domains = list(DOMAIN_KEYWORDS.keys())
    if domain not in valid_domains:
        print(f"  [error] 无效领域 '{domain}'，有效领域：{', '.join(valid_domains)}")
        return False

    # 6. 构建 flomo 内容
    filename = f"{domain}_{subdomain}_{knowledge}.md"
    full_path = BASE_DIR / "answers" / domain / subdomain / filename
    full_path.parent.mkdir(parents=True, exist_ok=True)

    tag_line = ' '.join(tags)
    bold_title = f"**{domain}_{subdomain}_{knowledge}**"

    flomo_content = f"""{tag_line}

{bold_title}

**来源**：网络

{body_text}
"""
    # 写文件
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(flomo_content)
    print(f"\n  [file] 已创建: answers/{domain}/{subdomain}/{filename}")

    # 6. 用 hook --staged 验证
    print("  [hook] 验证格式...")
    subprocess.run(["git", "add", "-f", str(full_path.relative_to(BASE_DIR))], cwd=str(BASE_DIR))
    hook_result = subprocess.run(
        [PYTHON_BIN, str(BASE_DIR / "hooks" / "pre-commit"), "--staged"],
        cwd=str(BASE_DIR),
        capture_output=True, text=True
    )
    if hook_result.returncode != 0:
        print(f"  [error] 格式验证失败:\n{hook_result.stdout}")
        subprocess.run(["git", "reset", "HEAD", "--", str(full_path.relative_to(BASE_DIR))], cwd=str(BASE_DIR))
        # 保留文件让用户修正
        print(f"  [file] 文件保留在: {full_path}")
        return False

    print("  [ok] 格式验证通过")

    # 7. flomo 查重
    print("  [flomo] 查重...")
    dup_result = search_flomo(knowledge)
    dup_memos = dup_result if dup_result and isinstance(dup_result, list) else []
    if dup_memos:
        # 取第一条相似笔记的 ID
        old_id = dup_memos[0].get("id") if isinstance(dup_memos[0], dict) else None
        if old_id:
            print(f"  [flomo] 检测到相似笔记 id={old_id}，自动更新...")
            ok = update_flomo(old_id, flomo_content)
            if ok:
                print(f"  [flomo] 更新成功 id={old_id}")
                subprocess.run(["git", "reset", "HEAD", "--", str(full_path.relative_to(BASE_DIR))], cwd=str(BASE_DIR))
                full_path.unlink()
                return True
            else:
                print(f"  [flomo] 更新失败，继续新建")

    # 8. 上传到 flomo
    flomo_id = upload_flomo(flomo_content)
    if flomo_id:
        print(f"  [flomo] 上传成功 id={flomo_id}")
        # 清理
        subprocess.run(["git", "reset", "HEAD", "--", str(full_path.relative_to(BASE_DIR))], cwd=str(BASE_DIR))
        full_path.unlink()
        print(f"  [cleanup] 已删除本地文件")
    else:
        print(f"  [flomo] 上传失败，文件保留在: {full_path}")

    print(f"\n✅ 处理完成!")
    return True


def main():
    parser = argparse.ArgumentParser(description="mynews inbox 处理器（本地分类版本）")
    parser.add_argument("--batch-size", type=int, default=100,
                        help="最多处理文件数 (默认 100, 单条用 --batch-size 1)")
    parser.add_argument("--source-type", choices=["rss_entry", "github_commit", "all"],
                        default="all", help="过滤源类型")
    parser.add_argument("--verbose", "-v", action="store_true", help="显示详细输出")
    parser.add_argument("--url", type=str,
                        help="直接处理单个 URL（无需 inbox 文件，交互式输入正文）")
    parser.add_argument("--domain", type=str, help="领域（可选，配合 --url 使用，如 --domain 技术 --subdomain AI）")
    parser.add_argument("--subdomain", type=str, help="二级领域（可选）")
    parser.add_argument("--tags", type=str,
                        help="标签（可选，多个用空格分隔，如 --tags '#信号笔记 #AI'）")
    parser.add_argument("--content", type=str,
                        help="原材料正文（作为 AI 理解的输入）")
    parser.add_argument("--ai-content", type=str,
                        help="AI 生成的概念和子概念内容（直接传入，跳过交互输入）")
    parser.add_argument("--title", type=str,
                        help="知识点标题（三段式，如：WAIC2026_中国AI_新产品发布；将作为文件名第三段）")
    args = parser.parse_args()

    if args.url:
        process_url(args.url, args)
        return

    try:
        lock = CrossPlatformLock(LOCK_FILE)
        lock.acquire(blocking=False)
    except BlockingIOError:
        print("Another instance is running, exiting")
        return

    print(f"mynews inbox processor (local-classify version)")
    print(f"  BASE_DIR: {BASE_DIR}")
    print(f"  INBOX_DIR: {INBOX_DIR}")
    print(f"  Batch size: {args.batch_size}, Source type: {args.source_type}")
    print()

    count = 0
    try:
        while count < args.batch_size:
            f = get_next_file(args.source_type)
            if not f:
                break

            if process_file(f, args):
                count += 1
            if count >= args.batch_size:
                print(f"\nProcessed {count} files, stopping for now")
                break

        if count == 0:
            print("No new files to process")
    finally:
        lock.release()


if __name__ == "__main__":
    main()
