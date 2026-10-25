#!/usr/bin/env python3
# respire provider 的 hermes respire <子命令> CLI 树（官方约定：register_cli(subparser)，活跃 provider 才现身）
# 仅做装配与打印，不 import Hermes 内部件——子命令逻辑直接调本目录 __init__ 的引擎包装。
from __future__ import annotations


def _hermes_home() -> str:
    import os
    return os.environ.get("HERMES_HOME") or os.path.join(os.path.expanduser("~"), ".hermes")


def _provider():
    import importlib.util
    import os
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    # 同目录 __init__.py 按 Hermes 目录插件方式载入（非包导入，避免名字冲突）
    spec = importlib.util.spec_from_file_location("respire_provider_cli", os.path.join(here, "__init__.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.RespireMemoryProvider(), mod


def cmd_status(args):
    try:
        prov, _ = _provider()
        prov.initialize("cli-status", hermes_home=_hermes_home())
        st = prov.get_status_config()
        print(f"engine: {st['engine'] or '(not found — npm i -g rsrs-cli@1.0.16)'}")
        print(f"logged_in: {st['logged_in']}")
        print(f"entries: {st['entries']}")
        print(f"probe_ok: {st['probe_ok']}")
    except Exception as e:
        print(f"respire status failed: {type(e).__name__}: {e}")


def cmd_config(args):
    import json
    import os
    path = os.path.join(_hermes_home(), "respire.json")
    if not os.path.exists(path):
        print(f"(no config at {path} — defaults in effect; field reference in the plugin README)")
        return
    with open(path, encoding="utf-8") as f:
        print(json.dumps(json.load(f), ensure_ascii=False, indent=2))


def cmd_recall(args):
    try:
        prov, _ = _provider()
        prov.initialize("cli-recall", hermes_home=_hermes_home())
        out = prov.handle_tool_call("respire_recall", {"query": args.query, "limit": args.limit})
        print(out)
    except Exception as e:
        print(f"respire recall failed: {type(e).__name__}: {e}")


def cmd_install_engine(args):
    print("Engine install: npm i -g rsrs-cli@1.0.16")
    print("Then: rsrs --version   (register at https://rsrs.rs, then `rsrs register --user <name>` on this machine)")
    print("Optional semantic model: rsrs model install && rsrs model activate m3")


def register_cli(subparser) -> None:
    """Build the `hermes respire` argparse tree. Called by discover_plugin_cli_commands()."""
    subs = subparser.add_subparsers(dest="respire_command")
    subs.add_parser("status", help="Show provider/engine status (engine path, login, entry count)")
    subs.add_parser("config", help="Show $HERMES_HOME/respire.json")
    p_recall = subs.add_parser("recall", help="Run a title-tier recall against the local library")
    p_recall.add_argument("query", help="Search terms: project name + keywords")
    p_recall.add_argument("--limit", type=int, default=8, help="Top N hits (default 8)")
    subs.add_parser("install-engine", help="Print engine (rsrs CLI) install instructions")
    subparser.set_defaults(func=_dispatch)


def _dispatch(args):
    sub = getattr(args, "respire_command", None)
    if sub == "status":
        cmd_status(args)
    elif sub == "config":
        cmd_config(args)
    elif sub == "recall":
        cmd_recall(args)
    elif sub == "install-engine":
        cmd_install_engine(args)
    else:
        print("Usage: hermes respire <status|config|recall|install-engine>")
