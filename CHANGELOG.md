# Changelog

## 0.1.0 (2026-10-23)

- 首版：Hermes Agent memory provider 目录插件（wrapper 式，rsrs CLI sidecar）
- prefetch 每轮标题档自动召回（预算截断、阈值兜底、/nomem 跳过）
- system_prompt_block 简则＋引擎状态引导（未装/未登录/空库/缺模型分段、已就绪零打扰）
- 工具 respire_recall / respire_show / respire_remember（判后即存三态回执）
- 「记：/存：/remember:」前缀直落库（仅 primary 上下文）；sync_turn 后台刷近窗落库缓存供存储催促
- 配置边车 $HERMES_HOME/respire.json；向导极简（仅 rsrs_path）；hermes respire status|config|recall|install-engine 子命令
- 只读技能 respire:respire；plugin.log JSONL 留痕；fail-open 全链
