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
    text = (title + " " + content).lower()

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





def process_without_kimi(source_url: str, source_type: str, content: str, feed_title: str, filepath: str, args, article_title: str = None) -> tuple:
    """inbox 批处理：分类 → 写入原始内容 → 停住等我补充概念/子概念。返回 (success, created_file)。"""
    title = article_title or feed_title or "未命名"
    if not content or len(content) < 50:
        return False, None

    # 1. 分类（结构性工作，程序可以做）
    domain, subdomain, knowledge = classify_content(title, content)
    if not domain:
        print(f"    [error] 无法分类内容，请手动处理")
        return False, None

    print(f"    [classify] {domain} / {subdomain} / {knowledge}")

    # 2. 写入原始内容，等我补充概念/子概念（创造性工作禁止程序处理）
    filename = f"{domain}_{subdomain}_{knowledge}.md"
    name_part = filename[:-3]
    parts = name_part.split('_')
    while len(parts) > 3:
        parts[2] = parts[2] + '_' + parts[3]
        parts.pop(3)
    filename = '_'.join(parts) + ".md"

    full_path = BASE_DIR / "answers" / domain / subdomain / filename
    full_path.parent.mkdir(parents=True, exist_ok=True)
    if full_path.exists():
        full_path.unlink()

    import re
    clean = re.sub(r'<[^>]+>', '', content)
    clean = re.sub(r'\s+', ' ', clean).strip()
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(clean)
    print(f"    [file] 已创建（待补充概念/子概念）: {full_path.relative_to(BASE_DIR)}")
    print(f"    [stop] 请手动补充 **概念** 和 **子概念** 后重新运行")

    subprocess.run(["git", "reset", "HEAD", "--", str(full_path.relative_to(BASE_DIR))], cwd=str(BASE_DIR))
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
                            raw = result["result"]
                            # 解析外层：content 是 [{"type":"text","text":"{\"memos\":[...]}"}]
                            content_list = raw.get("content", []) if isinstance(raw, dict) else []
                            all_memos = []
                            for item in content_list:
                                if isinstance(item, dict) and item.get("type") == "text":
                                    text_str = item.get("text", "")
                                    if text_str:
                                        try:
                                            inner = json.loads(text_str)
                                            memos = inner.get("memos", [])
                                            all_memos.extend(memos)
                                        except json.JSONDecodeError:
                                            pass
                            return all_memos
            return None
    except Exception as e:
        print(f"    [flomo search] error: {e}")
        return None


