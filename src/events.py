# -*- coding: utf-8 -*-
"""WBOX - 结构化事件流（v2.13.0）。

为什么在 guard.log 之外再加一份：guard.log 是给人读的中文流水账，排查
「哪个插件把守护拖慢了」「ACE 这一轮为什么没清」「弹窗是被哪条规则关的」
时只能靠肉眼 grep，且字段（插件 id / pid / 耗时 / 错误码）都混在句子里。
这里把它们拆成固定字段，按行写 JSON（JSONL），一行一个事件：

    {"ts":"2026-11-19 20:31:02","ver":"2.13.0","cat":"plugin.hook",
     "code":"HOOK_SLOW","plugin":"ace","pid":0,"session":17,
     "dur_ms":6120,"msg":"on_tick 耗时 6.1s"}

约定（写代码时定死）：
  - 文件固定 `events.jsonl`，与 guard.log 同目录；追加写，1 MB 轮转保留一代。
  - 字段名固定：ts / ver / cat / code / plugin / pid / session / dur_ms / msg；
    其余键原样并入（都是标量，便于后续按字段过滤）。
  - **永不抛异常**：事件流是诊断设施，不能反过来把守护弄挂。
  - 只在守护 / 安装器进程启用；面板调用 `configure()` 后再 emit 才落盘。

诊断包（bundle）把 guard.log、events.jsonl、崩溃日志、插件状态、配置和
各插件自己写的运行时状态打成一个 zip，用户点「导出诊断」或跑 `WBOX.exe --diag`
即可拿到，不必再让人手工翻安装目录。
"""
import json
import os
import time
import zipfile

import core_defaults

MAX_BYTES = 1024 * 1024
NAME = 'events.jsonl'

_path = None
_lock = None
_dropped = 0


def _get_lock():
    global _lock
    if _lock is None:
        import threading
        _lock = threading.Lock()
    return _lock


def events_path(base):
    return os.path.join(base, NAME)


def configure(base):
    """绑定安装根目录；重复调用只换路径（自检进程用临时目录）。"""
    global _path
    _path = events_path(base) if base else None
    return _path


def _rotate(path):
    try:
        if os.path.exists(path) and os.path.getsize(path) > MAX_BYTES:
            old = path + '.1'
            try:
                if os.path.exists(old):
                    os.remove(old)
            except OSError:
                pass
            os.replace(path, old)
    except OSError:
        pass


def emit(cat, code='', msg='', plugin='', pid=None, session=None, dur_ms=None,
         **extra):
    """写一条事件。cat 是类别（plugin.hook / session / ace / popup / config /
    install / startup），code 是稳定错误码（HOOK_SLOW / HOOK_ERROR / ...）。

    返回 True 表示已落盘。没 configure 或写失败都返回 False，不抛异常。
    """
    global _dropped
    path = _path
    if not path:
        return False
    rec = {'ts': time.strftime('%Y-%m-%d %H:%M:%S'),
           'ver': core_defaults.VERSION,
           'cat': str(cat)[:32],
           'code': str(code or '')[:40],
           'plugin': str(plugin or '')[:32],
           'pid': int(pid) if pid else 0,
           'session': int(session) if session is not None else 0,
           'dur_ms': int(dur_ms) if dur_ms is not None else 0,
           'msg': str(msg or '')[:400]}
    for k, v in (extra or {}).items():
        if isinstance(v, (int, float, bool)) or v is None:
            rec[str(k)[:24]] = v
        else:
            rec[str(k)[:24]] = str(v)[:120]
    try:
        line = json.dumps(rec, ensure_ascii=False, sort_keys=False)
        with _get_lock():
            _rotate(path)
            with open(path, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        return True
    except Exception:
        _dropped += 1
        return False


def dropped():
    """写失败被吞掉的条数（自检输出用；不参与任何判定）。"""
    return _dropped


# ---------------------------------------------------------------- 诊断包
def _add(zf, path, arcname=None):
    try:
        if os.path.isfile(path):
            zf.write(path, arcname or os.path.basename(path))
            return True
    except OSError:
        pass
    return False


def _plugin_rows(plugins_dir):
    """插件清单：id / 版本 / 状态。读 plugin.json 与 plugins_state.json。"""
    rows = []
    try:
        names = sorted(os.listdir(plugins_dir))
    except OSError:
        return rows
    for name in names:
        pj = os.path.join(plugins_dir, name, 'plugin.json')
        if not os.path.isfile(pj):
            continue
        try:
            with open(pj, 'r', encoding='utf-8-sig') as f:
                raw = json.load(f)
        except Exception:
            raw = {}
        rows.append({'dir': name,
                     'id': raw.get('id') or name,
                     'version': raw.get('version') or '?',
                     'api': raw.get('api')})
    return rows


def bundle(base, out_path=None):
    """打一个诊断包，返回 zip 绝对路径（失败返回空串）。

    内容：meta.json（版本/时间/插件清单）+ guard.log(+.1) + events.jsonl(+.1)
    + guard_crash.log(+.1) + plugins_state.json + config.json + ace_state.json
    + 各插件的 data\\*.json（只取 json，二进制/大文件跳过）。
    """
    base = os.path.abspath(base)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    out_path = out_path or os.path.join(base, 'wbox-diag-%s.zip' % stamp)
    tmp = out_path + '.tmp'
    meta = {
        'app': 'WBOX',
        'version': core_defaults.VERSION,
        'created': time.strftime('%Y-%m-%d %H:%M:%S'),
        'pid': os.getpid(),
        'base': base,
        'python': '%d.%d.%d' % (0, 0, 0),
        'plugins': _plugin_rows(os.path.join(base, 'plugins')),
    }
    try:
        import sys
        meta['python'] = '%d.%d.%d' % sys.version_info[:3]
    except Exception:
        pass
    try:
        with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('meta.json', json.dumps(meta, ensure_ascii=False,
                                                indent=2))
            for name in ('guard.log', 'guard.log.1', NAME, NAME + '.1',
                         'guard_crash.log', 'guard_crash.log.1',
                         'plugins_state.json', 'config.json', 'config.json.bak',
                         'ace_state.json'):
                _add(zf, os.path.join(base, name))
            # 各插件运行时状态（data\*.json）
            pdir = os.path.join(base, 'plugins')
            try:
                for pid_dir in sorted(os.listdir(pdir)):
                    ddir = os.path.join(pdir, pid_dir, 'data')
                    if not os.path.isdir(ddir):
                        continue
                    for fn in sorted(os.listdir(ddir)):
                        if fn.lower().endswith('.json'):
                            _add(zf, os.path.join(ddir, fn),
                                 '%s/data/%s' % (pid_dir, fn))
            except OSError:
                pass
        os.replace(tmp, out_path)
        return out_path
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return ''
