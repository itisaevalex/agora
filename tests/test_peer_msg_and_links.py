"""Tests for peer-msg format + link store."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestPeerMsg(unittest.TestCase):
    def setUp(self):
        for mod in list(sys.modules):
            if mod.startswith("lib"):
                del sys.modules[mod]
        from lib import peer_msg
        self.pm = peer_msg

    def test_roundtrip(self):
        msg = self.pm.PeerMsg(
            sender_label="alice",
            thread="t_abc",
            msg_type="ask",
            body="hello world",
            at="2026-05-22T10:00:00Z",
        )
        wire = msg.to_wire()
        parsed = self.pm.parse(wire)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].sender_label, "alice")
        self.assertEqual(parsed[0].thread, "t_abc")
        self.assertEqual(parsed[0].msg_type, "ask")
        self.assertEqual(parsed[0].body, "hello world")

    def test_parse_finds_multiple_msgs(self):
        text = (
            '<peer-msg from="a" thread="t1" type="ask" at="ts">body1</peer-msg>\n'
            'some prose\n'
            '<peer-msg from="b" thread="t1" type="reply" at="ts">body2</peer-msg>'
        )
        result = self.pm.parse(text)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].sender_label, "a")
        self.assertEqual(result[1].sender_label, "b")

    def test_parse_skips_malformed_missing_attrs(self):
        text = '<peer-msg from="a" thread="t1" type="ask">no at</peer-msg>'
        self.assertEqual(self.pm.parse(text), [])

    def test_parse_skips_invalid_type(self):
        text = (
            '<peer-msg from="a" thread="t1" type="shout" at="ts">x</peer-msg>'
        )
        self.assertEqual(self.pm.parse(text), [])

    def test_parse_handles_multiline_body(self):
        text = (
            '<peer-msg from="a" thread="t1" type="ask" at="ts">'
            'line one\nline two\nline three'
            '</peer-msg>'
        )
        result = self.pm.parse(text)
        self.assertEqual(len(result), 1)
        self.assertIn("line three", result[0].body)

    def test_attr_escaping_survives_roundtrip(self):
        msg = self.pm.PeerMsg(
            sender_label='label with "quotes"',
            thread="t1", msg_type="ask",
            body="body", at="ts",
        )
        parsed = self.pm.parse(msg.to_wire())[0]
        self.assertEqual(parsed.sender_label, 'label with "quotes"')

    def test_parse_no_match_returns_empty(self):
        self.assertEqual(self.pm.parse("just regular prose"), [])


class TestLinks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["AGORA_ROOT"] = self.tmp
        for mod in list(sys.modules):
            if mod.startswith("lib"):
                del sys.modules[mod]
        from lib import bus, links
        self.bus = bus
        self.links = links
        self.bus.BUS_ROOT = Path(self.tmp)
        self.bus.ensure_bus_root()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("AGORA_ROOT", None)

    def test_empty_links_for_new_session(self):
        self.assertEqual(self.links.load("self123"), [])

    def test_add_and_load(self):
        added, msg = self.links.add("self123", "peer456", "Slovenia")
        self.assertTrue(added)
        loaded = self.links.load("self123")
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["aoe_id"], "peer456")
        self.assertEqual(loaded[0]["label"], "Slovenia")

    def test_add_is_idempotent(self):
        self.links.add("self123", "peer456", "Slovenia")
        added, msg = self.links.add("self123", "peer456", "Slovenia")
        self.assertFalse(added)
        self.assertIn("already linked", msg)
        self.assertEqual(len(self.links.load("self123")), 1)

    def test_refuse_self_link(self):
        added, msg = self.links.add("self123", "self123", "myself")
        self.assertFalse(added)
        self.assertIn("itself", msg)

    def test_remove_by_label(self):
        self.links.add("self123", "peer456", "Slovenia")
        removed, _ = self.links.remove("self123", "Slovenia")
        self.assertTrue(removed)
        self.assertEqual(self.links.load("self123"), [])

    def test_remove_by_id_prefix(self):
        self.links.add("self123", "peer456abc", "Slovenia")
        removed, _ = self.links.remove("self123", "peer456")
        self.assertTrue(removed)

    def test_remove_missing(self):
        removed, msg = self.links.remove("self123", "ghost")
        self.assertFalse(removed)
        self.assertIn("no link", msg)

    def test_find(self):
        self.links.add("self123", "peer456", "Slovenia")
        found = self.links.find("self123", "Slovenia")
        self.assertIsNotNone(found)
        self.assertEqual(found["aoe_id"], "peer456")
        self.assertIsNone(self.links.find("self123", "ghost"))


if __name__ == "__main__":
    unittest.main()
