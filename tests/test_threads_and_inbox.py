"""Tests for thread log + inbox."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestThreadsAndInbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["AOE_BUS_ROOT"] = self.tmp
        for mod in list(sys.modules):
            if mod.startswith("lib"):
                del sys.modules[mod]
        from lib import bus, threads, inbox, peer_msg
        self.bus = bus
        self.threads = threads
        self.inbox = inbox
        self.pm = peer_msg
        self.bus.BUS_ROOT = Path(self.tmp)
        self.bus.ensure_bus_root()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("AOE_BUS_ROOT", None)

    def _make_msg(self, sender="alice", thread="t_aaa", mtype="ask",
                  body="hi", at="2026-05-22T10:00:00Z"):
        return self.pm.PeerMsg(sender_label=sender, thread=thread,
                               msg_type=mtype, body=body, at=at)

    def test_create_and_read_thread(self):
        self.threads.create_thread("t_aaa", ["alice-id", "bob-id"])
        data = self.threads.read_thread("t_aaa")
        self.assertIsNotNone(data)
        self.assertEqual(data["header"]["participants"], ["alice-id", "bob-id"])
        self.assertEqual(data["msgs"], [])

    def test_create_is_idempotent(self):
        self.threads.create_thread("t_aaa", ["a", "b"])
        self.threads.create_thread("t_aaa", ["c", "d"])  # should not overwrite
        data = self.threads.read_thread("t_aaa")
        self.assertEqual(data["header"]["participants"], ["a", "b"])

    def test_append_msg(self):
        self.threads.create_thread("t_aaa", ["alice-id", "bob-id"])
        msg = self._make_msg()
        self.threads.append_msg("t_aaa", msg, "alice-id")
        data = self.threads.read_thread("t_aaa")
        self.assertEqual(len(data["msgs"]), 1)
        self.assertEqual(data["msgs"][0]["from_label"], "alice")
        self.assertEqual(data["msgs"][0]["body"], "hi")

    def test_read_nonexistent_thread(self):
        self.assertIsNone(self.threads.read_thread("t_ghost"))

    def test_count_rounds(self):
        self.threads.create_thread("t_aaa", ["a", "b"])
        self.assertEqual(self.threads.count_rounds("t_aaa"), 0)
        self.threads.append_msg("t_aaa", self._make_msg(mtype="ask"), "a")
        self.assertEqual(self.threads.count_rounds("t_aaa"), 1)
        self.threads.append_msg("t_aaa", self._make_msg(mtype="reply"), "b")
        self.assertEqual(self.threads.count_rounds("t_aaa"), 1)
        self.threads.append_msg("t_aaa", self._make_msg(mtype="ask"), "a")
        self.assertEqual(self.threads.count_rounds("t_aaa"), 2)

    def test_inbox_append_and_read_clear(self):
        msg = self._make_msg()
        self.inbox.append_to("bob-id", msg)
        content = self.inbox.read_and_clear("bob-id")
        self.assertIn("hi", content)
        self.assertIn("alice", content)
        # Second read returns empty (cleared)
        self.assertEqual(self.inbox.read_and_clear("bob-id"), "")

    def test_inbox_archives_on_clear(self):
        msg = self._make_msg(body="archived note")
        self.inbox.append_to("bob-id", msg)
        self.inbox.read_and_clear("bob-id")
        archive = Path(self.tmp) / "sessions" / "bob-id" / "inbox-archive.md"
        self.assertTrue(archive.exists())
        self.assertIn("archived note", archive.read_text())

    def test_inbox_peek_does_not_clear(self):
        self.inbox.append_to("bob-id", self._make_msg(body="peeked"))
        a = self.inbox.peek("bob-id")
        b = self.inbox.peek("bob-id")
        self.assertEqual(a, b)
        self.assertIn("peeked", a)

    def test_recent_outbound_filter_by_sender(self):
        self.threads.create_thread("t_aaa", ["alice-id", "bob-id"])
        # Use a timestamp that's clearly recent (now)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        msg = self._make_msg(at=now)
        self.threads.append_msg("t_aaa", msg, "alice-id")
        outbound_alice = self.threads.recent_outbound_for("alice-id")
        outbound_bob = self.threads.recent_outbound_for("bob-id")
        self.assertEqual(len(outbound_alice), 1)
        self.assertEqual(len(outbound_bob), 0)

    def test_recent_outbound_window(self):
        self.threads.create_thread("t_aaa", ["alice-id", "bob-id"])
        # Old message (2024)
        old = self._make_msg(at="2024-01-01T00:00:00Z")
        self.threads.append_msg("t_aaa", old, "alice-id")
        results = self.threads.recent_outbound_for("alice-id", since_secs=60)
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
