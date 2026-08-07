#!/usr/bin/env python3
"""
mynews 内容处理器
--content 模式：接收正文 → 构造 flomo 笔记 → 查重 → 上传
"""
import os
import sys
import json
import subprocess
import argparse
import shutil
import re
import urllib.request
import urllib.parse
import tempfile
from pathlib import Path
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Windows 下禁止子进程弹出控制台窗口（避免终端闪现）
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def get_base_dir() -> Path:
    """返回项目根目录（scripts/ 的父目录）。"""
    return Path(__file__).resolve().parent.parent


def get_temp_dir() -> Path:
    """返回系统临时目录。"""
    return Path(tempfile.gettempdir())


def _find_kimi_bin():
    """查找 kimi CLI 二进制路径：PATH → 常见安装目录。找不到返回 None。"""
    kimi_bin = shutil.which("kimi")
    if not kimi_bin:
        for cand in (os.path.expanduser("~/.kimi-code/bin/kimi"),
                     os.path.expanduser("~/.local/bin/kimi"),
                     "/usr/local/bin/kimi"):
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                kimi_bin = cand
                break
    return kimi_bin


def _check_kimi():
    """启动检查：kimi 是否存在、是否最新版。
    找不到 → 报错退出（脚本依赖 kimi CLI）；版本过旧 → 警告不阻断。返回 kimi 二进制路径。

    版本检查的远端请求在网络不可用时以 2 秒快速失败；也可用环境变量
    MYNEWS_SKIP_KIMI_CHECK=1 完全跳过网络检查（无网络/离线环境提速）。
    """
    kimi_bin = _find_kimi_bin()
    if not kimi_bin:
        print("[error] 未找到 kimi 命令，脚本依赖 kimi CLI 才能运行。")
        print("  安装: curl -fsSL https://code.kimi.com/kimi-code/install.sh | bash")
        sys.exit(1)

    # 本地版本检查（仅提示不阻断）
    local_ver = ""
    try:
        r = subprocess.run([kimi_bin, "--version"], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10, creationflags=CREATE_NO_WINDOW)
        raw_ver = (r.stdout or r.stderr or "").strip()
        m = re.search(r"(\d+\.\d+\.\d+)", raw_ver)
        if m:
            local_ver = m.group(1)
    except Exception:
        pass
    if os.environ.get("MYNEWS_SKIP_KIMI_CHECK") == "1":
        return kimi_bin
    # 远端版本检查（2 秒超时快速失败，离线环境不阻塞）
    latest_ver = ""
    try:
        with urllib.request.urlopen("https://code.kimi.com/kimi-code/latest", timeout=2) as resp:
            latest_ver = resp.read().decode("utf-8").strip()
    except Exception:
        pass
    if local_ver and latest_ver and local_ver != latest_ver:
        print(f"  [warning] kimi 版本 {local_ver}，最新版 {latest_ver}，建议升级")
    return kimi_bin


# 脚本最前面：先检查 kimi 是否安装、是否最新版（在 FLOMO_TOKEN 检查之前）
_check_kimi()



BASE_DIR = get_base_dir()
PYTHON_BIN = sys.executable if sys.executable else ("python" if os.name == "nt" else "python3")
FLOMO_API_URL = "https://flomoapp.com/mcp"
FLOMO_TOKEN = os.environ.get("FLOMO_TOKEN") or ""
if not FLOMO_TOKEN:
    print("[error] 环境变量 FLOMO_TOKEN 未设置")
    sys.exit(1)









def _flomo_call(name: str, arguments: dict, tag: str = "", timeout: int = 30):
    """调用 flomo MCP tools/call，返回所有 SSE data 行解析后的 JSON 对象列表。

    - 成功：返回 data 行 JSON 列表（可能为空列表）
    - 网络/超时失败：返回 None（调用方据此区分"无结果"与"请求失败"）
    """
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments}
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
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8")
    except Exception as e:
        print(f"    [flomo {tag or name}] error: {e}")
        return None
    results = []
    for line in data.split("\n"):
        if line.startswith("data:"):
            json_str = line[5:].strip()
            if json_str:
                try:
                    results.append(json.loads(json_str))
                except json.JSONDecodeError:
                    pass
    return results


def search_flomo(keyword):
    """搜索 flomo"""
    results = _flomo_call("memo_search", {"keywords": keyword}, tag="search")
    if results is None:
        return None
    for result in results:
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


