"""Белый список инструментов. Агент не вызывает ничего, кроме этого реестра."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator

from agent.context import ToolContext
from agent.errors import ToolDenied

Handler = Callable[[dict[str, Any], ToolContext], dict[str, Any]]
_PREVIEW_LIMIT = 400


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    permission: str
    handler: Handler
    binds_client: bool = False


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.permission != "read":
            raise ToolDenied(f"инструмент {spec.name} отклонён: разрешено только чтение")
        if spec.name in self._tools:
            raise ToolDenied(f"инструмент {spec.name} уже зарегистрирован")
        Draft202012Validator.check_schema(spec.input_schema)
        self._tools[spec.name] = spec

    def names(self) -> list[str]:
        return sorted(self._tools)

    def catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "permission": spec.permission,
                "input_schema": spec.input_schema,
            }
            for spec in self._tools.values()
        ]

    def call(self, name: str, args: dict[str, Any] | None, ctx: ToolContext) -> dict[str, Any]:
        started = time.perf_counter()
        ctx.budget.charge_step()
        incoming = dict(args or {})
        if name not in self._tools:
            result = {"error": "denied", "detail": f"инструмент {name} не в белом списке"}
            self._record(ctx, name, incoming, result, started, error=True)
            raise ToolDenied(result["detail"])
        spec = self._tools[name]
        if spec.binds_client:
            incoming["client_id"] = ctx.session.client_id
        errors = sorted(Draft202012Validator(spec.input_schema).iter_errors(incoming), key=lambda err: list(err.path))
        if errors:
            result = {"error": "invalid_arguments", "detail": errors[0].message}
        else:
            try:
                result = spec.handler(incoming, ctx)
            except ToolDenied:
                raise
            except Exception as exc:  # инструмент не должен ронять цикл
                result = {"error": "tool_failed", "detail": exc.__class__.__name__}
        self._record(ctx, name, incoming, result, started, error="error" in result)
        return result

    def _record(self, ctx: ToolContext, name: str, args: dict, result: dict, started: float, error: bool) -> None:
        safe_args = dict(args)
        safe_args.pop("client_id", None)
        ctx.steps.append(
            {
                "kind": "tool",
                "name": name,
                "args": safe_args,
                "result_preview": _preview(result),
                "started_at": datetime.now(timezone.utc).isoformat(),
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "tokens": 0,
                "model": None,
                "error": error,
            }
        )


def _preview(payload: dict) -> str:
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) <= _PREVIEW_LIMIT:
        return text
    return text[: _PREVIEW_LIMIT - 1] + "…"
