# -*- coding: utf-8 -*-
"""把两位成员产出的 HTML 片段汇编成单页预览。

主理人只做装配：统一 CSS 变量、导航、主题切换、视图切换。
片段里的视觉与文案结论原样保留，不改写。

运行：python design/v2-ui/assemble.py
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PARTS = ('part-panel.frag.html', 'part-setup.frag.html')
OUT = os.path.join(ROOT, 'preview.html')

HEAD = u'''<!DOCTYPE html>
<html lang="zh-CN" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>僚机 Wingman · v2.x UI 改版预览（待确认）</title>
<style>
:root, html[data-theme="light"]{
  --bg:#FFFFFF; --fg:#000000; --t2:#595959; --t3:#767676;
  --line:#E5E5E5; --border:#767676; --hover:#F5F5F5; --active:#EBEBEB;
  --inv:#000000; --invfg:#FFFFFF;
}
html[data-theme="dark"]{
  --bg:#000000; --fg:#FFFFFF; --t2:#B3B3B3; --t3:#8C8C8C;
  --line:#2A2A2A; --border:#8C8C8C; --hover:#141414; --active:#202020;
  --inv:#FFFFFF; --invfg:#000000;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{background:var(--bg);color:var(--fg);
 font-family:"Microsoft YaHei UI","Segoe UI","PingFang SC",system-ui,sans-serif;
 font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased}
button{font:inherit;color:inherit;cursor:pointer}
:focus-visible{outline:2px solid var(--fg);outline-offset:2px}
.pv-nav{position:sticky;top:0;z-index:60;display:flex;align-items:center;gap:4px;
 height:56px;padding:0 24px;background:var(--bg);border-bottom:1px solid var(--line);
 flex-wrap:wrap}
.pv-brand{font-weight:700;font-size:14px;margin-right:16px;white-space:nowrap}
.pv-badge{font-size:11px;color:var(--t3);border:1px solid var(--line);
 padding:2px 8px;margin-right:12px;white-space:nowrap}
.pv-nav button{background:transparent;border:1px solid transparent;
 padding:6px 10px;font-size:13px;white-space:nowrap}
.pv-nav button:hover{background:var(--hover)}
.pv-nav button.is-on{background:var(--fg);color:var(--invfg)}
.pv-theme{margin-left:auto;border:1px solid var(--border)!important;padding:6px 12px!important}
.pv-main{max-width:1180px;margin:0 auto;padding:32px 24px 96px}
.pv-main > section{display:none}
.pv-main > section.is-on{display:block}
.pv-hint{color:var(--t3);font-size:12px;margin:0 0 20px}
.pv-note{border:1px solid var(--line);padding:12px 16px;margin:24px 0 0;
 font-size:12px;color:var(--t2)}
.pv-note b{color:var(--fg)}
.ic{width:20px;height:20px;display:inline-block;vertical-align:middle;color:inherit}
</style>
</head>
<body>
<nav class="pv-nav">
  <span class="pv-brand">僚机 Wingman</span>
  <span class="pv-badge">v2.x UI 预览 · 待确认</span>
  <button data-target="overview" class="is-on">总览</button>
  <button data-target="panel">设置面板</button>
  <button data-target="menu">齿轮菜单</button>
  <button data-target="setup">安装程序</button>
  <button data-target="icons">图标板</button>
  <button data-target="copy">文案对照</button>
  <button data-target="confirm">待确认</button>
  <button class="pv-theme" id="pv-theme" type="button">切换为深色</button>
</nav>
<main class="pv-main" id="pv-main">
'''

TAIL = u'''</main>
<p class="pv-note"><b>这是设计预览，不是真实窗口。</b> 本轮没有修改任何生产代码、没有重新打包。
确认后才会改 Tkinter 界面、图标与安装包；详见同目录 README.md。</p>
<script>
(function(){
  var main=document.getElementById('pv-main');
  var secs=main.querySelectorAll(':scope > section');
  var navs=document.querySelectorAll('.pv-nav [data-target]');
  function show(v){
    var hit=false;
    for(var i=0;i<secs.length;i++){
      var on=secs[i].getAttribute('data-view')===v;
      secs[i].classList.toggle('is-on',on);
      if(on) hit=true;
    }
    for(var j=0;j<navs.length;j++){
      navs[j].classList.toggle('is-on',navs[j].getAttribute('data-target')===v);
    }
    if(hit) window.scrollTo(0,0);
    return hit;
  }
  for(var k=0;k<navs.length;k++){
    navs[k].addEventListener('click',function(e){
      var v=e.currentTarget.getAttribute('data-target');
      show(v);
      if(history.replaceState) history.replaceState(null,'','#'+v);
    });
  }
  var themeBtn=document.getElementById('pv-theme');
  function setTheme(t){
    document.documentElement.setAttribute('data-theme',t);
    themeBtn.textContent=(t==='dark')?'切换为浅色':'切换为深色';
  }
  themeBtn.addEventListener('click',function(){
    var cur=document.documentElement.getAttribute('data-theme');
    setTheme(cur==='dark'?'light':'dark');
  });
  var initial=(location.hash||'').replace('#','');
  if(!show(initial||'overview')) show('overview');
})();
</script>
</body>
</html>
'''


def main():
    chunks = []
    for name in PARTS:
        p = os.path.join(ROOT, name)
        if not os.path.isfile(p):
            print('[FAIL] missing fragment: %s' % name)
            return 2
        with io.open(p, 'r', encoding='utf-8') as f:
            body = f.read()
        # 只查真正的文档级标签；<header> 是合法片段内容，不能误报。
        for bad in (r'<!doctype', r'<html[\s>]', r'<head[\s>]', r'<body[\s>]'):
            if re.search(bad, body, re.I):
                print('[WARN] %s contains %s (should be a fragment)' % (name, bad))
        chunks.append(body)
        print('[ok] %-26s %7d B' % (name, len(body.encode('utf-8'))))

    html = HEAD + u'\n'.join(chunks) + TAIL
    with io.open(OUT, 'w', encoding='utf-8') as f:
        f.write(html)

    views = re.findall(r'data-view="([a-z]+)"', html)
    symbols = re.findall(r'<symbol\s+id="(ic-[a-z0-9-]+)"', html)
    ext = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
    print('[ok] preview.html %d B' % len(html.encode('utf-8')))
    print('[ok] views(%d): %s' % (len(set(views)), ', '.join(sorted(set(views)))))
    print('[ok] symbols(%d)' % len(set(symbols)))
    print('[ok] external refs: %d' % len(ext))
    missing = [v for v in ('overview', 'panel', 'menu', 'setup', 'icons', 'copy', 'confirm')
               if v not in views]
    if missing:
        print('[FAIL] missing views: %s' % ', '.join(missing))
        return 3
    if ext:
        print('[FAIL] external dependency found')
        return 4
    return 0


if __name__ == '__main__':
    sys.exit(main())
