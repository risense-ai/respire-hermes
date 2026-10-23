---
name: respire
description: respire（rsrs）本地加密记忆操作法（Hermes memory provider 版）。每轮自动召回已随 system prompt 注入；未见注入、命中不贴题、或涉旧（项目、报错、约定、日期）都须换词补查（同义词/上位词/英文词分路试）；工具 respire_recall 翻忆、respire_remember 判后即存、respire_show 读全文；消息以「记：」开头由本层直接落库，「/nomem」前缀关闭本轮召回。触发词：记忆、翻忆、recall、remember、存记忆、忘掉、同步。
---

# respire · 记忆操作法（Hermes）

本 provider 行为：system prompt 注简则＋状态引导；每轮 prefetch 自动召回（rsrs 自带无关过滤（零命中即 skip），本层再按阈值兜底）；消息以「记：」「存：」「remember:」开头由本层先落库保底并回执——**回执带暂拟标题与短 id 时，你须从事实抽取「主语＋动作＋结果」完整句替换标题（rsrs retitle <短id> --title，硬约束，title 是召回与判重的门面）**；`/nomem` 关本轮；每轮结束若近 10 分钟库无落库且本轮有新事实，下轮注入催存提示。

## 工具与命令

- `respire_recall(query, limit?)` —— 答前必查（注入为空绝不等于无记忆，须自行补查）；报错先按报错原文翻忆。等价 shell：`rsrs recall "项目名+关键词" --limit 5 --json --titles`
- `respire_remember(content, title?, importance?, parent?, force?)` —— 一事一条，先判后存；`pending` 回执=判为相似未写入，`parent` 挂靠或 `force` 直写
- `respire_show(id)` —— 短 id 前 8 位即可读全文（含因果链上下文）；`rsrs chain` 一次取整条因果链
- shell 直通：`rsrs forget <id>`（墓碑删除）、`rsrs candidates`（存前候选账）、`rsrs sync`（双向同步，autosync 常开）、`rsrs status`、`rsrs prompt`（完整工作流）、`rsrs tree/resort/retitle`（因果树整理）

## 解析要点（CLI 信封）

- 一切 rsrs 命令 `--json` 走统一信封：`status`、`summary`、`items`、`details`、`errors`；**失败也是 HTTP 200＋`status:"fail"`＋`errors[]`**，须查 status 不查退出码
- recall 的分数与条目在 `details[].score`、`details[].id`（全 id，取前 8 位当短 id）、`details[].title`、`details[].superseded_by`
- 进度写 stderr，勿 `2>&1` 混进 JSON
- remember 判后即存：`status:"ok"`＝已写入（summary.id）；`status:"pending"`＋`written:false`＝判为相似**未写入**
- 默认 `trivial` 档进日记区、不进召回主区——事实类落库用 `importance="important"`

## 红线

- 口令、证件号、手机号、卡号、私钥禁入记忆正文与查询词，只写存放位置——库虽加密，密钥泄露即裸奔
- 召回分数是相关度不是可信度；存前判重，改＞并＞挂＞存
- 账号与同步配置不归本 provider 管：注册去 rsrs.rs，本机 `rsrs register`／`rsrs login`，provider 不代办
