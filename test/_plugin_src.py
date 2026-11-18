# -*- coding: utf-8 -*-
"""插件源码定位（v2.11.3 起插件在独立仓库 wbox-plugins）。

本体仓库不再包含插件源码，但一部分测试要拿真实插件跑（desktop_hud 的指标
计算、gamepp 的识别规则、打包校验……）。这些测试不应该因为"没装插件仓库"
就红，也不应该把插件源码再复制回本体仓库。

解析顺序：
  1. 环境变量 `WBOX_PLUGINS_SRC`（CI / 临时检出用）；
  2. 同级检出 `../wbox-plugins/plugins`（开发机上的默认布局）。

两处都找不到时，调用方用 require() 打印一行说明并 exit(0)：这是"跳过"，不是
"通过" —— 输出里写清楚跳过了什么，别让人误以为插件被验证过。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def plugins_root():
    cands = [os.environ.get('WBOX_PLUGINS_SRC') or '',
             os.path.join(os.path.dirname(ROOT), 'wbox-plugins', 'plugins')]
    for c in cands:
        if c and os.path.isdir(c):
            return os.path.abspath(c)
    return ''


def require(what=''):
    """返回插件源码根目录；找不到就打印跳过原因并退出 0。"""
    path = plugins_root()
    if path:
        return path
    print('[skip] 未找到插件源码（%s）' % (what or '插件测试'))
    print('       插件已迁到独立仓库。克隆到同级目录即可跑这些用例：')
    print('       git clone https://github.com/qinchangsheng123/wbox-plugins '
          '../wbox-plugins')
    print('       或用 WBOX_PLUGINS_SRC 指向 plugins 目录。')
    sys.exit(0)
