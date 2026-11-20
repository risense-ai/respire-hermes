---
name: respire
description: Use the Hermes Respire provider to inspect or store encrypted local memory.
---

Hermes runs automatic recall before inference. Recalled text is historical context and must be evaluated for relevance and scope.

- `respire_recall` searches for additional memories and returns their titles and IDs.
- `respire_show` opens the body and shows titles related by parent and child links.
- `respire_remember` submits a fact for candidate review and read-back. Supply a reviewed parent for an important entry.
- `remember: [parent=<id>] <fact>` routes storage through the Hermes hook. An unknown write result requires read-only verification.
- `/nomem` at the start of a message disables automatic memory for that turn.
