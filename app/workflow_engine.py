"""Deterministic local workflow steps. External providers register executors later."""
from collections import deque

AI_TASKS = frozenset({"script", "image", "video", "voice", "music"})


def ordered_nodes(graph):
    """Topological order with stable ordering for independent nodes."""
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


def execute_graph(graph, project, assets, enabled_tools):
    """Return all node outcomes; never invoke providers or bill credits."""
    results = {}
    for node, dependencies in ordered_nodes(graph):
        kind = node["type"]
        if any(results[parent]["status"] != "completed" for parent in dependencies):
            status, detail, output = "skipped", "Chờ bước phía trước hoàn thành.", None
        elif kind == "idea":
            if project.topic.strip() or project.title.strip():
                status, detail, output = "completed", "Đã lấy ý tưởng từ dự án.", {"title": project.title, "topic": project.topic}
            else:
                status, detail, output = "blocked", "Dự án chưa có nội dung ý tưởng.", None
        elif kind == "assets":
            status, detail, output = "completed", "Đã liệt kê media trong studio.", {"assets": [{"id": a.id, "filename": a.filename, "content_type": a.content_type} for a in assets]}
        elif kind in AI_TASKS:
            detail = "Đã chọn model, nhưng chưa kết nối API provider." if kind in enabled_tools else "Chưa chọn công cụ AI cho tác vụ này."
            status, output = "blocked", None
        elif kind == "review":
            status, detail, output = "blocked", "Cần bước duyệt thủ công trước khi tiếp tục.", None
        else:
            status, detail, output = "blocked", "Bước này chưa có bộ thực thi.", None
        results[node["id"]] = {"node_id": node["id"], "node_type": kind, "status": status, "detail": detail, "output": output}
    return list(results.values())
