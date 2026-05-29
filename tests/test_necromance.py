"""
Tests for lib/necromance.py — raise the dead, dispatch the question, cull.

Subprocesses (aoe + tmux) and pane-ready waits are mocked. The real-world
end-to-end path is exercised separately by the godot benchmark and won't be
in CI.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _user(text: str) -> dict:
    return {"type": "user", "message": {"content": text}}


def _aoe_add_ok(child_id: str = "abcdef123456") -> MagicMock:
    return MagicMock(returncode=0, stdout=f"✓ Added session: necro-x\n  ID:      {child_id}\n", stderr="")


def _aoe_start_ok() -> MagicMock:
    return MagicMock(returncode=0, stdout="✓ Started session", stderr="")


class TestNecromanceBase(unittest.TestCase):
    """Shared scaffolding — fresh tempdir per test, fixture jsonl planted."""

    UUID = "11111111-2222-3333-4444-555555555555"

    def setUp(self):
        self.tmp_agora = tempfile.mkdtemp()
        self.tmp_claude = tempfile.mkdtemp()
        self.tmp_aoe_cfg = tempfile.mkdtemp()
        os.environ["AGORA_ROOT"] = self.tmp_agora
        for mod in list(sys.modules):
            if mod.startswith("lib"):
                del sys.modules[mod]
        from lib import bus, graveyard, necromance, peer_msg, inbox
        self.bus = bus
        self.gy = graveyard
        self.necro = necromance
        self.peer_msg = peer_msg
        self.inbox = inbox

        # Point all module-level paths at the tempdirs.
        self.gy.CLAUDE_PROJECTS_ROOT = Path(self.tmp_claude)
        self.gy.GRAVEYARD_ROOT = Path(self.tmp_agora) / "graveyard"
        self.gy.INDEX_PATH = self.gy.GRAVEYARD_ROOT / "index.jsonl"
        self.gy.LIVE_DIR = self.gy.GRAVEYARD_ROOT / "live"
        self.necro.PROFILE_DIR = Path(self.tmp_aoe_cfg) / "profiles" / "graveyard"
        self.necro.PROFILE_SESSIONS_JSON = self.necro.PROFILE_DIR / "sessions.json"

        # Plant a dead session.
        p = Path(self.tmp_claude) / "proj" / f"{self.UUID}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w") as f:
            f.write(json.dumps(_user("hey claude lets investigate vietnam backfill")) + "\n")

        # Caller identity for tests that need one.
        self.caller = self.bus.SessionIdentity(aoe_id="caller-aoe-id", label="caller-tab")

    def tearDown(self):
        shutil.rmtree(self.tmp_agora, ignore_errors=True)
        shutil.rmtree(self.tmp_claude, ignore_errors=True)
        shutil.rmtree(self.tmp_aoe_cfg, ignore_errors=True)
        os.environ.pop("AGORA_ROOT", None)


class TestNecromanceSpawn(TestNecromanceBase):
    def test_refuses_unknown_label(self):
        ok, msg, necro = self.necro.necromance(self.caller, "does-not-exist", "hi?")
        self.assertFalse(ok)
        self.assertIn("no dead session", msg)
        self.assertIsNone(necro)

    def test_refuses_empty_question(self):
        ok, msg, necro = self.necro.necromance(self.caller, self.UUID, "   ")
        self.assertFalse(ok)
        self.assertIn("empty", msg.lower())

    def test_raises_under_graveyard_profile_and_patches_sessions_json(self):
        """Verify the spawn path: aoe add → patch sessions.json → aoe session start.

        The patch must include --resume <target_uuid>, --model claude-opus-4-8,
        and --effort xhigh.
        """
        with patch.object(self.necro, "_aoe") as mock_aoe, \
             patch.object(self.bus, "wait_for_pane_ready", return_value=True), \
             patch.object(self.necro, "_dismiss_resume_prompt", return_value=True), \
             patch.object(self.bus, "aoe_send_peer_msg", return_value=(True, "ok")):
            child_id = "abcdef123456"

            # _aoe is called several times; first call = aoe add, return ok with the ID.
            # Subsequent calls (session start) also return ok. We need to track them.
            def fake_aoe(cmd, timeout=30.0):
                # On `aoe add` we need to write a fake sessions.json record so
                # the subsequent _patch_session_record call has something to find.
                if "add" in cmd:
                    self.necro.PROFILE_SESSIONS_JSON.parent.mkdir(parents=True, exist_ok=True)
                    self.necro.PROFILE_SESSIONS_JSON.write_text(json.dumps({"sessions": [
                        {"id": child_id, "title": "necro-vietnam-11111111"},
                    ]}))
                    return _aoe_add_ok(child_id=child_id)
                if "start" in cmd:
                    return _aoe_start_ok()
                return MagicMock(returncode=0, stdout="", stderr="")
            mock_aoe.side_effect = fake_aoe

            ok, msg, necro = self.necro.necromance(
                self.caller, self.UUID, "what did you decide?")

        self.assertTrue(ok, msg=msg)
        self.assertIsNotNone(necro)
        # Profile-scoping is verified by test_aoe_runs_with_graveyard_profile_env
        # (which exercises _aoe in isolation); here we only assert that the
        # spawn produced the right sessions.json contents.
        # sessions.json now has the patched fields.
        data = json.loads(self.necro.PROFILE_SESSIONS_JSON.read_text())
        rec = next(s for s in data["sessions"] if s["id"] == "abcdef123456")
        self.assertIn(f"--resume {self.UUID}", rec["extra_args"])
        self.assertIn("--model claude-opus-4-8", rec["extra_args"])
        self.assertIn("--effort xhigh", rec["extra_args"])
        self.assertEqual(rec["agent_session_id"], self.UUID)

    def test_aoe_runs_with_graveyard_profile_env(self):
        """The internal _aoe helper must force AGENT_OF_EMPIRES_PROFILE=graveyard
        so subcommand bugs can't accidentally hit the default profile."""
        with patch("subprocess.run", return_value=_aoe_add_ok()) as mock_run:
            self.necro._aoe(["aoe", "add", "."])
        env = mock_run.call_args.kwargs["env"]
        self.assertEqual(env["AGENT_OF_EMPIRES_PROFILE"], "graveyard")

    def test_lock_written_after_successful_raise(self):
        with patch.object(self.necro, "_aoe") as mock_aoe, \
             patch.object(self.bus, "wait_for_pane_ready", return_value=True), \
             patch.object(self.necro, "_dismiss_resume_prompt", return_value=True), \
             patch.object(self.bus, "aoe_send_peer_msg", return_value=(True, "ok")):
            def fake_aoe(cmd, timeout=30.0):
                if "add" in cmd:
                    self.necro.PROFILE_SESSIONS_JSON.parent.mkdir(parents=True, exist_ok=True)
                    self.necro.PROFILE_SESSIONS_JSON.write_text(json.dumps({"sessions": [
                        {"id": "abcdef123456", "title": "necro-x"},
                    ]}))
                    return _aoe_add_ok()
                return MagicMock(returncode=0, stdout="", stderr="")
            mock_aoe.side_effect = fake_aoe

            ok, msg, necro = self.necro.necromance(self.caller, self.UUID, "q?")

        self.assertTrue(ok, msg=msg)
        lock = necro.lock_path()
        self.assertTrue(lock.exists())
        data = json.loads(lock.read_text())
        self.assertEqual(data["uuid"], self.UUID)
        self.assertEqual(data["aoe_id"], "abcdef123456")

    def test_pane_never_ready_aborts_and_cleans_up(self):
        """If wait_for_pane_ready times out, necromance must NOT type into
        the unready pane (same V2-bug protection as spawn.py) and must
        tear down the dangling AoE record."""
        cleanup_calls = []

        with patch.object(self.necro, "_aoe") as mock_aoe, \
             patch.object(self.bus, "wait_for_pane_ready", return_value=False), \
             patch.object(self.bus, "aoe_send_peer_msg") as mock_send:
            def fake_aoe(cmd, timeout=30.0):
                if "add" in cmd:
                    self.necro.PROFILE_SESSIONS_JSON.parent.mkdir(parents=True, exist_ok=True)
                    self.necro.PROFILE_SESSIONS_JSON.write_text(json.dumps({"sessions": [
                        {"id": "abcdef123456", "title": "necro-x"},
                    ]}))
                    return _aoe_add_ok()
                if "stop" in cmd or "remove" in cmd:
                    cleanup_calls.append(cmd)
                return MagicMock(returncode=0, stdout="", stderr="")
            mock_aoe.side_effect = fake_aoe

            ok, msg, necro = self.necro.necromance(self.caller, self.UUID, "q?")

        self.assertFalse(ok)
        # No tmux send was attempted into the unready pane.
        mock_send.assert_not_called()
        # And BOTH cleanup calls fired (LOW-5: was OR'd; now split).
        self.assertTrue(any("stop" in c for c in cleanup_calls),
                        f"expected `aoe session stop`, got: {cleanup_calls}")
        self.assertTrue(any("remove" in c for c in cleanup_calls),
                        f"expected `aoe remove`, got: {cleanup_calls}")
        # And the provisional lock was rolled back, not left orphaned.
        self.assertFalse((self.gy.LIVE_DIR / f"{self.UUID}.lock").exists(),
                         "provisional lock should be cleaned up on spawn failure")


