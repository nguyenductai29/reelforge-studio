"""Node settings: the typed fields each node type declares, checked the same way everywhere.

A node's ``config`` holds only the settings the user changed; a missing key
(or ``null``) means the field's default. One list of ``ConfigField`` per node
type drives all of it:

* ``GET /api/workflow-node-types`` serves the fields, so the editor renders
  the inspector from them instead of keeping its own copy of the rules;
* saving a workflow rejects invalid settings with 422 and a stable ``code``;
* before a node runs (and in readiness checks) invalid settings block that
  node with the same code, so an old or hand-edited workflow never crashes a run.
"""
from dataclasses import dataclass, replace
import json
import math
import re
from typing import Any, Iterable, Mapping

MAX_CONFIG_CHARS = 8000

# Field types, as the editor renders them.
SELECT = "select"    # one of ``options``
INTEGER = "integer"  # whole number in [minimum, maximum]; ``presets`` are suggested values
NUMBER = "number"    # decimal in [minimum, maximum]
TEXT = "text"        # string of at most ``max_length``; ``multiline`` for a textarea
TOOL = "tool"        # ID of one of the workspace's AI tools for ``task``
ASSET = "asset"      # ID of one of the workspace's media assets, of one of ``content_types``
MOVIE_SOURCE = "movie_source"  # ID of one of the workspace's movie sources (migration 0027)

_TOOL_ID = re.compile(r"[A-Za-z0-9-]{1,64}\Z")


class ConfigError(ValueError):
    """An invalid setting. ``code`` is stable for clients; ``field`` is the setting's key, if any."""

    def __init__(self, code: str, message: str, field: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "field": self.field, "message": self.message}


@dataclass(frozen=True)
class ConfigField:
    key: str
    type: str
    default: Any = None
    options: tuple = ()
    presets: tuple = ()
    minimum: float | None = None
    maximum: float | None = None
    max_length: int | None = None
    multiline: bool = False
    required: bool = False
    # Shown only in the editor's Advanced mode.
    advanced: bool = False
    # i18n key for the editor's label; defaults to ``key``.
    label: str | None = None
    # Tool fields: the AI tool task and the providers that can serve it.
    task: str | None = None
    providers: tuple[str, ...] = ()
    # Asset fields: the media types the step can read.
    content_types: tuple[str, ...] = ()
    # Error code for an invalid value, e.g. "invalid_language".
    code: str = "invalid_config"

    def check(self, value: Any) -> None:
        """Raise ``ConfigError`` unless ``value`` is acceptable; ``None`` means the default."""
        if value is None:
            if self.required and self.default is None:
                raise ConfigError(self.code, f"{self.key} is required", self.key)
            return
        if self.type == SELECT:
            if not isinstance(value, str) or value not in self.options:
                raise ConfigError(self.code, f"{self.key} must be one of {', '.join(map(str, self.options))}", self.key)
        elif self.type in (INTEGER, NUMBER):
            number_types = int if self.type == INTEGER else (int, float)
            if (isinstance(value, bool) or not isinstance(value, number_types)
                    or (isinstance(value, float) and not math.isfinite(value))
                    or (self.minimum is not None and value < self.minimum)
                    or (self.maximum is not None and value > self.maximum)):
                kind = "a whole number" if self.type == INTEGER else "a number"
                raise ConfigError(self.code, f"{self.key} must be {kind} from {_plain(self.minimum)} "
                                             f"to {_plain(self.maximum)}", self.key)
        elif self.type == TEXT:
            if not isinstance(value, str) or (self.max_length is not None and len(value) > self.max_length):
                raise ConfigError(self.code, f"{self.key} must be text of at most {self.max_length} characters",
                                  self.key)
        elif self.type == TOOL:
            if not isinstance(value, str) or not _TOOL_ID.fullmatch(value):
                raise ConfigError(self.code, f"{self.key} must be the ID of an AI model", self.key)
        elif self.type == ASSET:
            if not isinstance(value, str) or not _TOOL_ID.fullmatch(value):
                raise ConfigError(self.code, f"{self.key} must be the ID of a media file", self.key)
        elif self.type == MOVIE_SOURCE:
            if not isinstance(value, str) or not _TOOL_ID.fullmatch(value):
                raise ConfigError(self.code, f"{self.key} must be the ID of a movie source", self.key)
        else:
            raise ConfigError("invalid_config", f"{self.key} has an unknown field type", self.key)

    def describe(self) -> dict[str, Any]:
        described = {"key": self.key, "type": self.type, "label": self.label or self.key, "default": self.default,
                     "required": self.required, "advanced": self.advanced, "code": self.code}
        for name in ("options", "presets", "providers", "content_types"):
            if getattr(self, name):
                described[name] = list(getattr(self, name))
        for name in ("minimum", "maximum", "max_length", "task"):
            if getattr(self, name) is not None:
                described[name] = getattr(self, name)
        if self.multiline:
            described["multiline"] = True
        return described