def _validate_and_extract_domain(content):
    """从 flomo content 中提取并验证 domain/subdomain/标签，返回 (domain, subdomain)。

    第一行必须有且仅有一组：①信号类型标签（必填且唯一，#信号笔记/#趋势信号/#知识基座/#分析框架/#知识载体）
                                ②一级领域标签（必填且唯一，必须是有效一级领域）
                                ③二级领域标签（必填且唯一，可以是任意字符串；只需与标题中的二级领域一致即可，不强制预注册）
    其他任何 # 标签均视为非法（如 #学习强国、#Others 等）；@ 标签允许但暂不校验白名单。
    """
    # 格式必填项检查
    colon = '：'
    if f'**概念**{colon}' not in content:
        raise ValueError(f"内容缺少 **概念**{colon}")
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
    # 解析第一行所有标签
    tag_tokens = first_line.split()
    parsed_tags = {}
    for t in tag_tokens:
        if not (t.startswith('#') or t.startswith('@')):
            raise ValueError(f"标签 '{t}' 必须以 # 或 @ 开头")
        prefix, name = t[0], t[1:]
        parsed_tags.setdefault(prefix, []).append(name)

    # 必须恰好 3 个 # 标签：信号类型 + 一级领域 + 二级领域（均自由填写，无白名单）
    if len(parsed_tags.get('#', [])) != 3:
        raise ValueError(f"第一行 # 标签必须恰好 3 个（信号类型 + 一级领域 + 二级领域），当前 {len(parsed_tags.get('#', []))} 个：{parsed_tags.get('#', [])}")
    # 信号类型校验：必须有一个且仅一个（必须属于 SIGNAL_TYPES）
    matched_signal = [t for t in parsed_tags.get('#', []) if t in SIGNAL_TYPES]
    if not matched_signal:
        raise ValueError(f"缺少信号类型标签（{', '.join(SIGNAL_TYPES)} 中任选一个），当前：'{first_line}'")
    if len(matched_signal) > 1:
        raise ValueError(f"只能有一个信号类型标签，当前包含：{', '.join(matched_signal)}")
    # 去掉信号类型的 # 标签后，剩下的就是领域标签（按顺序为一级领域、二级领域）
    domain_tags = [t for t in parsed_tags.get('#', []) if t not in matched_signal]
    if len(domain_tags) != 2:
        raise ValueError(f"除信号类型外，必须恰好 2 个 # 标签（一级领域 + 二级领域），当前：{domain_tags}")
    matched_primary, matched_secondary = domain_tags
    # 找 **domain_subdomain_knowledge** 格式的粗体标题行
    match = re.search(r'^\*\*(?:\\\_|[^_])*\_(?:\\\_|[^_])*\_(?:\\\_|[^*])*\*\*$', content, re.MULTILINE)
    if not match:
        raise ValueError("无法从内容中找到粗体标题行（格式：**领域_二级领域_知识点**）")
    full_title = match.group(0)[2:-2]  # 去掉首尾 **
    # 兼容旧笔记：标题里的 \_ 转义下划线还原为普通下划线，否则解析出的领域会带反斜杠
    full_title = full_title.replace('\\_', '_')
    if '-' in full_title:
        raise ValueError(f"标题禁止使用连字符（-）：'{full_title}'")
    if full_title.count('_') != 2:
        raise ValueError(f"标题必须恰好 2 个下划线（domain_subdomain_knowledge），当前 {full_title.count('_')} 个：'{full_title}'")
    parts = full_title.split('_', 2)
    if len(parts) < 2:
        raise ValueError(f"标题 '{full_title}' 不符合 三段式格式（领域_二级领域_知识点）")
    domain, subdomain = parts[0], parts[1]
    # 标题里的 subdomain 必须与第一行标签中的二级领域一致
    if subdomain != matched_secondary:
        raise ValueError(f"标题中的二级领域 '{subdomain}' 与第一行二级领域标签 '{matched_secondary}' 不一致")
    # 标题里的一级领域必须与第一行标签中的一级领域一致
    if domain != matched_primary:
        raise ValueError(f"标题中的一级领域 '{domain}' 与第一行一级领域标签 '{matched_primary}' 不一致")
    return domain, subdomain


