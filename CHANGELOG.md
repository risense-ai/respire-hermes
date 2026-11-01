# Changelog

## 0.1.4 — 复审二轮整改：容量清理/TTL 不撤销 unknown/in-flight 保护（PR #6 复核 P2）

### Fixed

- **容量清理只淘汰安全终态 written**（复核 P2 残留缺陷）：「记：」幂等表 `_prefix_stores` 超容（`_PREFIX_STORE_CAP`=200，提取为模块常量便于测试覆写）时不再 `clear()` 全清——改为只淘汰 `state=='written'` 条目（先清 TTL 已过期的，再按 ts 从旧到新）；`in-flight`/`unknown` 永不淘汰。淘汰后仍超容（理论上被未决项占满）→ **保守拒绝新的自动提交**：prefetch「记：」路径回固定回执（"memory idempotency table saturated with unresolved outcomes; store manually via rsrs and verify read-only"）、不发起 `do_store`——无界内存不增长，且未决结果的防重发保护不因容量清理失效（旧实现 clear() 曾让 unknown 项被重复提交、in-flight 预占被并发重穿）。
- **unknown 永不过期重发**：1800s TTL 收窄为模块常量 `_PREFIX_STORE_TTL` 且**只作用于 `written`**（过期=可安全再存）；`unknown` 状态无 TTL——重复 prefetch 永远走「只读核实、绝不重发」回执；`in-flight` 无 TTL（本就短时，完成后即迁移）。旧代码「unknown 超 30 分钟后自动重写」路径不复存在。

### Changed

- 版本 0.1.3 → 0.1.4（plugin.yaml 与 `__init__.py VERSION` 两处）。
- 测试：provider 套件新增 28 项复审二轮反例断言（150 → 178）：① unknown＋超容未决项＋再次触发→零第二次 remember；② in-flight＋容量清理＋并发重发→仍只一次 remember；③ unknown 远超 1800s（直接改内部 ts）仍拒重发＋written 过期可再存对照；④ 容量淘汰只清 written（先过期、再最旧）、unknown 在容量压力下存活、饱和后表规模有界。容量测试以 `mod._PREFIX_STORE_CAP=3` 覆写避免真造 200 键；负对照已在临时副本上验证（回植旧 clear()/TTL 全态两缺陷 → 17 项 FAIL）。

## 0.1.3 — 复审整改：原子幂等预占＋回执/回读加固（PR #6 复核意见 #1/#2）

### Fixed

- **幂等预占原子化（意见 #1）**：「记：」prefetch 显式快存改为调用 `do_store` **前**在锁内一步完成「检查＋登记 in-flight」——并发重复请求不可能同时通过检查；结果三态迁移：`written`（ok/warn）保持占用、重复触发回 already-submitted 回执；`unknown`（超时/坏回执/缺 id）**保持占用**——重复 prefetch 只回「前次结果未知，只读核实（rsrs list/show），绝不重发」，杜绝已提交但回执丢失后的自动重发；`pending`/`fail`/`skip`（明确未写）删除键、允许模型决断后再试。30 分钟窗与 200 条上限保留。
- **回执/回读加固（意见 #2）**：`summary` 字段类型防护＋id 白名单（`re.fullmatch(r"[0-9a-fA-F][0-9a-fA-F-]{7,}")`）——缺失/畸形 id 一律归 **unknown**（不再误入明确失败 `did not accept`）；明确拒绝收窄为白名单 **仅 `fail`**；新增 `skip` 分支（引擎按 readonly/paused 模式拒绝：未写也不是失败，文案「never force a write」）；其余陌生状态全归 unknown。写后回读改**全量核验**：`summary.id == 本条 id`、标题相等、完整正文相等（strip 规范化）——前 40 字相同不再宣称 verified；verified 文案如实标注 id/title/full content match。共享预算：remember 后剩余预算（`RESPITE_HOOK_BUDGET_S` 可覆盖，默认 20s−2.5s−已耗）<1.5s 时跳过回读与只读核实（unavailable），`_verify_recent_write` 受 `max_wait` 约束不再发预算外调用。

### Changed

- 版本 0.1.2 → 0.1.3（plugin.yaml 与 `__init__.py VERSION` 两处）。
- 测试：provider 套件新增 31 项复审反例断言（并发原子预占只发一次、unknown 后重复 prefetch 零引擎调用、pending/skip/fail 键解除后再走引擎、缺 id/畸形 id/坏 summary 归 unknown、回读错 id 与前 40 字同尾部篡改判 mismatch、坏回读 unavailable 且保留 written 态、紧预算慢坏回执零追加调用；119 → 150，`--store` 探针 155）。桩 `store_stub` 扩展 10 种回执模式并把调用日志/回放状态改为实例私有文件。

