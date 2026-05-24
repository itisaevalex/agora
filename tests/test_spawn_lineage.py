"""Tests for spawn + lineage."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestLineageStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["AGORA_ROOT"] = self.tmp
        for mod in list(sys.modules):
            if mod.startswith("lib"):
                del sys.modules[mod]
        from lib import bus, lineage
        self.bus = bus
        self.lineage = lineage
        self.bus.BUS_ROOT = Path(self.tmp)
        self.bus.ensure_bus_root()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("AGORA_ROOT", None)

    def test_empty_lineage(self):
        self.assertEqual(self.lineage.load(), {})

    def test_register_root(self):
        self.lineage.register("a", "Alice", parent_id=None, task="hello")
        data = self.lineage.load()
        self.assertEqual(data["a"]["title"], "Alice")
        self.assertIsNone(data["a"]["parent"])

    def test_register_child(self):
        self.lineage.register("a", "Alice")
        self.lineage.register("b", "Bob", parent_id="a")
        self.assertEqual(self.lineage.load()["b"]["parent"], "a")

    def test_ancestors(self):
        # a -> b -> c -> d
        self.lineage.register("a", "A")
        self.lineage.register("b", "B", parent_id="a")
        self.lineage.register("c", "C", parent_id="b")
        self.lineage.register("d", "D", parent_id="c")
        self.assertEqual(self.lineage.ancestors("d"), ["c", "b", "a"])
        self.assertEqual(self.lineage.ancestors("a"), [])

    def test_children_direct_only(self):
        # a -> b, a -> c, b -> d
        self.lineage.register("a", "A")
        self.lineage.register("b", "B", parent_id="a")
        self.lineage.register("c", "C", parent_id="a")
        self.lineage.register("d", "D", parent_id="b")
        self.assertEqual(sorted(self.lineage.children("a")), ["b", "c"])
        self.assertEqual(self.lineage.children("b"), ["d"])
        self.assertEqual(self.lineage.children("c"), [])

    def test_descendants_transitive(self):
        # a -> b -> c, a -> d
        self.lineage.register("a", "A")
        self.lineage.register("b", "B", parent_id="a")
        self.lineage.register("c", "C", parent_id="b")
        self.lineage.register("d", "D", parent_id="a")
        descs = sorted(self.lineage.descendants("a"))
        self.assertEqual(descs, ["b", "c", "d"])
        self.assertEqual(self.lineage.descendants("c"), [])

    def test_ancestors_doesnt_infinite_loop(self):
        # Defensive: corrupted lineage with a cycle
        self.lineage.register("a", "A", parent_id="b")
        self.lineage.register("b", "B", parent_id="a")
        # Should not hang
        result = self.lineage.ancestors("a")
        self.assertIn("b", result)
        self.assertLessEqual(len(result), 2)

    def test_count_recent_children(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.lineage.register("a", "A")
        # Two recent children
        self.lineage.register("b", "B", parent_id="a")
        self.lineage.register("c", "C", parent_id="a")
        # One ancient child (back-date manually)
        data = self.lineage.load()
        self.lineage.register("d", "D", parent_id="a")
        data = self.lineage.load()
        data["d"]["spawned_at"] = "2020-01-01T00:00:00Z"
        self.lineage.save(data)
        self.assertEqual(self.lineage.count_recent_children("a", since_secs=3600), 2)

    def test_render_tree_basic(self):
        # a -> b -> c, a -> d
        self.lineage.register("a", "Alice")
        self.lineage.register("b", "Bob", parent_id="a")
        self.lineage.register("c", "Carol", parent_id="b")
        self.lineage.register("d", "Dave", parent_id="a")
        rendered = self.lineage.render_tree("a")
        self.assertIn("Alice", rendered)
        self.assertIn("Bob", rendered)
        self.assertIn("Carol", rendered)
        self.assertIn("Dave", rendered)
        # Bob should appear before Carol (parent before child)
        self.assertLess(rendered.index("Bob"), rendered.index("Carol"))

    def test_render_tree_empty_for_leaf_with_no_kids(self):
        self.lineage.register("a", "Alice")
        rendered = self.lineage.render_tree("a")
        self.assertIn("Alice", rendered)


class TestSpawn(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["AGORA_ROOT"] = self.tmp
        for mod in list(sys.modules):
            if mod.startswith("lib"):
                del sys.modules[mod]
        from lib import bus, lineage, spawn, links
        self.bus = bus
        self.lineage = lineage
        self.spawn = spawn
        self.links = links
        self.bus.BUS_ROOT = Path(self.tmp)
        self.bus.ensure_bus_root()
        self.parent = self.bus.SessionIdentity(aoe_id="parent-id", label="Parent")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("AGORA_ROOT", None)
        os.environ.pop("AGORA_SPAWN_BUDGET", None)

    def test_dry_run_does_not_call_aoe(self):
        with patch("subprocess.run") as run:
            ok, msg, cid = self.spawn.spawn(self.parent, "child1", "do the thing", dry_run=True)
        self.assertTrue(ok)
        self.assertIsNone(cid)
        run.assert_not_called()

    def test_invalid_title_rejected(self):
        ok, msg, _ = self.spawn.spawn(self.parent, "child;rm -rf /", "task")
        self.assertFalse(ok)
        self.assertIn("invalid title", msg)

    def test_spawn_creates_lineage_and_links(self):
        # Mock aoe add to return a fake aoe-id
        fake_out = "✓ Added session: child1\n  ID:      abcdef123456\n"
        fake_run = MagicMock(returncode=0, stdout=fake_out, stderr="")
        with patch("subprocess.run", return_value=fake_run), \
             patch("time.sleep"):
            ok, msg, cid = self.spawn.spawn(self.parent, "child1", "go forth")
        self.assertTrue(ok)
        self.assertEqual(cid, "abcdef123456")
        # Lineage recorded
        data = self.lineage.load()
        self.assertEqual(data["abcdef123456"]["parent"], "parent-id")
        # Bidirectional link
        parent_links = [L["aoe_id"] for L in self.links.load("parent-id")]
        child_links = [L["aoe_id"] for L in self.links.load("abcdef123456")]
        self.assertIn("abcdef123456", parent_links)
        self.assertIn("parent-id", child_links)

    def test_spawn_links_grandparent_to_grandchild(self):
        # Set up: GP -> Parent
        self.lineage.register("gp-id", "GrandParent", parent_id=None)
        self.lineage.register(self.parent.aoe_id, self.parent.label, parent_id="gp-id")

        fake_out = "  ID:      abc123def456\n"
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout=fake_out, stderr="")), \
             patch("time.sleep"):
            ok, _, cid = self.spawn.spawn(self.parent, "GrandChild", "task")
        self.assertTrue(ok)
        # GP should now have a link to GrandChild
        gp_links = [L["aoe_id"] for L in self.links.load("gp-id")]
        self.assertIn(cid, gp_links)
        # GrandChild should have a link back to GP
        gc_links = [L["aoe_id"] for L in self.links.load(cid)]
        self.assertIn("gp-id", gc_links)

    def test_budget_blocks_spawn_after_threshold(self):
        os.environ["AGORA_SPAWN_BUDGET"] = "2"
        # Pre-populate 2 recent children
        self.lineage.register("c1", "C1", parent_id="parent-id")
        self.lineage.register("c2", "C2", parent_id="parent-id")
        ok, msg, _ = self.spawn.spawn(self.parent, "third", "task", dry_run=True)
        self.assertFalse(ok)
        self.assertIn("budget exhausted", msg)

    def test_aoe_add_failure_returns_false(self):
        with patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="", stderr="bad")):
            ok, msg, _ = self.spawn.spawn(self.parent, "child1", "task")
        self.assertFalse(ok)

    def test_no_id_in_output_returns_false(self):
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="weird no id here", stderr="")):
            ok, msg, _ = self.spawn.spawn(self.parent, "child1", "task")
        self.assertFalse(ok)
        self.assertIn("parse aoe-id", msg)


class TestSpawnTaskDelivery(unittest.TestCase):
    """Regression: initial task must land in child's inbox.md durably,
    even when tmux delivery silently truncates large bodies."""

    def setUp(self):
        import tempfile, os, sys
        from pathlib import Path
        self.tmp = tempfile.mkdtemp()
        os.environ["AGORA_ROOT"] = self.tmp
        for mod in list(sys.modules):
            if mod.startswith("lib"):
                del sys.modules[mod]
        from lib import bus, lineage, spawn, inbox
        self.bus = bus
        self.lineage = lineage
        self.spawn = spawn
        self.inbox = inbox
        self.bus.BUS_ROOT = Path(self.tmp)
        self.bus.ensure_bus_root()
        self.parent = self.bus.SessionIdentity(aoe_id="parent-id", label="Parent")

    def tearDown(self):
        import os, shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("AGORA_ROOT", None)

    def _spawn_with(self, task: str):
        """Helper: run spawn() with all external IO mocked to return success."""
        fake_add = MagicMock(returncode=0, stdout="  ID:      cafebabe1234\n", stderr="")
        with patch("subprocess.run", return_value=fake_add), \
             patch("time.sleep"), \
             patch.object(self.bus, "aoe_send_peer_msg", return_value=(True, "delivered")) as mock_send:
            ok, msg, cid = self.spawn.spawn(self.parent, "child1", task)
        return ok, cid, mock_send

    def test_full_task_written_to_inbox(self):
        large_task = "ROLE: builder\n" + ("X" * 5000)
        ok, cid, _ = self._spawn_with(large_task)
        self.assertTrue(ok)
        self.assertEqual(cid, "cafebabe1234")
        # Inbox holds the FULL body, not just a preview
        inbox_text = self.inbox.peek(cid)
        self.assertIn("ROLE: builder", inbox_text)
        # 5000-byte tail must be present
        self.assertIn("X" * 100, inbox_text)
        self.assertGreater(len(inbox_text), 5000)

    def test_full_task_written_to_lineage_task_md(self):
        large_task = "FULL TASK BRIEF\n" + ("Y" * 8000)
        ok, cid, _ = self._spawn_with(large_task)
        self.assertTrue(ok)
        # Durable copy on disk for recovery
        full = self.lineage.read_task(cid)
        self.assertIsNotNone(full)
        self.assertEqual(full, large_task)
        # lineage.json still has just the 200-char preview (index stays small)
        data = self.lineage.load()
        self.assertEqual(len(data[cid]["task"]), 200)

    def test_delivery_routes_through_peer_msg(self):
        """spawn must NOT call `aoe send <id> <huge-body>` directly anymore."""
        task = "any task"
        ok, cid, mock_send = self._spawn_with(task)
        self.assertTrue(ok)
        # aoe_send_peer_msg must be called exactly once with the child id
        mock_send.assert_called_once()
        args, _ = mock_send.call_args
        self.assertEqual(args[0], cid)
        self.assertEqual(args[1], self.parent.label)
        # Wire text contains the task body
        self.assertIn("any task", args[3])

    def test_inbox_persists_even_when_tmux_send_fails(self):
        """If aoe_send_peer_msg fails (tmux gone), inbox.md still holds
        the body so the child can recover on next prompt-submit."""
        task = "important brief"
        fake_add = MagicMock(returncode=0, stdout="  ID:      deadbeef5678\n", stderr="")
        with patch("subprocess.run", return_value=fake_add), \
             patch("time.sleep"), \
             patch.object(self.bus, "aoe_send_peer_msg",
                          return_value=(False, "tmux not running")):
            ok, msg, cid = self.spawn.spawn(self.parent, "child2", task)
        # spawn still returns ok=True because the body is recoverable
        self.assertTrue(ok)
        self.assertIn("tmux nudge failed", msg)
        # Inbox + lineage task.md still populated
        self.assertIn("important brief", self.inbox.peek(cid))
        self.assertEqual(self.lineage.read_task(cid), task)

    def test_no_raw_aoe_send_subprocess_call(self):
        """spawn must not shell out to `aoe send` directly — that path was
        the original bug where tmux silently dropped large bodies."""
        fake_add = MagicMock(returncode=0, stdout="  ID:      abcdef987654\n", stderr="")
        captured = []
        def track(cmd, *a, **kw):
            captured.append(cmd)
            return fake_add
        with patch("subprocess.run", side_effect=track), \
             patch("time.sleep"), \
             patch.object(self.bus, "aoe_send_peer_msg", return_value=(True, "ok")):
            self.spawn.spawn(self.parent, "child3", "task")
        # Only `aoe add` should have been invoked. No `aoe send`.
        aoe_send_calls = [c for c in captured
                          if isinstance(c, list) and len(c) >= 2
                          and c[0] == "aoe" and c[1] == "send"]
        self.assertEqual(aoe_send_calls, [],
                         f"spawn called raw `aoe send` ({len(aoe_send_calls)} times) — "
                         f"this is the original bug. Route through aoe_send_peer_msg.")


if __name__ == "__main__":
    unittest.main()
