"""
Tests for lib/graveyard.py — the dead-session index + grave-dig search.

All filesystem state is rooted at a per-test tempdir via the AGORA_ROOT env
var (so writes don't touch the developer's real ~/.agora/) AND a fake
CLAUDE_PROJECTS_ROOT pointing at another tempdir of fixture jsonls.
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
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for d in lines:
            f.write(json.dumps(d) + "\n")


def _user(text: str) -> dict:
    return {"type": "user", "message": {"content": text}}


def _assistant(text: str) -> dict:
    return {"type": "assistant", "message": {"content": text}}


class TestGraveyardIndex(unittest.TestCase):
    def setUp(self):
        self.tmp_agora = tempfile.mkdtemp()
        self.tmp_claude = tempfile.mkdtemp()
        os.environ["AGORA_ROOT"] = self.tmp_agora
        # Force fresh import so module-level paths pick up AGORA_ROOT.
        for mod in list(sys.modules):
            if mod.startswith("lib"):
                del sys.modules[mod]
        from lib import bus, graveyard
        self.bus = bus
        self.gy = graveyard
        self.gy.CLAUDE_PROJECTS_ROOT = Path(self.tmp_claude)
        self.gy.GRAVEYARD_ROOT = Path(self.tmp_agora) / "graveyard"
        self.gy.INDEX_PATH = self.gy.GRAVEYARD_ROOT / "index.jsonl"
        self.gy.LIVE_DIR = self.gy.GRAVEYARD_ROOT / "live"

    def tearDown(self):
        shutil.rmtree(self.tmp_agora, ignore_errors=True)
        shutil.rmtree(self.tmp_claude, ignore_errors=True)
        os.environ.pop("AGORA_ROOT", None)

    def _make_session(self, project: str, uuid: str, lines: list[dict]) -> Path:
        p = Path(self.tmp_claude) / project / f"{uuid}.jsonl"
        _write_jsonl(p, lines)
        return p

    def test_build_index_finds_all_uuid_jsonls(self):
        # Arrange — two valid sessions, one bad-name file that must be skipped.
        self._make_session("proj-a", "11111111-2222-3333-4444-555555555555",
                           [_user("hey claude lets do thing")])
        self._make_session("proj-b", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                           [_user("yo, vietnam backfill question")])
        self._make_session("proj-a", "notauuid",
                           [_user("should be ignored")])  # filename not a uuid

        # Act
        entries = self.gy.build_index()

        # Assert
        uuids = sorted(e.uuid for e in entries)
        self.assertEqual(uuids, [
            "11111111-2222-3333-4444-555555555555",
            "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        ])

    def test_build_index_extracts_first_user_message(self):
        self._make_session("proj-a", "11111111-2222-3333-4444-555555555555", [
            {"type": "system", "content": "boot"},
            _user("<system-reminder>noise</system-reminder>"),       # filtered
            _user("hey claude, the real first user message here"),
            _assistant("ok"),
            _user("follow up question"),
        ])
        entries = self.gy.build_index()
        self.assertEqual(len(entries), 1)
        self.assertIn("the real first user message", entries[0].first_user)

    def test_build_index_extracts_last_user_message(self):
        self._make_session("proj-a", "11111111-2222-3333-4444-555555555555", [
            _user("first thing"),
            _assistant("..."),
            _user("middle thing"),
            _assistant("..."),
            _user("the actual last user message"),
        ])
        entries = self.gy.build_index()
        self.assertEqual(len(entries), 1)
        self.assertIn("the actual last user message", entries[0].last_user)

    def test_build_index_resolves_label_from_aoe_registry(self):
        uuid = "11111111-2222-3333-4444-555555555555"
        self._make_session("proj-a", uuid, [_user("anything")])
        # Plant a fake AoE sessions.json mapping this UUID to a friendly title.
        aoe_dir = Path(self.tmp_agora) / "aoe-config" / "default"
        aoe_dir.mkdir(parents=True)
        sessions_path = aoe_dir / "sessions.json"
        sessions_path.write_text(json.dumps({"sessions": [
            {"id": "deadbeef", "title": "the-friendly-name",
             "agent_session_id": uuid},
        ]}))
        self.bus.SESSIONS_JSON = sessions_path

        entries = self.gy.build_index(force=True)
        self.assertEqual(entries[0].label, "the-friendly-name")

    def test_build_index_falls_back_to_uuid_prefix(self):
        uuid = "deadbeef-1111-2222-3333-444444444444"
        # No user messages → can't derive a label from text.
        self._make_session("proj-a", uuid, [_assistant("just me talking")])
        entries = self.gy.build_index()
        self.assertEqual(entries[0].label, "deadbeef")

    def test_build_index_derives_label_from_first_user_when_no_aoe_mapping(self):
        uuid = "11111111-2222-3333-4444-555555555555"
        self._make_session("proj-a", uuid, [
            _user("hey claude lets do vietnam backfill cleanup"),
        ])
        entries = self.gy.build_index()
        # Should NOT be the uuid prefix; should be slug from first user text.
        self.assertNotEqual(entries[0].label, "11111111")
        self.assertIn("vietnam", entries[0].label.lower())

    def test_index_caching_skips_rebuild_when_jsonls_unchanged(self):
        self._make_session("proj-a", "11111111-2222-3333-4444-555555555555",
                           [_user("test")])
        first_pass = self.gy.build_index()
        # Touch index so its mtime is newer than the jsonl.
        time.sleep(0.01)
        self.gy.INDEX_PATH.touch()

        # build_index should detect no staleness and return the cached read.
        with patch.object(self.gy, "_scan_jsonl") as mock_scan:
            mock_scan.side_effect = AssertionError("should not be called")
            second_pass = self.gy.build_index()

        self.assertEqual(len(first_pass), len(second_pass))

    def test_index_rebuilds_when_jsonl_newer_than_index(self):
        uuid = "11111111-2222-3333-4444-555555555555"
        p = self._make_session("proj-a", uuid, [_user("first")])
        self.gy.build_index()

        # Sleep guards against same-second mtime equality on coarse-clock FS.
        time.sleep(1.1)
        _write_jsonl(p, [_user("changed content")])

        self.assertTrue(self.gy.index_is_stale())

    def test_turns_count_is_actual_message_count_not_capped(self):
        """Earlier version broke out of the head scan at HEAD_LINES and
        reported every session as ~201 turns. Verify turns now reflects the
        actual user+assistant message count. Phase 2 self-challenge."""
        uuid = "11111111-2222-3333-4444-555555555555"
        # 350 messages — well above the 200-line head scan cutoff.
        lines = [_user("hey claude lets start")] + [_assistant("ok") for _ in range(349)]
        self._make_session("proj-a", uuid, lines)
        entries = self.gy.build_index()
        self.assertEqual(entries[0].turns, 350,
                         f"turns should be 350, got {entries[0].turns}")

    def test_turns_skips_non_message_lines(self):
        """LOW-1 — tool-result echoes, system entries, and corrupt lines
        must NOT count toward `turns`. Otherwise sessions with heavy tool
        usage report inflated message counts."""
        uuid = "11111111-2222-3333-4444-555555555555"
        lines = [
            _user("hey"),
            {"type": "tool_result", "content": "ls output here"},   # not a message
            _assistant("ok"),
            {"type": "system", "content": "boot"},                  # not a message
            _user("follow up"),
        ]
        self._make_session("proj-a", uuid, lines)
        entries = self.gy.build_index()
        self.assertEqual(entries[0].turns, 3,
                         f"turns should count only user+assistant = 3, got {entries[0].turns}")

    def test_incremental_rebuild_reuses_unchanged_entries(self):
        """MED-3 — when only one jsonl is new/changed, the other entries are
        reused from the previous index (no rescan). Saves ~30s on a 167-session
        graveyard when a single session writes a new line."""
        uuid_a = "11111111-2222-3333-4444-555555555555"
        uuid_b = "22222222-3333-4444-5555-666666666666"
        self._make_session("proj-a", uuid_a, [_user("a-content")])
        self._make_session("proj-b", uuid_b, [_user("b-content")])

        # First pass — full build.
        first = {e.uuid: e for e in self.gy.build_index()}

        # Touch uuid_b's jsonl so only it is stale.
        time.sleep(1.1)
        path_b = Path(self.tmp_claude) / "proj-b" / f"{uuid_b}.jsonl"
        path_b.write_text(json.dumps(_user("b-content-CHANGED")) + "\n")

        # Spy on _scan_jsonl — should only be called for uuid_b on rebuild.
        called_for = []
        original_scan = self.gy._scan_jsonl
        def spy(path, label_map):
            called_for.append(path.stem)
            return original_scan(path, label_map)
        with patch.object(self.gy, "_scan_jsonl", side_effect=spy):
            second = {e.uuid: e for e in self.gy.build_index()}

        # uuid_a was reused (not rescanned); uuid_b was rescanned.
        self.assertEqual(called_for, [uuid_b],
                         f"expected only {uuid_b[:8]} rescanned, got {called_for}")
        # Verify uuid_a kept the SAME entry object content.
        self.assertEqual(second[uuid_a].first_user, first[uuid_a].first_user)
        # And uuid_b has the new content.
        self.assertIn("CHANGED", second[uuid_b].first_user)

    def test_force_rebuild_ignores_cache(self):
        """`build_index(force=True)` re-scans every jsonl even if unchanged."""
        uuid = "11111111-2222-3333-4444-555555555555"
        self._make_session("proj-a", uuid, [_user("anything")])
        self.gy.build_index()

        called = []
        original_scan = self.gy._scan_jsonl
        def spy(path, label_map):
            called.append(path.stem)
            return original_scan(path, label_map)
        with patch.object(self.gy, "_scan_jsonl", side_effect=spy):
            self.gy.build_index(force=True)
        self.assertEqual(called, [uuid],
                         f"force=True should always rescan; got {called}")

    def test_skip_non_user_and_short_messages(self):
        """Tool-result echoes + <-tag system reminders + sub-10-char messages
        must not be picked as first_user or last_user."""
        uuid = "11111111-2222-3333-4444-555555555555"
        self._make_session("proj-a", uuid, [
            _user("ok"),                          # too short
            _user("<tool_result>blah</tool_result>"),  # tag-prefixed
            _user("the real first user message"),
            _assistant("ack"),
        ])
        entries = self.gy.build_index()
        self.assertIn("the real first user message", entries[0].first_user)
        self.assertNotIn("tool_result", entries[0].first_user)


class TestGraveyardSearch(unittest.TestCase):
    def setUp(self):
        self.tmp_agora = tempfile.mkdtemp()
        self.tmp_claude = tempfile.mkdtemp()
        os.environ["AGORA_ROOT"] = self.tmp_agora
        for mod in list(sys.modules):
            if mod.startswith("lib"):
                del sys.modules[mod]
        from lib import graveyard
        self.gy = graveyard
        self.gy.CLAUDE_PROJECTS_ROOT = Path(self.tmp_claude)
        self.gy.GRAVEYARD_ROOT = Path(self.tmp_agora) / "graveyard"
        self.gy.INDEX_PATH = self.gy.GRAVEYARD_ROOT / "index.jsonl"
        self.gy.LIVE_DIR = self.gy.GRAVEYARD_ROOT / "live"

    def tearDown(self):
        shutil.rmtree(self.tmp_agora, ignore_errors=True)
        shutil.rmtree(self.tmp_claude, ignore_errors=True)
        os.environ.pop("AGORA_ROOT", None)

    def _seed(self, uuid: str, first: str, last: str = "") -> None:
        p = Path(self.tmp_claude) / "proj" / f"{uuid}.jsonl"
        _write_jsonl(p, [_user(first), _user(last or "trailing message")])

    def test_grave_dig_returns_empty_when_no_match(self):
        self._seed("11111111-2222-3333-4444-555555555555",
                   "totally unrelated content here")
        results = self.gy.grave_dig("vietnam backfill")
        self.assertEqual(results, [])

    def test_grave_dig_matches_on_keyword(self):
        self._seed("11111111-2222-3333-4444-555555555555",
                   "vietnam backfill is broken")
        self._seed("22222222-3333-4444-5555-666666666666",
                   "unrelated session about react components")
        results = self.gy.grave_dig("vietnam")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].entry.uuid,
                         "11111111-2222-3333-4444-555555555555")
        self.assertIn("vietnam", results[0].hits)

    def test_grave_dig_ranks_label_match_above_body_only(self):
        # Same keyword presence in both, but one has the term in label.
        u_label = "11111111-2222-3333-4444-555555555555"
        u_body = "22222222-3333-4444-5555-666666666666"
        self._seed(u_label, "hey claude lets do vietnam analysis")
        self._seed(u_body, "background mention of vietnam somewhere",
                   last="follow-up about vietnam")

        # Plant AoE label only on the label-target entry.
        from lib import bus
        aoe_dir = Path(self.tmp_agora) / "aoe-config" / "default"
        aoe_dir.mkdir(parents=True)
        sessions_path = aoe_dir / "sessions.json"
        sessions_path.write_text(json.dumps({"sessions": [
            {"id": "x", "title": "vietnam-special",
             "agent_session_id": u_label},
        ]}))
        bus.SESSIONS_JSON = sessions_path

        results = self.gy.grave_dig("vietnam", limit=10)
        # Top result should be the labeled one (matches label AND keywords).
        self.assertEqual(results[0].entry.uuid, u_label)

    def test_resolve_by_exact_uuid(self):
        u = "11111111-2222-3333-4444-555555555555"
        self._seed(u, "anything")
        entry = self.gy.resolve(u)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.uuid, u)

    def test_resolve_by_uuid_prefix(self):
        u = "deadbeef-1111-2222-3333-444444444444"
        self._seed(u, "anything")
        entry = self.gy.resolve("deadbeef")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.uuid, u)

    def test_resolve_returns_none_for_ambiguous_prefix(self):
        self._seed("deadbeef-1111-2222-3333-444444444444", "a")
        self._seed("deadbeef-9999-9999-9999-999999999999", "b")
        self.assertIsNone(self.gy.resolve("deadbeef"))

    def test_resolve_handles_special_chars_safely(self):
        # Query with characters that would break a naive regex must NOT crash.
        self._seed("11111111-2222-3333-4444-555555555555", "normal session")
        # Should just return None, not raise.
        self.assertIsNone(self.gy.resolve("..*[invalid]"))
        self.assertEqual(self.gy.grave_dig("..*[invalid]"), [])


if __name__ == "__main__":
    unittest.main()
