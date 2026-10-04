"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { FieldLabel } from "@/components/reelforge/primitives";
import { useI18n } from "@/lib/i18n";
import type { Dictionary } from "@/lib/i18n/vi";
import { useDashboard, useMovieSources } from "@/lib/queries";
import { formatClock } from "@/lib/studio";
import type { AiTool, ConfigField, NodeConfig } from "@/lib/types";
import { fieldValue, toolChoices, WARNING_CODES } from "./node-config";

/** `typing` marks keystrokes, which the editor groups into one undo step per field. */
export type ConfigChange = (field: ConfigField, value: unknown, typing?: boolean) => void;

/** What "auto" choices resolve to in this workspace. */
export type WorkspaceDefaults = { language: string; aspect: string | null };

// Radix Select items cannot use an empty string, so "no value" gets its own token.
const NONE = "__none__";

const isDuration = (field: ConfigField) => field.label.endsWith("duration");

function integerText(t: Dictionary, field: ConfigField, value: number) {
  if (!isDuration(field)) return String(value);
  return value >= 60 && value % 60 === 0 ? t.config.minutes(value / 60) : t.config.seconds(value);
}

function optionText(t: Dictionary, field: ConfigField, value: string, defaults: WorkspaceDefaults) {
  const label = t.config.options[field.label]?.[value] ?? value;
  if (value !== "auto") return label;
  if (field.label === "language" || field.label === "target_language") {
    return t.config.workspace(t.config.options.language[defaults.language] ?? defaults.language);
  }
  if (field.label === "aspect_ratio" && defaults.aspect) return t.config.workspace(defaults.aspect);
  return label;
}

function Trigger({ id, label, invalid }: { id: string; label: string; invalid: boolean }) {
  return (
    <SelectTrigger id={id} aria-label={label} aria-invalid={invalid} className={cn("bg-surface", invalid && "border-destructive")}>
      <SelectValue />
    </SelectTrigger>
  );
}