def _escape_bold_underscores(content: str) -> str:
    """将加粗标题中的下划线转义为 \\_（flomo MCP 会转义下划线）。"""
    def escape_underscore_in_bold(match):
        return "**" + match.group(1).replace("_", "\\_") + "**"
    return re.sub(r'^\*\*([^*]+)\*\*$', escape_underscore_in_bold, content, flags=re.MULTILINE)


def upload_flomo(content):
    """上传到 flomo"""
    # 归一化格式：确保符合当前标准
    content = _normalize_flomo_content(content)
    # 验证 domain/subdomain
    _validate_and_extract_domain(content)
    # 转义 content 中的下划线
    content_escaped = _escape_bold_underscores(content)

    results = _flomo_call("memo_create", {"content": content_escaped}, tag="upload")
    if results is None:
        return None
    for result in results:
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


def fetch_flomo_memo(memo_id, keyword=None):
    """通过 flomo MCP 拉取指定 memo_id 的完整 markdown 内容。

    优先走 memo_batch_get（服务端尽量返回全文，无截断），失败再 fallback memo_search。
    memo_search 服务端对单条笔记 content 截断到 ~500 字符（保留首尾+省略标记），
    memo_batch_get 累计长度上限 30000 字——本项目单条 ai-content < 5000 字直接够用。
    """
    # 优先：memo_batch_get 直接按 id 拉完整内容（不受 keyword 长度限制）
    results = _flomo_call("memo_batch_get", {"ids": [memo_id]}, tag="fetch")
    if results:
        for result in results:
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

    # fallback：memo_search（仅在 batch_get 失败时使用）
    if not keyword or keyword == memo_id or len(keyword) < 4:
        print(f"    [flomo fetch] memo_id={memo_id} 未传标题关键词（knowledge），无法拉取旧内容")
        return None
    search_result = search_flomo(keyword)
    if search_result:
        for m in search_result:
            if isinstance(m, dict) and m.get("id") == memo_id and "content" in m:
                return m["content"]
    return None


def update_flomo(memo_id, content):
    """合并更新 flomo 已有笔记——AI 必须把旧内容与新内容合并成单一 markdown 后传入 content。

    ⚠️ 此函数将传入的 content 整体覆盖到 flomo 笔记。
    ⚠️ flomo MCP 不保留历史版本——更新是不可逆操作。

    调用方（auto 模式，process_inbox 内部）：
    ① fetch_flomo_memo(memo_id, keyword=knowledge) 拉取旧内容
    ② _auto_decide 判定：有增量 → 更新；无增量 → 跳过；假阳性 → 新建
    ③ 判定 update 时，_auto_merge 构造完整合并 markdown
    ④ update_flomo 把合并后的完整 markdown 整体覆盖写入 flomo 笔记
    """
    # 归一化格式：确保符合当前标准
    content = _normalize_flomo_content(content)
    # 验证 domain/subdomain
    _validate_and_extract_domain(content)
    # 转义 content 中的下划线
    content_escaped = _escape_bold_underscores(content)

    # === update_flomo 安全约束 ===
    # 此函数用 memo_update 把传入 content 整体覆盖写入 flomo。
    # 调用方必须把"旧内容 + 新内容合并"的完整 markdown 传给 content（不是只传新的 ai-content）。
    print(f"  [warning] update_flomo 是覆盖操作（flomo MCP 无版本控制、不可逆）")
    print(f"  调用方必须已 fetch_flomo_memo({memo_id}) 拉旧内容 + 构造合并 markdown 传入 content")

    results = _flomo_call("memo_update", {"id": memo_id, "content": content_escaped}, tag="update")
    if results is None:
        return False
    for result in results:
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


def _normalize_flomo_content(content: str) -> str:
    """将任意格式的 flomo 笔记归一化为当前标准格式。

    处理：
    1. 移除禁止语法（Markdown 标题/引用/代码块/链接/图片/水平线/表格）
    2. 列表项统一用 `- ` 前缀
    3. `- 关键词：说明` 自动添加 <mark> 高亮（如果缺失）
    4. 确保段落间有空行分隔
    """
    import re as _re
    content = _re.sub(r'`{3}[\s\S]*?`{3}', '', content)
    content = _re.sub(r'!\[.*?\]\(.*?\)', '', content)
    content = _re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', content)
    content = _re.sub(r'^#{2,}\s+\S.*$', '', content, flags=_re.MULTILINE)
    content = _re.sub(r'^>\s+.*$', '', content, flags=_re.MULTILINE)
    content = _re.sub(r'^---+$', '', content, flags=_re.MULTILINE)
    content = _re.sub(r'^\|.+\|$', '', content, flags=_re.MULTILINE)
    content = _re.sub(r'^\s*[•●◦‣❦]\s+', '- ', content, flags=_re.MULTILINE)
    content = _re.sub(r'^\s*\d+[\.\)]\s+', '- ', content, flags=_re.MULTILINE)

    def _auto_mark(m):
        prefix = m.group(1)
        keyword = m.group(2).strip()
        rest = m.group(3)
        if '<mark>' in keyword:
            return f'{prefix}{keyword}{rest}'
        return f'{prefix}<mark>{keyword}</mark>{rest}'

    content = _re.sub(
        r'^(- )([^<：:\n]+?)([：:][^\n]*)$',
        _auto_mark, content, flags=_re.MULTILINE
    )
    content = _re.sub(r'\n{3,}', '\n\n', content)
    return content.strip()