class TestNecromanceFollowUp(TestNecromanceBase):
    def test_follow_up_reuses_live_necromancy(self):
        """Second necromance() call against the same UUID, while the lock is
        live and tmux still has the session, must skip the spawn flow and
        just send a follow-up message."""
        # Pre-populate a live lock.
        n = self.necro.Necromancy(
            uuid=self.UUID, label="vietnam-thing", aoe_id="abcdef123456",
            title="necro-vietnam-11111111", started_at=time.time(),
            last_active=time.time() - 60, thread_id="t_necro_old",
        )
        n.write_lock()

        with patch.object(self.necro, "_tmux_alive", return_value=True), \
             patch.object(self.necro, "_aoe") as mock_aoe, \
             patch.object(self.bus, "aoe_send_peer_msg", return_value=(True, "ok")):
            ok, msg, necro = self.necro.necromance(
                self.caller, self.UUID, "follow-up question?")

        self.assertTrue(ok, msg=msg)
        self.assertIn("follow-up", msg)
        # No aoe add / session start should have been called.
        for call in mock_aoe.call_args_list:
            cmd = call[0][0]
            self.assertNotIn("add", cmd, f"unexpected aoe add in follow-up path: {cmd}")
            self.assertNotIn("start", cmd, f"unexpected aoe start in follow-up path: {cmd}")
        # Lock timestamp was refreshed.
        data = json.loads(necro.lock_path().read_text())
        self.assertGreater(data["last_active"], n.last_active)


