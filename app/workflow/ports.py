"""Typed ports: what each node type reads and writes, and how edges connect them.

A port has a data type from a small fixed set. An edge may name its ports
(``sourceHandle`` → ``targetHandle``); an edge without them is a legacy edge and
connects the source's default (first) output to the first target input that
accepts that data type, preferring inputs that list the type earliest.

An input's value comes from, in order: connected edges, the node's config
(``config_key``), then the run's context (``context``). Several connections to
a ``multiple`` input are combined: text joined by blank lines, lists concatenated.
"""
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

# Data types. "brief" is a short idea (topic, title); "text" is written content.
BRIEF = "brief"
TEXT = "text"
SCENES = "scenes"
IMAGE_ASSETS = "image_assets"
VIDEO_ASSETS = "video_assets"
AUDIO_ASSETS = "audio_assets"
SUBTITLE_ASSET = "subtitle_asset"
PUBLICATION = "publication"
DATA_TYPES = (BRIEF, TEXT, SCENES, IMAGE_ASSETS, VIDEO_ASSETS, AUDIO_ASSETS, SUBTITLE_ASSET, PUBLICATION)
TEXT_TYPES = frozenset({BRIEF, TEXT})
LIST_TYPES = frozenset({SCENES, IMAGE_ASSETS, VIDEO_ASSETS, AUDIO_ASSETS})

# Context fallbacks an input can declare.
PROJECT_TOPIC = "project_topic"


@dataclass(frozen=True)
class OutputPort:
    name: str
    type: str
    # Output keys to read, first non-empty wins; older outputs may use a later key.
    keys: tuple[str, ...] = ()
    # Derives the value from the whole output instead of reading keys.
    extract: Callable[[Mapping[str, Any]], Any] | None = field(default=None, compare=False)

    def read(self, output: Mapping[str, Any] | None) -> Any:
        if not isinstance(output, Mapping):
            return None
        if self.extract is not None:
            return self.extract(output)
        for key in self.keys or (self.name,):
            value = output.get(key)
            if not is_empty(value):
                return value
        return None

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.type}


@dataclass(frozen=True)
class InputPort:
    name: str
    # Accepted data types, most natural first; legacy edges prefer an earlier position.
    accepts: tuple[str, ...]
    multiple: bool = False
    config_key: str | None = None
    context: str | None = None

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "accepts": list(self.accepts), "multiple": self.multiple}


def is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict)):
        return not value
    return False


def valid_value(data_type: str, value: Any) -> bool:
    """Whether ``value`` has the shape its data type promises; malformed values are dropped."""
    if data_type in TEXT_TYPES:
        return isinstance(value, str)
    if data_type in LIST_TYPES:
        return isinstance(value, list) and all(isinstance(item, Mapping) for item in value)
    return isinstance(value, Mapping)


def combine(port: InputPort, values: list[Any]) -> Any:
    if not port.multiple:
        return values[0]
    if all(isinstance(value, str) for value in values):
        return "\n\n".join(value.strip() for value in values)
    if all(isinstance(value, list) for value in values):
        return [item for value in values for item in value]
    return values[0]


def find_port(ports: Iterable, name: str | None):
    return next((port for port in ports if port.name == name), None) if name else None


def default_output(handler) -> OutputPort | None:
    return handler.outputs[0] if handler.outputs else None


def legacy_input(handler, data_type: str, taken: set[str]) -> InputPort | None:
    """The input a legacy edge carrying ``data_type`` feeds, skipping single inputs already used."""
    candidates = [(port.accepts.index(data_type), index, port) for index, port in enumerate(handler.inputs)
                  if data_type in port.accepts and (port.multiple or port.name not in taken)]
    return min(candidates, key=lambda item: item[:2])[2] if candidates else None


@dataclass(frozen=True)
class Binding:
    """One edge resolved to concrete ports; ``explicit`` when the edge named its target port."""

    source: str
    target: str
    output: OutputPort
    input: InputPort
    explicit: bool


