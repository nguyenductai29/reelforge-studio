import type { Dictionary } from "@/lib/i18n/vi";
import type { GraphEdge, NodePorts } from "@/lib/types";

export type PortCatalog = Record<string, NodePorts>;

export const NO_PORTS: NodePorts = { inputs: [], outputs: [], requires: [], config: [] };

/** A node type's ports and settings; an API from before Phase 3.5 sends no `config`, which reads as none. */
export const portsOf = (catalog: PortCatalog, type: string | undefined): NodePorts => {
  const ports = type ? catalog[type] : undefined;
  return ports ? { ...NO_PORTS, ...ports } : NO_PORTS;
};

export const portLabel = (t: Dictionary, name: string | null | undefined) => (name ? (t.ports[name] ?? name) : "");

/** One edge per port pair; two nodes may be joined through several ports. */
export const edgeId = (edge: GraphEdge) =>
  `${edge.source}:${edge.sourceHandle ?? ""}->${edge.target}:${edge.targetHandle ?? ""}`;

/** Whether an output can feed an input, using the same data types the backend checks. */
export function canConnect(
  catalog: PortCatalog,
  sourceType: string | undefined,
  sourceHandle: string | null | undefined,
  targetType: string | undefined,
  targetHandle: string | null | undefined,
) {
  const output = portsOf(catalog, sourceType).outputs.find((port) => port.name === sourceHandle);
  const input = portsOf(catalog, targetType).inputs.find((port) => port.name === targetHandle);
  return Boolean(output && input && input.accepts.includes(output.type));
}

/** The text a step produced: all of it when the node keeps a "text" copy, else its main output. */
export function outputText(output: Record<string, unknown> | null | undefined, ports: NodePorts): string | null {
  if (!output) return null;
  for (const key of ["text", ports.outputs[0]?.name]) {
    const value = key ? output[key] : undefined;
    if (typeof value === "string" && value.trim()) return value;
  }
  return null;
}

export type Scene = { index: number; text: string; duration?: number | null };

export function outputScenes(output: Record<string, unknown> | null | undefined): Scene[] | null {
  const scenes = output?.scenes;
  if (!Array.isArray(scenes)) return null;
  return scenes.filter((scene): scene is Scene => typeof scene?.text === "string");
}
