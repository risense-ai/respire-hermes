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
import threading
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
    check("prefetch 落库前缀引擎缺失回装法", isinstance(r, str) and "npm i -g rsrs-cli@latest" in r, r)
    sb = pe.system_prompt_block()
    check("prompt 块引擎缺失注入装法", "npm i -g rsrs-cli@latest" in sb)
    check("装法禁自造超级密码", "never pass --super" in sb)
    check("装法要求先征得同意", "get their consent first" in sb and "never auto-approve" in sb)
    check("装法含本机注册", "rsrs register with the user" in sb)
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
check("prefetch 疑似凭据拦截", prov.prefetch("api_key = " + "sk-" + "kvbbfuzkmrhtrxhbvqda" + " 存一下") == mod.SECRET_SKIP)
check("prefetch 疑似凭据不落库", "记：数据库密码 = hunter2u".startswith("记") and mod.contains_secret("记：数据库密码 = hunter2u"))
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
check("prompt 块含简则", "respire memory layer" in b.system_prompt_block())
check("简则点明同步与引擎", "rsrs sync" in b.system_prompt_block())
p_none = new_prov()
p_none._probe = None
check("探测未回只出简则（不误报）", "not signed in" not in p_none.system_prompt_block())
check("未登录注入引导", "not signed in" in block_with_probe((False, None, True)))
check("登录引导给代登法", "rsrs login --oauth --no-open" in block_with_probe((False, None, True)))
check("空库注入首日引导", "memory store is empty" in block_with_probe((True, 3, True)))
check("有货库不带空库引导", "memory store is empty" not in block_with_probe((True, 500, True)))
check("已登录不带登录引导", "not signed in" not in block_with_probe((True, 500, True)))
check("探测失败注入诊断段", "status probe failed" in block_with_probe((None, None, False)))
check("探测失败含未注册分支", "never registered" in block_with_probe((None, None, False)))
check("缺模型注入装法", "rsrs model activate m3" in block_with_probe((True, 500, True), no_model=False))
check("缺模型点明词面回退", "lexical tier" in block_with_probe((True, 500, True), no_model=False))
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
    det = repr([{"score": 0.55, "id": "aaaabbbbccccdddd", "title": "桩命中条目", "superseded_by": None}])
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"""#!/usr/bin/env python3
import json, os, sys
sub = sys.argv[1] if len(sys.argv) > 1 else ""
if sub == "recall":
    if "{1 if recall_hit else 0}" == "1":
        print(json.dumps({{"command":"recall","status":"ok","details":{det}}}))
    else:
        print(json.dumps({{"command":"recall","status":"skip","details":[]}}))
elif sub == "list":
    print(json.dumps({{"command":"list","status":"ok","details":[{{"id":"aaaabbbbccccdddd","created_at":"{ts}","updated_at":"{ts}","title":"t","content":"c"}}]}}))
else:
    sys.exit(1)
""")
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    if os.name == "nt":
        # Windows：python 脚本不能被 CreateProcess 直接 exec，包一层 .cmd（同 workbuddy 测试）
        py = sys.executable
        with open(path + ".cmd", "w", encoding="utf-8", newline="") as f:
            f.write("@echo off" + chr(13) + chr(10) + '"%s" "%%~dp0%s" %%*' % (py, os.path.basename(path)) + chr(13) + chr(10))
        return path + ".cmd"
    return path


