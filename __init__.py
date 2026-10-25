#!/usr/bin/env python3
# respire —— respire(rsrs) × Hermes Agent 记忆 provider（目录插件，根=__init__.py+register()）
# 与 respire-zcode / respire-kimi / respire-dimcode / respire-qoder / respire-workbuddy 同源方案，
# 按 Hermes「Memory Provider Plugins」官方契约（Hermes v0.22.5 基线）移植：
#   prefetch(query)            ← 每轮 UserPromptSubmit 召回（标题档）＋「记：」前缀直落库＋/nomem 跳过
#   system_prompt_block()      ← SessionStart 简则＋引擎状态引导（未装/未登录/空库/缺模型）
#   sync_turn(user, assistant) ← 非阻塞后台刷「近窗落库」缓存，供存储催促判定（铁律：spawn_context_thread）
#   工具 respire_recall / respire_show / respire_remember（判后即存三态回执）
# 契约要点：
#   1. is_available() 禁网络调用——只做二进制存在性探测；
#   2. 路径一律从 initialize(hermes_home=...) 派生（profile 隔离），禁硬编码 ~/.hermes；
#   3. register(ctx) 每次构造新实例（multiplexed gateway 禁模块级单例）；
#   4. 配置边车 $HERMES_HOME/respire.json（read_json_or_empty / atomic_json_write）；
#   5. wrapper 式零 Python 依赖面（引擎=npm 装的 rsrs CLI sidecar）→ hermes update 不动本目录。
# 铁律: fail-open —— 生命周期钩子与工具调用绝不抛异常阻断宿主；引擎缺失=降级为空注入。
from __future__ import annotations

import datetime
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

VERSION = "0.1.0"
PROVIDER_NAME = "respire"

logger = logging.getLogger("hermes.plugins.respire")

# ---- Hermes 宿主导入（缺席时回退为结构等价桩，供脱离 Hermes 自测/CI） ----
try:
    from agent.memory_provider import MemoryProvider, spawn_context_thread
except Exception:  # pragma: no cover - 仅在无 Hermes 环境触发
    class MemoryProvider:  # type: ignore[no-redef]
        """结构桩：正式宿主下被 agent.memory_provider.MemoryProvider 取代。"""

    def spawn_context_thread(target, name=None):  # type: ignore[no-redef]
        # 桩环境无 contextvars 剖面可带；daemon 线程保证不阻进程退出
        return threading.Thread(target=target, name=name or "respire", daemon=True)

try:  # Hermes utils（配置边车读写契约）
    from utils import atomic_json_write, read_json_or_empty
except Exception:  # pragma: no cover
    def read_json_or_empty(path):
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
            return doc if isinstance(doc, dict) else {}
        except Exception:
            return {}

    def atomic_json_write(path, data):
        tmp = f"{path}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

# 超时窗（秒）：须盖住 rsrs runtime 冷启动（实测 doctor 模型加载 7s+），否则首轮静默、热后即灵
RECALL_T = 14
PROBE_T = 10
STORE_T = 18
LIST_T = 30   # 「近窗落库」检查窗口（1.0.16 冷启动实测可达 27s；热态约 0.3–0.9s）
LOG_MAX = 256 * 1024  # plugin.log 超此整文件清轮

DEFAULTS = {
    "rsrs": "",                 # respire 引擎（rsrs）路径；空 = 自动探测（向导字段 rsrs_path 落此键）
    "recall_threshold": 0.0,    # 召回下限，0=默认关（rsrs 直出）：skip 门管无关、limit 限流、标题档廉价
    "recall_limit": 5,          # prefetch 取前 N 条（respire_recall 工具不受此限，用调用方 limit）
    "min_prompt_chars": 2,      # 过短不检索（1 字才跳；2 字起即查，rsrs 自带 skip 门、本地 100–400ms）
    "session_rules": True,      # system_prompt_block 是否注入用法简则
    "budget_chars": 1500,       # 单轮 prefetch 注入总字数预算（宿主安全上限 100k，远低于即可）
    "log": True,                # plugin.log 轻量留痕（每事件一行，复盘时灵时不灵的抓手）
    "enabled": True,            # 总开关：false=记忆层全线静默
    "auto_recall": True,        # prefetch 每轮自动召回开关（false=只留工具，不自动注入）
    "store_nudge_every": 1,     # 存储催促节流：1=每轮、N=每 N 轮一次、0=关闭（依引擎近窗落库判定）
    "skip_prefixes": [],        # 宿主内部提示白名单：命中前缀的 query 不召回也不落库
}

# 显式落库前缀：query 以「记：」等开头 → 直接入库并回执（仅 primary 上下文执行写入）
STORE_PREFIX = re.compile(r"^(?:记|存|记录|记住|remember|store)\s*[:：]\s*", re.I)
# 本轮跳过召回
SKIP_PREFIX = re.compile(r"^(?:/nomem|#nomem)\b")

