#!/usr/bin/env python3
# respire-hermes 插件自检（CI 与本地共用）：结构 / 清单 / 可导入 / 必选方法 / fail-open / 敏感扫描。
# 全部通过 exit 0；任一失败 exit 1 并打印原因。
from __future__ import annotations

import os
import py_compile
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
ROOT = os.path.normpath(ROOT)

FAILS = []


def check(name: str, ok: bool, detail: str = ""):
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def main() -> int:
    # 1) 结构
    for rel in ["__init__.py", "plugin.yaml", "README.md", "cli.py",
                "skills/respire/SKILL.md", "respire.config.example.json",
                "scripts/install.sh", "scripts/package.sh", "tests/test_provider.py"]:
        check(f"file {rel}", os.path.isfile(os.path.join(ROOT, rel)))

    # 2) plugin.yaml 清单字段（stdlib 无 yaml——关键行正则；装了 yaml 则真解析）
    raw = open(os.path.join(ROOT, "plugin.yaml"), encoding="utf-8").read()
    try:
        import yaml  # type: ignore
        meta = yaml.safe_load(raw) or {}
        check("plugin.yaml parses", isinstance(meta, dict))
        check("plugin.yaml name=respire", meta.get("name") == "respire")
        check("plugin.yaml version x.y.z", bool(re.fullmatch(r"\d+\.\d+\.\d+", str(meta.get("version", "")))))
        check("plugin.yaml description non-empty", bool(str(meta.get("description", "")).strip()))
        check("plugin.yaml hooks is list", isinstance(meta.get("hooks"), list))
        version = str(meta.get("version"))
    except ImportError:
        keys = re.findall(r"^(\w+):", raw, re.M)
        for k in ("name", "version", "description", "hooks"):
            check(f"plugin.yaml has {k}", k in keys)
        check("plugin.yaml name: respire", bool(re.search(r"^name:\s*respire\s*$", raw, re.M)))
        m = re.search(r"^version:\s*(\S+)\s*$", raw, re.M)
        check("plugin.yaml version x.y.z", bool(m and re.fullmatch(r"\d+\.\d+\.\d+", m.group(1))))
        version = m.group(1) if m else ""

    # 3) __init__.py 可编译
    try:
        py_compile.compile(os.path.join(ROOT, "__init__.py"), doraise=True)
        check("__init__.py compiles", True)
    except Exception as e:
        check("__init__.py compiles", False, str(e))
    try:
        py_compile.compile(os.path.join(ROOT, "cli.py"), doraise=True)
        check("cli.py compiles", True)
    except Exception as e:
        check("cli.py compiles", False, str(e))

    # 4) provider 形状（脱离 Hermes 导入——模块自带回退桩）
    sys.path.insert(0, ROOT)
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "respire_plugin_under_test", os.path.join(ROOT, "__init__.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    prov_cls = getattr(mod, "RespireMemoryProvider", None)
    check("class RespireMemoryProvider", prov_cls is not None)
    if prov_cls:
        inst = prov_cls()
        for m in ("is_available", "initialize", "get_tool_schemas", "handle_tool_call",
                  "get_config_schema", "save_config", "system_prompt_block", "prefetch",
                  "sync_turn", "shutdown", "get_status_config", "post_setup"):
            check(f"method {m}", callable(getattr(inst, m, None)))
        check("name property == respire", inst.name == "respire")
        check("register(ctx) callable", callable(getattr(mod, "register", None)))
        schemas = inst.get_tool_schemas()
        names = sorted(s["function"]["name"] for s in schemas if isinstance(s, dict))
        check("tool schemas = 3 respire_*", names == ["respire_recall", "respire_remember", "respire_show"], str(names))
        schema_keys = {f["key"] for f in inst.get_config_schema()}
        check("config schema minimal (rsrs_path)", schema_keys == {"rsrs_path"}, str(schema_keys))
        # fail-open 抽查：异常参数不得抛
        r = inst.handle_tool_call("respire_recall", {})
        check("handle_tool_call empty args no-raise", isinstance(r, str))
        r = inst.handle_tool_call("no_such_tool", {"x": 1})
        check("unknown tool no-raise", isinstance(r, str) and "未知工具" in r)
        check("prefetch None on empty", inst.prefetch("") is None)
        check("prefetch /nomem skip", inst.prefetch("/nomem something") is None)
        check("system_prompt_block no-raise", isinstance(inst.system_prompt_block(), str))

    # 5) fail-open 静态扫描：生命周期与工具分发必须包 try/except
    src = open(os.path.join(ROOT, "__init__.py"), encoding="utf-8").read()
    for m in ("def handle_tool_call", "def prefetch", "def system_prompt_block", "def sync_turn"):
        i = src.find(m)
        j = src.find("\n    def ", i + 1)
        body = src[i:j if j > 0 else len(src)]
        check(f"fail-open try in {m.split('def ')[1]}", "except Exception" in body)

    # 6) 敏感扫描：禁硬编码 token/私钥/口令字面量
    sensitive = [p for p in (re.findall(r"\b(?:sk-|ghp|gho|github_pat|AKIA|xox)[A-Za-z0-9_-]{10,}\b", src)
                             + re.findall(r"(?i)(password|passwd|api_key|token)\s*[:=]\s*[\"'][^\"']{6,}[\"']", src))
                 if "REDACTED" not in p and "[REDACTED]" not in p]
    check("no hardcoded secrets", not sensitive, str(sensitive[:3]))

    # 7) README 声明的安装/激活词在场（文档自查面）
    rd = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    for token in ("$HERMES_HOME/plugins/respire", "memory.provider", "hermes memory setup"):
        check(f"README mentions {token}", token in rd)

    print()
    if FAILS:
        print(f"validate_plugin: {len(FAILS)} FAIL → {FAILS}")
        return 1
    print(f"validate_plugin: all green (version {version})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