def _call_kimi(prompt: str, timeout: int = 180) -> str:
    """调用本地 kimi CLI 处理提示，返回 stdout。"""
    import os as _os
    import subprocess as _sp
    # 优先用绝对路径，避免子进程 PATH 缺失时找不到命令
    kimi_bin = _find_kimi_bin()
    if not kimi_bin:
        return "[error] kimi 调用失败: 未找到 kimi 命令"
    try:
        r = _sp.run(
            [kimi_bin, "-p", prompt],
            capture_output=True, text=True, encoding='utf-8', timeout=timeout,
            creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0) if _os.name == "nt" else 0
        )
        raw = r.stdout.strip() or r.stderr.strip() or ""
        # 过滤掉 "To resume this session: ..." 行和 v0.31 的 "• " 项目符号前缀
        lines = []
        for l in raw.split("\n"):
            if l.startswith("To resume this session:"):
                continue
            l = l.lstrip("•\u00a0 \t")
            lines.append(l)
        return "\n".join(lines).strip() or raw
    except _sp.TimeoutExpired:
        return "[error] kimi 调用超时"
    except Exception as e:
        return f"[error] kimi 调用失败: {e}"


def _auto_analyze(text: str, hint: str = "") -> dict:
    """全自动模式：调用 AI 分析正文，返回 {{domain, subdomain, title, tags, ai_content}}。hint 为重试时的修正提示。"""
    prompt = '你是一个知识文档分析助手。只输出 JSON，不要 markdown 代码块、不要额外文字。\n\n' + json.dumps({
        "domain": "一级领域（根据内容自选）",
        "subdomain": "二级领域（根据内容自选，如 AI芯片/大模型/外交/产业）",
        "title": "知识点名称（10字以内，不含下划线/空格/斜杠。用中性客观的知识点短语，不要新闻式标题或主观评价，如 科创板利好信号、纳米金催化MMA工艺、Go监督式后台任务）",
        "tags": ["#信号类型标签", "#领域标签", "#二级领域标签"],
        "ai_content": "**概念**：<mark>核心概念</mark>定义..."
    }, ensure_ascii=False, indent=2) + '\n\n信号类型（五选一贴在第一行）：#知识基座（概念/定理）、#趋势信号（正在发生的结构性变化）、#信号笔记（单次事件）、#分析框架（方法论）、#知识载体（工具/资源）\n\n⚠️ 严格遵守：\n- ai_content 必须包含 **概念**：段落（用中文冒号：），不要包含 # 标签行（脚本自动添加）\n- **概念** 段用 <mark>高亮</mark> 标记核心关键词\n- 禁止：代码块、链接、图片、表格、>引用\n- JSON 内所有引号必须用 \\" 转义，值内不能出现未转义的双引号\n- 不要用 ```json 包裹\n- 原文未明确提及的年份/月份/日期一律禁止填入，宁缺毋错\n- 信息量大时学会分段：不要一段堆到底，用空行分隔成多个自然段，每段聚焦一个要点\n' + (hint + '\n\n' if hint else '') + '文章：\n' + text[:8000]

    out = _call_kimi(prompt)
    if not out:
        print("  [auto] AI 返回空结果，重试...")
        return {}
    import re as _re
    # 去掉 markdown 代码块包裹和行首列表符号（不剥 *，避免破坏 JSON 或粗体）
    out = _re.sub(r'^\s*[•\-]\s*', '', out, flags=_re.MULTILINE)
    out = _re.sub(r'```(?:json)?\s*', '', out)
    # 提取第一个 { 到最后一个 }
    start = out.find('{')
    end = out.rfind('}')
    if start < 0 or end <= start:
        print(f"  [auto] AI 输出无有效 JSON，原始输出：{out[:300]}")
        return {}
    raw = out[start:end+1]
    # 截断检测：{ 比 } 多时尝试补齐缺失的右花括号再解析
    balance = out.count('{') - out.count('}')
    if balance > 0:
        raw = raw + '}' * min(balance, 5)
        print(f"  [auto] 输出疑似截断（缺 {balance} 个 }}），尝试补齐修复...")
    # 解析链
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # 修复 AI 输出中结构引号被转义的情况（\"key\" → "key"）
    try:
        return json.loads(raw.replace('\\"', '"'))
    except json.JSONDecodeError:
        pass
    # 尝试修复值内未转义的双引号（将 "xxx" 替换为 \"xxx\"）
    fixed = _re.sub(r'(?<=[:,\s])"([^":,\}\]]+)"(?=\s*[:,\}\]])', r'\\"\1\\"', raw)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    # 更暴力的修复：把值内所有非结构性的 " 转义
    try:
        return json.loads(raw.replace('"', '\\"').replace('\\"{', '{').replace('\\"\\[', '[').replace('\\\\\\"', '\\"'))
    except json.JSONDecodeError:
        pass
    print(f"  [auto] AI 输出解析失败，原始输出：{out[:500]}")
    return {}