RULES = """【respire 记忆层 · 已挂接】respire（rsrs）记忆库随本会话生效；数据本地加密、云端同步（账号注册与登录不归本层管，见 rsrs.rs）。
1. 答前先翻忆：工具 respire_recall（或 shell 跑 rsrs recall "项目名+关键词" --json --titles）；报错、异常先按报错原文翻忆。
2. 收尾可存：respire_remember（判后即存：相似会给候选，先决断再发；title 是召回与判重的门面，须「主语＋动作＋结果」完整事实句）；消息以「记：」开头时由本层先落库保底——回执带暂拟标题与短 id 时，你须抽取更准的事实句 respire_remember 换标题（retitle）替换。
3. 每轮自动召回已随 system prompt 注入；未见召回注入、命中不贴题、或话题涉及此前的项目、报错、约定、日期时，换词补查再答（同义词、上位词、英文对应词各试一路）；仅当已有注入确实相关方才勿重复。本轮 /nomem 前缀可关闭自动召回。
4. 读全文用 respire_show（短 id 前 8 位即可）；因果链深挖 rsrs chain。
5. 口令、证件号、手机号、卡号、私钥禁入记忆正文与查询词，只写存放位置——库虽加密，密钥泄露即裸奔。
6. 同步 rsrs sync（autosync 常开）、rsrs status 查态、rsrs prompt 读完整工作流；深用法见 respire:respire 技能。"""

NO_RSRS = """【respire·引擎未装】provider 已就绪，只差引擎。先向用户说明并征得同意，再执行安装（遵循 Hermes 的审批流程，不得自动批准）：
  npm i -g rsrs-cli@1.0.16
（无全局写权限时先试：npm config set prefix ~/.npm-global && npm i -g rsrs-cli@1.0.16）
装完跑 rsrs --version 确认——本层每轮实时探测，装完重启 Hermes 或下一会话即恢复召回与「记：」落库。
配置下一步（你顺手引导即可）：rsrs status 查登录态；未登录则请用户去 https://rsrs.rs 注册后本机跑 rsrs register --user <名>（登录密码本人设≥8位；超级密码绝不自造、绝不传 --super——系统自动生成 A3-… 恢复码并在输出中醒目列出，务必抄下保存，丢失则任何设备都解不开记忆）；日常 rsrs login；语义模型可选，想要更好召回再 rsrs model install && rsrs model activate m3。"""

LOGIN_NUDGE = """注意：本机 respire 尚未登录——记忆只存本机、不同步、不跨设备。引导用户：去 rsrs.rs 注册后跑 rsrs register（登录密码本人设；超级密码由系统自动生成 A3-… 恢复码，不要自造，务必保存）；日常登录可由你代跑 rsrs login --oauth --no-open，把授权链接交给用户点击。"""

EMPTY_NUDGE = """注意：本机 respire 记忆库还是空的——没有记忆可召回，自动召回无动静是正常而非故障。引导：用「记：〈事实〉」由本层落库，或先让 agent 把既有项目的约定、报错教训、关键结论存上几条；有货之后每轮召回才有内容。"""

NO_MODEL = """注意：respire 语义模型（BGE-M3）未装——现为词面档。完全可用：注册、登录、同步与召回都不依赖模型；想要更好效果再装：rsrs model install 下载模型，再 rsrs model activate m3 建索引激活召回升为语义档（可代跑，建索引可后台挂起）。"""

PROBE_FAIL = """注意：respire 状态探测失败（rsrs status 未回）——若是全新机器尚未注册，先引导用户去 rsrs.rs 注册并跑 rsrs register（首设备）；否则可能 runtime 冷启动中，首轮召回空转稍候即通，持续无回跑 rsrs doctor 诊断并转告用户。"""

STORE_NUDGE = ("记忆检查（自动一次）：只做一件事——若你本轮产生了新的事实、决定、约定、教训或踩坑，"
               "用 respire_remember 分条存入（--importance important；标题按「主语＋动作＋结果」事实句；"
               "判重给候选先决断：parent 挂靠或 force 直写）；若本轮只是问答闲聊而无新事实，直接结束、勿重复作答。")

_REDACT = [
    (re.compile(r"(?i)(password|passwd|pwd|密码|口令)\s*[:=：]\s*\S+"), r"\1=[REDACTED]"),
    (re.compile(r"\b(?:sk-|ghp|gho|github_pat|AKIA|xox)[A-Za-z0-9_-]{8,}\b"), "[REDACTED]"),
    (re.compile(r"\b\d{11,19}\b"), "[NUM]"),
]


def redact(s: str) -> str:
    """日志脱敏：口令式赋值、token 前缀、11-19 位长数字（手机/卡号）。日志只用于诊断，绝不落明文凭据。"""
    try:
        for pat, rep in _REDACT:
            s = pat.sub(rep, s)
    except Exception:
        pass
    return s


