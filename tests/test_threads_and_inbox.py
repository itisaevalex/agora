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
        os.environ["AGORA_ROOT"] = self.tmp
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
        os.environ.pop("AGORA_ROOT", None)

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


class TestBodyStdin(unittest.TestCase):
    """Tests for --body-stdin handling in cmd_ask/reply/escalate/spawn."""
    def setUp(self):
        import os, tempfile, sys
        from pathlib import Path
        self.tmp = tempfile.mkdtemp()
        os.environ["AGORA_ROOT"] = self.tmp
        for mod in list(sys.modules):
            if mod.startswith("lib"):
                del sys.modules[mod]
        from lib import bus, cli
        self.bus = bus
        self.cli = cli
        self.bus.BUS_ROOT = Path(self.tmp)
        self.bus.ensure_bus_root()

    def tearDown(self):
        import os, shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("AGORA_ROOT", None)

    def test_read_body_stdin_returns_stdin_content(self):
        import argparse, io, sys
        old_stdin = sys.stdin
        sys.stdin = io.StringIO("body via stdin\nwith newlines and (parens) and --flags")
        try:
            args = argparse.Namespace(body_stdin=True, body=[])
            result = self.cli._read_body(args)
        finally:
            sys.stdin = old_stdin
        self.assertEqual(result, "body via stdin\nwith newlines and (parens) and --flags")

    def test_read_body_positional_when_no_stdin_flag(self):
        import argparse
        args = argparse.Namespace(body_stdin=False, body=["hello", "world"])
        self.assertEqual(self.cli._read_body(args), "hello world")

    def test_read_body_handles_missing_attr(self):
        import argparse
        # No body_stdin attribute at all → falls through to positional
        args = argparse.Namespace(body=["x"])
        self.assertEqual(self.cli._read_body(args), "x")

    def test_read_body_alternate_positional_attr(self):
        import argparse, io, sys
        # escalate uses 'reason', spawn uses 'task'
        args = argparse.Namespace(body_stdin=False, reason=["why", "stuck"])
        self.assertEqual(self.cli._read_body(args, positional_attr="reason"), "why stuck")


class TestPaneAttachedDetection(unittest.TestCase):
    """The attached-pane check that avoids stuffing peer-msgs into a watched pane."""
    def setUp(self):
        import os, tempfile, sys
        from pathlib import Path
        self.tmp = tempfile.mkdtemp()
        os.environ["AGORA_ROOT"] = self.tmp
        for mod in list(sys.modules):
            if mod.startswith("lib"):
                del sys.modules[mod]
        from lib import bus
        self.bus = bus
        self.bus.BUS_ROOT = Path(self.tmp)
        self.bus.ensure_bus_root()

    def tearDown(self):
        import os, shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("AGORA_ROOT", None)

    def test_attached_returns_false_when_tmux_lists_no_matching_session(self):
        from unittest.mock import patch, MagicMock
        with patch("subprocess.run",
                   return_value=MagicMock(returncode=0, stdout="aoe_other_xyz123\n", stderr="")):
            self.assertFalse(self.bus.pane_is_attached("abc12345deadbeef"))

    def test_attached_returns_true_when_prefix_matches(self):
        from unittest.mock import patch, MagicMock
        with patch("subprocess.run",
                   return_value=MagicMock(returncode=0, stdout="aoe_some_abc12345\n", stderr="")):
            self.assertTrue(self.bus.pane_is_attached("abc12345deadbeef"))

    def test_attached_returns_false_on_subprocess_error(self):
        from unittest.mock import patch
        import subprocess
        with patch("subprocess.run", side_effect=FileNotFoundError):
            self.assertFalse(self.bus.pane_is_attached("any-id"))

    def test_peer_msg_falls_back_to_nudge_when_attached(self):
        from unittest.mock import patch, MagicMock
        # Simulate: short message, but pane is attached
        with patch.object(self.bus, "pane_is_attached", return_value=True), \
             patch("subprocess.run",
                   return_value=MagicMock(returncode=0, stdout="", stderr="")) as run:
            ok, _ = self.bus.aoe_send_peer_msg("target-id", "alice", "t_xyz", "hi")
        self.assertTrue(ok)
        # The send-keys text should be the nudge form, not "hi"
        sent_text = run.call_args[0][0][-1]  # last arg = the text
        self.assertIn("📨 agora peer-msg", sent_text)
        self.assertIn("human focused", sent_text)
        self.assertNotIn("hi", sent_text.split("📨")[0])  # original body NOT delivered

    def test_peer_msg_sends_full_when_unattached_and_small(self):
        from unittest.mock import patch, MagicMock
        with patch.object(self.bus, "pane_is_attached", return_value=False), \
             patch("subprocess.run",
                   return_value=MagicMock(returncode=0, stdout="", stderr="")) as run:
            self.bus.aoe_send_peer_msg("target-id", "alice", "t_xyz", "the full msg")
        sent_text = run.call_args[0][0][-1]
        self.assertEqual(sent_text, "the full msg")
