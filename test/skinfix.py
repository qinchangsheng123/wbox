# -*- coding: utf-8 -*-
"""第三方「老包」fixture：声明 `performance=standard` 的皮肤长什么样。

v2.17.0 起骨架只剩默认一套，`standard` 不再换骨架（不换卡片、不换浮层、不开
动效）。这份 fixture 现在的作用是验证「老包照常加载、按默认骨架渲染、不报错」；
它同时保留了历史 id `liquid-glass`，供 test_plain_ui 验证老配置回退与日志留痕。

字段沿用旧内置档口径（standard / flat / custom、52/15/22/48、圆角 26/20/14），
颜色借内置默认的 token——断言的是行为与几何，不是配色。
只写临时目录，不碰生产配置。
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