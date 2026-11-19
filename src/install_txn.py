# -*- coding: utf-8 -*-
"""WBOX - 安装/更新/卸载事务状态（v2.13.0，审查项 7）。

安装涉及这些会中途失败的步骤：停守护、暂存用户插件、解包程序文件、从安装器
补齐共享运行时、合并 config.json、建快捷方式、建计划任务、写卸载项。断电、
任务管理器强杀、杀软占用文件都可能停在任意一步，用户看到的却是"安装器没反应
了"或"装完打不开"，事后只能靠 %TEMP%\\wbox_setup.log 猜。

本模块给每个流程写一份**可恢复的事务状态**：

  - 每一步落一次盘（原子替换），记录 phase / 步骤名 / 时间 / 关键路径；
  - `%ProgramData%\\WBOX\\install_txn.json` 放事务日志（安装目录可能还不存在，
    所以不能放安装目录）；不可写时退到 %TEMP%；
  - 关键文件（config.json）在覆盖前备份到 `%ProgramData%\\WBOX\\backup\\`，
    事务中断且当前配置坏了就自动还原；
  - 下一次运行（安装器启动 / 守护启动）调 `recover()`：识别半完成状态，
    返回一行可读结论 —— "上次安装停在第 4 步，程序文件已就位，重跑安装器
    即可续做" / "已从备份恢复 config.json" / "上次卸载未删干净，需要再卸载一次"。

约定：所有函数**永不抛异常**（安装器自己已经在做危险动作，事务日志不能再
成为新的失败点）。
"""
import json
import os
import shutil
import tempfile
import time

NAME = 'install_txn.json'
MARKER = 'install_incomplete.txt'


def _data_dir():
    """事务目录：ProgramData\\WBOX（机器级、重启后仍在）；不可写时退到 TEMP。"""
    cands = []
    program_data = os.environ.get('ProgramData') or \
        os.environ.get('ALLUSERSPROFILE') or ''
    if program_data:
        cands.append(os.path.join(program_data, 'WBOX'))
    cands.append(os.path.join(tempfile.gettempdir(), 'wbox-txn'))
    for d in cands:
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, '.w')
            with open(probe, 'w', encoding='ascii') as f:
                f.write('1')
            os.remove(probe)
            return d
        except OSError:
            continue
    return tempfile.gettempdir()


def journal_path():
    return os.path.join(_data_dir(), NAME)


def backup_dir():
    d = os.path.join(_data_dir(), 'backup')
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def _write(data):
    path = journal_path()
    tmp = path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def read():
    try:
        with open(journal_path(), 'r', encoding='utf-8-sig') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def clear():
    for p in (journal_path(), journal_path() + '.tmp'):
        try:
            os.remove(p)
        except OSError:
            pass


def begin(kind, target, task='', version=''):
    """开始一次事务。kind ∈ {install, uninstall}；返回事务 dict（失败也返回）。"""
    data = {'kind': str(kind), 'target': str(target), 'task': str(task or ''),
            'version': str(version or ''), 'phase': 'begin',
            'started': round(time.time(), 3),
            'steps': [], 'backup': {}, 'pid': os.getpid()}
    _write(data)
    return data


def step(data, name, phase=None, **info):
    """推进一个步骤（写盘）。data 是 begin()/read() 的 dict。"""
    if not isinstance(data, dict):
        return
    try:
        record = {'name': str(name), 'at': round(time.time(), 3),
                  'pid': os.getpid()}
        record.update(dict((k, v) for k, v in info.items()
                           if isinstance(v, (int, float, bool, str)) or v is None))
        data.setdefault('steps', []).append(record)
        data['phase'] = str(phase or name)
        data['updated'] = record['at']
        _write(data)
    except Exception:
        pass


def finish(data):
    """事务完成：清日志。finished 标记写最后一次，便于崩溃在 clear 前时识别。"""
    if isinstance(data, dict):
        data['phase'] = 'done'
        data['finished'] = round(time.time(), 3)
        _write(data)
    clear()