def _validate_and_extract_domain(content):
    """从 flomo content 中提取并验证 domain/subdomain/标签，返回 (domain, subdomain)。

    第一行必须有且仅有一组：①信号类型标签（必填且唯一，#信号笔记/#趋势信号/#知识基座/#分析框架/#知识载体）
                                ②一级领域标签（必填且唯一，必须是 DOMAIN_KEYWORDS 的有效一级领域）
                                ③二级领域标签（必填且唯一，可以是任意字符串；只需与标题中的二级领域一致即可，不强制预注册）
    其他任何 # 标签均视为非法（如 #学习强国、#Others 等）；@ 标签允许但暂不校验白名单。
    """
    # 格式必填项检查
    colon = '：'
    if f'**概念**{colon}' not in content:
        raise ValueError(f"内容缺少 **概念**{colon}")
    if f'**子概念**{colon}' not in content:
        raise ValueError(f"内容缺少 **子概念**{colon}")
    if f'**来源**{colon}' not in content:
        raise ValueError(f"内容缺少 **来源**{colon}")
    # 来源行有且仅有一行
    source_lines = [line for line in content.splitlines() if line.strip().startswith(f'**来源**{colon}')]
    if len(source_lines) != 1:
        raise ValueError(f"**来源**{colon} 行必须恰好出现一次，当前出现 {len(source_lines)} 次")
    # 概念行有且仅有一行
    concept_lines = [line for line in content.splitlines() if line.strip().startswith(f'**概念**{colon}')]
    if len(concept_lines) != 1:
        raise ValueError(f"**概念**{colon} 行必须恰好出现一次，当前出现 {len(concept_lines)} 次")
    # 子概念行有且仅有一行
    sub_concept_lines = [line for line in content.splitlines() if line.strip().startswith(f'**子概念**{colon}')]
    if len(sub_concept_lines) != 1:
        raise ValueError(f"**子概念**{colon} 行必须恰好出现一次，当前出现 {len(sub_concept_lines)} 次")
    # 标签行检查（第一行必须是 #xxx 或 @xxx，且整个内容里只能有这一行标签）
    lines = content.splitlines()
    first_line = lines[0].strip() if lines else ''
    if not (first_line.startswith('#') or first_line.startswith('@')):
        raise ValueError(f"第一行必须是标签行（#或@开头），当前第一行：'{first_line}'")
    # 校验整个内容里标签行有且仅有一行（防止内容中间重复出现 #xxx 标签行）
    tag_lines = [i for i, line in enumerate(lines) if line.strip().startswith('#') or line.strip().startswith('@')]
    if len(tag_lines) != 1 or tag_lines[0] != 0:
        raise ValueError(f"分类标签行有且仅有一行且必须在第一行，当前发现 {len(tag_lines)} 行（位置：{tag_lines}）")
    # 信号类型（必填，唯一）
    SIGNAL_TYPES = {'信号笔记', '趋势信号', '知识基座', '分析框架', '知识载体'}
    # 一级领域白名单（来自 DOMAIN_KEYWORDS keys）
    PRIMARY_DOMAINS = set(DOMAIN_KEYWORDS.keys())
    # 解析第一行所有标签
    tag_tokens = first_line.split()
    parsed_tags = {}
    for t in tag_tokens:
        if not (t.startswith('#') or t.startswith('@')):
            raise ValueError(f"标签 '{t}' 必须以 # 或 @ 开头")
        prefix, name = t[0], t[1:]
        parsed_tags.setdefault(prefix, []).append(name)
    # 信号类型校验：必须有一个且仅一个（必须属于 SIGNAL_TYPES）
    matched_signal = [t for t in parsed_tags.get('#', []) if t in SIGNAL_TYPES]
    if not matched_signal:
        raise ValueError(f"第一行缺少信号类型标签（{', '.join(SIGNAL_TYPES)} 中任选一个），当前：'{first_line}'")
    if len(matched_signal) > 1:
        raise ValueError(f"第一行只能有一个信号类型标签，当前包含：{', '.join(matched_signal)}")
    # 一级领域校验：必须有一个且仅一个（必须属于 PRIMARY_DOMAINS）
    matched_primary = [t for t in parsed_tags.get('#', []) if t in PRIMARY_DOMAINS]
    if not matched_primary:
        raise ValueError(f"第一行缺少一级领域标签（{', '.join(sorted(PRIMARY_DOMAINS))} 中任选一个），当前：'{first_line}'")
    if len(matched_primary) > 1:
        raise ValueError(f"第一行只能有一个一级领域标签，当前包含：{', '.join(matched_primary)}")
    # 二级领域校验：必须有一个（任意字符串都可以，但必须与标题中 subdomain 一致——一致性校验在下面进行）
    # 排除掉信号类型和一级领域后，剩下的 # 标签就是二级领域
    used_names = set(matched_signal) | set(matched_primary)
    secondary_candidates = [t for t in parsed_tags.get('#', []) if t not in used_names]
    if len(secondary_candidates) != 1:
        raise ValueError(f"第一行必须恰好一个二级领域标签，当前匹配到：{secondary_candidates}（去掉信号类型和一级领域后的 # 标签）")
    matched_secondary = secondary_candidates[0]
    # 严格白名单：第一行所有 # 标签必须能归类为 信号类型 / 一级领域 / 二级领域 三者之一（禁止其他任何 # 标签）
    # 因为已经强制 1+1+1=3 个 # 标签，且每个都校验过分类，所以这里隐含通过；再显式断言 # 标签总数
    if len(parsed_tags.get('#', [])) != 3:
        raise ValueError(f"第一行 # 标签必须恰好 3 个（信号类型 + 一级领域 + 二级领域），当前 {len(parsed_tags.get('#', []))} 个：{parsed_tags.get('#', [])}")
    # 找 **domain_subdomain_knowledge** 格式的粗体标题行
    match = re.search(r'^\*\*(?:\\\_|[^_])*\_(?:\\\_|[^_])*\_(?:\\\_|[^*])*\*\*$', content, re.MULTILINE)
    if not match:
        raise ValueError("无法从内容中找到粗体标题行（格式：**领域_二级领域_知识点**）")
    full_title = match.group(0)[2:-2]  # 去掉首尾 **
    if '-' in full_title:
        raise ValueError(f"标题禁止使用连字符（-）：'{full_title}'")
    parts = full_title.split('_', 2)
    if len(parts) < 2:
        raise ValueError(f"标题 '{full_title}' 不符合 三段式格式（领域_二级领域_知识点）")
    domain, subdomain = parts[0], parts[1]
    valid_domains = list(DOMAIN_KEYWORDS.keys())
    if domain not in valid_domains:
        raise ValueError(f"无效领域 '{domain}'，有效领域：{', '.join(valid_domains)}")
    # 标题里的 subdomain 必须与第一行标签中的二级领域一致
    if subdomain != matched_secondary:
        raise ValueError(f"标题中的二级领域 '{subdomain}' 与第一行二级领域标签 '{matched_secondary}' 不一致")
    # 标题里的一级领域必须与第一行标签中的一级领域一致
    if domain != matched_primary[0]:
        raise ValueError(f"标题中的一级领域 '{domain}' 与第一行一级领域标签 '{matched_primary[0]}' 不一致")
    return domain, subdomain


