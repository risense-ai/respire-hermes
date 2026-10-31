# Changelog

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