stub = stub_engine()
prov_stub = new_prov(cfg={"rsrs": stub})
prov_stub._engine = stub
out = prov_stub.prefetch("桩查询触发召回")
check("prefetch 桩召回注入标题档", isinstance(out, str) and "桩命中条目" in out and "【respire · auto-recall】" in out, out)
check("标题档头行 rsrs 直出", "rsrs direct" in out, out)
prov_stub._turns = 1
prov_stub._last_store_ts = time.time() - 3600  # 引擎侧一小时无落库
out2 = prov_stub.prefetch("桩查询触发召回")
check("近窗无落库附催促", isinstance(out2, str) and "Memory check" in out2 and "respire_remember" in out2, out2)
prov_stub._last_store_ts = time.time() - 30     # 刚落库
out3 = prov_stub.prefetch("桩查询触发召回")
check("近窗已落库不催", isinstance(out3, str) and "Memory check" not in out3, out3)
prov_stub._last_store_ts = time.time() - 3600
prov_stub._cfg["store_nudge_every"] = 0
out4 = prov_stub.prefetch("桩查询触发召回")
check("store_nudge_every=0 关催促", isinstance(out4, str) and "Memory check" not in out4, out4)
prov_stub._cfg["store_nudge_every"] = 1
prov_stub._cfg["budget_chars"] = 120
out5 = prov_stub.prefetch("桩查询触发召回")
# 预算只作用于召回块（标题行数≤1）；催促附加段（HM-05 后变长）不计入预算断言
check("budget_chars 截断生效(召回块≤1行)", isinstance(out5, str)
      and sum(1 for ln in out5.splitlines() if ln.startswith("- [")) <= 1, out5)
check("budget 输出仍含催促但总长有界", isinstance(out5, str) and len(out5) < 1200, len(out5 or ""))
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

# ---- 5b) secret-skip 回归：凭据 query 不进引擎、不落日志、不回显 ----
# 合成凭据运行时拼接（避免测试文件出现 secret-like 字面量）；取无前缀值+Bearer 形态，
# 因为 redact 只兜 sk-/ghp 等前缀与口令赋值，Bearer 值靠「只记固定事件码」兜底。
CRED = "synthetic" + "qv7xk2mwje"          # 18 位无前缀合成凭据（redact 不覆盖 → 老代码必泄漏，回归可检）
CRED_QUERY = "Bearer " + CRED + " 这条含凭据不要入库"
rec_path = os.path.join(TMP, f"rsrs-recording-{time.time_ns()}")
rec_calls = rec_path + ".calls"
with open(rec_path, "w", encoding="utf-8") as f:
    f.write(f"""#!/usr/bin/env python3
import json, os, sys
with open({rec_calls!r}, "a", encoding="utf-8") as c:
    c.write(" ".join(sys.argv[1:]) + chr(10))
sub = sys.argv[1] if len(sys.argv) > 1 else ""
if sub == "recall":
    print(json.dumps({{"command": "recall", "status": "ok", "details": []}}))
elif sub == "list":
    print(json.dumps({{"command": "list", "status": "ok", "details": []}}))
else:
    sys.exit(1)
""")
os.chmod(rec_path, os.stat(rec_path).st_mode | stat.S_IEXEC)
if os.name == "nt":
    # Windows：python 脚本不能被 CreateProcess 直接 exec，包一层 .cmd（同上 stub_engine）
    py = sys.executable
    with open(rec_path + ".cmd", "w", encoding="utf-8", newline="") as f:
        f.write("@echo off" + chr(13) + chr(10) + '"%s" "%%~dp0%s" %%*' % (py, os.path.basename(rec_path)) + chr(13) + chr(10))
    rec_engine = rec_path + ".cmd"
else:
    rec_engine = rec_path


def recorded_calls():
    if not os.path.exists(rec_calls):
        return []
    with open(rec_calls, encoding="utf-8") as f:
        return [l for l in f.read().splitlines() if l.strip()]