class TestNecromanceCull(TestNecromanceBase):
    def _seed_lock(self, last_active_offset: float) -> "Necromancy":
        n = self.necro.Necromancy(
            uuid=self.UUID, label="x", aoe_id="abcdef123456",
            title="necro-x", started_at=time.time() - 1000,
            last_active=time.time() - last_active_offset,
            thread_id="t_necro_x",
        )
        n.write_lock()
        return n

    def test_cull_releases_stale_lock(self):
        self._seed_lock(last_active_offset=999)  # well past TTL

        with patch.object(self.necro, "_aoe") as mock_aoe:
            mock_aoe.return_value = MagicMock(returncode=0, stdout="", stderr="")
            released = self.necro.cull(ttl_seconds=300)

        self.assertEqual(released, [self.UUID])
        # Lock file is gone.
        self.assertFalse((self.gy.LIVE_DIR / f"{self.UUID}.lock").exists())
        # `aoe session stop` was called.
        stop_calls = [c for c in mock_aoe.call_args_list
                      if "stop" in c[0][0]]
        self.assertTrue(stop_calls, "expected at least one `aoe session stop` call")

    def test_cull_preserves_fresh_lock(self):
        self._seed_lock(last_active_offset=10)  # well within TTL

        with patch.object(self.necro, "_aoe") as mock_aoe:
            mock_aoe.return_value = MagicMock(returncode=0, stdout="", stderr="")
            released = self.necro.cull(ttl_seconds=300)

        self.assertEqual(released, [])
        self.assertTrue((self.gy.LIVE_DIR / f"{self.UUID}.lock").exists())
        # No aoe stop calls.
        stop_calls = [c for c in mock_aoe.call_args_list
                      if "stop" in c[0][0]]
        self.assertEqual(stop_calls, [])

    def test_release_is_safely_idempotent(self):
        """MED-6: release() unlinks the lock FIRST as advisory ownership.
        A second release() on the same Necromancy must not crash, but must
        return False (signalling "another caller already did it"). This
        prevents duplicate `aoe session stop` and duplicate audit lines
        under a concurrent-cull race."""
        n = self._seed_lock(last_active_offset=10)
        with patch.object(self.necro, "_aoe") as mock_aoe:
            mock_aoe.return_value = MagicMock(returncode=0, stdout="", stderr="")
            ok1 = self.necro.release(n, reason="manual")
            ok2 = self.necro.release(n, reason="manual")
        self.assertTrue(ok1, "first release must succeed")
        self.assertFalse(ok2, "second release must return False (lock already gone)")


