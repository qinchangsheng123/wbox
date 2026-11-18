# -*- coding: utf-8 -*-
"""WBOX 空闲/动画/浮层成本探针 v1.0.0（perf-bench 的补充）

三个问题，各测一个数：
  1) idle   零定时器时，面板开着不动，2 秒吃多少 CPU
  2) anim   16 ms 定时器 + 每帧改 1 个元素颜色，2 秒吃多少 CPU（过渡动画的代价）
  3) popup  行内展开 vs 新建 Toplevel 浮层，各要多少毫秒

用法：C:\\Python38\\python.exe design\\perf-probe.py
"""

import os
import sys
import time

import tkinter as tk


def cpu_ms():
    """进程累计 CPU 时间（ms）。time.process_time 是本进程 CPU 时间的可移植读数，
    比 GetProcessTimes 少一次 P/Invoke，也不需要打开进程句柄。"""
    return time.process_time() * 1000.0


def base_window():
    r = tk.Tk()
    r.overrideredirect(True)
    r.geometry('640x332+60+60')
    r.configure(bg='#FFFFFF')
    bar = tk.Canvas(r, width=640, height=48, bg='#FFFFFF', highlightthickness=0)
    bar.pack()
    item = bar.create_rectangle(20, 16, 80, 32, fill='#000000', outline='')
    body = tk.Frame(r, bg='#FFFFFF')
    body.pack(fill='both', expand=True)
    for i in range(5):
        tk.Label(body, text='插件 %d' % (i + 1), bg='#FFFFFF', anchor='w').pack(fill='x', padx=16, pady=6)
    r.update_idletasks()
    r.update()
    return r, bar, item, body


def probe_idle(seconds=2.0):
    r, bar, item, body = base_window()
    time.sleep(0.3)
    c0 = cpu_ms()
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        r.update()
        time.sleep(0.002)
    used = cpu_ms() - c0
    r.destroy()
    return used / (seconds * 1000.0) * 100.0


def probe_anim(seconds=2.0, interval=16):
    """模拟 60 fps 过渡动画：每帧改一个元素颜色并强制刷新。

    和 idle 用同一套 while+update 结构，只多一件事：每帧重绘，
    这样两档的差值就是"动画本身"的代价，不掺 mainloop 调度差异。
    """
    r, bar, item, body = base_window()
    frames = 0
    time.sleep(0.3)
    c0 = cpu_ms()
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        frames += 1
        bar.itemconfigure(item, fill='#111111' if frames % 2 else '#000000')
        r.update()
        elapsed = (time.perf_counter() - t0) * 1000.0
        ahead = frames * interval - elapsed
        if ahead > 0:
            time.sleep(ahead / 1000.0)
    used = cpu_ms() - c0
    fps = frames / seconds
    r.destroy()
    return used / (seconds * 1000.0) * 100.0, fps


def probe_popup():
    r, bar, item, body = base_window()
    # 行内展开：直接在 body 里 pack 一个 frame
    t0 = time.perf_counter()
    inline = tk.Frame(body, bg='#F5F5F5', height=88)
    inline.pack(fill='x')
    for i in range(3):
        tk.Label(inline, text='设置项 %d' % (i + 1), bg='#F5F5F5', anchor='w').pack(fill='x', padx=18)
    r.update_idletasks()
    r.update()
    t_inline = (time.perf_counter() - t0) * 1000.0
    inline.destroy()
    r.update()

    # 独立 Toplevel 浮层
    t0 = time.perf_counter()
    top = tk.Toplevel(r)
    top.overrideredirect(True)
    top.geometry('296x180+200+200')
    top.configure(bg='#FBFBFD')
    for i in range(3):
        tk.Label(top, text='设置项 %d' % (i + 1), bg='#FBFBFD', anchor='w').pack(fill='x', padx=12)
    top.update_idletasks()
    top.update()
    t_top = (time.perf_counter() - t0) * 1000.0
    top.destroy()
    r.destroy()
    return t_inline, t_top


def main():
    results = {}
    idle = [probe_idle() for _ in range(2)]
    results['idle'] = sum(idle) / len(idle)
    anim = [probe_anim() for _ in range(2)]
    results['anim'] = sum(v[0] for v in anim) / len(anim)
    results['fps'] = sum(v[1] for v in anim) / len(anim)
    ins = []
    tops = []
    for _ in range(3):
        a, b = probe_popup()
        ins.append(a)
        tops.append(b)
    results['inline_ms'] = sorted(ins)[1]
    results['toplevel_ms'] = sorted(tops)[1]

    print('空闲 CPU（零定时器）      %5.2f %%' % results['idle'])
    print('空闲 CPU（60fps 重绘）    %5.2f %%   @ %.0f fps  ← 动画常驻的代价'
          % (results['anim'], results['fps']))
    print('行内展开 首次绘制         %5.1f ms' % results['inline_ms'])
    print('Toplevel 浮层 首次绘制    %5.1f ms   ← 倍数 %.1fx' %
          (results['toplevel_ms'], results['toplevel_ms'] / max(results['inline_ms'], .01)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
