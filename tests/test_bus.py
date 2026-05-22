"""Tests for the core bus library. Stdlib only."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Make lib/ importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestBusCore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["AOE_BUS_ROOT"] = self.tmp
        # Force re-import with patched root
        for mod in list(sys.modules):
            if mod.startswith("lib"):
                del sys.modules[mod]
        from lib import bus  # noqa: E402
        self.bus = bus
        self.bus.BUS_ROOT = Path(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("AOE_BUS_ROOT", None)

    def test_ensure_bus_root_creates_layout(self):
        self.bus.ensure_bus_root()
        self.assertTrue((Path(self.tmp) / "sessions").is_dir())
        self.assertTrue((Path(self.tmp) / "threads").is_dir())
        self.assertTrue((Path(self.tmp) / "audit.log").exists())
        self.assertTrue((Path(self.tmp) / "human-inbox.md").exists())

    def test_ensure_bus_root_is_idempotent(self):
        self.bus.ensure_bus_root()
        self.bus.ensure_bus_root()  # second call must not error
        self.assertTrue((Path(self.tmp) / "sessions").is_dir())

    def test_detect_self_without_env_returns_none(self):
        os.environ.pop("AOE_INSTANCE_ID", None)
        self.assertIsNone(self.bus.detect_self())

    def test_detect_self_with_env_uses_short_id_as_fallback_label(self):
        os.environ["AOE_INSTANCE_ID"] = "deadbeef1234567890"
        # No sessions.json available in tmp; falls back to first 12 chars
        with patch.object(self.bus, "SESSIONS_JSON", Path(self.tmp) / "nope.json"):
            self.bus._lookup_label = lambda _: None  # type: ignore
            ident = self.bus.detect_self()
        self.assertEqual(ident.aoe_id, "deadbeef1234567890")
        self.assertEqual(ident.label, "deadbeef1234")
        del os.environ["AOE_INSTANCE_ID"]

    def test_lookup_session_by_label(self):
        fake = Path(self.tmp) / "sessions.json"
        fake.write_text(json.dumps([
            {"id": "abc123", "title": "AOE Admin"},
            {"id": "def456", "title": "Slovenia"},
        ]))
        with patch.object(self.bus, "SESSIONS_JSON", fake):
            result = self.bus.lookup_session_by_label("Slovenia")
        self.assertEqual(result.aoe_id, "def456")

    def test_lookup_returns_none_for_unknown_label(self):
        fake = Path(self.tmp) / "sessions.json"
        fake.write_text(json.dumps([{"id": "x", "title": "real"}]))
        with patch.object(self.bus, "SESSIONS_JSON", fake):
            self.assertIsNone(self.bus.lookup_session_by_label("ghost"))

    def test_new_thread_id_format(self):
        tid = self.bus.new_thread_id()
        self.assertTrue(tid.startswith("t_"))
        self.assertEqual(len(tid), 10)  # "t_" + 8 hex

    def test_new_thread_id_uniqueness(self):
        ids = {self.bus.new_thread_id() for _ in range(50)}
        self.assertEqual(len(ids), 50)

    def test_msg_hash_normalizes_whitespace_and_case(self):
        a = self.bus.msg_hash("t1", "ask", "Hello World")
        b = self.bus.msg_hash("t1", "ask", "  hello world  ")
        c = self.bus.msg_hash("t1", "ask", "Hello there")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_msg_hash_differs_by_target_and_type(self):
        body = "same body"
        self.assertNotEqual(
            self.bus.msg_hash("t1", "ask", body),
            self.bus.msg_hash("t2", "ask", body),
        )
        self.assertNotEqual(
            self.bus.msg_hash("t1", "ask", body),
            self.bus.msg_hash("t1", "reply", body),
        )

    def test_bus_enabled_default_true(self):
        os.environ.pop("AOE_BUS", None)
        self.assertTrue(self.bus.bus_enabled())

    def test_pause_resume_round_trip(self):
        self.bus.ensure_bus_root()
        self.bus.pause_bus()
        self.assertFalse(self.bus.bus_enabled())
        self.bus.resume_bus()
        self.assertTrue(self.bus.bus_enabled())

    def test_env_kill_switch(self):
        os.environ["AOE_BUS"] = "off"
        try:
            self.assertFalse(self.bus.bus_enabled())
        finally:
            del os.environ["AOE_BUS"]

    def test_aoe_send_dry_run_is_pure(self):
        ok, out = self.bus.aoe_send("xyz", "hello", dry_run=True)
        self.assertTrue(ok)
        self.assertIn("xyz", out)
        self.assertIn("hello", out)

    def test_audit_appends_jsonl(self):
        self.bus.audit("test-event", foo="bar", n=42)
        lines = (Path(self.tmp) / "audit.log").read_text().strip().split("\n")
        entry = json.loads(lines[-1])
        self.assertEqual(entry["event"], "test-event")
        self.assertEqual(entry["foo"], "bar")
        self.assertEqual(entry["n"], 42)
        self.assertIn("ts", entry)


if __name__ == "__main__":
    unittest.main()