# ---------------- 引擎侧（与 hook 系家族同源，subprocess rsrs CLI sidecar） ----------------

# 传给 sidecar 的最小环境：宿主进程可能持有各家的 API key / token，绝不整包透传。
# 只放运行 rsrs 所需的路径/本地化/代理/TLS 变量，外加 RSRS_*/RESPIRE_* 配置；
# 名字里带敏感词的（RSRS_SUPER、TOKEN、SECRET…）一律剔除。
_SIDECAR_ENV_ALLOW = frozenset({
    "HOME", "USER", "LOGNAME", "PATH", "TMPDIR", "TMP", "TEMP",
    "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TERM", "SHELL", "PWD",
    "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS", "GNOME_KEYRING_CONTROL",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS",
    "NO_COLOR", "FORCE_COLOR", "HERMES_HOME",
})
_SIDECAR_SECRET_NAME = re.compile(r"(TOKEN|SECRET|PASSWORD|PASSWD|SUPER|API_?KEY|CREDENTIAL|AUTH)", re.I)


def sidecar_env() -> dict:
    """Build the environment for the rsrs sidecar (never the full host environment)."""
    env = {k: v for k, v in os.environ.items() if k in _SIDECAR_ENV_ALLOW}
    for k, v in os.environ.items():
        if k.startswith(("RSRS_", "RESPIRE_")) and not _SIDECAR_SECRET_NAME.search(k):
            env[k] = v
    return env


def find_rsrs(cfg: dict):
    """引擎探测链：显式配置 → 环境变量 → PATH → 常见安装位。纯文件探测，无 exec、无网络（is_available 契约）。"""
    home = os.path.expanduser("~")
    cands = [cfg.get("rsrs") or "",
             os.environ.get("RESPITE_RSRS") or "",
             os.environ.get("RESPIRE_BIN") or "",
             shutil.which("rsrs") or "",
             shutil.which("respire") or "",
             f"{home}/.local/bin/respire", f"{home}/.local/bin/rsrs",
             f"{home}/.rsrs/bin/rsrs", "/usr/local/bin/rsrs", "/usr/bin/rsrs"]
    seen = set()
    for c in cands:
        if not c or c in seen:
            continue
        seen.add(c)
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def run_rsrs(bin_path: str, args: list, timeout: int):
    # rsrs 的 --json 模式下进度写 stderr，capture 须分离，只解析 stdout
    env = sidecar_env()  # 仅传最小环境（含 HERMES_HOME/RSRS_* 配置），绝不透传宿主密钥
    return subprocess.run([bin_path] + args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, env=env)


def extract_title(content: str) -> str:
    """无 LLM 下尽量抽完整事实句：整句优先 → 退到分隔符 → 绝不在词中硬截（上限 70 字）。"""
    s = re.sub(r"\s+", " ", content.strip())
    if not s:
        return ""
    m = re.match(r"^(.{6,}?[。！？!?])", s)          # 第一个 ≥6 字的完整句
    if m:
        return m.group(1)[:70].strip()
    head = s[:70]
    cuts = [head.rfind(c) for c in "，；、,;"]        # 退到分隔符，不切半词
    cut = max(cuts)
    if cut >= 20:
        return head[:cut + 1].rstrip("，,；; ").strip()
    return head                                       # 无标点长串才落到 70 字上界


def format_recall(doc: dict, limit: int, threshold: float, budget: int):
    """recall --titles --json 信封 → 注入/工具共用的标题档文本；返回 (text, n_hits)。"""
    details = doc.get("details") or []
    if not isinstance(details, list):
        return "", 0
    keep, max_score = [], 0.0
    for d in details:
        if not isinstance(d, dict):
            continue
        score = d.get("score")
        if isinstance(score, (int, float)):
            max_score = max(max_score, float(score))
        if isinstance(score, (int, float)) and float(score) >= threshold:
            keep.append((float(score), d))
        if len(keep) >= limit:
            break
    if not keep:
        return "", 0
    lines, used = [], 90  # 90 留给头行上界，砍点据此提前
    for score, d in keep:
        title = str(d.get("title") or "").strip() or "(无题)"
        eid = str(d.get("id") or "")[:8]
        stale = " →已被取代" if d.get("superseded_by") else ""
        line = f"- [{score:.2f}] {title}（{eid}）{stale}"
        if lines and used + len(line) + 1 > budget:
            break
        lines.append(line)
        used += len(line) + 1
    if not lines:
        return "", 0
    gate = f"阈值 {threshold:g}，" if threshold > 0 else "rsrs 直出，"
    head = (f"【respire·自动召回】命中 {len(lines)} 条（{gate}标题档，"
            "正文 respire_show <短id> 按需读；分数是相关度不是可信度）：")
    return "\n".join([head] + lines), len(lines)


