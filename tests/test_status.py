"""Tests for status roll-up."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["AOE_BUS_ROOT"] = self.tmp
        for mod in list(sys.modules):
            if mod.startswith("lib"):
                del sys.modules[mod]
        from lib import bus, status, threads, peer_msg, links
        self.bus = bus
        self.status = status
        self.threads = threads
        self.pm = peer_msg
        self.links = links
        self.bus.BUS_ROOT = Path(self.tmp)
        self.bus.ensure_bus_root()

        # Stub session list — three fake sessions
        fake = Path(self.tmp) / "fake_sessions.json"
        fake.write_text(json.dumps([
            {"id": "alice-id", "title": "Alice"},
            {"id": "bob-id", "title": "Bob"},
            {"id": "carol-id", "title": "Carol"},
        ]))
        self._orig_sessions = self.bus.SESSIONS_JSON
        self.bus.SESSIONS_JSON = fake

    def tearDown(self):
        self.bus.SESSIONS_JSON = self._orig_sessions
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("AOE_BUS_ROOT", None)

    def test_collect_empty_state(self):
        rows = self.status.collect()
        self.assertEqual(len(rows), 3)
        for r in rows:
            self.assertEqual(r["link_count"], 0)
            self.assertEqual(r["inbox_bytes"], 0)
            self.assertEqual(r["waiting_on_me"], [])
            self.assertEqual(r["waiting_for_reply"], [])
            self.assertEqual(r["escalated"], [])

    def test_waiting_for_reply_after_ask(self):
        # Alice asks Bob — Alice is now waiting for reply
        self.threads.create_thread("t_xx", ["alice-id", "bob-id"])
        self.threads.append_msg("t_xx",
            self.pm.PeerMsg("Alice","t_xx","ask","hello?","ts"), "alice-id")
        rows = {r["label"]: r for r in self.status.collect()}
        self.assertEqual(len(rows["Alice"]["waiting_for_reply"]), 1)
        self.assertEqual(len(rows["Bob"]["waiting_on_me"]), 1)
        self.assertEqual(len(rows["Carol"]["waiting_on_me"]), 0)

    def test_escalation_classifies_thread_as_escalated(self):
        self.threads.create_thread("t_zz", ["alice-id", "bob-id"])
        self.threads.append_msg("t_zz",
            self.pm.PeerMsg("Alice","t_zz","ask","q","ts"), "alice-id")
        self.threads.append_msg("t_zz",
            self.pm.PeerMsg("Bob","t_zz","escalate-cc","stuck","ts"), "bob-id")
        rows = {r["label"]: r for r in self.status.collect()}
        # Once escalated, no longer counted as waiting on either side
        self.assertEqual(len(rows["Alice"]["escalated"]), 1)
        self.assertEqual(len(rows["Bob"]["escalated"]), 1)
        self.assertEqual(rows["Alice"]["waiting_for_reply"], [])
        self.assertEqual(rows["Bob"]["waiting_on_me"], [])

    def test_inbox_bytes_reported(self):
        msg = self.pm.PeerMsg("Alice","t_aa","ask","body","ts")
        from lib import inbox
        inbox.append_to("bob-id", msg)
        rows = {r["label"]: r for r in self.status.collect()}
        self.assertGreater(rows["Bob"]["inbox_bytes"], 0)
        self.assertEqual(rows["Alice"]["inbox_bytes"], 0)

    def test_render_marks_attention_sessions(self):
        # Make Bob owe Alice a reply
        self.threads.create_thread("t_ww", ["alice-id", "bob-id"])
        self.threads.append_msg("t_ww",
            self.pm.PeerMsg("Alice","t_ww","ask","q","ts"), "alice-id")
        rendered = self.status.render(self.status.collect())
        self.assertIn("● Bob", rendered)  # bob owes a reply, attention
        self.assertIn("YOU OWE REPLY", rendered)
        self.assertIn("1/3 sessions need attention", rendered)

    def test_render_compact_omits_detail(self):
        self.threads.create_thread("t_ww", ["alice-id", "bob-id"])
        self.threads.append_msg("t_ww",
            self.pm.PeerMsg("Alice","t_ww","ask","q","ts"), "alice-id")
        rendered = self.status.render(self.status.collect(), compact=True)
        self.assertNotIn("DETAIL", rendered)
        self.assertIn("● Bob", rendered)


if __name__ == "__main__":
    unittest.main()
