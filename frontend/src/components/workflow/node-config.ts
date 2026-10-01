import type { AiTool, ConfigField, NodeConfig } from "@/lib/types";

/**
 * Node settings, driven by the field schema the backend serves with each node
 * type (`GET /api/workflow-node-types`). These checks mirror the backend's so
 * the inspector can flag a problem before saving; the backend stays authoritative.
 */

type Value = NodeConfig[string];

/** A field's effective value: the node's setting, else the field default. */
export function fieldValue(field: ConfigField, config: NodeConfig | null | undefined): Value {
  const value = config?.[field.key];
  return value === undefined || value === null ? field.default : value;
}

/** Enabled workspace tools a model field can pick: its task and a provider the backend supports. */
export function toolChoices(field: ConfigField, tools: AiTool[]): AiTool[] {
  return tools.filter(
    (tool) => tool.is_enabled && tool.task === field.task && (!field.providers || field.providers.includes(tool.provider)),
  );
}

/** A disabled tool can be saved (readiness reports it); only this code does not block saving. */
export const WARNING_CODES: ReadonlySet<string> = new Set(["tool_unavailable"]);

/**
 * The error code for one field's setting, or null when it is fine. `tools` is
 * the workspace's tools, or undefined while they load (model fields then pass).
 */
export function fieldError(field: ConfigField, value: Value, tools: AiTool[] | undefined): string | null {
  if (value === undefined || value === null) return field.required && field.default === null ? field.code : null;
  switch (field.type) {
    case "select":
      return typeof value === "string" && (field.options ?? []).includes(value) ? null : field.code;
    case "integer":
    case "number": {
      const valid =
        typeof value === "number" &&
        Number.isFinite(value) &&
        (field.type === "number" || Number.isInteger(value)) &&
        (field.minimum === undefined || value >= field.minimum) &&
        (field.maximum === undefined || value <= field.maximum);
      return valid ? null : field.code;
    }
    case "text":
      return typeof value === "string" && (field.max_length === undefined || value.length <= field.max_length)
        ? null
        : field.code;
    case "asset":
      // Whether the file is this workspace's own is checked by the backend on save.
      return typeof value === "string" && value.length > 0 ? null : field.code;
    case "tool": {
      if (typeof value !== "string") return field.code;
      if (!tools) return null;
      const tool = tools.find((item) => item.id === value);
      if (!tool || tool.task !== field.task || (field.providers && !field.providers.includes(tool.provider))) {
        return field.code;
      }
      return tool.is_enabled ? null : "tool_unavailable";
    }
  }
}

/** Every problem in a node's settings, by key; keys the schema does not know are errors too. */
export function configErrors(
  fields: ConfigField[],
  config: NodeConfig | null | undefined,
  tools: AiTool[] | undefined,
): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const key of Object.keys(config ?? {})) {
    if (!fields.some((field) => field.key === key)) errors[key] = "unknown_setting";
  }
  for (const field of fields) {
    const code = fieldError(field, config?.[field.key], tools);
    if (code) errors[field.key] = code;
  }
  return errors;
}

/** Whether any error would make the backend reject the save. */
export const blocksSaving = (errors: Record<string, string>) =>
  Object.values(errors).some((code) => !WARNING_CODES.has(code));

/**
 * The node's config after setting one field. Clearing a field or choosing its
 * default removes the key, so untouched settings keep following the defaults.
 */
export function withValue(config: NodeConfig | null | undefined, field: ConfigField, value: Value): NodeConfig | null {
  const next: NodeConfig = { ...(config ?? {}) };
  if (value === undefined || value === null || value === "" || value === field.default) delete next[field.key];
  else next[field.key] = value;
  return Object.keys(next).length ? next : null;
}