def upload_flomo(content):
    """上传到 flomo"""
    # 验证 domain/subdomain
    _validate_and_extract_domain(content)
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
                        # 检查是否是错误响应
                        if result.get("error"):
                            err = result["error"]
                            print(f"    [flomo upload] API error: {err.get('message', err)}")
                            return None
                        if "result" in result:
                            memo = result["result"]
                            if isinstance(memo, dict) and memo.get("isError"):
                                print(f"    [flomo upload] API error: {memo}")
                                return None
                            if "id" in memo:
                                return memo["id"]
                            # Check structuredContent
                            if "structuredContent" in memo and "id" in memo["structuredContent"]:
                                return memo["structuredContent"]["id"]
            return None
    except Exception as e:
        print(f"    [flomo upload] error: {e}")
        return None


def fetch_flomo_memo(memo_id, keyword=None):
    """通过 flomo MCP 拉取指定 memo_id 的完整 markdown 内容。

    优先走 memo_batch_get（服务端尽量返回全文，无截断），失败再 fallback memo_search。
    memo_search 服务端对单条笔记 content 截断到 ~500 字符（保留首尾+省略标记），
    memo_batch_get 累计长度上限 30000 字——本项目单条 ai-content < 5000 字直接够用。
    """
    # 优先：memo_batch_get 直接按 id 拉完整内容（不受 keyword 长度限制）
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "memo_batch_get",
            "arguments": {"ids": [memo_id]}
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
                    if not json_str:
                        continue
                    result = json.loads(json_str)
                    if result.get("error"):
                        print(f"    [flomo fetch] batch_get error: {result['error'].get('message', result['error'])}")
                    else:
                        raw = result.get("result", {})
                        if isinstance(raw, dict) and not raw.get("isError"):
                            content_list = raw.get("content", [])
                            for item in content_list:
                                if isinstance(item, dict) and item.get("type") == "text":
                                    text_str = item.get("text", "")
                                    if text_str:
                                        try:
                                            inner = json.loads(text_str)
                                            if isinstance(inner, dict):
                                                memos = inner.get("memos", [])
                                                for m in memos:
                                                    if isinstance(m, dict) and "content" in m:
                                                        return m["content"]
                                        except json.JSONDecodeError:
                                            return text_str
    except Exception as e:
        print(f"    [flomo fetch] batch_get error: {e}")

    # fallback：memo_search（仅在 batch_get 失败时使用）
    if not keyword or keyword == memo_id or len(keyword) < 4:
        print(f"    [flomo fetch] memo_id={memo_id} 未传标题关键词；建议传入 --title 的 slug")
        return None
    search_result = search_flomo(keyword)
    if search_result:
        for m in search_result:
            if isinstance(m, dict) and m.get("id") == memo_id and "content" in m:
                return m["content"]
    return None