/** One of the workspace's uploaded files, limited to the types the step can read (Source steps). */
function AssetSelect({
  id,
  field,
  raw,
  invalid,
  onChange,
}: {
  id: string;
  field: ConfigField;
  raw: unknown;
  invalid: boolean;
  onChange: ConfigChange;
}) {
  const { t } = useI18n();
  const { data } = useDashboard();
  const label = t.config.fields[field.label] ?? field.label;
  const accepted = new Set(field.content_types ?? []);
  // Uploaded files only: generated media belongs to runs and is not a source.
  const choices = (data?.assets ?? []).filter(
    (asset) => !asset.run_id && (!accepted.size || accepted.has(asset.content_type)),
  );
  const current = typeof raw === "string" ? raw : NONE;
  const listed = current === NONE || choices.some((asset) => asset.id === current);
  return (
    <>
      <Select value={current} onValueChange={(v) => onChange(field, v === NONE ? null : v)}>
        <Trigger id={id} label={label} invalid={invalid} />
        <SelectContent>
          <SelectItem value={NONE}>{t.config.assetNone}</SelectItem>
          {!listed && <SelectItem value={current}>{t.config.assetMissing}</SelectItem>}
          {choices.map((asset) => (
            <SelectItem key={asset.id} value={asset.id}>
              {asset.filename}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {data && choices.length === 0 && (
        <p className="mt-1.5 text-[11px] text-muted-foreground">
          {t.config.noAsset}{" "}
          <Link href="/media" className="text-primary hover:underline">
            {t.config.uploadAsset}
          </Link>
        </p>
      )}
    </>
  );
}

/** One of the workspace's movie sources (Media → Movie sources); only usable ones are offered. */
function MovieSourceSelect({
  id,
  field,
  raw,
  invalid,
  onChange,
}: {
  id: string;
  field: ConfigField;
  raw: unknown;
  invalid: boolean;
  onChange: ConfigChange;
}) {
  const { t } = useI18n();
  const sources = useMovieSources({ limit: 100, offset: 0 });
  const label = t.config.fields[field.label] ?? field.label;
  const choices = (sources.data?.items ?? []).filter((item) => item.can_use);
  const current = typeof raw === "string" ? raw : NONE;
  const listed = current === NONE || choices.some((item) => item.id === current);
  return (
    <>
      <Select value={current} onValueChange={(v) => onChange(field, v === NONE ? null : v)}>
        <Trigger id={id} label={label} invalid={invalid} />
        <SelectContent>
          <SelectItem value={NONE}>{t.config.movieSourceNone}</SelectItem>
          {!listed && <SelectItem value={current}>{t.config.movieSourceMissing}</SelectItem>}
          {choices.map((item) => (
            <SelectItem key={item.id} value={item.id}>
              {item.duration_seconds ? `${item.name} · ${formatClock(item.duration_seconds)}` : item.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {sources.data && choices.length === 0 && (
        <p className="mt-1.5 text-[11px] text-muted-foreground">
          {t.config.noMovieSource}{" "}
          <Link href="/media/movie-sources" className="text-primary hover:underline">
            {t.config.addMovieSource}
          </Link>
        </p>
      )}
    </>
  );
}

/** A number typed freely: the draft may be empty or out of range while the user edits it. */
function NumberInput({
  id,
  field,
  raw,
  invalid,
  onChange,
}: {
  id: string;
  field: ConfigField;
  raw: unknown;
  invalid: boolean;
  onChange: ConfigChange;
}) {
  const external = typeof raw === "number" ? String(raw) : "";
  const [draft, setDraft] = useState(external);
  // Undo and redo change the setting from outside; keep the draft unless it already says the same.
  useEffect(() => {
    setDraft((current) => (current.trim() !== "" && Number(current) === raw ? current : external));
  }, [external, raw]);
  return (
    <Input
      id={id}
      type="number"
      inputMode={field.type === "integer" ? "numeric" : "decimal"}
      min={field.minimum}
      max={field.maximum}
      step={field.type === "integer" ? 1 : 0.1}
      value={draft}
      placeholder={field.default === null ? "" : String(field.default)}
      aria-invalid={invalid}
      onChange={(e) => {
        setDraft(e.target.value);
        onChange(field, e.target.value.trim() === "" ? null : Number(e.target.value), true);
      }}
      className={cn("bg-surface", invalid && "border-destructive")}
    />
  );
}

function FieldInput({
  id,
  field,
  config,
  invalid,
  tools,
  defaults,
  onChange,
}: {
  id: string;
  field: ConfigField;
  config: NodeConfig | null | undefined;
  invalid: boolean;
  tools: AiTool[] | undefined;
  defaults: WorkspaceDefaults;
  onChange: ConfigChange;
}) {
  const { t } = useI18n();
  const label = t.config.fields[field.label] ?? field.label;
  const raw = config?.[field.key];
  const value = fieldValue(field, config);

  if (field.type === "asset") {
    return <AssetSelect id={id} field={field} raw={raw} invalid={invalid} onChange={onChange} />;
  }

  if (field.type === "movie_source") {
    return <MovieSourceSelect id={id} field={field} raw={raw} invalid={invalid} onChange={onChange} />;
  }

  if (field.type === "tool") {
    const choices = toolChoices(field, tools ?? []);
    const current = typeof raw === "string" ? raw : NONE;
    const chosen = tools?.find((tool) => tool.id === raw);
    const listed = current === NONE || choices.some((tool) => tool.id === current);
    return (
      <>
        <Select value={current} onValueChange={(v) => onChange(field, v === NONE ? null : v)}>
          <Trigger id={id} label={label} invalid={invalid} />
          <SelectContent>
            <SelectItem value={NONE}>{t.config.modelAuto}</SelectItem>
            {!listed && (
              <SelectItem value={current}>
                {chosen ? t.config.modelDisabled(`${chosen.provider} · ${chosen.model}`) : t.config.modelMissing}
              </SelectItem>
            )}
            {choices.map((tool) => (
              <SelectItem key={tool.id} value={tool.id}>
                {tool.provider} · {tool.model}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {tools && choices.length === 0 && (
          <p className="mt-1.5 text-[11px] text-muted-foreground">
            {t.config.noModel}{" "}
            <Link href="/models" className="text-primary hover:underline">
              {t.editor.inspector.configureModels}
            </Link>
          </p>
        )}
      </>
    );
  }

  if (field.type === "select" || (field.type === "integer" && field.presets?.length)) {
    const choices = field.type === "select" ? (field.options ?? []) : (field.presets ?? []).map(String);
    const current = value === null || value === undefined ? NONE : String(value);
    const text = (item: string) =>
      field.type === "select" ? optionText(t, field, item, defaults) : integerText(t, field, Number(item));
    return (
      <Select
        value={current}
        onValueChange={(v) => onChange(field, v === NONE ? null : field.type === "integer" ? Number(v) : v)}
      >
        <Trigger id={id} label={label} invalid={invalid} />
        <SelectContent>
          {field.default === null && <SelectItem value={NONE}>{t.config.notSet}</SelectItem>}
          {current !== NONE && !choices.includes(current) && (
            <SelectItem value={current}>
              {t.config.custom(typeof value === "number" ? integerText(t, field, value) : current)}
            </SelectItem>
          )}
          {choices.map((item) => (
            <SelectItem key={item} value={item}>
              {text(item)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    );
  }

  if (field.type === "integer" || field.type === "number") {
    return <NumberInput id={id} field={field} raw={raw} invalid={invalid} onChange={onChange} />;
  }

  const props = {
    id,
    value: typeof raw === "string" ? raw : "",
    maxLength: field.max_length,
    placeholder: t.config.placeholders[field.label] ?? "",
    "aria-invalid": invalid,
    className: cn("bg-surface", invalid && "border-destructive"),
  };
  return field.multiline ? (
    <Textarea
      {...props}
      rows={field.max_length && field.max_length > 1000 ? 4 : 3}
      className={cn(props.className, "resize-y text-sm")}
      onChange={(e) => onChange(field, e.target.value, true)}
    />
  ) : (
    <Input {...props} onChange={(e) => onChange(field, e.target.value, true)} />
  );
}

/**
 * The inspector's settings for one node, rendered from the node type's schema.
 * Advanced-only fields appear in Advanced mode, or whenever they hold a problem.
 */
export function ConfigFields({
  nodeId,
  fields,
  config,
  errors,
  tools,
  advanced,
  defaults,
  onChange,
  onRemoveKey,
}: {
  nodeId: string;
  fields: ConfigField[];
  config: NodeConfig | null | undefined;
  errors: Record<string, string>;
  tools: AiTool[] | undefined;
  advanced: boolean;
  defaults: WorkspaceDefaults;
  onChange: ConfigChange;
  onRemoveKey: (key: string) => void;
}) {
  const { t } = useI18n();
  const shown = fields.filter((field) => advanced || !field.advanced || errors[field.key]);
  const unknown = Object.keys(errors).filter((key) => !fields.some((field) => field.key === key));

  return (
    <div className="space-y-4">
      {shown.map((field) => {
        const id = `config-${nodeId}-${field.key}`;
        const error = errors[field.key];
        const warning = error ? WARNING_CODES.has(error) : false;
        const range =
          (field.type === "integer" || field.type === "number") &&
          field.minimum !== undefined &&
          field.maximum !== undefined &&
          !field.presets?.length
            ? t.config.range(field.minimum, field.maximum)
            : null;
        const hint = [t.config.hints[field.label], range].filter(Boolean).join(" ");
        return (
          <div key={field.key}>
            <FieldLabel htmlFor={id}>
              {t.config.fields[field.label] ?? field.label}
              {advanced && <span className="ml-1.5 font-mono normal-case tracking-normal opacity-70">{field.key}</span>}
            </FieldLabel>
            <FieldInput
              id={id}
              field={field}
              config={config}
              invalid={Boolean(error) && !warning}
              tools={tools}
              defaults={defaults}
              onChange={onChange}
            />
            {error ? (
              <p role="alert" className={cn("mt-1.5 text-[11px]", warning ? "text-warning" : "text-destructive")}>
                {t.config.errors[error] ?? error}
                {range && !warning ? ` ${range}` : ""}
              </p>
            ) : (
              hint && <p className="mt-1.5 text-[11px] text-muted-foreground">{hint}</p>
            )}
          </div>
        );
      })}
      {unknown.map((key) => (
        <div
          key={key}
          className="flex items-center justify-between gap-2 rounded-lg border border-destructive/40 p-2 text-[11px] text-destructive"
        >
          <span className="min-w-0 break-all">
            <span className="font-mono">{key}</span> · {t.config.errors.unknown_setting}
          </span>
          <button
            type="button"
            onClick={() => onRemoveKey(key)}
            aria-label={t.editor.node.delete}
            title={t.editor.node.delete}
            className="rounded p-0.5 hover:bg-surface-2"
          >
            <X className="size-3.5" />
          </button>
        </div>
      ))}
    </div>
  );
}