def do_recall(bin_path: str, query: str, limit: int, threshold: float, budget: int):
    """标题档召回：rsrs 自带相关性门（无关→skip 零命中），本层再按阈值滤掉碰瓷命中。"""
    try:
        r = run_rsrs(bin_path, ["recall", query, "--limit", str(int(limit)),
                                "--titles", "--json"], RECALL_T)
    except Exception as e:
        logger.warning("recall spawn failed: %s", e)
        return ""
    if r.returncode != 0:
        logger.warning("recall rc=%d stderr=%s", r.returncode, (r.stderr or "")[:120])
        return ""
    try:
        doc = json.loads(r.stdout)
    except Exception:
        return ""
    if not isinstance(doc, dict) or doc.get("status") not in ("ok", "skip"):
        return ""  # fail 静默（fail-open）；skip = 自带门判为无关
    text, _ = format_recall(doc, limit, threshold, budget)
    return text


def do_store(bin_path: str, content: str, title: str = "", importance: str = "important",
             parent: str = "", force: bool = False):
    """判后即存三态——ok 直写 / pending 给候选待决 / 其余为失败；返回回执文本，绝不抛异常。"""
    if not content:
        return "【respire·落库】内容为空，未入库。"
    title = (title or "").strip() or extract_title(content) or "respire 快存"
    args = ["remember", content, "--title", title, "--importance",
            "trivial" if importance == "trivial" else "important", "--json"]
    if parent:
        args += ["--parent", parent]
    if force:
        args += ["--force"]
    try:
        r = run_rsrs(bin_path, args, STORE_T)
    except Exception as e:
        return f"【respire·落库】respire 超时未回（{type(e).__name__}），请改手动 rsrs remember 核实是否入库。"
    try:
        doc = json.loads(r.stdout) if r.stdout else {}
    except Exception:
        doc = {}
    if not isinstance(doc, dict):
        doc = {}
    status = str(doc.get("status") or "")
    if status == "ok":
        sid = str((doc.get("summary") or {}).get("id") or "")
        tail = f"（{sid[:8]}）" if sid else ""
        return (f"【respire·落库】已存入：{title}{tail}。若标题系规则暂拟——"
                "请从内容抽取「主语＋动作＋结果」的完整事实句，"
                f"rsrs retitle {sid[:8]} --title 替换（title 是召回与判重的门面）。")
    if status == "pending":
        # 判后即存判定疑似重复/可挂靠，未写入（written:false）——转述候选，模型接续决断
        lines = ["【respire·落库待决】respire 判定本条与既有记忆相似，未写入。候选："]
        for it in (doc.get("items") or [])[:5]:
            if isinstance(it, dict):
                lines.append(f"- [{it.get('name', '?')}] {it.get('value', '')}")
        lines.append("请决断：挂靠则 respire_remember 同文 parent=<id> 重发；确属独立则 force=true 直写。")
        return "\n".join(lines)
    errs = doc.get("errors") or []
    msg = json.dumps(errs, ensure_ascii=False)[:200] if errs else f"status={status or r.returncode}"
    return f"【respire·落库失败】respire 未收：{msg}"


def do_show(bin_path: str, entry_id: str):
    """读全文：show 信封 details={entry, ancestors, children}——正文＋因果链上下文。"""
    try:
        r = run_rsrs(bin_path, ["show", entry_id, "--json"], LIST_T)
    except Exception as e:
        return f"【respire·读取失败】rsrs show 超时/异常（{type(e).__name__}）。"
    try:
        doc = json.loads(r.stdout) if r.stdout else {}
    except Exception:
        return f"【respire·读取失败】输出不可解析（rc={r.returncode}）。"
    if not isinstance(doc, dict) or doc.get("status") != "ok":
        errs = doc.get("errors") if isinstance(doc, dict) else None
        return f"【respire·读取失败】{json.dumps(errs, ensure_ascii=False)[:200] if errs else 'status!=ok'}"
    det = doc.get("details") or {}
    entry = det.get("entry") or {} if isinstance(det, dict) else {}
    if not entry:
        return "【respire·读取失败】信封无 entry。"
    out = [f"【respire·条目】{entry.get('title', '(无题)')}（{str(entry.get('id', ''))[:8]}）",
           f"重要度：{entry.get('importance', '?')}；改于：{entry.get('updated_at', '?')}",
           "", str(entry.get("content", ""))]
    anc = det.get("ancestors") or []
    if anc:
        out += ["", "【因果·上游】" + " ← ".join(str(a.get("title", "?")) for a in anc[-3:][::-1])]
    ch = det.get("children") or []
    if ch:
        out += ["【因果·下游】" + "；".join(str(c.get("title") or c.get("summary", "?")) for c in ch[:5])]
    return "\n".join(out)