# ---------- Adversarial-review regressions (2026-05-29) ----------

class TestProvisionalLockRace(TestNecromanceBase):
    """HIGH-1 / HIGH-3 — the lock is claimed atomically right after `aoe add`
    and only promoted to status=live after the consultation msg lands. A second
    concurrent necromance() call must see the lock and route to follow-up."""

    def test_claim_lock_uses_o_excl(self):
        """First caller wins the O_EXCL claim; second caller fails False."""
        n1 = self.necro.Necromancy(
            uuid=self.UUID, label="x", aoe_id="", title="",
            started_at=time.time(), last_active=time.time(),
            thread_id="", status=self.necro.STATUS_LAUNCHING,
        )
        n2 = self.necro.Necromancy(
            uuid=self.UUID, label="x", aoe_id="", title="",
            started_at=time.time(), last_active=time.time(),
            thread_id="", status=self.necro.STATUS_LAUNCHING,
        )
        self.assertTrue(n1.claim_lock())
        self.assertFalse(n2.claim_lock(),
                         "second claim must fail because first holds the lock")

    def test_concurrent_necromance_routes_loser_to_follow_up(self):
        """Two necromance() calls for the same uuid: the loser must see the
        winner's lock (after a brief retry window) and send a follow-up."""
        # Simulate the winner having already claimed.
        winner = self.necro.Necromancy(
            uuid=self.UUID, label="x", aoe_id="abcdef123456",
            title="necro-x", started_at=time.time(),
            last_active=time.time(), thread_id="t_w",
            status=self.necro.STATUS_LIVE,
        )
        winner.write_lock()

        with patch.object(self.necro, "_tmux_alive", return_value=True), \
             patch.object(self.necro, "_aoe") as mock_aoe, \
             patch.object(self.bus, "aoe_send_peer_msg", return_value=(True, "ok")):
            ok, msg, necro = self.necro.necromance(
                self.caller, self.UUID, "follow-up question?")

        # Must have skipped the spawn flow entirely.
        self.assertTrue(ok)
        self.assertIn("follow-up", msg)
        for call in mock_aoe.call_args_list:
            cmd = call[0][0]
            self.assertNotIn("add", cmd, f"unexpected spawn under contention: {cmd}")

    def test_provisional_lock_present_before_session_start(self):
        """Verify the lock is created with status=launching BEFORE
        aoe session start, so a concurrent cull's launch-orphan
        timeout-based cleanup is the only way to recover from crashes."""
        lock_status_at_start = {}

        def fake_aoe(cmd, timeout=30.0):
            if "add" in cmd:
                self.necro.PROFILE_SESSIONS_JSON.parent.mkdir(parents=True, exist_ok=True)
                self.necro.PROFILE_SESSIONS_JSON.write_text(json.dumps({"sessions": [
                    {"id": "abcdef123456", "title": "necro-x"},
                ]}))
                return _aoe_add_ok()
            if "start" in cmd:
                # By now the provisional lock must exist with status=launching.
                lock = self.gy.LIVE_DIR / f"{self.UUID}.lock"
                if lock.exists():
                    lock_status_at_start["status"] = json.loads(lock.read_text())["status"]
                return _aoe_start_ok()
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch.object(self.necro, "_aoe", side_effect=fake_aoe), \
             patch.object(self.bus, "wait_for_pane_ready", return_value=True), \
             patch.object(self.necro, "_dismiss_resume_prompt", return_value=True), \
             patch.object(self.bus, "aoe_send_peer_msg", return_value=(True, "ok")):
            ok, msg, necro = self.necro.necromance(self.caller, self.UUID, "q?")

        self.assertTrue(ok, msg=msg)
        self.assertEqual(lock_status_at_start.get("status"),
                         self.necro.STATUS_LAUNCHING,
                         "lock must be `launching` while session is starting")
        # And after success, status is promoted to live.
        final = json.loads(necro.lock_path().read_text())
        self.assertEqual(final["status"], self.necro.STATUS_LIVE)