def bind_edges(graph: Mapping[str, Any], registry) -> list[Binding | None]:
    """Resolve every edge to ports, in edge order; ``None`` for an edge that carries no data.

    Named ports that no longer exist or do not fit fall back to the legacy mapping,
    so old snapshots keep running. Explicit edges claim their inputs before legacy
    edges fill what is left.
    """
    types = {node["id"]: node["type"] for node in graph["nodes"]}
    taken: dict[str, set[str]] = {}
    bindings: list[Binding | None] = [None] * len(graph["edges"])
    pending = []
    for index, edge in enumerate(graph["edges"]):
        source_handler = registry.resolve(types.get(edge["source"], ""))
        target_handler = registry.resolve(types.get(edge["target"], ""))
        output = find_port(source_handler.outputs, edge.get("sourceHandle")) or default_output(source_handler)
        if output is None:
            continue
        named = find_port(target_handler.inputs, edge.get("targetHandle"))
        if named is not None and output.type in named.accepts:
            taken.setdefault(edge["target"], set()).add(named.name)
            bindings[index] = Binding(edge["source"], edge["target"], output, named, True)
        else:
            pending.append((index, edge, output, target_handler))
    for index, edge, output, target_handler in pending:
        used = taken.setdefault(edge["target"], set())
        port = legacy_input(target_handler, output.type, used)
        if port is not None:
            used.add(port.name)
            bindings[index] = Binding(edge["source"], edge["target"], output, port, False)
    return bindings


def normalize_edges(graph: Mapping[str, Any], registry) -> dict[str, Any]:
    """A copy of the graph whose edges name the ports they actually connect.

    Edges that cannot carry data keep only handles that still exist, so the
    canvas can draw them; they still order the run.
    """
    types = {node["id"]: node["type"] for node in graph["nodes"]}
    edges = []
    for edge, binding in zip(graph["edges"], bind_edges(graph, registry)):
        if binding is not None:
            edges.append({**edge, "sourceHandle": binding.output.name, "targetHandle": binding.input.name})
            continue
        source_ports = registry.resolve(types.get(edge["source"], "")).outputs
        target_ports = registry.resolve(types.get(edge["target"], "")).inputs
        edges.append({**edge,
                      "sourceHandle": edge.get("sourceHandle") if find_port(source_ports, edge.get("sourceHandle")) else None,
                      "targetHandle": edge.get("targetHandle") if find_port(target_ports, edge.get("targetHandle")) else None})
    return {**graph, "edges": edges}


def edge_problems(graph: Mapping[str, Any], registry) -> list[str]:
    """Reasons an edited graph's named handles are invalid; legacy edges are always accepted."""
    types = {node["id"]: node["type"] for node in graph["nodes"]}
    problems = []
    single: dict[tuple[str, str], int] = {}
    for edge in graph["edges"]:
        source_type, target_type = types.get(edge["source"]), types.get(edge["target"])
        source_handler, target_handler = registry.resolve(source_type), registry.resolve(target_type)
        label = f"{edge['source']} → {edge['target']}"
        output = None
        if edge.get("sourceHandle") is not None:
            output = find_port(source_handler.outputs, edge["sourceHandle"])
            if output is None:
                problems.append(f"{label}: {source_type} has no output {edge['sourceHandle']!r}")
                continue
        if edge.get("targetHandle") is not None:
            port = find_port(target_handler.inputs, edge["targetHandle"])
            if port is None:
                problems.append(f"{label}: {target_type} has no input {edge['targetHandle']!r}")
                continue
            output = output or default_output(source_handler)
            if output is None or output.type not in port.accepts:
                given = output.type if output else "nothing"
                problems.append(f"{label}: input {port.name!r} accepts {', '.join(port.accepts)}, not {given}")
                continue
            if not port.multiple:
                key = (edge["target"], port.name)
                single[key] = single.get(key, 0) + 1
                if single[key] > 1:
                    problems.append(f"{label}: input {port.name!r} accepts only one connection")
    return problems


def unsatisfied_inputs(graph: Mapping[str, Any], registry, node: Mapping[str, Any]) -> list[str]:
    """Ports of the first ``requires`` group that nothing can fill, judged before a run.

    A port can be filled by a connected edge, a non-empty config setting, or a
    context fallback such as the project topic (every project has a title).
    """
    handler = registry.resolve(node["type"])
    if not handler.requires:
        return []
    bound = {binding.input.name for binding in bind_edges(graph, registry)
             if binding is not None and binding.target == node["id"]}
    config = node.get("config") if isinstance(node.get("config"), Mapping) else {}
    fillable = {port.name for port in handler.inputs
                if port.name in bound or port.context is not None
                or (port.config_key and not is_empty(config.get(port.config_key)))}
    for group in handler.requires:
        if not fillable.intersection(group):
            return list(group)
    return []


def describe_node_types(registry) -> dict[str, Any]:
    """The catalog the editor renders from: typed ports for handles, settings for the inspector."""
    return {node_type: {"inputs": [port.describe() for port in handler.inputs],
                        "outputs": [port.describe() for port in handler.outputs],
                        "requires": [list(group) for group in handler.requires],
                        "config": [field.describe() for field in handler.config_fields]}
            for node_type, handler in sorted(registry.handlers().items())}
