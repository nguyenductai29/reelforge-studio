"""Reading stored workflow graphs and ordering their nodes."""
from collections import deque
import json


def parse_graph(definition: str) -> dict:
    """Read a workflow definition or run snapshot.

    Workflows created before visual editing stored a list of node types; they
    read as a left-to-right chain.
    """
    value = json.loads(definition)
    if isinstance(value, list):
        nodes = [{"id": f"n{i}", "type": kind, "x": i * 260, "y": 180} for i, kind in enumerate(value)]
        return {"nodes": nodes, "edges": [{"source": f"n{i}", "target": f"n{i + 1}"} for i in range(len(nodes) - 1)]}
    return value


def ordered_nodes(graph: dict) -> list[tuple[dict, list[str]]]:
    """Topological order with stable ordering for independent nodes, each with its parent IDs."""
    nodes = {node["id"]: node for node in graph["nodes"]}
    incoming = {key: [] for key in nodes}
    outgoing = {key: [] for key in nodes}
    for edge in graph["edges"]:
        incoming[edge["target"]].append(edge["source"])
        outgoing[edge["source"]].append(edge["target"])
    degree = {key: len(sources) for key, sources in incoming.items()}
    queue = deque(key for key, count in degree.items() if not count)
    order = []
    while queue:
        key = queue.popleft()
        order.append((nodes[key], incoming[key]))
        for target in outgoing[key]:
            degree[target] -= 1
            if degree[target] == 0:
                queue.append(target)
    if len(order) != len(nodes):
        raise ValueError("Workflow contains a cycle")
    return order


def count_nodes(graph: dict, node_type: str) -> int:
    return sum(node["type"] == node_type for node in graph["nodes"])
