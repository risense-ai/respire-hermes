#!/usr/bin/env python3
"""Registration and CLI write-policy tests, independent of a live Runtime."""
import importlib.util
import os
from pathlib import Path

spec = importlib.util.spec_from_file_location("respire_test", Path(__file__).resolve().parents[1] / "__init__.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def check(name, passed):
    assert passed, name


# ---- 1) 注册与实例语义 ----
class FakeCtx:
    def __init__(self):
        self.providers, self.skills, self.hooks = [], [], {}

    def register_hook(self, name, callback):
        self.hooks[name] = callback

    def register_memory_provider(self, p):
        self.providers.append(p)

    def register_skill(self, name, path, desc):
        self.skills.append((name, str(path), desc))


ctx1, ctx2 = FakeCtx(), FakeCtx()
mod.register(ctx1)
mod.register(ctx2)
check("pre_tool_call registered", callable(ctx1.hooks.get("pre_tool_call")))
check("terminal missing parent blocked", ctx1.hooks["pre_tool_call"](tool_name="terminal", args={"command": "rsrs remember fact --importance important"})["action"] == "block")
check("unrelated terminal unaffected", ctx1.hooks["pre_tool_call"](tool_name="terminal", args={"command": "echo hello"}) is None)
for command in ["rsrs remember 'body --parent aaaabbbb' --importance important", "rsrs remember fact --parent ''",
                "rsrs remember fact --parent aaaabbbb --force", "rsrs update deadbeef --clear-parent",
                "rsrs remember fact --parent none", "echo ok; rsrs remember fact",
                "echo ok\nrsrs remember fact", "rsrs remember fact --parent $P",
                "rsrs remember fact --parent aaaabbbb | cat", "env X=y rsrs remember fact",
                "rsrs --model-task-id task remember fact --importance important", "echo ok |& rsrs remember fact", ">out rsrs remember fact",
                "echo $(rsrs remember fact)", "npx rsrs remember fact --importance important",
                "rsrs >out.log remember fact --importance important --force",
                "rsrs remember combined --importance trivial --merge-ids aaaabbbb",
                "rsrs update aaaabbbb --importance important",
                'echo "$(rsrs >out remember fact --importance important)"',
                'echo >"$(rsrs remember fact --force)"',
                "echo " + chr(96) + "rsrs remember fact --force" + chr(96)]:
    check("guard rejects unsafe CLI write", bool(mod.guard_memory_tool("terminal", {"command": command}, lambda p: True)))
check("read query remember unaffected", mod.guard_memory_tool("terminal", {"command": "rsrs recall remember --titles --json"}, lambda p: False) is None)
check("global flags write blocked", bool(mod.guard_memory_tool("terminal", {"command": "rsrs --client-only --json remember fact --importance important"}, lambda p: True)))
check("literal valid parent permitted", mod.guard_memory_tool("terminal", {"command": "rsrs remember fact --parent aaaabbbb"}, lambda p: True) is None)
check("invalid parent blocked", bool(mod.guard_memory_tool("terminal", {"command": "rsrs remember fact --parent aaaabbbb"}, lambda p: False)))
check("trivial without parent permitted", mod.guard_memory_tool("terminal", {"command": "rsrs remember fact --importance trivial"}, lambda p: False) is None)
check("valid metadata options permitted", mod.guard_memory_tool("terminal", {"command": "rsrs remember fact --parent aaaabbbb --project p --tags t --type context --summary s --emotion -1 --clear-review-at"}, lambda p: True) is None)
check("quoted command text unaffected", mod.guard_memory_tool("terminal", {"command": "echo 'rsrs remember fact --force'"}, lambda p: False) is None)
check("native empty parent blocked", bool(mod.guard_memory_tool("respire_remember", {"importance": "trivial", "parent": ""}, lambda p: True)))
for command in ["rsrs remember --help", "rsrs remember diary",
                'bash -c "rsrs recall remember --titles --json"',
                'echo "$(rsrs --client-only recall remember)"',
                'rsrs remember "CPU dropped 50%" --importance important --parent aaaabbbb',
                'rsrs remember ">" --importance important --parent aaaabbbb']:
    check("read help and literal CLI content permitted", mod.guard_memory_tool("terminal", {"command": command}, lambda p: True) is None)
for name in ["memory_remember", "mcp__rsrs__memory_remember"]:
    check("unbound memory tool blocked", bool(mod.guard_memory_tool(name, {"importance": "important", "parent": "aaaabbbb"}, lambda p: True)))
check("bound Hermes provider permitted", mod.guard_memory_tool("respire_remember", {"importance": "important", "parent": "aaaabbbb"}, lambda p: True, provider_tool=True) is None)
check("update with verified existing parent permitted", mod.guard_memory_tool("terminal", {"command": "rsrs update aaaabbbb --importance important"}, lambda p: True, lambda target: True) is None)
check("native force blocked", bool(mod.guard_memory_tool("respire_remember", {"parent": "aaaabbbb", "force": True}, lambda p: True)))
check("register 注册 provider", len(ctx1.providers) == 1 and ctx1.providers[0].name == "respire")
check("register 每次 new 实例（禁单例）", ctx1.providers[0] is not ctx2.providers[0])
check("register 注册技能 respire", ctx1.skills and ctx1.skills[0][0] == "respire"
      and os.path.isfile(ctx1.skills[0][1]))
check("name 属性", mod.RespireMemoryProvider().name == "respire")

check("pre_llm_call registered", callable(ctx1.hooks.get("pre_llm_call")))
print("Provider registration and write guard checks passed.")
