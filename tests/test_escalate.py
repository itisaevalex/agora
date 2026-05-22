"""Tests for escalation."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestEscalate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["AOE_BUS_ROOT"] = self.tmp
        for mod in list(sys.modules):
            if mod.startswith("lib"):
                del sys.modules[mod]
        from lib import bus, escalate, threads, peer_msg
        self.bus = bus
        self.esc = escalate
        self.threads = threads
        self.pm = peer_msg
        self.bus.BUS_ROOT = Path(self.tmp)
        self.bus.ensure_bus_root()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("AOE_BUS_ROOT", None)

    def test_write_freeform_escalation(self):
        block = self.esc.write("slovenia", "ad-hoc", "we're stuck on Spain CNMV scope")
        content = self.bus.human_inbox_path().read_text()
        self.assertIn("slovenia", content)
        self.assertIn("we're stuck on Spain CNMV scope", content)
        self.assertIn("ad-hoc", content)

    def test_write_includes_thread_context(self):
        # Build a thread with 5 messages — only last 3 should be quoted
        self.threads.create_thread("t_xxx", ["a", "b"])
        for i in range(5):
            self.threads.append_msg(
                "t_xxx",
                self.pm.PeerMsg(
                    sender_label=f"agent{i}", thread="t_xxx",
                    msg_type="ask" if i % 2 == 0 else "reply",
                    body=f"message body number {i}",
                    at="2026-05-22T10:00:00Z",
                ),
                f"agent{i}-id",
            )
        block = self.esc.write("alice", "t_xxx", "deadlocked at round 3",
                               thread_id="t_xxx")
        content = self.bus.human_inbox_path().read_text()
        self.assertIn("deadlocked at round 3", content)
        self.assertIn("Last exchanges", content)
        # Last 3: messages 2, 3, 4
        self.assertIn("message body number 4", content)
        self.assertIn("message body number 3", content)
        self.assertIn("message body number 2", content)
        # First two should NOT appear
        self.assertNotIn("message body number 0", content)

    def test_long_body_truncated_in_block(self):
        self.threads.create_thread("t_xxx", ["a", "b"])
        long_body = "x" * 500
        self.threads.append_msg(
            "t_xxx",
            self.pm.PeerMsg(sender_label="a", thread="t_xxx",
                            msg_type="ask", body=long_body, at="ts"),
            "a-id",
        )
        self.esc.write("alice", "t_xxx", "test", thread_id="t_xxx")
        content = self.bus.human_inbox_path().read_text()
        # Truncated body should be < 250 chars including the ellipsis
        self.assertIn("...", content)

    def test_multiple_escalations_append(self):
        self.esc.write("a", "ref1", "first")
        self.esc.write("b", "ref2", "second")
        content = self.bus.human_inbox_path().read_text()
        self.assertIn("first", content)
        self.assertIn("second", content)
        # Both have separator
        self.assertEqual(content.count("---"), 2)

    def test_notify_send_returns_false_when_missing(self):
        with patch("shutil.which", return_value=None):
            self.assertFalse(self.esc.fire_desktop_notification("alice", "reason"))

    def test_audit_logged(self):
        self.esc.write("alice", "ref", "reason", thread_id="t_zzz")
        audit_text = self.bus.audit_log_path().read_text()
        self.assertIn("escalate", audit_text)
        self.assertIn("alice", audit_text)


class TestNotificationExitCode(unittest.TestCase):
    def setUp(self):
        import tempfile, os, sys
        from pathlib import Path
        self.tmp = tempfile.mkdtemp()
        os.environ["AOE_BUS_ROOT"] = self.tmp
        for mod in list(sys.modules):
            if mod.startswith("lib"):
                del sys.modules[mod]
        from lib import bus, escalate
        self.bus = bus
        self.esc = escalate
        self.bus.BUS_ROOT = Path(self.tmp)
        self.bus.ensure_bus_root()

    def tearDown(self):
        import os, shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("AOE_BUS_ROOT", None)

    def test_returns_false_when_notify_send_exits_nonzero(self):
        from unittest.mock import patch, MagicMock
        with patch("shutil.which", return_value="/usr/bin/notify-send"), \
             patch("subprocess.run") as run:
            run.return_value = MagicMock(returncode=1, stderr="dbus broken")
            self.assertFalse(self.esc.fire_desktop_notification("alice", "x"))
            # Audit should record the failure
            audit = self.bus.audit_log_path().read_text()
            self.assertIn("notify.failed", audit)

    def test_returns_true_when_notify_send_exits_zero(self):
        from unittest.mock import patch, MagicMock
        with patch("shutil.which", return_value="/usr/bin/notify-send"), \
             patch("subprocess.run") as run:
            run.return_value = MagicMock(returncode=0, stderr="")
            self.assertTrue(self.esc.fire_desktop_notification("alice", "x"))
            audit = self.bus.audit_log_path().read_text()
            self.assertIn("notify.sent", audit)

    def test_passes_critical_urgency_flag(self):
        from unittest.mock import patch, MagicMock
        with patch("shutil.which", return_value="/usr/bin/notify-send"), \
             patch("subprocess.run") as run:
            run.return_value = MagicMock(returncode=0, stderr="")
            self.esc.fire_desktop_notification("alice", "x")
            args = run.call_args[0][0]
            # Should include -u critical
            self.assertIn("critical", args)
            self.assertIn("-u", args)


if __name__ == "__main__":
    unittest.main()
