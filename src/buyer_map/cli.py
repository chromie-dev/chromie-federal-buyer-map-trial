import argparse
import json
from pathlib import Path

from .models import validate_graph
from .ranking import rank_edges


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    graph = json.loads(args.input.read_text())
    errors = validate_graph(graph)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "graph.json").write_text(json.dumps(graph, indent=2) + "\n")
    (args.output / "buyer_paths.json").write_text(json.dumps(rank_edges(graph), indent=2) + "\n")
    (args.output / "evidence_gaps.json").write_text(json.dumps(errors, indent=2) + "\n")
    print(f"nodes={len(graph.get('nodes', []))} edges={len(graph.get('edges', []))} gaps={len(errors)}")


if __name__ == "__main__":
    main()

