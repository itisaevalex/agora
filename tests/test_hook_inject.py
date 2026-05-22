"""Tests for the hook-inject path."""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestHookInject(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["AOE_BUS_ROOT"] = self.tmp
        for mod in list(sys.modules):
            if mod.startswith("lib"):
                del sys.modules[mod]
        from lib import bus, inbox, peer_msg, cli
        self.bus = bus
        self.inbox = inbox
        self.pm = peer_msg
        self.cli = cli
        self.bus.BUS_ROOT = Path(self.tmp)
        self.bus.ensure_bus_root()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("AOE_BUS_ROOT", None)
        os.environ.pop("AOE_INSTANCE_ID", None)

    def _run_hook(self) -> str:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self.cli.cmd_hook_inject(None)  # type: ignore
        self.assertEqual(rc, 0)
        return buf.getvalue()

    def test_silent_when_no_session(self):
        os.environ.pop("AOE_INSTANCE_ID", None)
        self.assertEqual(self._run_hook(), "")

    def test_silent_when_empty_inbox(self):
        os.environ["AOE_INSTANCE_ID"] = "self-id"
        out = self._run_hook()
        self.assertEqual(out, "")

    def test_silent_when_paused(self):
        os.environ["AOE_INSTANCE_ID"] = "self-id"
        msg = self.pm.PeerMsg(
            sender_label="alice", thread="t_aaa", msg_type="ask",
            body="hi", at="2026-05-22T10:00:00Z",
        )
        self.inbox.append_to("self-id", msg)
        self.bus.pause_bus()
        try:
            self.assertEqual(self._run_hook(), "")
        finally:
            self.bus.resume_bus()
        # Inbox should NOT have been cleared while paused
        self.assertNotEqual(self.inbox.peek("self-id"), "")

    def test_injects_and_clears(self):
        os.environ["AOE_INSTANCE_ID"] = "self-id"
        msg = self.pm.PeerMsg(
            sender_label="alice", thread="t_aaa", msg_type="ask",
            body="logical question", at="2026-05-22T10:00:00Z",
        )
        self.inbox.append_to("self-id", msg)

        out = self._run_hook()
        self.assertIn("AOE-BUS INBOX", out)
        self.assertIn("alice", out)
        self.assertIn("logical question", out)
        self.assertIn("/bus-reply", out)
        self.assertIn("/bus-escalate", out)
        # Inbox cleared
        self.assertEqual(self.inbox.peek("self-id"), "")
        # Archived
        self.assertTrue((Path(self.tmp) / "sessions" / "self-id" / "inbox-archive.md").exists())

    def test_second_run_is_silent(self):
        os.environ["AOE_INSTANCE_ID"] = "self-id"
        self.inbox.append_to("self-id", self.pm.PeerMsg(
            sender_label="a", thread="t1", msg_type="ask",
            body="x", at="ts",
        ))
        first = self._run_hook()
        second = self._run_hook()
        self.assertNotEqual(first, "")
        self.assertEqual(second, "")


if __name__ == "__main__":
    unittest.main()
