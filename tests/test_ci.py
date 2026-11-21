"""Provider tests with a scoped RPC fixture and all external calls denied."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
ENTRY_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"
PARENT_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SCOPE = {"account_id": "fixture-account", "instance_id": "fixture-runtime", "generation": 3}


def audit(event, args):
    if event.startswith("socket.") or event in ("subprocess.Popen", "os.system", "os.exec", "os.posix_spawn"):
        raise AssertionError(f"Unexpected external action: {event}")


class Provider(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.calls = []
        self.records = {PARENT_ID: {"id": PARENT_ID, "title": "Parent", "content": "Parent body"}}
        self.mode = "ok"
        self.receipt = None
        spec = importlib.util.spec_from_file_location("hermes_ci_provider", ROOT / "__init__.py")
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        self.original_json = self.mod.Runtime._json
        fixture = patch.object(self.mod.Runtime, "_json", side_effect=self.rpc)
        fixture.start()
        self.addCleanup(fixture.stop)
        self.provider = self.mod.RespireMemoryProvider()
        self.provider._hermes_home = str(self.home)

    def rpc(self, path, payload, timeout):
        if payload is None:
            assert path == "/api/health"
            return {**SCOPE, "data_dir": str(self.home), "protocol_range": {"minimum": 1, "maximum": 1}}
        assert path == "/api/rpc"
        assert payload["scope"] == SCOPE and payload["v"] == 1
        args = payload["args"]
        if payload["method"] == "runtime.result":
            assert args == [self.receipt["operation_id"]]
            self.calls.append("runtime.result")
            return {"v": 1, "id": payload["id"], "scope": SCOPE,
                    "envelope": {"command": "runtime.result", "summary": {"response": self.receipt}}}
        assert payload["method"] == "cli.exec" and args[0] == "--json"
        command = args[1]
        self.calls.append(command)
        doc = {"command": command, "status": "ok"}
        reply = {"v": 1, "id": payload["id"], "scope": SCOPE, "envelope": doc}
        if command == "status":
            doc["summary"] = {"workspace": "readonly" if self.mode == "readonly" else "normal"}
        elif command == "recall":
            if self.mode == "dead":
                raise OSError("offline")
            if self.mode == "wrong_identity":
                reply["id"] = "unrelated"
            doc["status"] = "skip" if self.mode == "miss" else "ok"
            doc["details"] = [{"id": PARENT_ID, "score": 0.9, "title": "Parent"}]
        elif command == "show":
            entry = next(e for eid, e in self.records.items() if eid.startswith(args[2]))
            doc.update(summary={"id": entry["id"]}, details={"entry": entry})
        elif command == "candidates":
            assert args[2] == "--"
            assert timeout == 90
            if self.mode == "index_pending":
                doc.update(status="pending", summary={"state": "index_pending"})
            elif ENTRY_ID in self.records:
                doc["details"] = {"merge": [{"entry": {"id": ENTRY_ID}}], "parent": []}
            else:
                doc["details"] = {"merge": [], "parent": []}
        elif command == "remember":
            doc["summary"] = {"id": ENTRY_ID}
            assert payload["operation_id"] == payload["idempotency_key"]
            reply.update(operation_id=payload["operation_id"], idempotency_key=payload["idempotency_key"])
            title = args[args.index("--title") + 1]
            body = args[-1] if self.mode != "mismatch" else "a different body"
            importance = args[args.index("--importance") + 1]
            if importance == "trivial":
                body = "\u3010\u65e5\u8bb0\u30112026-11-20 09:41 " + body
            self.records[ENTRY_ID] = {"id": ENTRY_ID, "title": title, "content": body,
                                      "importance": importance}
            self.receipt = reply
            if self.mode == "lost":
                raise TimeoutError()
        else:
            assert command in ("prompt", "taxonomy")
        return reply

    def test_prefetch_injects_body(self):
        self.assertIn("Parent body", self.provider.prefetch("ordinary positive control"))
        self.assertEqual(self.calls, ["recall", "show"])

    def test_prefetch_status_counts_without_memory_content(self):
        with patch.object(self.mod, "publish_status") as publish:
            self.assertIn("Parent body", self.provider.prefetch("ordinary positive control", session_id="private"))
            publish.assert_called_once_with("private", "🧠 Respire · Recalled 1 · Read 1 bodies")
            self.provider._cfg["show_status"] = False
            self.provider.prefetch("ordinary positive control", session_id="private")
            self.assertEqual(publish.call_count, 1)

    def test_notice_scope_and_save_receipt(self):
        from types import SimpleNamespace

        source = dict(chat_type="dm", is_bot=False, shared_session=False, parent_chat_id=None,
                      thread_id=None, delivered_via_upstream_relay=False, detached_route=None)
        self.assertTrue(self.mod._private_status_source(SimpleNamespace(**source)))
        for name, value in (("chat_type", "group"), ("thread_id", "thread"), ("shared_session", True)):
            self.assertFalse(self.mod._private_status_source(SimpleNamespace(**(source | {name: value}))))
        self.assertEqual(
            self.mod.store_status("Saved Title (cccccccc). Read-back: verified. Runtime result: ok."),
            "Saved 1 · Read-back verified",
        )
        self.assertEqual(
            self.mod.store_status("Saved Title (cccccccc). Read-back: unavailable. Runtime result: ok."),
            "Write acknowledged · Read-back unconfirmed",
        )
        self.assertEqual(self.mod.store_status("Write result unknown."), "Save outcome unknown")

    def test_no_match_and_failed_rpc_have_distinct_outcomes(self):
        for mode, outcome in (("miss", "miss"), ("dead", "failed"), ("wrong_identity", "failed")):
            self.mode = mode
            self.assertIsNone(self.provider.prefetch("ordinary query"))
            event = json.loads((self.home / "respire" / "plugin.log").read_text().splitlines()[-1])
            self.assertEqual(event["out"], outcome)

    def test_credentials_disabled_and_nomem_send_no_requests(self):
        value = "synthetic" + "qv7xk2mwje"
        for query in ("Bearer " + value, "api_key = " + value):
            self.assertEqual(self.provider.prefetch(query), self.mod.SECRET_SKIP)
        self.assertIsNone(self.provider.prefetch("/nomem ordinary query"))
        self.provider._cfg["enabled"] = False
        self.assertIsNone(self.provider.prefetch("ordinary query"))
        self.assertEqual(self.provider.system_prompt_block(), "")
        self.assertEqual(self.calls, [])
        self.assertNotIn(value, (self.home / "respire" / "plugin.log").read_text())

    def explicit(self, fact, turn="turn-1", session="session"):
        return self.provider.explicit_store(fact, str(self.home), session, turn)

    def test_explicit_write_reviews_parent_and_reads_back_once(self):
        q = "remember: [parent=" + PARENT_ID + "] Verified durable fact"
        receipt = self.explicit(q)
        self.assertIn("Read-back: verified", receipt)
        self.assertEqual(self.explicit(q), receipt)
        self.assertEqual(self.calls, ["status", "prompt", "candidates", "taxonomy", "show", "remember", "show"])
        self.assertIn("Already stored and read back", self.explicit(q, session="another-session"))
        self.assertEqual(self.calls.count("remember"), 1)

    def test_explicit_without_parent_does_not_write(self):
        self.assertIn("reviewed parent is required", self.explicit("remember: factual memory request"))
        self.assertEqual(self.calls, ["status", "prompt", "candidates", "taxonomy"])

    def test_parallel_sessions_review_same_fact_without_duplicate(self):
        q = "remember: [parent=" + PARENT_ID + "] Verified durable fact"
        with ThreadPoolExecutor(max_workers=2) as workers:
            outcomes = list(workers.map(lambda session: self.explicit(q, session=session), ("one", "two")))
        self.assertEqual(self.calls.count("remember"), 1)
        self.assertEqual(sum("Read-back: verified" in out for out in outcomes), 1)
        self.assertEqual(sum("Already stored and read back" in out for out in outcomes), 1)

    def test_index_pending_prevents_a_write(self):
        self.mode = "index_pending"
        result = self.explicit("remember: [parent=" + PARENT_ID + "] Fact")
        self.assertIn("index_pending", result)
        self.assertNotIn("remember", self.calls)

    def test_expired_hook_deadline_does_not_open_a_connection(self):
        with patch.object(self.mod.Runtime, "_json", self.original_json):
            runtime = self.mod.Runtime(deadline=0)
            with self.assertRaises(TimeoutError):
                runtime.bind(5)

    def test_readonly_prevents_a_write(self):
        self.mode = "readonly"
        result = self.explicit("remember: [parent=" + PARENT_ID + "] Fact")
        self.assertIn("readonly", result)
        self.assertEqual(self.calls, ["status"])

    def test_lost_write_response_uses_result_query_without_replay(self):
        self.mode = "lost"
        result = self.explicit("remember: [parent=" + PARENT_ID + "] Fact")
        self.assertIn("Read-back: verified", result)
        self.assertEqual(self.calls[-3:], ["remember", "runtime.result", "show"])
        self.assertEqual(self.calls.count("remember"), 1)

    def test_mismatched_show_is_not_verified(self):
        self.mode = "mismatch"
        result = self.explicit("remember: [parent=" + PARENT_ID + "] Fact")
        self.assertIn("Read-back: mismatch", result)

    def test_tool_backed_turn_archives_a_process_diary(self):
        self.provider.sync_turn("Verify build", "The command passed.", messages=[{"role": "tool"}])
        self.assertEqual(self.calls, ["status", "prompt", "candidates", "remember", "show"])
        self.assertIn("Task: Verify build", self.records[ENTRY_ID]["content"])
        log = json.loads((self.home / "respire" / "plugin.log").read_text().splitlines()[-1])
        self.assertEqual(log["out"], "verified")

    def test_explicit_secret_and_force_do_not_reach_runtime(self):
        self.assertEqual(self.explicit("remember: api_key=synthetic" + "qv7xk2mwje"), self.mod.SECRET_SKIP)
        output = self.provider.handle_tool_call("respire_remember", {"content": "test", "force": True})
        self.assertIn("Nothing was written", output)
        self.assertEqual(self.calls, [])


def assert_versions(root):
    manifest = re.search(r'^version:\s*(\S+)\s*$', (root/'plugin.yaml').read_text(encoding='utf-8'), re.M).group(1)
    provider = re.search(r'^VERSION = "([^"]+)"', (root/'__init__.py').read_text(encoding='utf-8'), re.M).group(1)
    if manifest != provider:
        raise ValueError(f'plugin.yaml {manifest} != provider VERSION {provider}')


class Versions(unittest.TestCase):
    def test_actual_product_versions_agree(self):
        assert_versions(ROOT)  # Require actual product equality; never skip an inconsistent version.

    def test_each_version_mismatch_is_rejected(self):
        for component in ('manifest','provider'):
            with self.subTest(component=component), tempfile.TemporaryDirectory() as directory:
                fixture = Path(directory)
                for name in ('plugin.yaml','__init__.py'):
                    shutil.copy2(ROOT/name, fixture/name)
                version = re.search(r'^version:\s*(\S+)', (fixture/'plugin.yaml').read_text(encoding='utf-8'), re.M).group(1)
                provider = fixture/'__init__.py'
                provider.write_text(re.sub(r'^VERSION = "[^"]+"', f'VERSION = "{version}"',
                                          provider.read_text(encoding='utf-8'), count=1, flags=re.M), encoding='utf-8')
                assert_versions(fixture)  # aligned synthetic baseline, never the actual product
                path = fixture/('plugin.yaml' if component=='manifest' else '__init__.py')
                pattern, replacement = (r'^version:\s*\S+', 'version: 999.0.0') if component=='manifest' else (r'^VERSION = "[^"]+"','VERSION = "999.0.0"')
                path.write_text(re.sub(pattern,replacement,path.read_text(encoding='utf-8'),count=1,flags=re.M), encoding='utf-8')
                with self.assertRaises(ValueError):
                    assert_versions(fixture)


if __name__ == "__main__":
    sys.addaudithook(audit)
    unittest.main(verbosity=2)