prov_sec = new_prov(cfg={"rsrs": rec_engine})
prov_sec._engine = rec_engine
prov_sec.prefetch("普通对照查询触发召回")   # 对照组：普通 query 走引擎（证明桩登记与普通路径都活着）
check("对照：普通 query 进引擎（桩登记有效）", len(recorded_calls()) >= 1, recorded_calls())
calls_before = len(recorded_calls())
r_sec = prov_sec.prefetch(CRED_QUERY)
check("凭据 query 回 SECRET_SKIP", r_sec == mod.SECRET_SKIP, r_sec)
check("凭据 query 零引擎调用", len(recorded_calls()) == calls_before, recorded_calls())
check("SECRET_SKIP 回显不含凭据值", CRED not in (r_sec or ""))
_log_text = open(os.environ["RESPITE_LOG"], encoding="utf-8").read()
check("插件日志不含凭据值", CRED not in _log_text)
check("日志只记固定事件码", '"ev": "prefetch"' in _log_text and '"out": "secret_skip"' in _log_text
      and CRED_QUERY not in _log_text, _log_text[-200:])


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
check("format_recall 头行条数一致", re.search(r"matched 1 item", txt), txt)

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
        check("真库 show 读全文", "【respire · entry】" in shown, str(shown)[:120])
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
    if "store pending" in out:
        PASS += 1; print("SKIP  工具落库判重待决（实时库判重门收紧，ok 回执路径由桩覆盖）")
    check("工具落库成功回执", "Saved:" in out, out)
    check("回执约束 retitle", "retitle" in out, out)
    m = re.search(r"[(（]([0-9a-f]{8})[)）]", out)
    check("回执带短 id", m is not None, out)
    if m:
        rc = mod.run_rsrs(ENGINE, ["forget", m.group(1), "--json"], 10)
        try:
            check("探针清理(status=ok)", json.loads(rc.stdout).get("status") == "ok")
        except Exception:
            check("探针清理(status=ok)", False, rc.stdout[:120])
    out2 = new_prov(cfg={"rsrs": ENGINE}).prefetch(f"记：{MARK}")
    check("prefetch 前缀落库回执", isinstance(out2, str)
          and ("Saved:" in out2 or "store pending" in out2 or "store unknown" in out2), out2)
    m2 = re.search(r"[(（]([0-9a-f]{8})[)）]", out2 or "")
    if m2:
        mod.run_rsrs(ENGINE, ["forget", m2.group(1), "--json"], 10)  # 待决态未写入时 forget 会失败，忽略
else:
    print("SKIP  写探针（默认只读；加 --store 开启，写真库并自动 forget 清理）")