class TestResumePromptMatcher(TestNecromanceBase):
    """MED-1 — substring match was too loose. Tighter match requires the
    actual numbered-option row to be visible, not just a stray mention."""

    def test_looks_like_resume_prompt_accepts_real_picker(self):
        real_picker = (
            "Resuming the full session will consume usage limits.\n"
            "We recommend resuming from a summary.\n"
            "\n"
            "❯ 1. Resume from summary (recommended)\n"
            "  2. Resume full session as-is\n"
            "  3. Don't ask me again\n"
        )
        self.assertTrue(self.necro._looks_like_resume_prompt(real_picker))

    def test_looks_like_resume_prompt_rejects_substring_mention(self):
        # User message scrolled into the capture window — mentions the
        # marker text but no numbered-option row.
        scrollback = (
            "● Looking at the docs you wanted me to resume from summary mode\n"
            "  and load the full session anyway, here's what I found.\n"
        )
        self.assertFalse(self.necro._looks_like_resume_prompt(scrollback))


class TestLiveSessionGuard(TestNecromanceBase):
    """MED-4 — refuse to fork a currently-running AoE session."""

    def test_refuses_when_uuid_is_running_in_default_profile(self):
        # Plant a default-profile sessions.json that has our UUID as live.
        aoe_dir = Path(self.tmp_aoe_cfg) / "profiles" / "default"
        aoe_dir.mkdir(parents=True)
        live_sessions = aoe_dir / "sessions.json"
        live_sessions.write_text(json.dumps({"sessions": [
            {"id": "running-aoe-id", "title": "the-live-one",
             "agent_session_id": self.UUID, "status": "running"},
        ]}))
        self.bus.SESSIONS_JSON = live_sessions

        ok, msg, necro = self.necro.necromance(self.caller, self.UUID, "q?")
        self.assertFalse(ok)
        self.assertIn("currently live", msg)
        self.assertIn("/agora-ask", msg)
        self.assertIsNone(necro)

    def test_allows_when_uuid_is_idle_in_default_profile(self):
        # Idle ≠ running: necromance should proceed normally.
        aoe_dir = Path(self.tmp_aoe_cfg) / "profiles" / "default"
        aoe_dir.mkdir(parents=True)
        idle_sessions = aoe_dir / "sessions.json"
        idle_sessions.write_text(json.dumps({"sessions": [
            {"id": "stopped-aoe-id", "title": "the-idle-one",
             "agent_session_id": self.UUID, "status": "idle"},
        ]}))
        self.bus.SESSIONS_JSON = idle_sessions

        with patch.object(self.necro, "_aoe") as mock_aoe, \
             patch.object(self.bus, "wait_for_pane_ready", return_value=True), \
             patch.object(self.necro, "_dismiss_resume_prompt", return_value=True), \
             patch.object(self.bus, "aoe_send_peer_msg", return_value=(True, "ok")):
            def fake_aoe(cmd, timeout=30.0):
                if "add" in cmd:
                    self.necro.PROFILE_SESSIONS_JSON.parent.mkdir(parents=True, exist_ok=True)
                    self.necro.PROFILE_SESSIONS_JSON.write_text(json.dumps({"sessions": [
                        {"id": "abcdef123456", "title": "necro-x"},
                    ]}))
                    return _aoe_add_ok()
                return MagicMock(returncode=0, stdout="", stderr="")
            mock_aoe.side_effect = fake_aoe
            ok, msg, necro = self.necro.necromance(self.caller, self.UUID, "q?")
        self.assertTrue(ok, msg=msg)


