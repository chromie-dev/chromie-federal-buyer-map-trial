ROLE_WEIGHTS = {
    "owns_mission": 4.0,
    "owns_program": 4.0,
    "funds": 3.0,
    "procures_for": 3.0,
    "administers": 2.0,
    "holds": 1.0,
}


def rank_edges(graph: dict) -> list[dict]:
    ranked = []
    for edge in graph.get("edges", []):
        evidence = edge.get("evidence", {})
        score = ROLE_WEIGHTS.get(edge.get("type"), 0.5) * float(evidence.get("confidence", 0))
        ranked.append({**edge, "score": round(score, 3)})
    return sorted(ranked, key=lambda item: item["score"], reverse=True)