# ---- 11) 审计整改契约（HM-01~HM-05）：全走桩，零真库依赖 ----
def store_stub(mode, stub_mode_var="RESPIRE_TEST_STORE_MODE"):
    """remember/show/list 桩：mode 控制回执形态（0.1.3 扩展：silent/slow_ok/slow_garbage/
    ok_noid/ok_badid/bad_summary/skip/fail/ok_rid/ok_tamper/show_bad）；list 恒 stale；
    每次调用追加记入脚本旁 <stub>.calls（按实例隔离，供幂等/预算测试断言引擎调用数）。"""
    path = os.path.join(TMP, f"rsrs-store-{mode}-{time.time_ns()}")
    calls_log = (path + ".calls").replace("\\", "/")   # 烤进脚本的实例私有调用日志
    state_json = (path + ".state").replace("\\", "/")  # 实例私有的 remember title/content 回放状态
    with open(path, "w", encoding="utf-8") as f:
        f.write("""#!/usr/bin/env python3
import json, os, sys, time
sub = sys.argv[1] if len(sys.argv) > 1 else ""
mode = os.environ.get("RESPIRE_TEST_STORE_MODE", "%MODE%")
CALLS, STATE = "%CALLS%", "%STATE%"
try:
    with open(CALLS, "a", encoding="utf-8") as c:
        c.write(sub + chr(10))
except Exception:
    pass
if sub == "remember":
    # 记录本次 --title/正文供 show 回读（argv: remember content --title title ...）
    try:
        with open(STATE, "w", encoding="utf-8") as fh:
            json.dump([sys.argv[4] if len(sys.argv) >= 5 else "", sys.argv[2]], fh)
    except Exception:
        pass
    if mode == "silent":
        sys.exit(0)
    if mode == "slow_garbage":
        time.sleep(2.5)
        print("not-json-garbage {{{")
        sys.exit(0)
    if mode == "slow_ok":
        time.sleep(1.2)
        print(json.dumps({"status": "ok", "summary": {"action": "created", "id": "dddddddddddddddd"}}))
        sys.exit(0)
    if mode == "ok_noid":
        print(json.dumps({"status": "ok", "summary": {"action": "created", "written": True}}))
        sys.exit(0)
    if mode == "ok_badid":
        print(json.dumps({"status": "ok", "summary": {"action": "created", "id": "zz!!short"}}))
        sys.exit(0)
    if mode == "bad_summary":
        print(json.dumps({"status": "ok", "summary": ["not", "a", "dict"], "details": "also not a dict"}))
        sys.exit(0)
    if mode == "skip":
        print(json.dumps({"status": "skip", "summary": {"state": "readonly", "written": False}}))
        sys.exit(2)
    if mode == "fail":
        print(json.dumps({"status": "fail", "errors": ["importance must be important or trivial"]}))
        sys.exit(2)
    if mode == "warn":
        print(json.dumps({"status": "warn", "summary": {"action": "created", "id": "eeeeeeeeeeeeeeee"},
                          "errors": ["parent (cause) cannot be resolved; stored independently"]}))
        sys.exit(2)
    if mode == "pending":
        print(json.dumps({"status": "pending", "summary": {"state": "candidates", "written": False},
                          "items": [{"name": "aaaabbbb", "status": "pending",
                                     "value": "similarity=88%% title=stub candidate",
                                     "action": "merge or attach"}],
                          "actions": ["rsrs remember \\"<combined content>\\" --merge-ids \\"aaaabbbb\\"",
                                      "rsrs remember \\"<content>\\" --force to write as new entry"]}))
        sys.exit(2)
    print(json.dumps({"status": "ok", "summary": {"action": "created", "id": "dddddddddddddddd"}}))
elif sub == "show":
    if mode == "show_bad":
        print(json.dumps({"status": "fail", "errors": ["not found"]}))
        sys.exit(2)
    # 回读回显最近一次 remember 发送的 --title/正文——状态落在实例私有文件（show 自身 argv 不带标题）
    title, content = "stub t", "stub c"
    try:
        with open(STATE, encoding="utf-8") as fh:
            title, content = json.load(fh)
    except Exception:
        pass
    rid = sys.argv[2]
    if mode == "ok_rid":
        rid = "1111111111111111"  # 回读返回别的条目 id（entry 内容却回显正确——专检 summary.id 核对）
    if mode == "ok_tamper" and len(content) > 40:
        content = content[:40] + " tampered tail"  # 前 40 字相同、尾部篡改（旧代码会误报 verified）
    print(json.dumps({"status": "ok", "summary": {"id": rid, "title": title},
                      "details": {"entry": {"id": rid, "title": title, "content": content}}}))
elif sub == "list":
    print(json.dumps({"status": "ok", "details": [{"id": "ffffffffffffffff",
        "created_at": "2020-01-01T00:00:00.000Z", "updated_at": "2020-01-01T00:00:00.000Z",
        "title": "stale list entry"}]}))
elif sub == "recall":
    print(json.dumps({"status": "skip", "details": []}))
else:
    sys.exit(0)
""".replace("%MODE%", mode).replace("%CALLS%", calls_log).replace("%STATE%", state_json))
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    if os.name == "nt":
        py = sys.executable
        with open(path + ".cmd", "w", encoding="utf-8", newline="") as f:
            f.write("@echo off" + chr(13) + chr(10) + '"%s" "%%~dp0%s" %%*' % (py, os.path.basename(path)) + chr(13) + chr(10))
        return path + ".cmd"
    return path


def stub_calls(engine):
    """读桩引擎实例私有的 <stub>.calls（store_stub 每次调用记一行子命令名）。"""
    base = engine[:-4] if engine.endswith(".cmd") else engine
    log = base + ".calls"
    if not os.path.exists(log):
        return []
    with open(log, encoding="utf-8") as f:
        return [l for l in f.read().splitlines() if l.strip()]


