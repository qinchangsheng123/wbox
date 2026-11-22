# -*- coding: utf-8 -*-
"""构建收尾：把生产目录的活配置合并回出厂模板之上（v2.15.0）。

## 为什么需要这个

`build_exe.bat` 默认 OUTDIR 就是生产 `app\`，而它每次都会用 `factory_config`
重新生成 `app\config.json`（出厂模板）。于是"重建 exe"会顺手把用户的
`afterburner_path`、桌面 HUD、托盘启动、插件开关全部抹成出厂值——v2.15.0
实测踩过：构建后 `afterburner_path` 从 `F:/jk/MSI Afterburner` 变成空串，
联动直接失效。

修法不是"构建时别写出厂模板"：payload 里必须是干净的出厂件，不能带本机
路径。所以改成两步——构建前把活配置另存为 `config.json.live`，出厂模板生成
完再就地合并回来。`build_setup.py` 对 `config.json` 一律用
`factory_config.factory_bytes()` 重写，因此 payload 永远是出厂件，不受影响。

## 合并规则

- 出厂模板为底：新增键、新增子键都保留出厂默认。
- 用户配置覆盖同名键（递归进 dict，只覆盖它真的写了的子键）。
- `_` 开头的注释键一律用出厂版本（它们是文档，不是用户数据）。

用法：`python installer/merge_live_config.py --dir <OUTDIR>`
"""
import argparse
import io
import json
import os


def _merge(base, user):
    out = dict(base)
    for key, val in user.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], val)
        else:
            out[key] = val
    return out


def merge_files(factory_path, live_path):
    """返回合并后的 dict：注释键取出厂，其余以用户值为准。"""
    factory = json.load(io.open(factory_path, encoding='utf-8'))
    live = json.load(io.open(live_path, encoding='utf-8'))
    user = {k: v for k, v in live.items() if not k.startswith('_')}
    merged = _merge(factory, user)
    # 保持出厂的键序（注释键在前），值取合并结果；用户独有的键追加在后面。
    # 注意不能写成 dict(factory) 再"补缺失键"——那样 factory 已有的键永远是
    # 出厂值，合并等于白做（v2.15.0 首版就踩了这个坑，log_max_kb 没被覆盖）。
    ordered = {k: merged[k] for k in factory if k in merged}
    for key, val in merged.items():
        if key not in ordered:
            ordered[key] = val
    return ordered


def main(argv=None):
    ap = argparse.ArgumentParser(description='把活配置合并回出厂模板')
    ap.add_argument('--dir', required=True, help='构建输出目录（含 config.json）')
    args = ap.parse_args(argv)

    cfg = os.path.join(args.dir, 'config.json')
    live = os.path.join(args.dir, 'config.json.live')
    if not os.path.isfile(live):
        print('merge_live_config: 没有 %s，跳过（首次构建的正常情况）' % live)
        return 0
    if not os.path.isfile(cfg):
        print('merge_live_config: 没有出厂模板 %s，无法合并' % cfg)
        return 1

    merged = merge_files(cfg, live)
    io.open(cfg, 'w', encoding='utf-8').write(
        json.dumps(merged, ensure_ascii=False, indent=2) + '\n')
    os.remove(live)

    keys = [k for k in merged if not k.startswith('_')]
    print('merge_live_config: 已恢复用户配置，%d 个键' % len(keys))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