class TestReleaseOrdering(TestNecromanceBase):
    """MED-6 — release() unlinks the lock FIRST as advisory ownership.
    A concurrent second release on the same Necromancy must early-return
    without firing duplicate `aoe session stop` or duplicate audit entries."""

    def test_release_returns_false_on_second_call(self):
        n = self.necro.Necromancy(
            uuid=self.UUID, label="x", aoe_id="abcdef123456",
            title="necro-x", started_at=time.time(), last_active=time.time(),
            thread_id="t", status=self.necro.STATUS_LIVE,
        )
        n.write_lock()

        with patch.object(self.necro, "_aoe") as mock_aoe:
            mock_aoe.return_value = MagicMock(returncode=0, stdout="", stderr="")
            first = self.necro.release(n, reason="manual")
            second = self.necro.release(n, reason="manual")

        self.assertTrue(first)
        # Second call sees no lock and returns False without re-firing aoe stop.
        self.assertFalse(second)
        stop_calls = [c for c in mock_aoe.call_args_list if "stop" in c[0][0]]
        self.assertEqual(len(stop_calls), 1,
                         f"aoe session stop must fire exactly once, got {len(stop_calls)}")


class TestLaunchOrphanCull(TestNecromanceBase):
    """HIGH-3 — provisional locks (status=launching) older than
    LAUNCH_TIMEOUT_SECONDS are orphaned and cleaned up by cull."""

    def test_cull_releases_orphaned_launching_lock(self):
        n = self.necro.Necromancy(
            uuid=self.UUID, label="x", aoe_id="abcdef123456",
            title="necro-x",
            started_at=time.time() - 999,
            last_active=time.time() - 999,
            thread_id="", status=self.necro.STATUS_LAUNCHING,
        )
        n.write_lock()

        with patch.object(self.necro, "_aoe") as mock_aoe:
            mock_aoe.return_value = MagicMock(returncode=0, stdout="", stderr="")
            released = self.necro.cull()

        self.assertEqual(released, [self.UUID])

    def test_cull_preserves_fresh_launching_lock(self):
        """A still-spawning necromancy (status=launching, <60s old) must NOT
        be culled — its owner is mid-flight."""
        n = self.necro.Necromancy(
            uuid=self.UUID, label="x", aoe_id="",
            title="", started_at=time.time(), last_active=time.time(),
            thread_id="", status=self.necro.STATUS_LAUNCHING,
        )
        n.write_lock()

        with patch.object(self.necro, "_aoe") as mock_aoe:
            mock_aoe.return_value = MagicMock(returncode=0, stdout="", stderr="")
            released = self.necro.cull()

        self.assertEqual(released, [])