def probe_status(bin_path: str):
    """一次 status 三态共用（登录态＋库量＋探测成败；开销实测热态约 190ms）。
    返回 (logged, total, probe_ok)：logged True/False/None；total int/None；
    probe_ok False=status 未回（冷启动/异常），注入 PROBE_FAIL 段。"""
    t0 = time.time()
    try:
        r = run_rsrs(bin_path, ["status", "--json"], PROBE_T)
        doc = json.loads(r.stdout) if r.stdout else {}
        if not isinstance(doc, dict) or doc.get("status") != "ok":
            return (None, None, False)
        summ = doc.get("summary") or {}
        sess = summ.get("session") or {}
        logged = bool(sess.get("has_token")) and bool(sess.get("user"))
        total = summ.get("local_total")
        total = int(total) if isinstance(total, int) else None
        logger.info("probe ok logged=%s total=%s %dms", logged, total, (time.time() - t0) * 1000)
        return (logged, total, True)
    except Exception as e:
        logger.warning("probe failed: %s", e)
        return (None, None, False)


def check_model():
    """语义模型态：~/.rsrs/models/bge-m3 目录存在即已装（stat 零开销）。未装时引擎自带词面回退。"""
    try:
        return os.path.isdir(os.path.expanduser("~/.rsrs/models/bge-m3"))
    except Exception:
        return None  # 探测失败不打扰


def _epoch(rfc3339):
    """'2026-10-22T15:06:38.926Z' → epoch 秒；解析失败返回 None（不猜时区）。"""
    if not isinstance(rfc3339, str) or not rfc3339.strip():
        return None
    s = rfc3339.strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(s)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)  # 无偏移一律按 UTC（rsrs 输出恒带 Z）
    return dt.timestamp()


def engine_last_store(bin_path: str, timeout: int = LIST_T):
    """问引擎最近一条记录的落库时间（秒级 epoch），取 top3 的 updated/created 最大值。
    agent 直接跑 rsrs remember 不经本层，催促节流必须问库才看得见。失败返回 None（放行给上层催）。"""
    r = run_rsrs(bin_path, ["list", "--limit", "3", "--json"], timeout)
    doc = json.loads(r.stdout) if r.stdout else None
    dets = doc.get("details") if isinstance(doc, dict) else None
    best = None
    for d in (dets or []):
        if not isinstance(d, dict):
            continue
        for k in ("updated_at", "created_at"):
            v = _epoch(d.get(k))
            if v is not None and (best is None or v > best):
                best = v
    return best


# ---------------- Provider 本体 ----------------

