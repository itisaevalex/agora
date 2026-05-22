"""Tests for safety rails: budget, loop detection, round cap."""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["AGORA_ROOT"] = self.tmp
        for mod in list(sys.modules):
            if mod.startswith("lib"):
                del sys.modules[mod]
        from lib import bus, safety, threads, peer_msg
        self.bus = bus
        self.safety = safety
        self.threads = threads
        self.pm = peer_msg
        self.bus.BUS_ROOT = Path(self.tmp)
        self.bus.ensure_bus_root()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("AGORA_ROOT", None)

    def _now(self):
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _send_n_recent(self, self_id: str, target_id: str, n: int, body_template: str = "msg {}"):
        """Simulate n recent outbound messages from self."""
        for i in range(n):
            tid = f"t_{i:08x}"
            self.threads.create_thread(tid, [self_id, target_id])
            self.threads.append_msg(
                tid,
                self.pm.PeerMsg("alice", tid, "ask", body_template.format(i), self._now()),
                self_id,
            )

    def test_default_allows_first_send(self):
        ok, reason = self.safety.check_send("self", "target", "ask", "hello")
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_budget_blocks_at_threshold(self):
        self._send_n_recent("self", "target", 20)
        ok, reason = self.safety.check_send("self", "target", "ask", "new",
                                            budget_per_hour=20)
        self.assertFalse(ok)
        self.assertIn("budget exhausted", reason)

    def test_budget_does_not_block_when_other_sender(self):
        # Messages from a different sender don't count against my budget
        self._send_n_recent("OTHER-sender", "target", 25)
        ok, reason = self.safety.check_send("self", "target", "ask", "hello",
                                            budget_per_hour=20)
        self.assertTrue(ok)

    def test_loop_detection_blocks_exact_duplicate(self):
        self._send_n_recent("self", "target", 1, body_template="same body")
        ok, reason = self.safety.check_send("self", "target", "ask", "same body")
        self.assertFalse(ok)
        self.assertIn("loop detected", reason)

    def test_loop_detection_blocks_whitespace_variant(self):
        self._send_n_recent("self", "target", 1, body_template="HELLO  world")
        ok, reason = self.safety.check_send("self", "target", "ask", "hello world")
        self.assertFalse(ok)

    def test_loop_detection_allows_different_body(self):
        self._send_n_recent("self", "target", 1, body_template="first")
        ok, reason = self.safety.check_send("self", "target", "ask", "second")
        self.assertTrue(ok)

    def test_round_cap_blocks_reply_at_threshold(self):
        # Build a thread with 3 rounds (6 msgs)
        self.threads.create_thread("t_xxx", ["self", "target"])
        for i in range(6):
            self.threads.append_msg(
                "t_xxx",
                self.pm.PeerMsg("a", "t_xxx",
                                "ask" if i % 2 == 0 else "reply",
                                f"msg {i}", self._now()),
                "self" if i % 2 == 0 else "target",
            )
        ok, reason = self.safety.check_send(
            "self", "target", "reply", "another reply", thread_id="t_xxx",
            round_cap=3,
        )
        self.assertFalse(ok)
        self.assertIn("/agora-escalate", reason)

    def test_round_cap_does_not_apply_to_ask(self):
        # Even on a heavily-replied thread, opening a NEW ask shouldn't trip round cap
        self.threads.create_thread("t_old", ["self", "target"])
        for i in range(6):
            self.threads.append_msg(
                "t_old",
                self.pm.PeerMsg("a", "t_old",
                                "ask" if i % 2 == 0 else "reply",
                                f"msg {i}", self._now()),
                "self" if i % 2 == 0 else "target",
            )
        # Fresh ask (no thread_id) on the same target
        ok, reason = self.safety.check_send("self", "target", "ask", "fresh question")
        self.assertTrue(ok)

    def test_budget_is_first_check(self):
        # Budget exhaustion should fire before loop detection (cheaper, more decisive)
        self._send_n_recent("self", "target", 20, body_template="same")
        ok, reason = self.safety.check_send("self", "target", "ask", "same",
                                            budget_per_hour=20)
        self.assertFalse(ok)
        self.assertIn("budget", reason)


if __name__ == "__main__":
    unittest.main()