def update_flomo(memo_id, content):
    """合并更新 flomo 已有笔记——AI 必须把旧内容与新内容合并成单一 markdown 后传入 content。

    ⚠️ 此函数将传入的 content 整体覆盖到 flomo 笔记——如不合并旧内容，旧笔记的所有原有子概念将永久丢失。
    ⚠️ flomo MCP 不保留历史版本——更新是不可逆操作。

    调用方强制流程：
    ① fetch_flomo_memo(memo_id, keyword=args.title_slug) 拉取旧内容
    ② AI 比对旧内容 vs 新内容
    ③ AI 构造完整合并 markdown（保留旧子概念 + 追加新子概念 + 写入概念/子概念/来源）
    ④ 把合并后的完整 markdown 通过 process_inbox.py 的 --ai-content 传入
    ⑤ update_flomo 把这个完整合并 markdown 整体覆盖写入 flomo 笔记

    增量合并典型构造示例：
        旧：子概念 A、B、C
        新：子概念 D（新增）
        合并后概念：综合 AB C 加上 D
        合并后子概念：A、B、C、D（保留 A/B/C 原文，追加 D 原文）

    边界情况：
    - 完全重复（同主题无新增）→ 不调用 update，skip 即可
    - 主题不同（假阳性）→ --force-new，不要用 --update
    """
    # 验证 domain/subdomain
    _validate_and_extract_domain(content)
    def escape_underscore_in_bold(match):
        return "**" + match.group(1).replace("_", "\\_") + "**"
    content_escaped = re.sub(
        r'^\*\*([^*]+)\*\*$', escape_underscore_in_bold, content, flags=re.MULTILINE
    )

    # === update_flomo 安全约束 ===
    # 此函数用 memo_update 把传入 content 整体覆盖写入 flomo。
    # 调用方必须把"旧内容 + 新内容合并"的完整 markdown 传给 content（不是只传新的 ai-content）。
    # 严禁在未拉旧内容、未构造合并 markdown 情况下调用本函数，否则旧子概念全部丢失不可恢复。
    print(f"  [warning] update_flomo 是覆盖操作（flomo MCP 无版本控制、不可逆）")
    print(f"  调用方必须已 fetch_flomo_memo({memo_id}) 拉旧内容 + 构造合并 markdown 传入 content")
    print(f"  如不合并旧内容，旧笔记所有子概念将被永久覆盖丢失")

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
                        # 检查是否是错误响应
                        if result.get("error"):
                            err = result["error"]
                            print(f"    [flomo update] API error: {err.get('message', err)}")
                            return False
                        # 检查 result 中是否包含 isError
                        res_content = result.get("result", {})
                        if isinstance(res_content, dict) and res_content.get("isError"):
                            print(f"    [flomo update] API error: {res_content}")
                            return False
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

