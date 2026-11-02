"""ACHEWIW experimental production DAG core (no jobs or models executed).

This is a small, deterministic validation/visualization helper. It is not
connected to the v0.1 service and does not access the host GPU.
"""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

NodeResource = Literal["none", "cpu", "gpu", "author"]
NodeStatus = Literal[
    "pending", "ready", "running", "waiting_author", "done", "failed", "stale", "cancelled"
]


class GraphError(ValueError):
    """Invalid production DAG."""


class PipelineNode(BaseModel):
    id: str = Field(min_length=1, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    label: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    resource: NodeResource = "cpu"
    status: NodeStatus = "pending"
    source_snapshot_hash: str | None = None
    output_artifact_hash: str | None = None
    layout_x: float | None = None
    layout_y: float | None = None


class ProductionGraph(BaseModel):
    schema_version: int = Field(default=1, ge=1)
    work_id: str
    edition_id: str
    description: str = ""
    nodes: list[PipelineNode] = Field(default_factory=list)

    def _indexed(self) -> dict[str, PipelineNode]:
        result = {}
        for node in self.nodes:
            if node.id in result:
                raise GraphError(f"Duplicate node id: {node.id}")
            result[node.id] = node
        return result

    def topology(self) -> list[str]:
        """Return dependency-first order, rejecting missing refs and cycles."""
        nodes = self._indexed()
        incoming = {node.id: set(node.depends_on) for node in self.nodes}
        downstream: dict[str, set[str]] = {node_id: set() for node_id in nodes}
        for node in self.nodes:
            if len(node.depends_on) != len(set(node.depends_on)):
                raise GraphError(f"Duplicate dependency on node: {node.id}")
            for dep in node.depends_on:
                if dep not in nodes:
                    raise GraphError(f"Unknown dependency {dep} for {node.id}")
                if dep == node.id:
                    raise GraphError(f"Self-dependency: {node.id}")
                downstream[dep].add(node.id)
        ready = deque(sorted(key for key, deps in incoming.items() if not deps))
        ordered: list[str] = []
        while ready:
            current = ready.popleft()
            ordered.append(current)
            for child in sorted(downstream[current]):
                incoming[child].remove(current)
                if not incoming[child]:
                    ready.append(child)
        if len(ordered) != len(nodes):
            raise GraphError("Production graph contains a dependency cycle")
        return ordered

    def affected_by_change(self, node_id: str) -> list[str]:
        """Return all downstream node IDs; no artefacts are deleted."""
        nodes = self._indexed()
        if node_id not in nodes:
            raise GraphError(f"Unknown node: {node_id}")
        self.topology()
        affected = {node_id}
        for candidate in self.topology():
            if any(dep in affected for dep in nodes[candidate].depends_on):
                affected.add(candidate)
        affected.remove(node_id)
        return [id for id in self.topology() if id in affected]

    def mark_stale(self, node_id: str) -> list[str]:
        """Mark completed downstream nodes as stale, without editing outputs."""
        nodes = self._indexed()
        affected = self.affected_by_change(node_id)
        running = [id for id in affected if nodes[id].status == "running"]
        if running:
            raise GraphError("Cannot invalidate running nodes without cancellation: "
                             + ", ".join(running))
        for id in affected:
            if nodes[id].status in ("done", "ready"):
                nodes[id].status = "stale"
        return affected

    def validate_release_gate_structure(self) -> bool:
        """Validate required editorial QA ancestry for final publishing outputs.

        A structural safeguard only: statuses and legal judgements must be
        verified separately by an approved snapshot-bound ReleaseGate.
        """
        nodes = self._indexed()
        self.topology()
        kinds = {node.kind: node.id for node in self.nodes}
        gate_nodes = [node for node in self.nodes
                      if node.kind == "release.editorial_gate"]
        if len(gate_nodes) != 1 or gate_nodes[0].resource != "author":
            raise GraphError("Expected exactly one author-approved editorial gate")
        gate = gate_nodes[0]
        required_kinds = {
            "qa.canon", "qa.originality",
            "qa.jurisdictional_content", "qa.rights"
        }
        def ancestors(node_id: str) -> set[str]:
            found: set[str] = set()
            frontier = list(nodes[node_id].depends_on)
            while frontier:
                candidate = frontier.pop()
                if candidate in found:
                    continue
                found.add(candidate)
                frontier.extend(nodes[candidate].depends_on)
            return found
        qa_ancestors = ancestors(gate.id)
        for kind in required_kinds:
            if not any(nodes[ancestor].kind == kind for ancestor in qa_ancestors):
                raise GraphError(f"Missing upstream editorial check: {kind}")
        published_kinds = {
            "publish.pdfx", "publish.ebook",
            "marketing.package", "release.approve"
        }
        for node in self.nodes:
            if node.kind in published_kinds and gate.id not in ancestors(node.id):
                raise GraphError(f"Final exporter {node.id} bypasses editorial gate")
        return True

    def to_mermaid(self) -> str:
        """Render a safe text-based overview suitable for GitHub Markdown."""
        self.topology()
        def safe(s: str) -> str:
            return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", " ")
        lines = ["flowchart TD"]
        for n in self.nodes:
            lines.append(f'    {n.id}["{safe(n.label)} ({safe(n.status)})"]')
        for n in self.nodes:
            for dep in n.depends_on:
                lines.append(f"    {dep} --> {n.id}")
        return "\n".join(lines)

    @classmethod
    def from_json_file(cls, path: str | Path) -> "ProductionGraph":
        return cls.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Validate and render an ACHEWIW production DAG")
    parser.add_argument("path", help="Path to pipeline JSON")
    arguments = parser.parse_args()
    graph = ProductionGraph.from_json_file(arguments.path)
    print(graph.to_mermaid())
