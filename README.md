# mynews

AI 处理 RSS / 微信公众号 / GitHub Commit 等来源，生成 flomo 格式笔记并上传到 flomo。

## 文档格式

```
#信号类型 #领域 #二级领域 #具体概念

**领域_二级领域_知识点**

**来源**：出处

正文（可用 **加粗**、<mark>高亮</mark>、<u>下划线</u>、- 列表）
```

## 处理管道

```
RSS/Miniflux ──→ process_miniflux.py ──→ _inbox/ ──→ process_inbox.py ──→ flomo
       GitHub Commit ──────────────────────────────────┘
```

- **process_miniflux.py**: 从 Miniflux API 拉取新条目写入 `_inbox`
- **process_inbox.py**: 读取 `_inbox` 条目 → 调用 subagent 创建 flomo 格式文档 → `validate_flomo.py` 审查 → git commit（hook 验证）→ 上传 flomo → git reset

## Cron 自动化

```bash
# 每分钟执行
* * * * * cd /root/mynews && python3 scripts/process_miniflux.py >> logs/miniflux_cron.log 2>&1
* * * * * cd /root/mynews && python3 scripts/process_inbox.py >> logs/inbox_cron.log 2>&1

# 查看日志
tail -f logs/miniflux_cron.log
tail -f logs/inbox_cron.log

# 查看待处理数量
ls _inbox/*.md 2>/dev/null | wc -l
```

## 首次安装

```bash
./setup_hooks.sh          # 安装 pre-commit hook
mkdir -p logs             # 日志目录
```

## 目录结构

```
mynews/
├── answers/                  # 本地草稿（.gitignore，不上传）
├── _inbox/                   # 待处理条目
├── _inbox_done/              # 已处理
├── _inbox_failed/            # 处理失败
├── data/                     # 处理状态
├── logs/                     # cron 日志
├── scripts/                  # 自动化脚本
│   ├── process_miniflux.py  # 从 Miniflux 拉取条目
│   ├── process_inbox.py     # 处理 inbox 并上传 flomo
│   ├── validate_flomo.py    # flomo 格式审查
│   ├── title_to_path.py
│   ├── check_dir.py
│   ├── check_duplicate.py
│   └── doc_check.py
├── hooks/pre-commit           # flomo 格式验证 hook
├── struct-doc-answer/SKILL.md # 文档生成规范
└── opencode.json               # opencode 配置
```
