# Respire for Hermes Agent

Respire adds a locally encrypted cross-session memory store to Hermes. The plugin connects to the running Respire Runtime over loopback HTTP RPC. The host CLI installs, initializes, and starts the Runtime.

## Install

1. Install a compatible Respire CLI and Runtime, unlock the intended account, and start the host service. An unmanaged host can use `rsrs runtime start`. The plugin requires scoped RPC protocol 1 and operation receipts, as provided by Runtime 2.0.0-dev.27.
2. Install the release package into `$HERMES_HOME/plugins/respire`, with `plugin.yaml` at the plugin root. From a checkout, run `bash scripts/install.sh`.
3. Set `memory.provider: respire` in Hermes config and enable `respire` if the deployment uses a plugin enable list. You can also select the provider via `hermes memory setup`. Restart Hermes.
4. Run `hermes respire status`. The plugin connects to `127.0.0.1:15169`. To use a different port, set `RSRS_RPC_PORT` in both the Runtime environment and the Hermes environment.

Hermes Agent v0.23.2 or newer is required for the pre-inference storage hook. See the [Runtime setup](https://docs.rsrs.rs/runtime) and [RPC protocol](https://docs.rsrs.rs/runtime-rpc).

## Memory hooks

- Before inference, the provider recalls relevant entries and loads selected bodies into the same turn. If a hit has been superseded, the provider tries its replacement. Start a message with `/nomem` or `#nomem` to skip automatic memory for that turn.
- `remember:`, `store:`, `记：`, and related prefixes route to the storage hook before model inference. The hook reads the Runtime rules, reviews candidates, validates placement, and reads back a successful write. Example: `remember: [parent=0123abcd] The integration uses the local Runtime endpoint.` Use a relevant parent ID from the current account. An unresolved parent, update, or merge decision produces a pending result.
- After a primary turn with tool evidence, the provider reviews a short process record for diary storage. User text and the reported outcome pass through Hermes redaction and the plugin credential check.
- `respire_recall`, `respire_show`, and `respire_remember` remain available for targeted access. The write tool shares the storage hook's review and read-back path.

Each RPC uses the Runtime's account, instance, and generation. A write carries a unique operation ID and idempotency key. If the response is lost, the plugin queries the original operation receipt. An unresolved result is reported as unknown.

## Configuration and logs

Copy `respire.config.example.json` to `$HERMES_HOME/respire.json` to override defaults. `enabled` controls plugin memory operations, `auto_recall` controls prefetch, and `skip_prefixes` excludes internal prompts. `recall_limit`, `recall_threshold`, and `budget_chars` control the injected context. `rsrs` is an optional host CLI path displayed by setup and status.

With `log` enabled, the plugin logs outcomes to `$HERMES_HOME/respire/plugin.log` or `RESPITE_LOG`. A prefetch hit means Hermes received recalled context. A verified write means ID, title, and full body matched the read-back. Logs omit prompts and memory bodies.

## Verify

```sh
python scripts/validate_plugin.py
python tests/test_provider.py
python tests/test_ci.py
python -m unittest discover -s tests -p test_release.py -v
```

Local tests use scoped RPC fixtures with real network and subprocess calls disabled. For end-to-end verification, use a separate Runtime port and data directory and run an actual Hermes conversation against a synthetic memory library. GitHub Actions packages the exact release tag and publishes its checksum.
