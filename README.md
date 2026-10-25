# respire for Hermes Agent

[respire](https://rsrs.rs)（`rsrs`）本地优先加密记忆系统 的 Hermes Agent **memory provider 插件**。装上后 Hermes 每轮对话自动召回相关记忆标题、提供判后即存的落库工具，与本机其他 agent（ZCode / Kimi Code / Claude Code 等）共享同一座记忆库。

- 每轮 `prefetch`：按当轮 query 自动召回标题档（分数＋短 id＋标题，预算内截断），正文按需 `respire_show`
- `system_prompt_block`：注入记忆用法简则＋引擎状态引导（未装引擎/未登录/空库/缺语义模型时才出对应段，否则零打扰）
- 三个工具：`respire_recall` / `respire_show` / `respire_remember`（判后即存：相似会给候选，挂靠或直写由模型决断）
- 消息以「记：」「存：」「remember:」开头 → provider 直接落库保底并回执；`/nomem` 前缀关闭当轮自动召回
- 每轮结束检查近窗落库，闲聊零打扰、干完活没存才催
- fail-open：引擎缺失/超时/解析失败一律降级为空注入，绝不阻断宿主回合

## Overview (English)

Respire (`rsrs`) is a local-first, encrypted memory system. This repository is its **memory-provider plugin for [Hermes Agent](https://hermes-agent.nousresearch.com/)**. It is a directory plugin: `__init__.py` implements `MemoryProvider`, `plugin.yaml` is the manifest, and `cli.py` adds `hermes respire ...` commands.

- **Per-turn recall** — `prefetch()` asks the local `rsrs` engine for title-tier hits (score, short id, title) and injects a budgeted block. Full text is read on demand with `respire_show`.
- **Judge-then-store** — `respire_remember` returns candidates when a similar entry exists; the model decides to attach (`parent`) or force-write. Messages starting with `记：` / `存：` / `remember:` are stored as a fallback and acknowledged.
- **Status-aware prompt** — `system_prompt_block()` adds short usage rules and only surfaces engine/login/empty-library/model guidance when that state applies.
- **Shared library** — one user-level `rsrs` library is shared across Hermes profiles and other agents on the machine by design.
- **Fail-open** — every hook and tool catches errors, uses timeouts, and degrades to an empty injection rather than blocking the host turn.
- **No Python dependencies** — the engine is a separately installed Node.js sidecar (`rsrs-cli@1.0.16`, with a native platform package from the same publisher). The plugin directory has no `pyproject.toml` or `python_dependencies`.

Compatibility: **Hermes Agent >= 0.22.5**, Linux and macOS (Apple Silicon; the rsrs CLI does not support Intel macOS). Hermes ships its own Python; the plugin itself is Python 3.9+ compatible.

### Install (English)

1. Install and register the engine (official npm package; the agent may propose this command, but it must go through Hermes's normal approval flow):
   ```bash
   npm i -g rsrs-cli@1.0.16
   rsrs register --user <your-name>   # register at https://rsrs.rs; save the generated A3- recovery code
   rsrs model install && rsrs model activate m3   # optional semantic recall
   ```
   The engine is external and is not bundled or downloaded by the plugin. `rsrs-cli@1.0.16` is published by the same vendor; npm verifies the tarball integrity (`npm view rsrs-cli@1.0.16 dist.integrity` → `sha512-jm4j338f1yfvd+QSv5Yy+1IlAxOjrvKmKAvVcsLihK0MGpqxSBql4EuaobTebYIK5VTVqo1CuR1DGmlpanrwFQ==`). Pin the version; do not use `@latest`.
2. Install the plugin. After this catalog entry is merged: `hermes plugins install respire`. From a source checkout: `bash scripts/install.sh`.
3. Activate: `hermes memory setup` and choose `respire` (leave the engine path empty for auto-detection), or set `memory.provider: respire` and restart Hermes.
4. Verify: `hermes respire status`.

### Disclosures (read before installing)

- **Shell/sidecar**: the plugin spawns the `rsrs` CLI with fixed arguments (`recall`, `show`, `remember`, `status`, `list`) using `subprocess.run(..., shell=False)` and 6–30 s timeouts. It does not run a shell and does not spawn Hermes. Only a minimal environment (HOME/PATH/TMPDIR, locale, proxy/TLS, and non-secret `RSRS_*`/`RESPIRE_*` settings) is passed to the sidecar; the full Hermes environment is not inherited.
- **Background work**: `initialize()` starts one async status probe; `sync_turn()` starts a daemon thread (via Hermes `spawn_context_thread`) to refresh the last-store timestamp. Threads are non-blocking, bounded to the most recent 8, and joined briefly on `shutdown()`. The `rsrs` CLI may keep its own local runtime process resident between calls; the plugin only waits for each command and does not manage that runtime.
- **Network**: the plugin makes no HTTP requests itself. The `rsrs` sidecar may contact `rsrs.rs` (and the mirror configured in `rsrs`) for registration, login, sync, semantic-model download, and update checks. Those calls are made by the user-installed engine, not by plugin Python code.
- **Data outside the plugin directory**: the plugin writes `$HERMES_HOME/respire.json` (config) and `$HERMES_HOME/respire/plugin.log` (local JSONL diagnostics). It reads the user-level `rsrs` library (default `~/.rsrs`, shared across Hermes profiles and other agents) and checks `~/.rsrs/models/bge-m3` to detect the optional semantic model. It does not read Hermes's memory files, `.env`, `auth.json`, keychains, or browser profiles.
- **Credentials**: the plugin never receives or stores `rsrs` credentials. Sign-in, token storage, and refresh belong to the `rsrs` CLI; the plugin only invokes the CLI. It does not read token files or credential stores. Secret-looking `RSRS_*`/`RESPIRE_*` variables (for example `RSRS_TOKEN` or `RSRS_SUPER`) are intentionally not forwarded to the sidecar; use `rsrs login` so the CLI owns the session.
- **Writes**: `respire_remember`, the `remember` tool, and `记:`-prefixed messages write to the `rsrs` library. `prefetch` is read-only. `sync_turn` does not archive the transcript; it only checks the latest `rsrs` write time to decide whether to show a store nudge. Automatic writes are skipped for `agent_context` other than `primary` (subagent/cron).
- **No telemetry, no self-update**: the plugin does not report usage and does not fetch or replace its own files. Updates arrive through a catalog SHA-bump PR and `hermes plugins update respire`.
- **Local log**: event names and redacted query snippets only; password-like assignments, token prefixes, and 11–19 digit numbers are redacted. The log rotates at 256 KB.
- **Unattended runs**: hooks are non-blocking and fail-open. Setup guidance is printed; no hook waits for a person or opens a browser.

---

## 中文文档

## 形态与契约（为什么是 wrapper 式目录 provider）

按官方 [Memory Provider Plugins](https://hermes-agent.nousresearch.com/docs/developer-guide/memory-provider-plugin) 契约实现：

- **目录插件**：`$HERMES_HOME/plugins/respire/`，`__init__.py`（`RespireMemoryProvider` + `register(ctx)`）、`plugin.yaml`、`cli.py`
- **wrapper 式零 Python 依赖面**：引擎是 npm 装的 `rsrs` CLI sidecar（subprocess 调用），插件根无 `pyproject.toml` / `pip_dependencies` → 享受 `HERMES_HOME` 生存契约（`hermes update` 不动本目录），不进宿主依赖联合
- **profile 隔离**：一切路径从 `initialize(hermes_home=...)` 派生；`register()` 每次构造新实例（multiplexed gateway 每 profile 一实例）；日志写 `$HERMES_HOME/respire/plugin.log`
- **线程契约**：`sync_turn` 等后台工作全部走 `spawn_context_thread`（daemon），生命周期钩子绝不阻塞
- **注意**：respire 库本身是**用户级**（一人一库、跨 agent 共享）——多个 Hermes profile 共用同一座库，这与 Hermes「按 profile 隔离存储」的默认不同，是 respire 的产品设计
- 单 provider 规则：Hermes 同一时间只允许一个外部 memory provider 激活

## 安装

前置：Python 3.9+（Hermes 自带）、Node.js（引擎 sidecar 用）。

```bash
# 1) 引擎（已装可跳过；装完 rsrs --version 验证）
npm i -g rsrs-cli@1.0.16
rsrs register --user <你的名字>   # 首设备注册，去 https://rsrs.rs；超级密码用系统生成的 A3- 恢复码并抄下
# 语义召回（可选）：rsrs model install && rsrs model activate m3

# 2) 插件（二选一）
bash scripts/install.sh                 # 自动复制到 $HERMES_HOME/plugins/respire/
# 或手动：cp -R 本目录（去掉 .git/dist）到 ~/.hermes/plugins/respire/

# 3) 激活
hermes memory setup                     # 向导里选 respire（只问一项：rsrs 路径，留空自动探测）
# 或直接在 config 里设 memory.provider: respire，然后重启 Hermes
```

装好可用 `hermes respire status` 验证（子命令仅在本 provider 激活时出现）。

## 配置

向导极简（官方建议）：只提示 `rsrs_path`；其余可选项手改 **`$HERMES_HOME/respire.json`**（参考 `respire.config.example.json`）：

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `rsrs` | `""` | rsrs 引擎路径；空=自动探测（PATH → `~/.local/bin` → `~/.rsrs/bin` → …） |
| `recall_threshold` | `0.0` | 召回分数下限；0=rsrs 直出（自带无关 skip 门） |
| `recall_limit` | `5` | prefetch 取前 N 条 |
| `min_prompt_chars` | `2` | 过短 query 不检索 |
| `session_rules` | `true` | 是否注入用法简则 |
| `budget_chars` | `1500` | 单轮 prefetch 注入预算（宿主安全上限 100k） |
| `enabled` | `true` | 总开关：false=全线静默 |
| `auto_recall` | `true` | prefetch 自动召回开关（false=只留工具） |
| `store_nudge_every` | `1` | 存储催促节流：1=每轮、N=每 N 轮、0=关 |
| `skip_prefixes` | `[]` | query 前缀白名单：命中不召回不落库 |
| `log` | `true` | plugin.log 留痕 |

环境变量：`RESPIRE_BIN` / `RESPITE_RSRS`（引擎路径）、`RESPITE_CONFIG`（配置文件路径）、`RESPITE_LOG`（日志路径）。

## 工具

| 工具 | 参数 | 说明 |
| --- | --- | --- |
| `respire_recall` | `query`（必填）、`limit`（默认 8，≤20） | 语义召回标题档 |
| `respire_show` | `id`（短 id 前 8 位即可） | 读全文＋因果链上/下游 |
| `respire_remember` | `content`（必填）、`title`、`importance`、`parent`、`force` | 判后即存；`pending`=有候选待决断 |

另注册只读技能 `respire:respire`（记忆操作法全文）。

## 已核实与未核实

- 契约逐条取自官方 memory-provider-plugin 文档（Hermes v0.22.5 基线）：ABC 必选/可选方法、`register(ctx)`、`plugin.yaml`、线程契约、配置边车读写、CLI 约定、wrapper 生存契约
- **真机 E2E（2026-10-25）**：在 Hermes main（v0.22.5 基线）上跑通 `MemoryManager` 全链（`add_provider` → `initialize_all` → `get_tool_schemas` → `system_prompt_block` → `prefetch` → `handle_tool_call` → `sync_all` → `shutdown_all`）；临时 `HERMES_HOME` 下 `hermes memory status` 显示 `respire` 为 active/available，`hermes respire status` 正常。测试引擎为 npm 稳定版 `rsrs-cli@1.0.16`
- `hermes plugins validate`（Hermes main）通过：manifest、`requires_hermes`、capability probe、安全扫描、no-core-override、isolation 全绿
- `get_tool_schemas()` 返回 OpenAI function 包装格式；Hermes 的 `normalize_tool_schema` 同时接受该包装与扁平格式
- `config_schema.py`（桌面端配置面板文件）格式官方文档未公开，暂未随包；CLI setup wizard 与 `respire.json` 配置可用
- 写探针 `--store` 未在默认自测中执行（会写用户真库）；需要时手动跑：`python3 tests/test_provider.py --store`

## 开发

```bash
python3 scripts/validate_plugin.py    # 结构/清单/可编译/fail-open/敏感扫描
python3 tests/test_provider.py        # 真 rsrs 引擎回环（写探针自清理）；无引擎环境自动跳过引擎段
bash scripts/package.sh               # dist/respire-<ver>-<date>.zip + sha256（zip 根=插件根）
```