def backup_file(key, path):
    """覆盖前备份一个文件；返回备份路径（失败/文件不存在返回空串）。"""
    try:
        if not path or not os.path.isfile(path):
            return ''
        stamp = time.strftime('%Y%m%d-%H%M%S')
        dst = os.path.join(backup_dir(),
                           '%s-%s%s' % (key, stamp,
                                        os.path.splitext(path)[1] or '.bak'))
        shutil.copy2(path, dst)
        return dst
    except OSError:
        return ''


def remember_backup(data, key, src, dst):
    if isinstance(data, dict) and dst:
        data.setdefault('backup', {})[key] = {'src': src, 'dst': dst,
                                              'at': round(time.time(), 3)}
        _write(data)


def restore_backup(data, key):
    """把某个 key 的备份还原回原位置。返回 (ok, 说明)。"""
    try:
        info = (data or {}).get('backup', {}).get(key) or {}
        src, dst = info.get('dst'), info.get('src')
        if not src or not dst or not os.path.isfile(src):
            return False, '没有可用备份'
        shutil.copy2(src, dst)
        return True, '已从 %s 恢复 %s' % (os.path.basename(src), dst)
    except OSError as e:
        return False, '恢复失败：%r' % (e,)


def _config_ok(path):
    try:
        with open(path, 'r', encoding='utf-8-sig') as f:
            return isinstance(json.load(f), dict)
    except (OSError, ValueError):
        return False


def status_text(data, target=None):
    """把事务状态压成一句人话（面板/日志直接用）。"""
    if not isinstance(data, dict):
        return ''
    kind = data.get('kind') or '?'
    steps = data.get('steps') or []
    last = steps[-1] if steps else {}
    phase = data.get('phase') or '?'
    target = target or data.get('target') or ''
    label = {'install': '安装', 'uninstall': '卸载'}.get(kind, kind)
    return '上次%s在第 %d 步（%s）中断：%s' % (
        label, max(1, len(steps)), phase, last.get('name') or '-')


def recover(target=None, log=None, version=''):
    """识别上一次被打断的事务，必要时做安全修复。返回结论 dict：

    {'action': '', 'text': ''}
      action ∈ {'', 'cleanup', 'restored_config', 'incomplete', 'broken',
                'uninstall_incomplete'}
    永不抛异常；没有事务日志时返回空 action。
    """
    note = log if callable(log) else (lambda m: None)
    data = read()
    if not isinstance(data, dict):
        return {'action': '', 'text': ''}
    target = target or data.get('target') or ''
    kind = data.get('kind') or ''
    try:
        if data.get('phase') == 'done':
            clear()
            return {'action': 'cleanup',
                    'text': '上一次%s已正常结束，清掉残留状态' %
                            ('安装' if kind == 'install' else '卸载')}
        text = status_text(data, target)
        if kind == 'install':
            exe = os.path.join(target, 'WBOX.exe')
            cfg = os.path.join(target, 'config.json')
            had_cfg = 'config' in (data.get('backup') or {})
            if had_cfg and not _config_ok(cfg):
                ok, detail = restore_backup(data, 'config')
                if ok:
                    note('[install] %s；%s' % (text, detail))
                    clear()
                    return {'action': 'restored_config',
                            'text': '%s；已恢复上一份 config.json' % text}
            if os.path.isfile(exe):
                note('[install] %s；程序文件已就位，重跑安装器即可续做收尾步骤'
                     % text)
                clear()
                return {'action': 'incomplete',
                        'text': '%s；重跑安装器可完成剩余步骤' % text}
            note('[install] %s；目标目录缺少 WBOX.exe，需要重新安装' % text)
            clear()
            return {'action': 'broken',
                    'text': '%s；程序不完整，需要重新安装' % text}
        if kind == 'uninstall':
            if target and os.path.isdir(target):
                note('[uninstall] %s；安装目录仍在，重新卸载可删干净' % text)
                clear()
                return {'action': 'uninstall_incomplete',
                        'text': '%s；重新卸载可删干净' % text}
            clear()
            return {'action': 'cleanup',
                    'text': '上一次卸载已基本完成，清掉残留状态'}
    except Exception as e:      # 恢复过程本身绝不抛
        note('[install] 事务恢复失败：%r' % (e,))
    return {'action': '', 'text': ''}
