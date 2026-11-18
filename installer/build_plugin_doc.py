# -*- coding: utf-8 -*-
"""把插件开发规范（Markdown）编译成一份**自包含**的 HTML，随安装包一起分发。

为什么需要这一步：
  安装目录里的 `插件开发指南.md` 双击不一定有程序打开（很多人没装 Markdown
  阅读器），而安装包不能联网、不能依赖外部 CSS/JS。所以这里把规范正文编译成
  单个 .html：样式内嵌、图片零依赖、不引用任何外部资源，双击就能用系统默认
  浏览器看。

支持哪些 Markdown 语法（只做本仓库文档用到的那一子集，不做通用实现）：
  标题 #~###### / 段落 / 无序与有序列表 / 围栏代码块 / 表格 / 引用块 /
  分隔线 / 行内 `code` / **粗体** / *斜体* / [链接](url)

用法（仓库根执行；build_exe.bat 会自动调用）：
    C:\\Python38\\python.exe installer\\build_plugin_doc.py
    C:\\Python38\\python.exe installer\\build_plugin_doc.py --check   # 只校验不写文件
    C:\\Python38\\python.exe installer\\build_plugin_doc.py --deploy <安装树根>
        -> 同时把 HTML / Markdown / 示例模板铺到 <安装树根>\\docs\\，
           供 payload 打进安装包（build_exe.bat 用的就是这个模式）。
           注意：build_exe.bat 必须是纯 ASCII（cmd.exe 会误解 UTF-8 注释），
           中文文件名一律在这个脚本里拼，不写进 .bat。
"""
import hashlib
import io
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'docs', 'PLUGIN-SPEC.md')
OUT = os.path.join(ROOT, 'docs', '插件开发指南.html')
TITLE = 'WBOX 插件开发指南'
TEMPLATE_DIR = os.path.join(ROOT, 'docs', 'plugin-template')

# ---------------------------------------------------------------- 行内
_CODE_TOKEN = '\x00%d\x00'


def _inline(text, stash):
    """行内格式化：`code` / **粗体** / *斜体* / [链接](url)。

    stash 是本次转换共享的代码片段池：先摘出 code span 再用占位符替换，
    否则 code 里的 * 和 [ ] 会被当成格式符号处理。
    """

    def keep_code(m):
        stash.append('<code>%s</code>' % _escape(m.group(1)))
        return _CODE_TOKEN % (len(stash) - 1)

    text = re.sub(r'`([^`]+)`', keep_code, text)
    text = _escape(text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'<em>\1</em>', text)
    text = re.sub(r'\[([^\]]+)\]\(([^)\s]+)\)', r'<a href="\2">\1</a>', text)

    def restore(m):
        return stash[int(m.group(1))]

    return re.sub(r'\x00(\d+)\x00', restore, text)