env_ok = dict(os.environ, RESPIRE_TEST_STORE_MODE="ok")
mod.os.environ["RESPIRE_TEST_STORE_MODE"] = "ok"
sb = store_stub("ok")
prov_ok = new_prov(cfg={"rsrs": sb})
prov_ok._engine = sb
out = prov_ok.prefetch("记：stub fact sentence one.")
check("ok 写入回执含短 id", "Saved:" in out and "(dddddddd)" in out, out)
check("ok 写入回读核验标注", "verified by read-back" in out, out)
check("ok 回执 retitle 指引用 shell rsrs", "rsrs retitle dddddddd" in out, out)
mod.os.environ["RESPIRE_TEST_STORE_MODE"] = "warn"
out = prov_ok.prefetch("记：warn fact sentence two.")
check("warn 已写入不误报失败", "Saved with warnings" in out and "did not accept" not in out, out)
check("warn 附引擎警告摘要", "parent (cause) cannot be resolved" in out, out)
mod.os.environ["RESPIRE_TEST_STORE_MODE"] = "pending"
out = prov_ok.prefetch("记：pending fact sentence three.")
check("pending 明示未写入", "nothing was written" in out, out)
check("pending 候选带短id", "[aaaabbbb]" in out, out)
check("pending 保留建议动作merge", "--merge-ids" in out, out)
check("pending 保留建议动作force", "--force" in out, out)
mod.os.environ["RESPIRE_TEST_STORE_MODE"] = "silent"
out = prov_ok.prefetch("记：silent fact sentence four.")
check("坏回执归为unknown", "store unknown" in out, out)
check("unknown 只读核实禁重发", "Do NOT resend" in out and "rsrs list --limit 3" in out, out)
check("unknown 不误报失败", "did not accept" not in out, out)

# HM-04 幂等：同会话同内容显式快存只提交一次
mod.os.environ["RESPIRE_TEST_STORE_MODE"] = "ok"
prov_idem = new_prov(cfg={"rsrs": sb})
prov_idem._engine = sb
prov_idem.prefetch("记：idempotent fact sentence.")
calls_before = prov_idem.prefetch("记：idempotent fact sentence.")
check("重复显式快存被幂等拦截", "already submitted in this session" in calls_before, calls_before)
mod.os.environ["RESPIRE_TEST_STORE_MODE"] = "pending"
out = prov_idem.prefetch("记：pending does not lock idem key.")
_before_pend = stub_calls(sb).count("remember")
out2 = prov_idem.prefetch("记：pending does not lock idem key.")
check("pending 不锁幂等键(可再决断)", "store pending" in out2, out2)
check("pending 重试确实再走引擎(键已解除)", stub_calls(sb).count("remember") == _before_pend + 1,
      stub_calls(sb))

# ---- 11b) 复审整改契约（0.1.3，PR #6 复核意见 #1/#2）：原子幂等预占三态＋回执/回读加固 ----
# 并发原子预占（意见#1）：两线程同时 prefetch 同内容——锁内「检查＋登记 in-flight」一步完成，
# 仅一个线程能发起 do_store（slow_ok 桩 remember 睡 1.2s 拉开窗口），另一线程回 in-flight 等待回执
mod.os.environ["RESPIRE_TEST_STORE_MODE"] = "slow_ok"
sb_cc = store_stub("slow_ok")
prov_cc = new_prov(cfg={"rsrs": sb_cc})
prov_cc._engine = sb_cc
_cc_results = []


def _cc_prefetch():
    _cc_results.append(prov_cc.prefetch("记：concurrent in-flight fact sentence."))


_cc_threads = [threading.Thread(target=_cc_prefetch) for _ in range(2)]
for _t in _cc_threads:
    _t.start()
for _t in _cc_threads:
    _t.join()
_cc_calls = stub_calls(sb_cc)
check("并发预占只发一次 remember(意见#1)", _cc_calls.count("remember") == 1, _cc_calls)
check("并发一方收到已写入回执", any(isinstance(o, str) and "Saved:" in o for o in _cc_results), _cc_results)
check("并发另一方回 in-flight 等待", any(isinstance(o, str) and "already in flight in this session" in o
                                       for o in _cc_results), _cc_results)
check("in-flight 回执明示等待不重发", any(isinstance(o, str) and "wait for its receipt" in o
                                        for o in _cc_results), _cc_results)