def _auto_decide(old_note: str, new_note: str) -> str:
    """全自动模式下，让 AI 判断是新建、更新还是跳过。返回 'force-new' / 'update' / 'skip'。"""
    prompt = f"""你是一个知识库管理员。请对比以下两篇笔记，判断操作。

## 已有笔记
{old_note}

## 新内容
{new_note}

请输出 JSON：{{"action": "force-new" | "update" | "skip"}}

判断规则：
- force-new（假阳性）：两篇提到同一公司/人物/产品，但讲的是不同事件/角度，即便关键词大量重叠。例如：
  * 旧笔记讲"王兴兴登上时代封面"，新内容讲"日方拆解宇树G1" → 都是宇树科技但事件不同 → force-new
  * 旧笔记讲"产品A的性能参数"，新内容讲"产品A的价格调整" → 都是同一产品但维度不同 → force-new
- update：主题完全相同，新内容新增了旧笔记未覆盖的数据/结论/细节。如：旧笔记讲"某政策发布"，新内容讲同一政策的实施细则
- skip：新内容与旧笔记基本一致，无实质新增信息（真重复）
"""

    out = _call_kimi(prompt)
    import re as _re
    m = _re.search(r'"action"\s*:\s*"(force-new|update|skip)"', out)
    if m:
        return m.group(1)
    if 'force-new' in out or 'force_new' in out:
        return 'force-new'
    if 'update' in out:
        return 'update'
    return 'skip'


def _auto_merge(old_note: str, new_content: str) -> str:
    """全自动模式下，让 AI 将旧笔记和新内容合并为单一 flomo markdown。返回合并后的 markdown。"""
    prompt = f"""你是一个知识库编辑。请将以下"已有笔记"和"新内容"合并为单一 flomo 笔记。

flomo 笔记固定格式如下（原文格式可能不同，必须转为这个标准格式）：
```
#信号类型 #领域 #二级领域

**领域_二级领域_知识点**

**来源**：出处

**概念**：定义...（关键术语用<mark>高亮</mark>）
```

要求：
- **根据合并后的内容重新确定标签行和标题行**（领域/二级领域/知识点名称），确保标签行与标题行中的领域一致
- 将旧笔记和新内容的信息合并到 **概念** 段落中（去重、整合）；信息量大时用空行分隔成多个自然段，每段聚焦一个要点，不要一段堆到底
- 只允许 **加粗** 和 <mark>高亮</mark>
- 直接输出合并后的完整 flomo markdown，不要额外文字

## 已有笔记
{old_note}

## 新内容
{new_content}
"""

    out = _call_kimi(prompt)
    import re as _re
    # 清理行首项目符号和缩进：只剥 • 和空格，不剥 - /* 等内容符号
    cleaned = '\n'.join(
        _re.sub(r'^[•\s]+', '', line) for line in out.split('\n')
    )
    # 尝试提取标记围栏内的内容
    for delim in ['```markdown', '```', '---']:
        if delim in cleaned:
            parts = cleaned.split(delim)
            for i, p in enumerate(parts):
                if '**概念**' in p or '#信号' in p or '#知识' in p or '#趋势' in p:
                    return p.strip()
    # 找第一个 # 开头的标签行开始截取
    lines = cleaned.split('\n')
    start = -1
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith('#') and ('#信号' in s or '#知识' in s or '#趋势' in s or '#分析' in s):
            start = i
            break
    if start >= 0:
        return '\n'.join(lines[start:]).strip()
    # 最后回退：取包含 **概念** 的部分
    idx = cleaned.find('**概念**')
    if idx > 0:
        return cleaned[idx:].strip()
    return cleaned.strip()


