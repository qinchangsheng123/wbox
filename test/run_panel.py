# -*- coding: utf-8 -*-
"""从源码起设置面板（配置仍用 app\\config.json），只用于界面调试，不改任何设置。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = os.path.join(ROOT, 'app')
sys.path.insert(0, os.path.join(ROOT, 'src'))

import settings_gui as sg  # noqa: E402

sg.CFG = os.path.join(APP, 'config.json')
sg.LOG = os.path.join(APP, 'guard.log')
sg.PID_FILE = os.path.join(APP, 'guard.pid')
sg.STOP_FILE = os.path.join(APP, 'stop.flag')

if __name__ == '__main__':
    sg.main()
