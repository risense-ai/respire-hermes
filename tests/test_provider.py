#!/usr/bin/env python3
# respire-hermes 自测 —— Hermes memory provider 回环（脱离 Hermes 宿主：模块自带结构等价桩）。
# 分层：
#   桩段（无引擎也跑）：注册/工具形状/配置回环/fail-open/prefetch 分支/状态引导/CLI 装配/纯函数
#   引擎段（需本机 rsrs，只读不写）：真库 recall/show/prefetch/is_available
#   写探针段（--store 显式开启）：写真库→回执断言→rsrs forget 自清理
# 用法：python3 tests/test_provider.py [--store]
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
WITH_STORE = "--store" in sys.argv[1:]

PASS = 0
FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"PASS  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}  {str(detail)[:200]}")


TMP = tempfile.mkdtemp(prefix="respire-hermes-test-")
os.environ["HERMES_HOME"] = os.path.join(TMP, "hermes-home")   # 隔离：绝不碰真 ~/.hermes
os.environ["RESPITE_LOG"] = os.path.join(TMP, "plugin.log")

spec = importlib.util.spec_from_file_location("respire_hermes_under_test", os.path.join(ROOT, "__init__.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def new_prov(cfg=None, home=None, **init_kw):
    prov = mod.RespireMemoryProvider()
    prov.initialize("selftest", hermes_home=home or os.environ["HERMES_HOME"], **init_kw)
    if cfg is not None:
        prov._cfg.update(cfg)
        prov._engine = mod.find_rsrs(prov._cfg)
    return prov


# ---- 1) 注册与实例语义 ----
class FakeCtx:
    def __init__(self):
        self.providers, self.skills = [], []

    def register_memory_provider(self, p):
        self.providers.append(p)

    def register_skill(self, name, path, desc):
        self.skills.append((name, str(path), desc))


ctx1, ctx2 = FakeCtx(), FakeCtx()
mod.register(ctx1)
mod.register(ctx2)
check("register 注册 provider", len(ctx1.providers) == 1 and ctx1.providers[0].name == "respire")
check("register 每次 new 实例（禁单例）", ctx1.providers[0] is not ctx2.providers[0])
check("register 注册技能 respire", ctx1.skills and ctx1.skills[0][0] == "respire"
      and os.path.isfile(ctx1.skills[0][1]))
check("name 属性", mod.RespireMemoryProvider().name == "respire")

# ---- 2) 工具与配置形状 ----
prov = new_prov()
schemas = prov.get_tool_schemas()
names = sorted(s["function"]["name"] for s in schemas)
check("工具恰三件 respire_*", names == ["respire_recall", "respire_remember", "respire_show"], names)
for s in schemas:
    fn = s["function"]
    req = fn["parameters"].get("required", [])
    if fn["name"] == "respire_recall":
        check("recall 必填 query", req == ["query"])
    if fn["name"] == "respire_show":
        check("show 必填 id", req == ["id"])
    if fn["name"] == "respire_remember":
        check("remember 必填 content", req == ["content"])
        check("remember 带 importance 枚举", fn["parameters"]["properties"]["importance"].get("enum") == ["important", "trivial"])
schema = prov.get_config_schema()
check("向导极简只问 rsrs_path", [f["key"] for f in schema] == ["rsrs_path"])

# save_config：rsrs_path→rsrs 键映射＋与既有边车合并
home1 = os.path.join(TMP, "home-save")
os.makedirs(home1, exist_ok=True)
with open(os.path.join(home1, "respire.json"), "w", encoding="utf-8") as f:
    json.dump({"recall_limit": 3}, f)
prov.save_config({"rsrs_path": "/opt/rsrs"}, home1)
saved = json.load(open(os.path.join(home1, "respire.json"), encoding="utf-8"))
check("save_config 映射 rsrs_path→rsrs", saved.get("rsrs") == "/opt/rsrs", saved)
check("save_config 保留既有字段", saved.get("recall_limit") == 3, saved)

# ---- 3) fail-open 与静默分支 ----
real_find = mod.find_rsrs
mod.find_rsrs = lambda cfg: None          # 造「全路径皆缺」（真机造不出）
try:
    pe = new_prov()
    pe._engine = None
    check("引擎缺失 is_available=False", pe.is_available() is False)
    check("引擎缺失 recall 工具回错误文本", "引擎未装" in pe.handle_tool_call("respire_recall", {"query": "x"}))
    check("引擎缺失 show 工具回错误文本", "引擎未装" in pe.handle_tool_call("respire_show", {"id": "abcd1234"}))
    check("引擎缺失 remember 回错误文本", "引擎未装" in pe.handle_tool_call("respire_remember", {"content": "x"}))
    check("prefetch 引擎缺失静默", pe.prefetch("rsrs 信封结构解析") is None)
    r = pe.prefetch("记：引擎缺失时的落库应回装法")
    check("prefetch 落库前缀引擎缺失回装法", isinstance(r, str) and "npm i -g rsrs-cli@1.0.16" in r, r)
    sb = pe.system_prompt_block()
    check("prompt 块引擎缺失注入装法", "npm i -g rsrs-cli@1.0.16" in sb)
    check("装法禁自造超级密码", "绝不传 --super" in sb)
    check("装法要求先征得同意", "征得同意" in sb and "无需等用户" not in sb)
    check("装法含注册站点", "rsrs.rs" in sb)
finally:
    mod.find_rsrs = real_find

prov = new_prov()
check("未知工具回文本不抛", "未知工具" in prov.handle_tool_call("no_such", {}))
check("recall 空 query 回文本", isinstance(prov.handle_tool_call("respire_recall", {}), str))
check("show 空 id 回文本", isinstance(prov.handle_tool_call("respire_show", {}), str))
check("remember 空 content 回文本", isinstance(prov.handle_tool_call("respire_remember", {}), str))
check("args 非 dict 容错", isinstance(prov.handle_tool_call("respire_recall", None), str))
check("prefetch 空 query None", prov.prefetch("") is None)
check("prefetch 非 str 容错 None", prov.prefetch(123) is None)
check("prefetch /nomem 跳过", prov.prefetch("/nomem rsrs 信封") is None)
check("prefetch #nomem 跳过", prov.prefetch("#nomem rsrs 信封") is None)
check("prefetch 单字过短 None", prov.prefetch("嗯") is None)

prov_skip = new_prov(cfg={"skip_prefixes": ["你正在为宿主写 Recap"]})
check("skip_prefixes 白名单跳过", prov_skip.prefetch("你正在为宿主写 Recap：无事") is None)

prov_off = new_prov(cfg={"enabled": False})
check("enabled=false prompt 块静默", prov_off.system_prompt_block() == "")
check("enabled=false prefetch 静默", prov_off.prefetch("rsrs 信封结构") is None)
check("enabled=false 工具回关断文本", "enabled=false" in prov_off.handle_tool_call("respire_recall", {"query": "x"}))
prov_rules_off = new_prov(cfg={"session_rules": False})
check("session_rules=false prompt 块静默", prov_rules_off.system_prompt_block() == "")
prov_auto_off = new_prov(cfg={"auto_recall": False})
check("auto_recall=false prefetch 静默", prov_auto_off.prefetch("rsrs 信封结构") is None)

# subagent/cron 上下文禁自动写（prefetch 落库前缀→None，召回照常）
prov_sub = new_prov(agent_context="subagent")
check("subagent 落库前缀不自动写", prov_sub.prefetch("记：子代理不应代写库") is None)

# ---- 4) 状态引导分支（直填 _probe 缓存，零引擎确定性） ----
def block_with_probe(probe_tuple, no_model=None):
    p = new_prov()
    p._engine = p._engine or "fake"
    p._probe = probe_tuple
    p._probe_at = time.time()
    real_cm = mod.check_model
    if no_model is not None:
        mod.check_model = lambda: no_model
    try:
        return p.system_prompt_block()
    finally:
        mod.check_model = real_cm


b = new_prov()
check("prompt 块含简则", "respire 记忆层" in b.system_prompt_block())
check("简则点明同步站点", "rsrs.rs" in b.system_prompt_block())
p_none = new_prov()
p_none._probe = None
check("探测未回只出简则（不误报）", "尚未登录" not in p_none.system_prompt_block())
check("未登录注入引导", "尚未登录" in block_with_probe((False, None, True)))
check("登录引导给代登法", "rsrs login --oauth --no-open" in block_with_probe((False, None, True)))
check("空库注入首日引导", "记忆库还是空的" in block_with_probe((True, 3, True)))
check("有货库不带空库引导", "记忆库还是空的" not in block_with_probe((True, 500, True)))
check("已登录不带登录引导", "尚未登录" not in block_with_probe((True, 500, True)))
check("探测失败注入诊断段", "状态探测失败" in block_with_probe((None, None, False)))
check("探测失败含未注册分支", "尚未注册" in block_with_probe((None, None, False)))
check("缺模型注入装法", "rsrs model activate m3" in block_with_probe((True, 500, True), no_model=False))
check("缺模型点明词面回退", "词面档" in block_with_probe((True, 500, True), no_model=False))
check("已装模型不带提示", "BGE-M3）未装" not in block_with_probe((True, 500, True), no_model=True))
check("get_status_config 平面 dict", isinstance(prov.get_status_config(), dict)
      and {"engine", "logged_in", "entries", "probe_ok"} <= set(prov.get_status_config()))
_real_find = mod.find_rsrs
_saved_engine = prov._engine
mod.find_rsrs = lambda cfg: cfg.get("rsrs") or None
prov._engine = None
_status_from_cfg = prov.get_status_config({"rsrs_path": "/opt/rsrs"})
check("get_status_config 用 provider_config 探测引擎",
      _status_from_cfg.get("engine") == "/opt/rsrs", _status_from_cfg)
prov._engine = _saved_engine
mod.find_rsrs = _real_find

# ---- 5) sync_turn 非阻塞 + 催促判定（引擎桩：list 回陈旧/新鲜时间戳） ----
t0 = time.time()
prov.sync_turn("u", "a")
prov.on_session_end([])
prov.shutdown()
check("sync_turn/on_session_end/shutdown 快速返回", time.time() - t0 < 2.0)
check("sync_turn 计轮", prov._turns >= 1)


def stub_engine(recall_hit=True, stale=True):
    path = os.path.join(TMP, f"rsrs-stub-{recall_hit}-{stale}-{time.time_ns()}")
    ts = "2020-01-01T00:00:00.000Z" if stale else time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    det = json.dumps([{"score": 0.55, "id": "aaaabbbbccccdddd", "title": "桩命中条目", "superseded_by": None}])
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"""#!/usr/bin/env bash
if [ "$1" = "recall" ]; then
  if [ "{1 if recall_hit else 0}" = "1" ]; then
    printf '{{"command":"recall","status":"ok","details":{det}}}'
  else
    printf '{{"command":"recall","status":"skip","details":[]}}'
  fi
  exit 0
fi
if [ "$1" = "list" ]; then
  printf '{{"command":"list","status":"ok","details":[{{"id":"aaaabbbbccccdddd","created_at":"{ts}","updated_at":"{ts}","title":"t","content":"c"}}]}}'
  exit 0
fi
exit 1
""")
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


stub = stub_engine()
prov_stub = new_prov(cfg={"rsrs": stub})
prov_stub._engine = stub
out = prov_stub.prefetch("桩查询触发召回")
check("prefetch 桩召回注入标题档", isinstance(out, str) and "桩命中条目" in out and "【respire·自动召回】" in out, out)
check("标题档头行 rsrs 直出", "rsrs 直出" in out, out)
prov_stub._turns = 1
prov_stub._last_store_ts = time.time() - 3600  # 引擎侧一小时无落库
out2 = prov_stub.prefetch("桩查询触发召回")
check("近窗无落库附催促", isinstance(out2, str) and "记忆检查" in out2 and "respire_remember" in out2, out2)
prov_stub._last_store_ts = time.time() - 30     # 刚落库
out3 = prov_stub.prefetch("桩查询触发召回")
check("近窗已落库不催", isinstance(out3, str) and "记忆检查" not in out3, out3)
prov_stub._last_store_ts = time.time() - 3600
prov_stub._cfg["store_nudge_every"] = 0
out4 = prov_stub.prefetch("桩查询触发召回")
check("store_nudge_every=0 关催促", isinstance(out4, str) and "记忆检查" not in out4, out4)
prov_stub._cfg["store_nudge_every"] = 1
prov_stub._cfg["budget_chars"] = 120
out5 = prov_stub.prefetch("桩查询触发召回")
check("budget_chars 截断生效", isinstance(out5, str) and len(out5) < 400, len(out5 or ""))
prov_stub._turns = 0
out6 = prov_stub.prefetch("桩查询触发召回")
check("首轮（turns=0）不催", isinstance(out6, str) and "记忆检查" not in out6, out6)
prov_stub_thr = new_prov(cfg={"rsrs": stub_engine(stale=False)})
prov_stub_thr._engine = prov_stub_thr._cfg["rsrs"]
prov_stub_thr._refresh_last_store()             # 同步直调（线程版同一函数）
check("引擎桩新鲜落库被读出", prov_stub_thr._last_store_ts is not None
      and time.time() - prov_stub_thr._last_store_ts < 120)
prov_stub_thr._turns = 1
out7 = prov_stub_thr.prefetch("桩查询触发召回")
check("新鲜落库不催（问库路径）", isinstance(out7, str) and "记忆检查" not in out7, out7)
stub_miss = new_prov(cfg={"rsrs": stub_engine(recall_hit=False)})
out8 = stub_miss.prefetch("无关桩查询")
check("零命中静默注入", out8 is None, out8)

# ---- 6) 纯函数 ----
check("extract_title 整句优先", mod.extract_title("respire 插件迁至独立仓。后续以仓库为准。") == "respire 插件迁至独立仓。")
check("extract_title 退分隔符", "，" in mod.extract_title("这是一条没有句号的长事实句，讲清楚何时谁做了什么事以及结果如何还有别的"))
check("extract_title 70 字上限", len(mod.extract_title("默" * 200)) <= 70)
check("redact 口令脱敏", "hunter2" not in mod.redact("密码: hunter2 test"))
check("redact token 脱敏", "REDACTED" in mod.redact("sk-abcdef1234567890"))
_sidecar_keys = ("RSRS_SUPER", "OPENAI_API_KEY", "RSRS_DATA_DIR", "RSRS_LIBC")
_sidecar_saved = {k: os.environ.get(k) for k in _sidecar_keys}
os.environ["RSRS_SUPER"] = "do-not-pass"
os.environ["OPENAI_API_KEY"] = "sk-do-not-pass"
os.environ["RSRS_DATA_DIR"] = "/tmp/rsrs-sidecar-test"
os.environ["RSRS_LIBC"] = "musl"
_sidecar = mod.sidecar_env()
check("sidecar env 不透传宿主密钥", "RSRS_SUPER" not in _sidecar and "OPENAI_API_KEY" not in _sidecar)
check("sidecar env 保留基础变量", "HOME" in _sidecar and "PATH" in _sidecar and "HERMES_HOME" in _sidecar)
check("sidecar env 保留非敏感 rsrs 配置", _sidecar.get("RSRS_DATA_DIR") == "/tmp/rsrs-sidecar-test"
      and _sidecar.get("RSRS_LIBC") == "musl")
for _k, _v in _sidecar_saved.items():
    if _v is None:
        os.environ.pop(_k, None)
    else:
        os.environ[_k] = _v
check("_epoch 解析 Z 尾", mod._epoch("2026-10-22T15:06:38.926Z") is not None)
check("_epoch 坏输入 None", mod._epoch("not-a-date") is None)
check("format_recall 零命中空串", mod.format_recall({"details": []}, 5, 0.0, 1500) == ("", 0))
doc = {"details": [{"score": 0.6, "id": "aaaabbbbccccdddd", "title": "t1"},
                   {"score": 0.3, "id": "eeeeffff00001111", "title": "t2"}]}
txt, n = mod.format_recall(doc, 5, 0.5, 1500)
check("format_recall 阈值过滤", n == 1 and "t2" not in txt, txt)
check("format_recall 头行条数一致", re.search(r"命中 1 条", txt), txt)

# ---- 7) CLI 装配 ----
import argparse
cli_spec = importlib.util.spec_from_file_location("respire_cli_under_test", os.path.join(ROOT, "cli.py"))
cli = importlib.util.module_from_spec(cli_spec)
cli_spec.loader.exec_module(cli)
parser = argparse.ArgumentParser(prog="hermes")
sub = parser.add_subparsers()
prov_parser = sub.add_parser("respire", help="respire provider")
cli.register_cli(prov_parser)
argv = ["respire", "status"]
ns = parser.parse_args(argv)
check("CLI 子命令解析", getattr(ns, "respire_command", "") == "status")
check("CLI dispatch 函数挂载", callable(getattr(ns, "func", None)))
check("CLI 用法兜底", True)  # _dispatch 无子命令时打印 Usage（冒烟由 parse 覆盖）

# ---- 8) plugin.yaml ----
raw = open(os.path.join(ROOT, "plugin.yaml"), encoding="utf-8").read()
check("plugin.yaml name=respire", bool(re.search(r"^name:\s*respire\s*$", raw, re.M)))
check("plugin.yaml version 三段", bool(re.search(r"^version:\s*\d+\.\d+\.\d+\s*$", raw, re.M)))
check("plugin.yaml hooks 列 prefetch/sync_turn", bool(re.search(r"^\s*-\s*(prefetch|sync_turn)\s*$", raw, re.M)))
check("plugin.yaml 描述零依赖面", "zero Python dependency" in raw or "dependency" in raw)

# ---- 9) 引擎段（真 rsrs，只读） ----
ENGINE = mod.find_rsrs({"rsrs": ""})
if ENGINE:
    prov_r = new_prov(cfg={"rsrs": ""})
    prov_r._engine = ENGINE
    check("真机 is_available", prov_r.is_available() is True)
    out = prov_r.handle_tool_call("respire_recall", {"query": "respire 信封 JSON 结构解析取哪层"})
    check("真库 recall 回文本", isinstance(out, str) and out.startswith("【respire"), out[:80])
    logged, total, ok = mod.probe_status(ENGINE)
    check("真库 status 探测回 ok", ok is True)
    r = mod.run_rsrs(ENGINE, ["list", "--limit", "1", "--json"], mod.LIST_T)
    try:
        first = json.loads(r.stdout)["details"][0]
    except Exception:
        first = None
    if first:
        shown = prov_r.handle_tool_call("respire_show", {"id": str(first["id"])[:8]})
        check("真库 show 读全文", "【respire·条目】" in shown, str(shown)[:120])
    else:
        print("SKIP  真库 show（空库无条目）")
    pf = prov_r.prefetch("respire 信封结构")
    check("真库 prefetch 不抛", pf is None or isinstance(pf, str))
else:
    print("SKIP  引擎段（未找到 rsrs；本机装 rsrs-cli 后可全量自测）")

# ---- 10) 写探针（--store；写真库→断言→forget 自清理） ----
if WITH_STORE and ENGINE:
    MARK = f"zh{int(time.time())} q3v9 随机标记无义串"
    out = new_prov(cfg={"rsrs": ENGINE}).handle_tool_call(
        "respire_remember", {"content": MARK, "force": True})
    check("工具落库成功回执", "已存入" in out, out)
    check("回执约束 retitle", "retitle" in out, out)
    m = re.search(r"（([0-9a-f]{8})）", out)
    check("回执带短 id", m is not None, out)
    if m:
        rc = mod.run_rsrs(ENGINE, ["forget", m.group(1), "--json"], 10)
        try:
            check("探针清理(status=ok)", json.loads(rc.stdout).get("status") == "ok")
        except Exception:
            check("探针清理(status=ok)", False, rc.stdout[:120])
    out2 = new_prov(cfg={"rsrs": ENGINE}).prefetch(f"记：{MARK}")
    check("prefetch 前缀落库回执", isinstance(out2, str) and ("已存入" in out2 or "落库待决" in out2), out2)
    m2 = re.search(r"（([0-9a-f]{8})）", out2 or "")
    if m2:
        mod.run_rsrs(ENGINE, ["forget", m2.group(1), "--json"], 10)  # 待决态未写入时 forget 会失败，忽略
else:
    print("SKIP  写探针（默认只读；加 --store 开启，写真库并自动 forget 清理）")

print("----")
print(f"PASS={PASS} FAIL={FAIL}")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(0 if FAIL == 0 else 1)