## 0.1.2 — 审计整改：漏催修复＋写结果状态机＋幂等＋retitle 指引（HM-01~HM-05）

### Fixed

- **HM-01 催促漏触发**：催促判定移出「召回命中」分支——零命中/新主题轮同样催；空库（`_last_store_ts=None`）同样视为近窗无落库（旧代码两处都静默漏催）。
- **HM-02 写结果状态机**：`warn`+有效 id = **已写入带警告**（不误报失败、不引导重发）；超时/坏回执归为 **unknown**——回执只允许只读核实（`rsrs list`/`rsrs show`）并明令禁止重发（旧文案「手动 rsrs remember 核实」有重复写风险）；ok/warn 后 `show` 写后回读核验（verified/mismatch/unavailable 如实标注）；pending 保留候选短 id 与引擎建议动作全量（改＞并＞挂＞存，§7.2）。
- **HM-03 retitle 指引纠正**：简则不再指示用 `respire_remember (retitle)` 改题（工具无此动作）——改为 shell `rsrs retitle <short-id> --title`，并明示不得靠新存复制改题。
- **HM-04 显式快存幂等**：同会话同内容的「记：」prefetch 只提交一次（30 分钟窗），重复触发回执说明已提交、绝不自动重发；pending/unknown 不锁键（仍可决断）。
- **HM-05 催促不吞任务**：STORE_NUDGE 明示「当前任务的附加项，先完成用户请求再做检查，绝不替代/推迟当前任务」。

### Changed

- 测试：provider 套件新增 22 项契约断言（状态机各态、幂等、零命中/空库催促、HM-03/05 文本契约；99 → 119）；--store 探针修正过时中文回执断言（英文化遗留）并兼容半/全角括号（124/0）。`RESPIRE_TEST_STORE_MODE` 桩模式经 sidecar 白名单环境注入。

## 0.1.1 (2026-10-31)
## 0.1.1 (2026-10-31)

- 模型可见文本全量英文化（dimcode 0.5.6 同款）：`RULES` / `NO_RSRS` / `LOGIN_NUDGE` / `EMPTY_NUDGE` / `NO_MODEL` / `PROBE_FAIL` / `STORE_NUDGE`、`format_recall` 召回头行、`do_store` 落库回执（stored / store pending / store failed）、`do_show` 条目块（entry / causal·upstream / causal·downstream）全部改英文；「记：」等中文前缀兼容不变。
- 新增输入侧疑似凭据扫描（`contains_secret`）：`prefetch` 命中即拦截该条 query（不召回也不落库），返回 `SECRET_SKIP` 提示——补齐与 codex/pi/dsh 侧一致的防线。
- 安装与注册口径对齐 CLI 现状：`rsrs-cli@latest`（原钉 1.0.16）；本机 `rsrs register` 直注、无网站步骤。
- **修复 Windows 下 sidecar 环境缺失键**：`sidecar_env()` 最小环境白名单偏 Unix，缺 `SystemRoot`/`USERPROFILE`/`APPDATA` 等，导致 Windows 上 rsrs runtime 初始化失败（crypto provider 报错）；按 `os.name == "nt"` 补齐必需键。
- 测试桩引擎由 bash 改为 python（Windows 可直接 exec，附 `.cmd` 包装）；断言随英文输出同步；新增疑似凭据两条。93 项全绿（基线在本机因上述环境问题仅 1 项可过）。

## 0.1.0 (2026-10-23)

- 首版：Hermes Agent memory provider 目录插件（wrapper 式，rsrs CLI sidecar）
- prefetch 每轮标题档自动召回（预算截断、阈值兜底、/nomem 跳过）
- system_prompt_block 简则＋引擎状态引导（未装/未登录/空库/缺模型分段、已就绪零打扰）
- 工具 respire_recall / respire_show / respire_remember（判后即存三态回执）
- 「记：/存：/remember:」前缀直落库（仅 primary 上下文）；sync_turn 后台刷近窗落库缓存供存储催促
- 配置边车 $HERMES_HOME/respire.json；向导极简（仅 rsrs_path）；hermes respire status|config|recall|install-engine 子命令
- 只读技能 respire:respire；plugin.log JSONL 留痕；fail-open 全链
