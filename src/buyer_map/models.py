from dataclasses import dataclass


ALLOWED_NODE_TYPES = {
    "agency", "subagency", "program_office", "contracting_office",
    "program", "person", "solicitation", "award", "company",
}


@dataclass(frozen=True)
class Evidence:
    source_url: str
    observed_at: str
    confidence: float


def validate_graph(graph: dict) -> list[str]:
    errors: list[str] = []
    ids = {node.get("id") for node in graph.get("nodes", [])}
    for node in graph.get("nodes", []):
        if node.get("type") not in ALLOWED_NODE_TYPES:
            errors.append(f"invalid node type: {node.get('type')}")
    for edge in graph.get("edges", []):
        if edge.get("from") not in ids or edge.get("to") not in ids:
            errors.append(f"dangling edge: {edge.get('from')} -> {edge.get('to')}")
        evidence = edge.get("evidence", {})
        for field in ("source_url", "observed_at", "confidence"):
            if evidence.get(field) in (None, ""):
                errors.append(f"edge missing evidence.{field}: {edge.get('from')} -> {edge.get('to')}")
    return errors

