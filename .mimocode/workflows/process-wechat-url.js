export const meta = {
  name: "process-wechat-url",
  description: "收到微信URL → 抓取并存入notes.md → 等用户确认后才上传flomo"
};

export async function main(args: { url: string }, ctx: { worktree: string }) {
  const { fetch_wechat_article } = await import(`${ctx.worktree}/scripts/mynews_utils.py`);
  const { writeFile, readFile } = await import("fs/promises");
  const path = await import("path");

  // Step 1: Fetch article
  const [content, source, error, wx_title] = fetch_wechat_article(args.url, false);

  if (error || !content) {
    return { success: false, error: `抓取失败: ${error}` };
  }

  // Step 2: Write to notes.md - this is the BLOCKING step
  const notesPath = path.join(ctx.worktree, ".mimocode/sessions/ses_08c4f6069ffeNrx5mrwd6kJF0B/notes.md");
  const entry = `\n## [URL收到 ${new Date().toISOString()}]\nURL: ${args.url}\n标题: ${wx_title}\n来源: ${source}\n字数: ${content.length}\n内容摘要: ${content.slice(0, 500)}\n---`;

  const current = await readFile(notesPath, "utf-8").catch(() => "");
  await writeFile(notesPath, current + entry);

  // Step 3: Return content for user to review - this is the STOP point
  return {
    success: true,
    url: args.url,
    title: wx_title,
    source,
    content_length: content.length,
    content_preview: content.slice(0, 800),
    message: "文章已抓取，内容在上面。等你说'可以处理'再上传。"
  };
}