class TestAtomicLockWrite(TestNecromanceBase):
    """LOW-2 — lock is written via tmp+replace so a concurrent reader never
    sees a partial JSON payload."""

    def test_write_lock_uses_tmp_replace(self):
        n = self.necro.Necromancy(
            uuid=self.UUID, label="x", aoe_id="abcdef",
            title="t", started_at=time.time(), last_active=time.time(),
            thread_id="th", status=self.necro.STATUS_LIVE,
        )
        # Write once.
        n.write_lock()
        # Mid-write, the .tmp file must not linger (tmp.replace() removes it).
        tmp = n.lock_path().with_suffix(".lock.tmp")
        self.assertFalse(tmp.exists(),
                         "tmp file must be replaced atomically, not left behind")
        # Lock content is valid JSON (LOW-2 race would produce partial).
        data = json.loads(n.lock_path().read_text())
        self.assertEqual(data["uuid"], self.UUID)


class TestNecromancePreamble(TestNecromanceBase):
    def test_preamble_forbids_side_effects(self):
        text = self.necro._preamble("caller-tab")
        # Required guardrails the preamble must contain.
        self.assertIn("DO NOT modify files", text)
        self.assertIn("/agora-reply", text)
        # Must name the caller so the resurrected agent knows who's asking.
        self.assertIn("caller-tab", text)


class TestNecromanceResumePrompt(TestNecromanceBase):
    def test_dismiss_resume_prompt_sends_full_key_when_present(self):
        """When the option-1/option-2 prompt is visible, send "2" + Enter
        to pick full resume."""
        capture_output = (
            "Resuming the full session will consume a substantial portion of your usage limits.\n"
            "We recommend resuming from a summary.\n"
            "\n"
            "❯ 1. Resume from summary (recommended)\n"
            "  2. Resume full session as-is\n"
            "  3. Don't ask me again\n"
        )
        send_calls = []

        def fake_run(cmd, *args, **kwargs):
            if "capture-pane" in cmd:
                return MagicMock(returncode=0, stdout=capture_output, stderr="")
            if "send-keys" in cmd:
                send_calls.append(cmd)
                return MagicMock(returncode=0, stdout="", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch.object(self.bus, "_find_tmux_session_for", return_value="aoe_x"), \
             patch("subprocess.run", side_effect=fake_run), \
             patch.object(self.bus, "wait_for_pane_ready", return_value=True), \
             patch("time.sleep"):
            ok = self.necro._dismiss_resume_prompt("abcdef123456", summary_mode=False)

        self.assertTrue(ok)
        # The key sent was "2" (full resume).
        keys_sent = [c[-2] for c in send_calls]  # second-to-last arg in tmux send-keys
        self.assertIn("2", keys_sent)

    def test_dismiss_resume_prompt_returns_false_when_not_shown(self):
        """If the prompt never appears within the timeout (user clicked
        'Don't ask me again' previously), return False without sending keys."""
        # Pane shows just claude welcome, no resume prompt.
        capture_output = "❯ Try \"how do I log an error?\"\n"
        send_calls = []

        def fake_run(cmd, *args, **kwargs):
            if "capture-pane" in cmd:
                return MagicMock(returncode=0, stdout=capture_output, stderr="")
            if "send-keys" in cmd:
                send_calls.append(cmd)
                return MagicMock(returncode=0, stdout="", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch.object(self.bus, "_find_tmux_session_for", return_value="aoe_x"), \
             patch("subprocess.run", side_effect=fake_run), \
             patch.object(self.necro, "RESUME_PROMPT_TIMEOUT_S", 0.05), \
             patch("time.sleep"):
            ok = self.necro._dismiss_resume_prompt("abcdef123456")

        self.assertFalse(ok)
        self.assertEqual(send_calls, [],
                         "must NOT send keys when prompt is absent")


if __name__ == "__main__":
    unittest.main()