class TestSafetyEnvOverrides(unittest.TestCase):
    """Cover the AGORA_ROUND_CAP / AGORA_BUDGET_PER_HOUR env knobs."""

    def setUp(self):
        import tempfile, os, sys
        from pathlib import Path
        self.tmp = tempfile.mkdtemp()
        os.environ["AGORA_ROOT"] = self.tmp
        for mod in list(sys.modules):
            if mod.startswith("lib"):
                del sys.modules[mod]
        from lib import bus, safety, threads, peer_msg
        self.bus = bus
        self.safety = safety
        self.threads = threads
        self.pm = peer_msg
        self.bus.BUS_ROOT = Path(self.tmp)
        self.bus.ensure_bus_root()

    def tearDown(self):
        import os, shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("AGORA_ROOT", None)
        for k in ("AGORA_ROUND_CAP", "AGORA_BUDGET_PER_HOUR"):
            os.environ.pop(k, None)

    def _now(self):
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _build_thread(self, thread_id, n_msgs):
        self.threads.create_thread(thread_id, ["self", "peer"])
        for i in range(n_msgs):
            self.threads.append_msg(
                thread_id,
                self.pm.PeerMsg("a", thread_id,
                                "ask" if i % 2 == 0 else "reply",
                                f"msg {i}", self._now()),
                "self" if i % 2 == 0 else "peer",
            )

    def test_round_cap_env_raises_limit(self):
        import os
        # Build a thread at DEFAULT_ROUND_CAP rounds — at the cap line.
        # Bump budget via explicit kwarg so it doesn't trip first.
        cap = self.safety.DEFAULT_ROUND_CAP
        self._build_thread("t_x", cap * 2)  # exactly DEFAULT rounds
        big_budget = cap * 10
        ok_default, _ = self.safety.check_send("self", "peer", "reply", "next",
                                               thread_id="t_x",
                                               budget_per_hour=big_budget)
        self.assertFalse(ok_default)  # at-cap blocks
        # With env override raising to cap*2
        os.environ["AGORA_ROUND_CAP"] = str(cap * 2)
        ok_env, _ = self.safety.check_send("self", "peer", "reply", "next",
                                           thread_id="t_x",
                                           budget_per_hour=big_budget)
        self.assertTrue(ok_env)

    def test_round_cap_env_lowers_limit(self):
        import os
        self._build_thread("t_y", 2)  # 1 round
        ok_default, _ = self.safety.check_send("self", "peer", "reply", "next",
                                               thread_id="t_y")
        self.assertTrue(ok_default)
        os.environ["AGORA_ROUND_CAP"] = "1"
        ok_strict, reason = self.safety.check_send("self", "peer", "reply", "next",
                                                   thread_id="t_y")
        self.assertFalse(ok_strict)
        self.assertIn("1/1 rounds", reason)

    def test_round_cap_env_invalid_falls_back(self):
        import os
        cap = self.safety.DEFAULT_ROUND_CAP
        self._build_thread("t_z", cap * 2)
        os.environ["AGORA_ROUND_CAP"] = "not-a-number"
        ok, _ = self.safety.check_send("self", "peer", "reply", "next",
                                       thread_id="t_z",
                                       budget_per_hour=cap * 10)
        self.assertFalse(ok)  # garbage → fallback to default → at cap → block

    def test_round_cap_env_zero_or_negative_falls_back(self):
        import os
        cap = self.safety.DEFAULT_ROUND_CAP
        self._build_thread("t_w", cap * 2)
        os.environ["AGORA_ROUND_CAP"] = "-5"
        ok, _ = self.safety.check_send("self", "peer", "reply", "next",
                                       thread_id="t_w",
                                       budget_per_hour=cap * 10)
        self.assertFalse(ok)  # negative → fallback → still blocked at default

    def test_budget_env_raises_limit(self):
        import os
        # Saturate 20 outbound (default budget). Use direct thread writes.
        self.threads.create_thread("t_b", ["self", "peer"])
        for i in range(20):
            self.threads.append_msg("t_b",
                self.pm.PeerMsg("a", "t_b", "ask", f"m{i}", self._now()), "self")
        ok_default, _ = self.safety.check_send("self", "peer", "ask", "new")
        self.assertFalse(ok_default)
        os.environ["AGORA_BUDGET_PER_HOUR"] = "50"
        ok_env, _ = self.safety.check_send("self", "peer", "ask", "new2")
        self.assertTrue(ok_env)

    def test_explicit_argument_beats_env(self):
        import os
        self._build_thread("t_arg", 6)
        os.environ["AGORA_ROUND_CAP"] = "10"  # would allow
        # Explicit argument (not default) should win
        ok, reason = self.safety.check_send("self", "peer", "reply", "next",
                                            thread_id="t_arg", round_cap=2)
        self.assertFalse(ok)
        self.assertIn("3/2 rounds", reason)