{content}

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
        tag = input("输入信号类型标签（必填，五选一）: ").strip()
        if not tag:
            print("  错误：信号类型标签不能为空")
            continue
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

    # --content without --url: skip fetching, use provided content
    if url is None:
        if not (hasattr(args, 'content') and args.content):
            print("  [error] --content 模式需要提供 --content 参数")
            return False
        text = args.content
        source = None  # will default to "网络"
        source_title = None  # explicitly None so it defaults to "网络" in format string
        wx_title = ""
        print(f"\n[内容模式] 使用提供的 --content（{len(text)} 字符）")
    else:
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

        # 确定来源标题：微信文章用source（发布账号），其他用None（默认"网络"）
        source_title = source if (is_wechat_url(url) and source) else None

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
            if ' ' in knowledge:
                print("  禁止使用空格")
                continue
            break
    else:
        domain = args.domain
        subdomain = args.subdomain if args.subdomain else ask_subdomain()
        if not (hasattr(args, 'tags') and args.tags):
            print("错误：--tags 必须提供信号类型标签")
            exit(1)
        tags = args.tags.split()
        if args.title:
            knowledge = args.title
        else:
            # 禁用自动生成，要求必须指定 --title
            print("  [error] --url 模式必须指定 --title 知识点名称")
            print("  示例: --title 'WAIC2026新品发布'")
            return False

    # 4. 正文内容
    if hasattr(args, 'ai_content') and args.ai_content:
        # --ai-content：已生成的AI内容，直接使用
        body_text = args.ai_content
        print(f"  [ai] 使用 --ai-content 内容（{len(body_text)} 字符）")
    elif interactive:
        # 交互模式：通过 stdin 收集
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
        # 非交互 --url 模式：fetch_wechat_article 已抓取完整 text
        # 将全文打印出来供 AI 读取理解，避免只读部分内容就处理
        if hasattr(args, 'content') and args.content:
            # --content：原材料，打印出来让 AI 生成概念和子概念。
            # 硬规则：不分页、不截断，完整打印全文——否则 AI 容易基于片段漏掉信息。
            print(f"\n{'='*60}")
            print("【AI 生成阶段】请理解下方原材料，自己生成概念和子概念：")
            print(f"{'='*60}")
            print(f"【原文共 {len(args.content)} 字符，已完整打印，禁止跳读】")
            print(f"{'='*60}")
            print(args.content)
            print(f"{'='*60}")
            print("请粘贴你生成的 **概念** 和 **子概念**（直接粘贴，不要加额外说明）：")
            print("格式：\n**概念**：<mark>核心关键词</mark>...（核心词用<mark>高亮）\n\n**子概念**：\n- <mark>关键概念1</mark>：说明...\n- <mark>关键概念2</mark>：说明...\n（每个要点至少一个<mark>关键词</mark>高亮）")
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
            # --url 模式：打印抓取的完整文章内容，供 AI 读取理解。硬规则：不分页、不截断。
            print(f"\n{'='*60}")
            print(f"【文章全文 {len(text)} 字符 已完整打印，禁止跳读】请 AI 读取理解后再生成 flomo 内容：")
            print(f"{'='*60}")
            print(text)
            print(f"{'='*60}")
            print("请粘贴你生成的 **概念** 和 **子概念**（直接粘贴，不要加额外说明）：")
            print("格式：\n**概念**：<mark>核心关键词</mark>...（核心词用<mark>高亮）\n\n**子概念**：\n- <mark>关键概念1</mark>：说明...\n- <mark>关键概念2</mark>：说明...\n（每个要点至少一个<mark>关键词</mark>高亮）")
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