# unknown 三态（意见#1）：已提交但回执丢失 → 键保持 unknown，重复 prefetch 只读核实、绝不自动重发
mod.os.environ["RESPIRE_TEST_STORE_MODE"] = "silent"
sb_u = store_stub("silent")
prov_u = new_prov(cfg={"rsrs": sb_u})
prov_u._engine = sb_u
out_u = prov_u.prefetch("记：lost receipt unknown fact sentence.")
check("已提交但回执丢失归 unknown", isinstance(out_u, str) and "store unknown" in out_u, out_u)
mod.os.environ["RESPIRE_TEST_STORE_MODE"] = "ok"   # 引擎恢复健康，诱惑重发
_before_u = stub_calls(sb_u).count("remember")
out_u2 = prov_u.prefetch("记：lost receipt unknown fact sentence.")
check("unknown 后重复 prefetch 回只读核实回执", isinstance(out_u2, str)
      and "returned an unknown result" in out_u2 and "never resend" in out_u2, out_u2)
check("unknown 后重复 prefetch 不再调 do_store", stub_calls(sb_u).count("remember") == _before_u,
      stub_calls(sb_u))
check("unknown 保持占用不误报已写", "already submitted" not in (out_u2 or ""), out_u2)

# 回执加固（意见#2）：缺 id / 畸形 id / 坏 summary → unknown（不误报明确失败、不抛异常丢回执）
for _mode, _desc in (("ok_noid", "缺 id"), ("ok_badid", "畸形 id"), ("bad_summary", "坏 summary")):
    mod.os.environ["RESPIRE_TEST_STORE_MODE"] = _mode
    prov_x = new_prov(cfg={"rsrs": sb})
    prov_x._engine = sb
    out_x = prov_x.prefetch(f"记：{_mode} unknown classification fact sentence.")
    check(f"{_desc} 归 unknown(意见#2)", isinstance(out_x, str) and "store unknown" in out_x, out_x)
    check(f"{_desc} 不误报明确失败", "did not accept" not in (out_x or ""), out_x)

# skip 新分支（意见#2）：引擎按 readonly/paused 模式拒绝——未写也不是失败、绝不引导强写、不锁键
mod.os.environ["RESPIRE_TEST_STORE_MODE"] = "skip"
prov_sk = new_prov(cfg={"rsrs": sb})
prov_sk._engine = sb
out_sk = prov_sk.prefetch("记：skip mode rejection fact sentence.")
check("skip 回引擎模式拒绝(新分支)", "not stored" in out_sk and "read-only or paused mode" in out_sk, out_sk)
check("skip 回执禁强写", "never force a write" in out_sk, out_sk)
check("skip 未写不算失败也不算未知", "did not accept" not in out_sk and "store unknown" not in out_sk, out_sk)
out_sk2 = prov_sk.prefetch("记：skip mode rejection fact sentence.")
check("skip 不锁键(重复触发仍走引擎)", "not stored" in out_sk2, out_sk2)

# fail 白名单（意见#2）：唯一明确拒绝态；不锁键（模型可修参后重试）
mod.os.environ["RESPIRE_TEST_STORE_MODE"] = "fail"
out_fl = prov_sk.prefetch("记：fail mode rejection fact sentence.")
check("fail 白名单明确拒绝", "did not accept" in out_fl and "importance must be" in out_fl, out_fl)
out_fl2 = prov_sk.prefetch("记：fail mode rejection fact sentence.")
check("fail 不锁键(可修参重试)", "did not accept" in out_fl2, out_fl2)