class RespireMemoryProvider(MemoryProvider):
    """respire(rsrs) 引擎包装：prefetch 每轮召回、三工具、判后即存、fail-open。"""

    def __init__(self):
        # 剖面隔离：一切路径绑定在实例上（multiplexed gateway 下每 profile 一实例）
        self._hermes_home = None
        self._session_id = ""
        self._agent_context = "primary"
        self._cfg = dict(DEFAULTS)
        self._engine = None
        self._lock = threading.Lock()
        self._probe = None          # (logged, total, probe_ok) 缓存
        self._probe_at = 0.0
        self._last_store_ts = None  # 引擎侧最近落库 epoch（后台刷）
        self._turns = 0
        self._threads = []

    # ---- 生命周期（必选） ----

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    def is_available(self) -> bool:
        """能否激活。契约：禁网络调用——仅二进制探测（exec 也省，纯路径检查）。"""
        try:
            return find_rsrs(self._cfg) is not None
        except Exception:
            return False

    def initialize(self, session_id: str, **kwargs) -> None:
        """Agent 启动一次。kwargs 恒含 hermes_home；宽容缺省（可能无 agent/会话库）。"""
        self._session_id = session_id or ""
        self._agent_context = str(kwargs.get("agent_context") or "primary")
        home = kwargs.get("hermes_home")
        if isinstance(home, (str, os.PathLike)) and str(home).strip():
            self._hermes_home = str(home)
        self._cfg = self._load_config()
        self._engine = find_rsrs(self._cfg)
        self._spawn(self._probe_async)  # 冷启动可达秒级，进后台；首轮 prompt 拼装时能取到哪算哪

    # ---- 工具（必选） ----

    def get_tool_schemas(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": "respire_recall",
                    "description": ("语义召回本机 respire 记忆库（本地加密、跨会话跨 agent 共享）。"
                                    "返回标题档：分数＋短 id＋标题；正文用 respire_show 按需读。"
                                    "答前必查；报错先按报错原文翻忆。"),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "检索词：项目名＋核心词，报错则贴报错原文片段"},
                            "limit": {"type": "integer", "description": "取前 N 条（默认 8）", "default": 8},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "respire_show",
                    "description": "读一条记忆全文（含因果链上/下游标题）。短 id 前 8 位即可。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "recall 返回的短 id（前 8 位）或全 id"},
                        },
                        "required": ["id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "respire_remember",
                    "description": ("落库一条记忆（判后即存：与既有记忆相似会返回候选而不写入，须决断——"
                                    "parent 挂靠或 force 直写）。title 按「主语＋动作＋结果」完整事实句；"
                                    "标题写不准＝这条记忆等于不存在。一事一条。"),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string", "description": "正文；建议【前因】【行为】【后果】三段"},
                            "title": {"type": "string", "description": "完整事实句标题（缺省由本层暂拟并提示 retitle）"},
                            "importance": {"type": "string", "enum": ["important", "trivial"],
                                           "description": "可复用结论=important；过程流水=trivial", "default": "important"},
                            "parent": {"type": "string", "description": "挂靠父条目 id（因果下挂/候选决断）"},
                            "force": {"type": "boolean", "description": "确属独立新条时跳过判重直写", "default": False},
                        },
                        "required": ["content"],
                    },
                },
            },
        ]

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs):
        """工具分发。fail-open：任何异常→错误文本，绝不抛给宿主。kwargs 恒含 hermes_home/agent_identity/caller_gsk。"""
        try:
            if not self._cfg.get("enabled", True):
                return "【respire】总开关 enabled=false，记忆层静默。"
            engine = self._engine or find_rsrs(self._cfg)
            args = args if isinstance(args, dict) else {}
            if tool_name == "respire_recall":
                query = str(args.get("query") or "").strip()
                if not query:
                    return "【respire·recall】query 为空。"
                if not engine:
                    return "【respire·引擎未装】" + NO_RSRS
                try:
                    limit = max(1, min(int(args.get("limit") or 8), 20))
                except Exception:
                    limit = 8
                text = do_recall(engine, query, limit,
                                 float(self._cfg.get("recall_threshold", 0.0)),
                                 max(int(self._cfg.get("budget_chars", 1500)), 1500))
                return text or ("【respire·recall】零命中（rsrs 判无关或空库）。"
                                "换 2–3 组关键词再查；确认无相关记忆再下结论。")
            if tool_name == "respire_show":
                eid = str(args.get("id") or "").strip()
                if not eid:
                    return "【respire·show】id 为空。"
                if not engine:
                    return "【respire·引擎未装】" + NO_RSRS
                return do_show(engine, eid)
            if tool_name == "respire_remember":
                content = str(args.get("content") or "").strip()
                if not content:
                    return "【respire·remember】content 为空。"
                if not engine:
                    return "【respire·引擎未装】" + NO_RSRS
                out = do_store(engine, content,
                               title=str(args.get("title") or ""),
                               importance=str(args.get("importance") or "important"),
                               parent=str(args.get("parent") or ""),
                               force=bool(args.get("force")))
                self._spawn(self._refresh_last_store)  # 落库后即时刷新催促判定缓存
                return out
            return f"【respire】未知工具：{tool_name}"
        except Exception as e:  # fail-open——工具失败绝不阻断宿主回合
            logger.warning("tool %s failed: %s", tool_name, e)
            return f"【respire·工具异常】{type(e).__name__}: {e}（fail-open，请改用手动 rsrs 命令核实）"

    # ---- 配置（必选） ----

    def get_config_schema(self):
        # 向导极简（官方建议：只列必填项，其余进 $HERMES_HOME/respire.json 手改——README 有全字段参考）
        return [
            {
                "key": "rsrs_path",
                "description": ("rsrs 引擎二进制路径（留空=自动探测 PATH 与常见安装位）。"
                                "引擎安装：npm i -g rsrs-cli@1.0.16"),
                "default": "",
                "env_var": "RESPIRE_BIN",
            },
        ]

    def save_config(self, values: dict, hermes_home: str) -> None:
        """向导非敏感字段 → 配置边车 $HERMES_HOME/respire.json（rsrs_path 映射为 rsrs 键）。"""
        path = os.path.join(str(hermes_home), f"{PROVIDER_NAME}.json")
        doc = read_json_or_empty(path)
        for k, v in (values or {}).items():
            if k == "rsrs_path":
                doc["rsrs"] = str(v or "")
            else:
                doc[k] = v
        os.makedirs(str(hermes_home), exist_ok=True)
        atomic_json_write(path, doc)

    # ---- 可选钩子 ----

    def system_prompt_block(self) -> str:
        """每请求拼 system prompt 时调用：简则＋引擎状态引导（全部来自缓存，零阻塞）。"""
        try:
            if not self._cfg.get("enabled", True):
                return ""
            if not self._cfg.get("session_rules", True):
                return ""
            out = RULES
            if not self._engine:
                return out + "\n" + NO_RSRS
            segs = []
            probe = self._probe
            if probe is None:
                # 探测未回（冷启动）——懒重试一次后台探测，本轮只出简则
                self._spawn(self._probe_async)
                return out
            logged, total, probe_ok = probe
            if not probe_ok:
                out += "\n" + PROBE_FAIL
                segs.append("probe_fail")
                if time.time() - self._probe_at > 600:  # 探测失败 10 分钟懒重试
                    self._spawn(self._probe_async)
            else:
                if logged is False:                # 仅未登录才附引导，已登录零打扰
                    out += "\n" + LOGIN_NUDGE
                    segs.append("login")
                if total is not None and total < 10:   # 仅空库首日才附引导
                    out += "\n" + EMPTY_NUDGE
                    segs.append("empty")
            if check_model() is False:             # 仅缺模型才附装法，已装零打扰
                out += "\n" + NO_MODEL
                segs.append("no_model")
            self._log("prompt_block", "ok", "segs=" + (",".join(segs) or "none"))
            return out
        except Exception as e:
            logger.warning("system_prompt_block failed: %s", e)
            return ""  # fail-open

    def prefetch(self, query, *, session_id: str = ""):
        """每次 API 调用前：本 query 自动召回（标题档）。「记：」前缀直落库（仅 primary 上下文写）。"""
        try:
            if not self._cfg.get("enabled", True) or not self._cfg.get("auto_recall", True):
                return None
            q = query.strip() if isinstance(query, str) else ""
            if not q or SKIP_PREFIX.match(q):
                return None
            for pat in (self._cfg.get("skip_prefixes") or []):   # 宿主内部提示白名单
                if isinstance(pat, str) and pat and q[:len(pat)].lower() == pat.lower():
                    return None
            engine = self._engine or find_rsrs(self._cfg)
            m = STORE_PREFIX.match(q)
            if m:
                if self._agent_context not in ("", "primary"):  # subagent/cron 禁自动写
                    return None
                if not engine:
                    return NO_RSRS
                rest = q[m.end():].strip()
                self._log("store", "prefix", rest[:40])
                return do_store(engine, rest)
            if len(q) < int(self._cfg.get("min_prompt_chars", 2)):
                return None
            if not engine:
                return None
            text = do_recall(engine, q,
                             int(self._cfg.get("recall_limit", 5)),
                             float(self._cfg.get("recall_threshold", 0.0)),
                             int(self._cfg.get("budget_chars", 1500)))
            if not text:
                return None
            # 存储催促（Stop 钩子的等价物）：节流到每 N 轮、近 10 分钟引擎无落库才附
            every = int(self._cfg.get("store_nudge_every", 1))
            if every > 0 and self._turns > 0 and self._turns % every == 0:
                last = self._last_store_ts
                if last is not None and time.time() - last > 600:
                    text += "\n" + STORE_NUDGE
            self._log("prefetch", "hit", f"turns={self._turns} q={q[:40]}")
            return text
        except Exception as e:
            logger.warning("prefetch failed: %s", e)
            return None  # fail-open

    def queue_prefetch(self, query, *, session_id: str = ""):
        """每轮结束后预热下一轮。rsrs runtime 常驻、recall 热态亚秒——无需预热，留空。"""
        return None

    def sync_turn(self, user_content, assistant_content, *, session_id: str = "", messages=None) -> None:
        """每轮完成后台调。铁律：非阻塞——仅后台刷「近窗落库」缓存（spawn_context_thread）。
        respire 是 agent 策展记忆（判后即存），本层不自动整轮落库。"""
        try:
            with self._lock:
                self._turns += 1
            self._spawn(self._refresh_last_store)
        except Exception as e:
            logger.warning("sync_turn failed: %s", e)

    def on_session_end(self, messages) -> None:
        """会话结束。不自动归档全对话（违背策展原则）；留作扩展点。"""
        return None

    def shutdown(self) -> None:
        """进程退出：daemon 线程随进程回收，尽力 join 不超时。"""
        for t in list(self._threads):
            try:
                if t.is_alive():
                    t.join(timeout=0.5)
            except Exception:
                pass
        self._threads = []

    # ---- setup UX（官方 setup 面均由 provider 目录内文件驱动） ----

    def get_status_config(self, provider_config=None):
        """喂 hermes memory status：引擎/登录/库量（来自缓存探测）。

        Hermes 的 CLI 以 ``provider.get_status_config(provider_config)`` 调用本方法
        （见 hermes_cli/memory_setup.py），因此必须接受一个位置参数；
        只定义 ``self`` 会让 ``hermes memory status`` 报
        ``takes 1 positional argument but 2 were given``。

        ``provider_config`` 是 ``config.yaml`` 里 ``memory.respire`` 的原始 dict；
        CLI 在 ``initialize()`` 之前调用本方法，此时用 ``rsrs_path`` 重新探测引擎，
        否则状态页会误显示 engine 为空（Status 本身仍由 is_available 判定）。
        """
        cfg = dict(self._cfg)
        if isinstance(provider_config, dict):
            if isinstance(provider_config.get("rsrs_path"), str):
                cfg["rsrs"] = provider_config["rsrs_path"]
            for key, ref in DEFAULTS.items():
                if key in provider_config and _typed(provider_config[key], ref):
                    cfg[key] = provider_config[key]
        engine = self._engine or find_rsrs(cfg)
        probe = self._probe or (None, None, None)
        return {
            "engine": engine or "",
            "logged_in": probe[0],
            "entries": probe[1],
            "probe_ok": probe[2],
            "turns": self._turns,
        }

    def post_setup(self, hermes_home, config) -> None:
        """hermes memory setup 后置：引擎检查＋注册/登录/模型引导（非交互打印）。"""
        engine = self._engine or find_rsrs(self._cfg)
        if not engine:
            print(NO_RSRS)
            return
        logged, total, ok = probe_status(engine)
        if not ok:
            print(PROBE_FAIL)
        elif logged is False:
            print(LOGIN_NUDGE)
        elif total is not None and total < 10:
            print(EMPTY_NUDGE)
        else:
            print(f"respire 就绪：引擎 {engine}；库量 {total}。"
                  "配置文件 $HERMES_HOME/respire.json（可选字段见插件 README）。")

    # ---- 内部 ----

    def _data_dir(self) -> str:
        base = self._hermes_home or os.path.dirname(os.path.abspath(__file__))
        d = os.path.join(base, PROVIDER_NAME)
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            d = base
        return d

    def _log(self, event: str, outcome: str, detail: str = ""):
        """轻量留痕：plugin.log 单行 JSONL、超 256KB 整文件清轮；日志失败绝不影响主链。"""
        if not self._cfg.get("log", True):
            return
        try:
            path = os.environ.get("RESPITE_LOG") or os.path.join(self._data_dir(), "plugin.log")
            try:
                if os.path.exists(path) and os.path.getsize(path) > LOG_MAX:
                    os.remove(path)
            except Exception:
                pass
            rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "ev": event, "out": outcome}
            if detail:
                rec["d"] = redact(detail)[:160]
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _load_config(self) -> dict:
        cfg = dict(DEFAULTS)
        candidates = []
        if self._hermes_home:
            candidates.append(os.path.join(self._hermes_home, f"{PROVIDER_NAME}.json"))
        candidates += [os.environ.get("RESPITE_CONFIG") or "",
                       os.path.join(os.path.dirname(os.path.abspath(__file__)), "respire.config.json")]
        for path in candidates:
            if not path:
                continue
            doc = read_json_or_empty(path)
            for key, ref in DEFAULTS.items():
                if key in doc and _typed(doc[key], ref):
                    cfg[key] = doc[key]
            if doc:
                break
        return cfg

    def _spawn(self, target):
        """后台线程统一走 spawn_context_thread（contextvars 剖面随行；契约禁裸 threading.Thread）。"""
        try:
            t = spawn_context_thread(target, name=f"respire-{getattr(target, '__name__', 'bg')}")
            t.start()
            with self._lock:
                self._threads.append(t)
                self._threads = self._threads[-8:]  # 只留最近几个供 shutdown join
            return t
        except Exception as e:
            logger.warning("spawn failed: %s", e)
            return None

    def _probe_async(self):
        logged, total, ok = probe_status(self._engine) if self._engine else (None, None, False)
        with self._lock:
            self._probe = (logged, total, ok)
            self._probe_at = time.time()

    def _refresh_last_store(self):
        try:
            engine = self._engine or find_rsrs(self._cfg)
            if not engine:
                return
            ts = engine_last_store(engine)
            if ts is not None:
                with self._lock:
                    self._last_store_ts = ts
        except Exception as e:
            logger.warning("refresh_last_store failed: %s", e)


def _typed(val, ref) -> bool:
    if isinstance(ref, bool):
        return isinstance(val, bool)
    if isinstance(ref, (int, float)):
        return isinstance(val, (int, float)) and not isinstance(val, bool)
    return isinstance(val, type(ref))


SKILLS_DIR = Path(__file__).parent / "skills"


def register(ctx) -> None:
    """记忆插件发现系统入口。契约：每次构造新实例（禁模块级单例——multiplexed gateway 每剖面一实例）。"""
    ctx.register_memory_provider(RespireMemoryProvider())
    try:
        ctx.register_skill(
            "respire",
            SKILLS_DIR / "respire" / "SKILL.md",
            "respire (rsrs) 本地加密记忆操作法：召回/判后即存/因果树整理",
        )
    except Exception as e:  # 技能注册失败不拦 provider
        logger.warning("register_skill failed: %s", e)