**来源**：{source_title if source_title else "网络"}

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

    # 7. --update 分支：将 --ai-content 整体合并并覆盖更新到指定 memo_id
    #    流程：fetch_flomo_memo 拉旧内容 → 打印新旧对比 → 调用 update_flomo 覆盖
    if getattr(args, 'update', None):
        target_id = args.update
        print(f"  [update] 目标 memo_id={target_id}")
        print("  [update] 拉取旧内容...")
        # 用 knowledge（=args.title slug）作为搜索关键词，避免 memo_id 被服务端解析为 int 报错
        old_content = fetch_flomo_memo(target_id, keyword=knowledge if knowledge else None)
        if old_content is None:
            print(f"  [update] 拉取旧内容失败，退出")
            subprocess.run(["git", "reset", "HEAD", "--", str(full_path.relative_to(BASE_DIR))], cwd=str(BASE_DIR))
            full_path.unlink()
            return False
        print(f"\n========== 已有笔记内容（id={target_id}） ==========")
        print(old_content)
        print("==============================================\n")
        print(f"========== 新合并内容（{len(body_text)} 字符） ==========")
        print(body_text)
        print("==============================================\n")
        ok = update_flomo(target_id, flomo_content)
        if ok:
            print(f"  [update] 成功更新 id={target_id}")
            subprocess.run(["git", "reset", "HEAD", "--", str(full_path.relative_to(BASE_DIR))], cwd=str(BASE_DIR))
            full_path.unlink()
            print(f"  [cleanup] 已删除本地文件")
        else:
            print(f"  [update] 更新失败，文件保留在: {full_path}")
        print(f"\n✅ --update 处理完成!")
        return ok

    # 7. flomo 查重
    print("  [flomo] 查重...")
    dup_memos = search_flomo(knowledge)
    dup_memos = dup_memos if dup_memos and isinstance(dup_memos, list) else []
    if dup_memos:
        best = dup_memos[0]
        old_id = best.get("id") if isinstance(best, dict) else None
        relevance = best.get("relevance", 0) if isinstance(best, dict) else 0
        if old_id:
            print(f"  [flomo] 检测到相似笔记 id={old_id}（relevance={relevance:.2f}）")
            # 所有路径（TTY + 非 TTY）都强制 fetch 旧 markdown 到 stderr，AI 1 次跑能拿到完整内容
            import sys as _sys_for_stderr
            old_content = best.get("content", "")
            fetched_content = fetch_flomo_memo(old_id, keyword=knowledge if knowledge else None)
            if fetched_content:
                old_content = fetched_content
                print(f"  [update-hint] 已自动获取旧文档（{len(old_content)} 字符），用 --update 时按 SKILL §8 构造合并 markdown", file=_sys_for_stderr.stderr)
            else:
                print(f"  [update-hint] fetch_flomo_memo 未能拉取完整旧文档（{len(old_content)} 字符原始内容），如需 update 请按 SKILL §8.3 停下报告", file=_sys_for_stderr.stderr)
            if old_content:
                # 用 8= 号清晰标识旧 markdown 开始/结束，让 AI 用 tail 也能抓到完整内容
                print(f"\n==BEGIN_OLD==", file=_sys_for_stderr.stderr)
                print(f"==旧笔记 id={old_id}（{len(old_content)} 字符）==", file=_sys_for_stderr.stderr)
                print(f"==BEGIN_OLD_MARKDOWN==", file=_sys_for_stderr.stderr)
                print(old_content, file=_sys_for_stderr.stderr)
                print(f"==END_OLD_MARKDOWN==", file=_sys_for_stderr.stderr)
                print(f"==END_OLD==\n", file=_sys_for_stderr.stderr)
            print(f"\n==BEGIN_NEW==", file=_sys_for_stderr.stderr)
            print(f"==新文章（{len(body_text)} 字符）==", file=_sys_for_stderr.stderr)
            print(f"==BEGIN_NEW_MARKDOWN==", file=_sys_for_stderr.stderr)
            print(body_text, file=_sys_for_stderr.stderr)
            print(f"==END_NEW_MARKDOWN==", file=_sys_for_stderr.stderr)
            print(f"==END_NEW==\n", file=_sys_for_stderr.stderr)
            import os as _os_for_tty
            if _os_for_tty.isatty(0):
                import termios, tty
                if relevance >= 0.9:
                    print(f"\n========== relevance >= 0.9 决策表 ==========", file=_sys_for_stderr.stderr)
                    print(f"  主题对比：旧笔记内容已就绪（见上方），对比主题是否相同；如有实质增量选 [u]，无增量 [s]，假阳性 [n]", file=_sys_for_stderr.stderr)
                print(f"  选择: [u]更新旧笔记  [s]跳过上传  [n]新建: ", end='', flush=True)
                fd = os.open('/dev/tty', os.O_RDONLY)
                old_settings = termios.tcgetattr(fd)
                try:
                    tty.setraw(fd)
                    ch = os.read(fd, 1).decode()
                finally:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                    os.close(fd)
                print(ch)
                choice = ch.strip().lower()
            else:
                if relevance >= 0.9:
                    print(f"  [flomo] 检测到高相似笔记 id={old_id}（relevance={relevance:.2f}）")
                    if getattr(args, 'force_new', False):
                        print("  [flomo] --force-new 强制新建，跳过检测")
                        choice = None
                    else:
                        print("\n========== relevance >= 0.9 决策表 ==========", file=_sys_for_stderr.stderr)
                        print("  ┌─────────────────────┬──────────────┬─────────────┐", file=_sys_for_stderr.stderr)
                        print("  │ 主题对比            │ 增量判断     │ 应选操作     │", file=_sys_for_stderr.stderr)
                        print("  ├─────────────────────┼──────────────┼─────────────┤", file=_sys_for_stderr.stderr)
                        print("  │ 完全相同主题          │ 有实质增量   │ --update    │", file=_sys_for_stderr.stderr)
                        print("  │ 完全相同主题          │ 零增量       │ 跳过         │", file=_sys_for_stderr.stderr)
                        print("  │ 假阳性（关键词命中   │ —            │ --force-new│", file=_sys_for_stderr.stderr)
                        print("  │   但主题不同）       │              │             │", file=_sys_for_stderr.stderr)
                        print("  └─────────────────────┴──────────────┴─────────────┘", file=_sys_for_stderr.stderr)
                        print("\n  决策依据：上方打印的'已有笔记内容' 与 '新文章内容' 对比；只看主题概念不看关键词。", file=_sys_for_stderr.stderr)
                        print("  增量识别：新增事实数据 / 新增事件 / 新增参数 / 新增时间点 / 新增主体视角", file=_sys_for_stderr.stderr)
                        print("  假阳性识别：主题不同（即便关键词重叠度高），用 --force-new", file=_sys_for_stderr.stderr)
                        print("  旧文档已就绪：上方'已有笔记内容'段已自动 fetch_flomo_memo 拼好，可直接构造合并 markdown", file=_sys_for_stderr.stderr)
                        print("\n  强制规则：", file=_sys_for_stderr.stderr)
                        print("  - 有增量必须 --update MEMO_ID 或 --force-new，禁止跳过", file=_sys_for_stderr.stderr)
                        print("  - 零增量才能跳过（不重跑脚本）", file=_sys_for_stderr.stderr)
                        print("  - ai-content 必须详细，禁止压缩——子概念要展开论点+引用原文关键数据", file=_sys_for_stderr.stderr)
                        print("\n  可选操作：", file=_sys_for_stderr.stderr)
                        print("  --force-new 新建（独立新笔记，假阳性或主题不同）", file=_sys_for_stderr.stderr)
                        print("  --update MEMO_ID 更新（合并增量到已有笔记）", file=_sys_for_stderr.stderr)
                        print("  不重跑脚本 = 跳过（仅在零增量时合法）", file=_sys_for_stderr.stderr)
                        # 非 TTY 模式：打印对比后干净退出，由 AI 人工判断
                        print(f"  [flomo] 检测到高相似笔记 id={old_id}（relevance={relevance:.2f}），请比对上方内容后人工判断：")
                        print(f"  [flomo]   → 主题不同（关键词命中但内容无关，假阳性）→ 重跑加 --force-new")
                        print(f"  [flomo]   → 主题相同且有新增信息 → 重跑加 --update {old_id}")
                        print(f"  [flomo]   → 主题相同且无新增信息（真重复）→ 跳过，不动")
                        print(f"\n⏭️  已跳过（未上传）")
                        subprocess.run(["git", "reset", "HEAD", "--", str(full_path.relative_to(BASE_DIR))], cwd=str(BASE_DIR), capture_output=True)
                        if full_path.exists():
                            full_path.unlink()
                        return True
                else:
                    print(f"  [flomo] 低相关（relevance={relevance:.2f}），继续新建")
                    print("  [decide-rule] relevance < 0.9 → 脚本自动 continue 新建（不需要 AI 介入）")
                    choice = None  # non-TTY, low relevance: 跳过choice逻辑，直接新建
            if choice is None:
                pass  # 继续新建
            elif choice == 's':
                print(f"  [flomo] 跳过上传")
                subprocess.run(["git", "reset", "HEAD", "--", str(full_path.relative_to(BASE_DIR))], cwd=str(BASE_DIR))
                full_path.unlink()
                return True
            elif choice == 'u':
                print(f"  [flomo] 我来手动更新 id={old_id}，退出程序")
                import sys
                sys.exit(1)

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
    parser.add_argument("--tags", type=str, required=True,
                        help="标签（必填，第一个为信号类型标签：#知识基座/#趋势信号/#信号笔记/#分析框架/#知识载体，其余为领域/二级领域标签，如 --tags '#知识基座 #技术 #AI'）")
    parser.add_argument("--content", type=str,
                        help="原材料正文（作为 AI 理解的输入）")
    parser.add_argument("--ai-content", type=str,
                        help="AI 生成的概念和子概念内容（直接传入，跳过交互输入）")
    parser.add_argument("--title", type=str,
                        help="知识点标题（三段式，如：WAIC2026_中国AI_新产品发布；将作为文件名第三段）")
    parser.add_argument("--force-new", action="store_true",
                        help="强制新建，跳过高相似检测（用于内容明显不同却被误判为高相似的假阳性情况）")
    parser.add_argument("--update", type=str, metavar="MEMO_ID",
                        help="更新指定 memo_id 的旧笔记。流程：先 fetch_flomo_memo 拉旧内容；用户对比后传入完整合并后的 --ai-content；脚本验证格式后调用 update_flomo 覆盖更新。")
    args = parser.parse_args()

    if args.url:
        process_url(args.url, args)
        return

    # --content without --url: use content as body, source defaults to "网络"
    if hasattr(args, 'content') and args.content:
        process_url(None, args)
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
