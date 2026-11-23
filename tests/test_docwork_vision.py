"""Deterministic unit tests for the local vision-response trust boundary."""
import json
import unittest

from docwork_vision import parse_vision_reply, vision_prompt


class VisionParserTests(unittest.TestCase):
    def test_valid_boxes_text_and_horizontal_line(self):
        payload = {"proposals": [
            {"type": "rectangle", "x": 10, "y": 20, "w": 100, "h": 80, "confidence": .8},
            {"type": "text", "x": 15, "y": 30, "w": 60, "h": 20, "text": "Насос", "confidence": .9},
            {"type": "arrow", "x": 120, "y": 50, "w": 100, "h": 0, "confidence": .6},
        ]}
        result = parse_vision_reply(json.dumps(payload), width=500, height=300)
        self.assertEqual([x["id"] for x in result], ["candidate-1", "candidate-2", "candidate-3"])
        self.assertEqual(result[1]["text"], "Насос")
        self.assertTrue(all(x["verification"] == "unverified" for x in result))

    def test_reject_out_of_bounds_and_unknown_type(self):
        payload = {"proposals": [
            {"type": "rectangle", "x": 490, "y": 10, "w": 50, "h": 20},
            {"type": "exec", "x": 0, "y": 0, "w": 20, "h": 20},
        ]}
        self.assertEqual(parse_vision_reply(json.dumps(payload), width=500, height=300), [])

    def test_fenced_json(self):
        s = "```json\n" + json.dumps({"proposals": []}) + "\n```"
        self.assertEqual(parse_vision_reply(s, width=100, height=100), [])

    def test_reject_invalid_structure(self):
        for s in ('{"unknown":[]}', '{"proposals":null}', 'not JSON'):
            with self.assertRaises(ValueError):
                parse_vision_reply(s, width=100, height=100)

    def test_discard_ambiguous_text_and_bad_confidence(self):
        s = json.dumps({"proposals": [
            {"type":"text", "x":0,"y":0,"w":50,"h":20, "text": ""},
            {"type":"ellipse", "x":1,"y":1,"w":30,"h":30,"confidence":2}
        ]})
        result = parse_vision_reply(s, width=100, height=100)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["confidence"], 0)

    def test_reject_extreme_numbers_and_boolean_coordinates(self):
        payload = {"proposals": [
            {"type": "rectangle", "x": 10**400, "y": 0, "w": 10, "h": 10},
            {"type": "rectangle", "x": True, "y": 0, "w": 10, "h": 10},
            {"type": "rectangle", "x": 0, "y": 0, "w": 10, "h": 10}
        ]}
        result = parse_vision_reply(json.dumps(payload), width=100, height=100)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "candidate-1")

    def test_gpu_reservation_is_exclusive(self):
        import sqlite3
        from gpu_scheduling import init_gpu_leases, reserve_gpu, release_gpu
        conn = sqlite3.connect(":memory:")
        init_gpu_leases(conn)
        self.assertTrue(reserve_gpu(conn, "one", "docwork_vision", at=1000))
        self.assertFalse(reserve_gpu(conn, "two", "docwork_vision", at=1001))
        release_gpu(conn, "one")
        self.assertTrue(reserve_gpu(conn, "two", "docwork_vision", at=1001))
        conn.close()

    def test_prompt_is_bounded_and_explicit(self):
        s = vision_prompt(800, 500)
        self.assertIn("800x500", s)
        self.assertIn("Never invent", s)


if __name__ == "__main__":
    unittest.main()
