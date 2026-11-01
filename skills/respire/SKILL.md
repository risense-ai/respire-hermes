---
name: respire
description: respire (rsrs) local encrypted memory playbook (Hermes memory provider edition). Automatic per-turn recall is injected with the system prompt; if no injection is present, the hits are off-topic, or the topic involves an earlier project, error, agreement, or date, recall again with different wording (synonyms, hypernyms, English equivalents, one route each); tools respire_recall to search, respire_remember to store after judging, respire_show to read full bodies; a message starting with "记：" is stored directly by this layer; the "/nomem" prefix disables recall for the turn. Triggers: memory, recall, remember, store memory, forget, sync, 记忆, 翻忆, 存记忆, 忘掉, 同步.
---

# respire · memory playbook (Hermes)

Provider behavior: the system prompt carries the brief rules plus status guidance; every turn prefetch auto-recalls (rsrs skips irrelevant queries itself — zero hits = skip; this layer also applies a threshold backstop); a message starting with "记：", "存：", or "remember:" is stored first by this layer as a fallback and returns a receipt — **when the receipt includes a provisional title and short id, extract a complete "subject + action + result" sentence from the facts and replace the title (`rsrs retitle <short-id> --title`; hard rule — the title is the facade for recall and dedup)**; `/nomem` disables recall for the turn; at the end of a turn with new facts and no store in the last 10 minutes, a store nudge rides the next injection.

## Tools and commands

- `respire_recall(query, limit?)` — recall before answering (an empty injection never means no memory; search manually); on errors, recall by the original error text first. Equivalent shell: `rsrs recall "project name + keywords" --limit 5 --json --titles`
- `respire_remember(content, title?, importance?, parent?, force?)` — one item per record, judge before storing; a `pending` receipt means a similar record was found and nothing was written — attach with `parent` or force with `force`
- `respire_show(id)` — the first 8 chars of the id suffice to read the full body (with causal-chain context); `rsrs chain` fetches the whole causal chain
- shell passthrough: `rsrs forget <id>` (tombstone delete), `rsrs candidates` (pre-store candidate list), `rsrs sync` (two-way sync, autosync on), `rsrs status`, `rsrs prompt` (full workflow, on demand), `rsrs tree/resort/retitle` (causal tree upkeep)

## Parsing notes (CLI envelope)

- Every rsrs command with `--json` uses one envelope: `status`, `summary`, `items`, `details`, `errors`; **failures also return HTTP 200 with `status:"fail"` and `errors[]`** — check status, not the exit code
- recall scores and entries live in `details[].score`, `details[].id` (full id; use the first 8 chars as the short id), `details[].title` and `details[].superseded_by`
- Progress goes to stderr; never mix it into JSON with `2>&1`
- remember stores after judgement: `status:"ok"` means written (`summary.id`); `status:"pending"` plus `written:false` means a similar record was found and **nothing was written**
- The default `trivial` tier goes to the diary area, excluded from recall — use `importance="important"` for factual records

## Red lines

- Never put passwords, ID numbers, phone numbers, card numbers, or private keys into memory bodies or query terms; record only where they are stored — the library is encrypted, but a leaked key exposes everything
- Recall scores are relevance, not confidence; check dedup before storing, prefer edit > merge > attach > new
- Account and sync configuration are outside this provider: run `rsrs register` / `rsrs login` on this machine (no website step); the provider does not handle it
