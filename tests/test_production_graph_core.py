"""Tests of the not-yet-integrated production DAG."""
import unittest

from production_graph_core import GraphError, PipelineNode, ProductionGraph


class PublishingGraphTests(unittest.TestCase):
    def sample(self):
        return ProductionGraph(
            work_id="demo", edition_id="v1",
            nodes=[
                PipelineNode(id="canon", label="Truth", kind="canon.freeze", status="done"),
                PipelineNode(id="text", label="Text", kind="text.compile",
                             depends_on=["canon"], status="done"),
                PipelineNode(id="cover", label="Cover", kind="art.render",
                             depends_on=["canon"], status="done", resource="gpu"),
                PipelineNode(id="layout", label="Layout", kind="layout.compose",
                             depends_on=["text", "cover"], status="done"),
                PipelineNode(id="print", label="Print", kind="publish.pdfx",
                             depends_on=["layout"], status="done"),
            ],
        )

    def test_topology_and_dependencies(self):
        graph = self.sample()
        order = graph.topology()
        self.assertLess(order.index("canon"), order.index("text"))
        self.assertLess(order.index("cover"), order.index("layout"))
        self.assertLess(order.index("layout"), order.index("print"))

    def test_invalidate_just_downstream_branch(self):
        graph = self.sample()
        affected = graph.mark_stale("cover")
        self.assertEqual(affected, ["layout", "print"])
        self.assertEqual(graph._indexed()["text"].status, "done")
        self.assertEqual(graph._indexed()["layout"].status, "stale")

    def test_reject_cycle(self):
        graph = self.sample()
        graph.nodes[0].depends_on = ["print"]
        with self.assertRaisesRegex(GraphError, "cycle"):
            graph.topology()

    def test_reject_missing_dependency(self):
        graph = self.sample()
        graph.nodes[1].depends_on = ["missing"]
        with self.assertRaisesRegex(GraphError, "Unknown"):
            graph.topology()

    def test_running_outputs_not_touched(self):
        graph = self.sample()
        graph.nodes[-1].status = "running"
        with self.assertRaises(GraphError):
            graph.mark_stale("cover")
        self.assertEqual(graph.nodes[-2].status, "done")

    def test_mermaid(self):
        graph = self.sample()
        diagram = graph.to_mermaid()
        self.assertIn("canon --> cover", diagram)
        self.assertIn("layout --> print", diagram)


if __name__ == "__main__":
    unittest.main()