def _plain(number):
    return int(number) if isinstance(number, float) and number.is_integer() else number


def advanced(field: ConfigField) -> ConfigField:
    return replace(field, advanced=True)


def validate_config(fields: Iterable[ConfigField], config: Any) -> None:
    """Raise ``ConfigError`` for the first problem in ``config``; ``None`` and ``{}`` are always valid."""
    if config is None:
        return
    if not isinstance(config, Mapping):
        raise ConfigError("invalid_config", "settings must be an object")
    known = {field.key: field for field in fields}
    if config and not known:
        raise ConfigError("unknown_setting", "this step type has no settings", next(iter(config)))
    if len(json.dumps(config)) > MAX_CONFIG_CHARS:
        raise ConfigError("invalid_config", "settings are too large")
    for key, value in config.items():
        field = known.get(key)
        if field is None:
            raise ConfigError("unknown_setting", f"unknown setting {key!r}", key)
        field.check(value)
    for field in known.values():
        if field.required and field.default is None and config.get(field.key) is None:
            raise ConfigError(field.code, f"{field.key} is required", field.key)


def config_values(fields: Iterable[ConfigField], config: Any) -> dict[str, Any]:
    """Every field's effective value: the node's setting, else the field default."""
    config = config if isinstance(config, Mapping) else {}
    return {field.key: config[field.key] if config.get(field.key) is not None else field.default
            for field in fields}


def check_tools(fields: Iterable[ConfigField], config: Any, tools: Mapping[str, Any]) -> None:
    """Tool settings must name a workspace AI tool of the right task and a supported provider.

    ``tools`` maps tool IDs to the workspace's tools, enabled or not: a disabled
    tool may be saved (readiness reports it), a missing or foreign one may not.
    """
    if not isinstance(config, Mapping):
        return
    for field in fields:
        tool_id = config.get(field.key)
        if field.type != TOOL or tool_id is None:
            continue
        tool = tools.get(tool_id)
        if tool is None or tool.task != field.task or tool.provider not in field.providers:
            raise ConfigError(field.code, f"{field.key} is not a supported {field.task} model in this workspace",
                              field.key)


def check_assets(fields: Iterable[ConfigField], config: Any, assets: Mapping[str, Any]) -> None:
    """Asset settings must name a media file of this workspace with a type the step can read.

    ``assets`` maps asset IDs to the workspace's assets; a file of another
    workspace is indistinguishable from a missing one.
    """
    if not isinstance(config, Mapping):
        return
    for field in fields:
        asset_id = config.get(field.key)
        if field.type != ASSET or asset_id is None:
            continue
        asset = assets.get(asset_id)
        if asset is None or (field.content_types and asset.content_type not in field.content_types):
            raise ConfigError(field.code, f"{field.key} is not a supported media file in this workspace", field.key)


def check_movie_sources(fields: Iterable[ConfigField], config: Any, sources: Mapping[str, Any]) -> None:
    """Movie source settings must name a movie source of this workspace that is not deleted.

    ``sources`` maps IDs to the workspace's movie sources; one of another workspace counts as missing."""
    if not isinstance(config, Mapping):
        return
    for field in fields:
        source_id = config.get(field.key)
        if field.type != MOVIE_SOURCE or source_id is None:
            continue
        source = sources.get(source_id)
        if source is None or source.status in ("deleting", "deleted"):
            raise ConfigError(field.code, f"{field.key} is not a movie source of this workspace", field.key)


def describe_config(fields: Iterable[ConfigField]) -> list[dict[str, Any]]:
    return [field.describe() for field in fields]
