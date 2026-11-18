# -*- coding: utf-8 -*-
"""standard 档皮肤 fixture（v2.10.0 起液态玻璃不再内置）。

需要玻璃档语义（齿轮行尾 / 浮层 / tween 链）的测试往自己的临时 base 写一份
用户皮肤 `skins/liquid-glass/skin.json`：字段与旧内置档同口径（standard /
flat / custom、52/15/22/48、圆角 26/20/14），颜色借用内置极简的 token 值
（测试断言的是行为与几何，不是玻璃配色）。只写临时目录，不碰生产配置。
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))
import skins  # noqa: E402


def glass_data():
    plain = skins.BUILTIN[0]
    return {
        'id': 'liquid-glass', 'name': '液态玻璃', 'version': '1.0.0', 'api': 1,
        'engine': 'flat', 'performance': 'standard', 'chrome': 'custom',
        'author': 'WBOX', 'description': '圆角浮层动效参照档（测试 fixture）',
        'metrics': {'row': 52, 'font': 15, 'icon': 22, 'titlebar': 48},
        'radius': {'window': 26, 'card': 20, 'row': 14},
        'light': dict(plain['light']), 'dark': dict(plain['dark']),
    }


def write_glass(base):
    d = os.path.join(base, 'skins', 'liquid-glass')
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, 'skin.json'), 'w', encoding='utf-8') as f:
        json.dump(glass_data(), f, ensure_ascii=False, indent=2)
    return d