def _escape(text):
    return (text.replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;'))


def _slug(text):
    """标题 -> 锚点 id。保留中文（浏览器支持），去掉标点与空白。"""
    plain = re.sub(r'`([^`]+)`', r'\1', text)
    plain = re.sub(r'[*_\[\]()]', '', plain).strip().lower()
    plain = re.sub(r'[^\w\u4e00-\u9fff]+', '-', plain)
    return plain.strip('-') or 'sec'


# ---------------------------------------------------------------- 块级
_H_RE = re.compile(r'^(#{1,6})\s+(.*)$')
_UL_RE = re.compile(r'^[-*]\s+(.*)$')
_OL_RE = re.compile(r'^(\d+)\.\s+(.*)$')
_SEP_CELL_RE = re.compile(r'^:?-{2,}:?$')


def _is_table_sep(line):
    s = line.strip()
    if not s.startswith('|'):
        return False
    cells = [c.strip() for c in s.strip('|').split('|')]
    return bool(cells) and all(_SEP_CELL_RE.match(c) for c in cells if c)


def _split_row(line):
    return [c.strip() for c in line.strip().strip('|').split('|')]


def render_blocks(lines, stash, out, toc, level=0):
    """把一段 Markdown 行渲染进 out（list）；toc 收集二级标题。"""
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # 围栏代码块
        if stripped.startswith('```'):
            lang = stripped[3:].strip()
            i += 1
            buf = []
            while i < n and not lines[i].strip().startswith('```'):
                buf.append(lines[i])
                i += 1
            i += 1
            cls = ' class="lang-%s"' % _escape(lang) if lang else ''
            out.append('<pre><code%s>%s</code></pre>'
                       % (cls, _escape('\n'.join(buf))))
            continue

        # 表格
        if stripped.startswith('|') and i + 1 < n and _is_table_sep(lines[i + 1]):
            head = _split_row(lines[i])
            i += 2
            body = []
            while i < n and lines[i].strip().startswith('|'):
                body.append(_split_row(lines[i]))
                i += 1
            rows = ['<div class="table-wrap"><table><thead><tr>']
            for cell in head:
                rows.append('<th>%s</th>' % _inline(cell, stash))
            rows.append('</tr></thead><tbody>')
            for row in body:
                rows.append('<tr>%s</tr>'
                            % ''.join('<td>%s</td>' % _inline(c, stash)
                                      for c in row))
            rows.append('</tbody></table></div>')
            out.append(''.join(rows))
            continue

        # 标题
        m = _H_RE.match(stripped)
        if m:
            depth = len(m.group(1))
            text = m.group(2).strip()
            anchor = _slug(text)
            if depth == 1:
                out.append('<h1 id="%s">%s</h1>' % (anchor, _inline(text, stash)))
            else:
                out.append('<h%d id="%s">%s</h%d>'
                           % (depth, anchor, _inline(text, stash), depth))
                if depth == 2:
                    toc.append((anchor, _inline(text, stash)))
            i += 1
            continue

        # 分隔线
        if re.match(r'^(-{3,}|\*{3,}|_{3,})$', stripped):
            out.append('<hr>')
            i += 1
            continue

        # 引用块（递归：引用里可以有列表/段落）
        if stripped.startswith('>'):
            buf = []
            while i < n and lines[i].strip().startswith('>'):
                buf.append(re.sub(r'^\s*>\s?', '', lines[i]))
                i += 1
            inner = []
            render_blocks(buf, stash, inner, toc, level + 1)
            out.append('<blockquote>%s</blockquote>' % ''.join(inner))
            continue

        # 无序列表
        if _UL_RE.match(line):
            items = []
            while i < n:
                m = _UL_RE.match(lines[i])
                if not m:
                    break
                text = m.group(1).strip()
                i += 1
                # 续行（缩进的下一行）并进同一条
                while i < n and lines[i].strip() and \
                        not _UL_RE.match(lines[i]) and \
                        not _OL_RE.match(lines[i]) and \
                        lines[i].startswith(('  ', '\t')):
                    text += ' ' + lines[i].strip()
                    i += 1
                items.append('<li>%s</li>' % _inline(text, stash))
            out.append('<ul>%s</ul>' % ''.join(items))
            continue

        # 有序列表
        if _OL_RE.match(line):
            items = []
            while i < n:
                m = _OL_RE.match(lines[i])
                if not m:
                    break
                items.append('<li>%s</li>' % _inline(m.group(2).strip(), stash))
                i += 1
            out.append('<ol>%s</ol>' % ''.join(items))
            continue

        # 段落：连续非空行且不是其它块起始
        buf = [stripped]
        i += 1
        while i < n:
            nxt = lines[i]
            s = nxt.strip()
            if (not s or s.startswith('```') or s.startswith('>')
                    or s.startswith('|') or _H_RE.match(s)
                    or _UL_RE.match(nxt) or _OL_RE.match(nxt)
                    or re.match(r'^(-{3,})$', s)):
                break
            buf.append(s)
            i += 1
        out.append('<p>%s</p>' % _inline(' '.join(buf), stash))


# ---------------------------------------------------------------- 页面
CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  margin: 0 auto; padding: 40px 28px 80px; max-width: 960px;
  font: 15px/1.72 -apple-system, "Segoe UI", "Microsoft YaHei",
        "PingFang SC", "Hiragino Sans GB", sans-serif;
  color: #1d2126; background: #ffffff; word-wrap: break-word;
}
h1 { font-size: 27px; margin: 0 0 6px; letter-spacing: -.2px; }
h2 { font-size: 21px; margin: 40px 0 12px; padding-bottom: 7px;
     border-bottom: 1px solid #e6e8eb; }
h3 { font-size: 17px; margin: 26px 0 8px; }
h4 { font-size: 15px; margin: 20px 0 6px; }
p { margin: 10px 0; }
a { color: #1668c1; text-decoration: none; }
a:hover { text-decoration: underline; }
code { font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
       font-size: 13px; background: #f3f4f6; border: 1px solid #e6e8eb;
       border-radius: 4px; padding: .1em .35em; }
pre { background: #f7f8fa; border: 1px solid #e6e8eb; border-radius: 8px;
      padding: 14px 16px; overflow: auto; }
pre code { background: none; border: 0; padding: 0; font-size: 13px;
           line-height: 1.6; white-space: pre; }
blockquote { margin: 16px 0; padding: 12px 18px; border-left: 4px solid #e8a33d;
             background: #fffaf1; border-radius: 0 8px 8px 0; }
blockquote p { margin: 6px 0; }
blockquote strong { color: #8a4b00; }
.table-wrap { overflow-x: auto; margin: 14px 0; }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th, td { border: 1px solid #e6e8eb; padding: 8px 11px; text-align: left;
         vertical-align: top; }
th { background: #f7f8fa; font-weight: 600; }
hr { border: 0; border-top: 1px solid #e6e8eb; margin: 30px 0; }
ul, ol { margin: 10px 0; padding-left: 24px; }
li { margin: 4px 0; }
.toc { border: 1px solid #e6e8eb; border-radius: 10px; padding: 14px 20px;
       margin: 22px 0 30px; background: #fbfcfd; }
.toc-title { font-size: 13px; font-weight: 600; color: #5b6470;
             letter-spacing: .06em; margin-bottom: 6px; }
.toc ol { margin: 4px 0; padding-left: 22px; }
.toc a { color: #34404d; }
.meta { color: #6b7480; font-size: 13px; margin: 0 0 4px; }
.warn-mark { color: #a34a00; }
@media (prefers-color-scheme: dark) {
  body { color: #d7dbe0; background: #14171a; }
  h2, hr { border-color: #2a3037; }
  code { background: #1d2226; border-color: #2a3037; }
  pre { background: #1a1f23; border-color: #2a3037; }
  blockquote { background: #241d10; border-left-color: #c98a2e; }
  blockquote strong { color: #f0b75a; }
  th { background: #1a1f23; }
  th, td { border-color: #2a3037; }
  .toc { background: #1a1f23; border-color: #2a3037; }
  .toc a { color: #b8c2cc; }
  a { color: #62a8f0; }
}
@media print {
  body { max-width: none; padding: 0; }
  pre, blockquote, table { page-break-inside: avoid; }
}
"""

PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(title)s</title>
<style>%(css)s</style>
</head>
<body>
<p class="meta">%(title)s · 随安装包分发 · 与 WBOX 版本同步更新</p>
%(toc)s
%(body)s
</body>
</html>
"""


def convert(md_text):
    """Markdown 文本 -> 完整 HTML 字符串。"""
    stash = []
    lines = md_text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    body = []
    toc = []
    render_blocks(lines, stash, body, toc)
    if toc:
        items = ''.join('<li><a href="#%s">%s</a></li>' % (a, t)
                        for a, t in toc)
        toc_html = ('<nav class="toc"><div class="toc-title">目录</div>'
                    '<ol>%s</ol></nav>' % items)
    else:
        toc_html = ''
    return PAGE % {'title': _escape(TITLE), 'css': CSS,
                   'toc': toc_html, 'body': '\n'.join(body)}


def _read_src():
    if not os.path.isfile(SRC):
        raise SystemExit('找不到规范正文：%s' % SRC)
    with io.open(SRC, 'r', encoding='utf-8') as f:
        return f.read()


def build(write=True):
    """读 docs/PLUGIN-SPEC.md，写出 docs/插件开发指南.html，返回输出路径。"""
    md_text = _read_src()
    html = convert(md_text)
    if write:
        with io.open(OUT, 'w', encoding='utf-8', newline='\n') as f:
            f.write(html)
        print('插件开发指南 -> %s (%d B, 源 %d B)'
              % (os.path.relpath(OUT, ROOT), len(html.encode('utf-8')),
                 len(md_text.encode('utf-8'))))
    return OUT


def check():
    """--check：确认磁盘上的 HTML 与"用当前 Markdown 重编"的结果逐字节一致。

    这里以前只把 HTML 在内存里重算一遍就打印"校验通过"，**既没读磁盘也没比
    内容**：把正文改 4 个字、甚至把 HTML 整个删掉，它都照样 exit 0（QA 实测
    抓到的空壳）。现在真比字节：文件缺失、内容不一致都返回非 0，并打印可定位
    的差异摘要，方便在 CI / 提交前当守门员用。
    """
    want = convert(_read_src()).encode('utf-8')
    rel = os.path.relpath(OUT, ROOT)
    if not os.path.isfile(OUT):
        print('校验失败：%s 不存在（docs 里的 HTML 没有生成过）' % rel)
        print('  修法：C:\\Python38\\python.exe installer\\build_plugin_doc.py')
        return 1
    with io.open(OUT, 'rb') as f:
        got = f.read()
    if got == want:
        print('校验通过：%s 与 PLUGIN-SPEC.md 重编结果一致（%d B，md5 %s）'
              % (rel, len(want), hashlib.md5(want).hexdigest()))
        return 0
    # 定位第一处差异：只报位置和上下文，不打印整页 HTML
    pos = next((i for i in range(min(len(got), len(want))) if got[i] != want[i]),
               min(len(got), len(want)))
    print('校验失败：%s 与 PLUGIN-SPEC.md 重编结果不一致' % rel)
    print('  磁盘 %d B / md5 %s' % (len(got), hashlib.md5(got).hexdigest()))
    print('  重编 %d B / md5 %s' % (len(want), hashlib.md5(want).hexdigest()))
    print('  第一处差异在第 %d 字节' % pos)
    print('    磁盘：%r' % (got[pos:pos + 60],))
    print('    重编：%r' % (want[pos:pos + 60],))
    print('  修法：C:\\Python38\\python.exe installer\\build_plugin_doc.py')
    return 1


def deploy(target_root):
    """把指南三件套铺到 <安装树根>\\docs\\，供 payload.zip 打包。

    铺的是：编译出的自包含 HTML（双击可读）+ Markdown 原文件（可复制粘贴）+
    plugin-template\\（可直接改名使用的示例插件）。

    为什么由本脚本铺而不是 build_exe.bat 里的 xcopy：
      .bat 必须是纯 ASCII（cmd.exe 会按 ANSI 解释 UTF-8 注释与文件名），
      而 HTML 的文件名是中文；把中文路径放在 Python 里拼，两边都不出乱码。
    """
    html_path = build(write=True)
    docs = os.path.join(target_root, 'docs')
    if not os.path.isdir(docs):
        os.makedirs(docs)
    shutil.copy2(html_path, os.path.join(docs, os.path.basename(html_path)))
    shutil.copy2(SRC, os.path.join(docs, 'PLUGIN-SPEC.md'))
    tmpl_dst = os.path.join(docs, 'plugin-template')
    if os.path.isdir(tmpl_dst):
        shutil.rmtree(tmpl_dst)
    shutil.copytree(TEMPLATE_DIR, tmpl_dst,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shipped = []
    for dp, _dns, fns in os.walk(docs):
        for fn in sorted(fns):
            rel = os.path.relpath(os.path.join(dp, fn), target_root)
            shipped.append(rel.replace('\\', '/'))
    missing = [n for n in ('docs/插件开发指南.html', 'docs/PLUGIN-SPEC.md')
               if n not in shipped]
    if missing:
        raise SystemExit('指南部署失败，缺少：%s' % '、'.join(missing))
    print('插件开发指南已铺到 %s\\docs\\：%d 个文件' % (target_root, len(shipped)))
    return shipped


if __name__ == '__main__':
    argv = sys.argv[1:]
    if '--deploy' in argv:
        pos = argv.index('--deploy')
        if pos + 1 >= len(argv):
            raise SystemExit('用法：build_plugin_doc.py --deploy <安装树根>')
        deploy(os.path.abspath(argv[pos + 1]))
    elif '--check' in argv:
        sys.exit(check())
    else:
        build(write=True)