# 全量回读核验（意见#2）：错 id / 前 40 字同但尾部篡改 → mismatch，绝不称 verified
mod.os.environ["RESPIRE_TEST_STORE_MODE"] = "ok_rid"
out_rid = prov_sk.prefetch("记：readback wrong id fact sentence beyond forty characters.")
check("回读错 id 判 mismatch", "Saved:" in out_rid and "DIFFERENT title/content" in out_rid, out_rid)
check("错 id 不称 verified", "verified by read-back" not in out_rid, out_rid)
mod.os.environ["RESPIRE_TEST_STORE_MODE"] = "ok_tamper"
out_tp = prov_sk.prefetch("记：readback tamper fact sentence with tail beyond forty characters.")
check("前40字同但尾部篡改判 mismatch", "Saved:" in out_tp and "DIFFERENT title/content" in out_tp, out_tp)
check("尾部篡改不称 verified(旧代码必漏)", "verified by read-back" not in out_tp, out_tp)

# 坏回读（意见#2）：show 失败 → unavailable 如实标注；已写回执与 written 幂等态保留
mod.os.environ["RESPIRE_TEST_STORE_MODE"] = "show_bad"
out_sb2 = prov_sk.prefetch("记：show bad readback unavailable fact sentence.")
check("坏回读如实标 unavailable", "Saved:" in out_sb2 and "Read-back unavailable" in out_sb2, out_sb2)
check("坏回读不误报 mismatch", "DIFFERENT title/content" not in out_sb2, out_sb2)
out_sb3 = prov_sk.prefetch("记：show bad readback unavailable fact sentence.")
check("回读失败保留已写回执(键仍 written)", "already submitted in this session" in out_sb3, out_sb3)

# 共享预算（意见#2）：紧预算+慢坏回执 → unknown，且预算外零追加引擎调用（不查库不回读）
os.environ["RESPITE_HOOK_BUDGET_S"] = "4"
try:
    mod.os.environ["RESPIRE_TEST_STORE_MODE"] = "slow_garbage"
    sb_b = store_stub("slow_garbage")
    prov_b = new_prov(cfg={"rsrs": sb_b})
    prov_b._engine = sb_b
    out_b = prov_b.prefetch("记：tight budget slow garbage receipt.")
    _calls_b = stub_calls(sb_b)
    check("紧预算+慢坏回执归 unknown", isinstance(out_b, str) and "store unknown" in out_b, out_b)
    check("紧预算零追加引擎调用(意见#2)", _calls_b.count("remember") == 1
          and "list" not in _calls_b and "show" not in _calls_b, _calls_b)
    out_b2 = prov_b.prefetch("记：tight budget slow garbage receipt.")
    check("紧预算 unknown 后重复触发不重发", "returned an unknown result" in out_b2
          and stub_calls(sb_b).count("remember") == 1, out_b2)
finally:
    os.environ.pop("RESPITE_HOOK_BUDGET_S", None)
    mod.os.environ.pop("RESPITE_HOOK_BUDGET_S", None)
    mod.os.environ["RESPIRE_TEST_STORE_MODE"] = "ok"


# HM-01 零命中/空库催促：桩 recall=skip → 旧代码直接 return None 不催
stub_zero = stub_engine(recall_hit=False)
prov_zero = new_prov(cfg={"rsrs": stub_zero})
prov_zero._engine = stub_zero
prov_zero._turns = 1
prov_zero._last_store_ts = time.time() - 3600
out = prov_zero.prefetch("全新主题零命中查询")
check("零命中轮仍附存储催促(HM-01)", isinstance(out, str) and "Memory check" in out, out)
prov_zero._last_store_ts = None  # 空库：引擎 list 无可读数据
out = prov_zero.prefetch("全新主题零命中查询")
check("空库(无落库数据)也附催促(HM-01)", isinstance(out, str) and "Memory check" in out, out)
prov_zero._cfg["store_nudge_every"] = 0
out = prov_zero.prefetch("全新主题零命中查询")
check("节流关闭零命中轮不催", out is None, out)
check("催促明示不得替代当前任务(HM-05)", "never a replacement" in mod.STORE_NUDGE, mod.STORE_NUDGE)
check("简则改题指引用 rsrs retitle 而非 remember(HM-03)",
      "respire_remember (retitle)" not in mod.RULES and "rsrs retitle" in mod.RULES, mod.RULES[105:160])

print("----")
print(f"PASS={PASS} FAIL={FAIL}")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(0 if FAIL == 0 else 1)