def _git_unstage(full_path: Path, *, quiet: bool = True):
    """将已 git add 的草稿文件取消暂存（hook 验证/上传完成后的清理步骤）。

    - 捕获输出避免污染 stdout（否则 hook 校验、日志解析可能误判）
    - 静默失败：文件未被 add 时 git reset 也会成功，无需抛错
    """
    try:
        subprocess.run(
            ["git", "reset", "HEAD", "--", str(full_path.relative_to(BASE_DIR))],
            cwd=str(BASE_DIR), capture_output=quiet, creationflags=CREATE_NO_WINDOW
        )
    except Exception:
        pass


def process_content(args):
    """处理 --content 正文，直接完成 → 构建 → 验证 → 上传全流程。"""
    if not (hasattr(args, 'content') and args.content):
        print("  [error] 需要提供 --content 参数")
        return False
    text = args.content
    source = None  # will default to "网络"
    source_title = None
    wx_title = ""
    print(f"\n[文本模式] 使用提供的 --content（{len(text)} 字符）")

    # 取首行做标题参考
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    title = lines[0][:60] if lines else "未命名"
    if len(title) > 60:
        title = title[:57] + "..."

    # 全自动模式：用 AI 补全缺少的参数（失败自动重试修正）
    for retry in range(2):
        if retry > 0:
            print(f"  [auto] 第 {retry+1} 次尝试（修正上次错误）...")
        hint = ""
        if retry == 1:
            hint = ("⚠️ 上次输出不完整或解析失败。请输出更精简的 JSON：ai_content 控制在 300 字以内、"
                    "2-3 个自然段，JSON 必须完整闭合（以 } 结尾）、所有字符串引号闭合，不要截断。")
        result = _auto_analyze(text, hint)
        if not result:
            if retry < 2:
                print("  [auto] 分析失败，重试...")
                continue
            print("  [auto] AI 分析失败，跳过本条")
            break
        if not args.domain and result.get("domain"):
            args.domain = result["domain"]
        if not args.subdomain and result.get("subdomain"):
            args.subdomain = result["subdomain"]
        if not getattr(args, 'title', None) and result.get("title"):
            args.title = result["title"]
        if not args.tags and result.get("tags"):
            args.tags = ' '.join(result["tags"])
        if not getattr(args, 'ai_content', None) and result.get("ai_content"):
            args.ai_content = result["ai_content"].replace('\\n', '\n')
            # 去掉 ai_content 开头的标签行（脚本会自动拼接）
            lines = args.ai_content.split('\n', 1)
            if lines and lines[0].startswith('#'):
                args.ai_content = lines[1].strip() if len(lines) > 1 else ''
        # 三段统一清洗：每段自身不能含 _ 空格 / - 全角括号等非法字符
        # （先转空格为连字符再删连字符，等效于直接删空格，此处合并为一步删除）
        for _field in ['domain', 'subdomain', 'title']:
            val = getattr(args, _field, '') or ''
            val = val.replace('_', '').replace(' ', '').replace('/', '').replace('-', '').replace('（', '(').replace('）', ')')[:60]
            setattr(args, _field, val)
        # 构造标题并验证
        test_title = f"{args.domain}_{args.subdomain}_{args.title}"
        if test_title.count('_') != 2:
            print(f"  [auto] 标题格式不对（{test_title}，{test_title.count('_')}个_），重试...")
            text += f"\n\n[系统反馈：生成失败。领域、二级领域、知识点名称自身都不能包含下划线、空格、斜杠。当前产生的三段：领域='{args.domain}' 二级领域='{args.subdomain}' 知识点='{args.title}'。请修正后重试。]"
            continue
        # 用清洗后的 domain/subdomain 重建 tags，确保一致
        if hasattr(args, 'tags') and args.tags:
            signal_tag = args.tags.split()[0] if args.tags.split() else "#信号笔记"
            # 清理信号标签中的额外说明（如"#知识载体（工具/资源）"→"#知识载体"）
            import re as _re_tag
            clean_signal = _re_tag.match(r'(#[^#(\s]+)', signal_tag)
            if clean_signal:
                signal_tag = clean_signal.group(1)
            args.tags = f"{signal_tag} #{args.domain} #{args.subdomain}"
        print(f"  [auto] 领域: {args.domain}")
        print(f"  [auto] 二级领域: {args.subdomain}")
        print(f"  [auto] 标题: {args.title}")
        print(f"  [auto] 标签: {args.tags}")
        print(f"  [auto] ai-content 已生成（{len(getattr(args, 'ai_content', '') or '')} 字符）")
        break
    else:
        print("  [auto] 重试耗尽，跳过")

    # 3. 参数校验
    domain = args.domain
    subdomain = args.subdomain
    if not domain or not subdomain:
        print("  [error] AI 分析多次失败，未生成领域参数（domain/subdomain）")
        print("  [error] 请检查网络或稍后重试；也可手动指定 --domain \"领域\" --subdomain \"二级领域\"")
        return False
    if not (hasattr(args, 'tags') and args.tags):
        print("错误：--tags 必须提供信号类型标签")
        exit(1)
    tags = args.tags.split()
    knowledge = args.title

    # 4. 正文内容
    # 用 getattr 兜底：--auto 模式 AI 重试耗尽时 ai_content 可能从未生成
    body_text = getattr(args, 'ai_content', '')
    print(f"  [ai] 使用 AI 生成内容（{len(body_text)} 字符）")

    if not body_text:
        print("  [error] 正文内容为空")
        return False


    # 5.5 自动修正 body_text 格式：确保 **概念**：用粗体包裹
    body_text = re.sub(r'(?<!\*\*)(?<!子)概念：', '**概念**：', body_text)
    # 5.6 清洗标签行：去掉信号类型标签的中文括号（如 "#信号笔记（单次事件）" → "#信号笔记"）
    if tags:
        tags[0] = re.sub(r'（[^）]*）', '', tags[0])

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
    subprocess.run(["git", "add", "-f", str(full_path.relative_to(BASE_DIR))], cwd=str(BASE_DIR), creationflags=CREATE_NO_WINDOW)
    hook_result = subprocess.run(
        [PYTHON_BIN, str(BASE_DIR / "hooks" / "pre-commit"), "--staged"],
        cwd=str(BASE_DIR),
        capture_output=True, text=True, creationflags=CREATE_NO_WINDOW
    )
    if hook_result.returncode != 0:
        print(f"  [error] 格式验证失败:\n{hook_result.stdout}")
        _git_unstage(full_path, quiet=False)
        # 保留文件让用户修正
        print(f"  [file] 文件保留在: {full_path}")
        return False

    print("  [ok] 格式验证通过")

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
            # 强制 fetch 旧 markdown 到 stderr，AI 1 次跑能拿到完整内容
            import sys as _sys_for_stderr
            old_content = best.get("content", "")
            fetched_content = fetch_flomo_memo(old_id, keyword=knowledge if knowledge else None)
            if fetched_content:
                old_content = fetched_content
                print(f"  [update-hint] 已自动获取旧文档（{len(old_content)} 字符），供 AI 决策是否合并（SKILL §8）", file=_sys_for_stderr.stderr)
            else:
                print(f"  [update-hint] fetch_flomo_memo 未能拉取完整旧文档（{len(old_content)} 字符原始内容），AI 将基于原始内容做决策", file=_sys_for_stderr.stderr)
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
            if relevance >= 0.9:
                print(f"  [flomo] 检测到高相似笔记 id={old_id}（relevance={relevance:.2f}）")
                # 全自动模式：让 AI 自己决策
                _decision = _auto_decide(old_content, body_text)
                if _decision == "force-new":
                    print("  [auto] AI 决定：假阳性 → 强制新建")
                elif _decision == "update":
                    print(f"  [auto] AI 决定：有增量 → 更新 id={old_id}")
                    print("  [auto] 正在合并旧笔记和新内容...")
                    merged = _auto_merge(old_content, body_text)
                    if merged and len(merged) > 20:
                        update_content = merged
                        # 清除合并内容中残留的子概念段落（旧笔记带进来的）
                        if '**子概念**' in update_content:
                            update_content = update_content.split('**子概念**')[0].rstrip()
                        # 尝试用合并后内容自身的标题/标签（AI 已按 prompt 要求重新确定）
                        try:
                            merged_domain, merged_subdomain = _validate_and_extract_domain(update_content)
                            # 用合并后内容的领域覆盖原有值，确保后续校验一致
                            domain, subdomain = merged_domain, merged_subdomain
                            # 从合并内容解析标题，更新 knowledge
                            import re as _re_title_parse
                            _m = _re_title_parse.search(r'\*\*([^*]+)\*\*', update_content)
                            if _m:
                                _parts = _m.group(1).split('_', 2)
                                if len(_parts) == 3:
                                    knowledge = _parts[2]
                            print(f"  [auto] 合并后内容校验通过，使用合并后的领域: {domain}/{subdomain}")
                        except (ValueError, IndexError) as _merge_err:
                            print(f"  [error] 合并后内容格式校验失败: {_merge_err}")
                            print(f"  [error] 合并后的完整内容（{len(update_content)} 字符）：")
                            for _line in update_content.split('\n'):
                                print(f"  | {_line}")
                            print(f"  [error] 无法自动更新，旧笔记 id={old_id} 保持不变")
                            _git_unstage(full_path)
                            if full_path.exists(): full_path.unlink()
                            return False
                        # 确保 **概念**： 存在，否则回退到用新内容构造
                        if '**概念**' not in update_content:
                            print("  [auto] 合并结果缺少 **概念**，改用新内容构造")
                            tag_line = ' '.join(tags)
                            src = source_title if source_title else "网络"
                            update_content = f"""{tag_line}

{bold_title}

**来源**：{src}

{body_text}
"""
                        if '**来源**' not in update_content:
                            src = source_title if source_title else "网络"
                            update_content = update_content.replace('**概念**', f'**来源**：{src}\n\n**概念**', 1)
                        ok = update_flomo(old_id, update_content)
                        if ok:
                            print(f"  [auto] 更新成功 id={old_id}")
                        else:
                            print(f"  [auto] 更新失败")
                    else:
                        print(f"  [auto] 合并失败，跳过")
                    _git_unstage(full_path)
                    if full_path.exists(): full_path.unlink()
                    return True
                else:
                    print("  [auto] AI 决定：无增量 → 跳过")
                    _git_unstage(full_path)
                    if full_path.exists(): full_path.unlink()
                    return True
            else:
                print(f"  [flomo] 低相关（relevance={relevance:.2f}），继续新建")
                print("  [decide-rule] relevance < 0.9 → 脚本自动 continue 新建（不需要 AI 介入）")

    # 8. 上传到 flomo
    print(f"\n==BEGIN_FLOMO_NOTE==\n{flomo_content}\n==END_FLOMO_NOTE==")
    flomo_id = upload_flomo(flomo_content)
    if flomo_id:
        print(f"  [flomo] 上传成功 id={flomo_id}")
        # 清理
        _git_unstage(full_path)
        full_path.unlink()
        print(f"  [cleanup] 已删除本地文件")
    else:
        print(f"  [flomo] 上传失败，文件保留在: {full_path}")

    print(f"\n[完成] 处理完成!")
    return True


def main():
    parser = argparse.ArgumentParser(description="mynews inbox 处理器（本地分类版本）")
    parser.add_argument("--auto", action="store_true", help="全自动模式：脚本自行调用 AI 分析正文并生成全部参数")
    parser.add_argument("--domain", type=str, help="领域（必填，如 --domain 技术 --subdomain AI）")
    parser.add_argument("--subdomain", type=str, help="二级领域（必填）")
    parser.add_argument("--tags", type=str,
                        help="标签（第一个为信号类型标签：#知识基座/#趋势信号/#信号笔记/#分析框架/#知识载体，其余为领域/二级领域标签，如 --tags '#知识基座 #技术 #AI'）")
    parser.add_argument("--content", type=str,
                        help="原材料正文（必填，传递给 AI 处理的文本）")
    args = parser.parse_args()

    # auto 模式：--content 必填
    if not (hasattr(args, 'content') and args.content):
        print("错误：--content 必填（auto 模式的原材料正文）")
        return

    process_content(args)


if __name__ == "__main__":
    main()
