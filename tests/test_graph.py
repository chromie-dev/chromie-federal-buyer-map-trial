import unittest

from buyer_map.models import validate_graph
from buyer_map.ranking import rank_edges


class GraphTests(unittest.TestCase):
    def test_missing_evidence_is_rejected(self) -> None:
        graph = {
            "nodes": [{"id": "a", "type": "agency"}],
            "edges": [{"from": "a", "to": "a", "type": "owns_mission", "evidence": {}}],
        }
        self.assertEqual(len(validate_graph(graph)), 3)


    def test_mission_ownership_ranks_above_weak_administration(self) -> None:
        graph = {"edges": [
            {"type": "administers", "evidence": {"confidence": 0.5}},
            {"type": "owns_mission", "evidence": {"confidence": 0.9}},
        ]}
        self.assertEqual(rank_edges(graph)[0]["type"], "owns_mission")


if __name__ == "__main__":
    unittest.main()
