"""Tool-calling primitives for agents.

A tool is an async function decorated with ``@tool``.  The decorator inspects
its signature and builds a JSON-schema description from the single Pydantic
model argument that carries the tool's parameters.  Tools are pure functions:
they never execute generated code or touch the sandbox.
"""

from __future__ import annotations

import inspect
import typing
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Optional, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T")


@dataclass
class ToolSpec:
    """Description of a tool usable by an LLM."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class ToolCall:
    """A single tool call requested by an LLM."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    """Result of executing one tool call."""

    name: str
    output: Any = None
    error: Optional[str] = None

    def is_success(self) -> bool:
        return self.error is None


# A tool implementation receives (ctx, state, args_model) and returns anything.
ToolImpl = Callable[..., Coroutine[Any, Any, Any]]


def tool(
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> Callable[[T], T]:
    """Decorate an async function and expose it as a tool spec.

    The decorated function must accept ``ctx`` and ``state`` as its first two
    positional arguments, followed by a single Pydantic-model argument that
    describes the tool parameters.  Example::

        @tool()
        async def search(ctx, state, args: SearchArgs) -> list[EvidenceChunk]:
            ...
    """

    def decorator(fn: T) -> T:
        if not inspect.iscoroutinefunction(fn):
            raise TypeError(f"Tool {fn!r} must be an async function")
        fn_name = name or fn.__name__
        fn_desc = description or (fn.__doc__ or "").strip()

        sig = inspect.signature(fn)
        params = list(sig.parameters.values())
        # Skip ctx and state parameters; the third parameter is the args model.
        arg_params = [p for p in params if p.name not in ("ctx", "state")]
        if not arg_params:
            raise TypeError(f"Tool {fn_name} must have a Pydantic args parameter")
        args_param = arg_params[0]
        try:
            hints = typing.get_type_hints(fn, globalns=fn.__globals__, localns=fn.__globals__)
            args_cls = hints[args_param.name]
        except Exception as exc:
            raise TypeError(
                f"Tool {fn_name} could not resolve args type for {args_param.name!r}: {exc}"
            ) from exc
        if not (isinstance(args_cls, type) and issubclass(args_cls, BaseModel)):
            raise TypeError(
                f"Tool {fn_name} args parameter must be a Pydantic model, got {args_cls!r}"
            )

        schema = args_cls.model_json_schema()
        # Remove $defs and title noise so the schema is compact for prompts.
        schema.pop("$defs", None)
        schema.pop("title", None)
        schema["type"] = "object"
        fn._tool_spec = ToolSpec(  # type: ignore[attr-defined]
            name=fn_name,
            description=fn_desc,
            parameters=schema,
        )
        return fn

    return decorator


def get_tool_spec(fn: Callable[..., Any]) -> ToolSpec:
    """Return the ToolSpec attached by the @tool decorator."""
    spec = getattr(fn, "_tool_spec", None)
    if not isinstance(spec, ToolSpec):
        raise ValueError(f"{fn!r} is not a @tool-decorated function")
    return spec


def as_function_schema(spec: ToolSpec) -> dict[str, Any]:
    """Format a ToolSpec as an OpenAI/Ollama function tool."""
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        },
    }


async def execute_tool_call(
    registry: dict[str, ToolImpl],
    call: ToolCall,
    ctx: Any,
    state: Any,
) -> ToolResult:
    """Execute a single tool call against a registry."""
    impl = registry.get(call.name)
    if impl is None:
        return ToolResult(
            name=call.name,
            error=f"unknown tool: {call.name!r}",
        )
    try:
        spec = get_tool_spec(impl)
        args_cls = _get_args_class(impl)
        args = args_cls.model_validate(call.arguments)
        output = await impl(ctx, state, args)
        return ToolResult(name=call.name, output=output)
    except ValidationError as exc:
        return ToolResult(
            name=call.name,
            error=f"invalid arguments for {call.name}: {exc.errors()}",
        )
    except Exception as exc:
        return ToolResult(
            name=call.name,
            error=f"tool {call.name} failed: {exc!r}",
        )


async def execute_tool_calls(
    registry: dict[str, ToolImpl],
    calls: list[ToolCall],
    ctx: Any,
    state: Any,
) -> list[ToolResult]:
    """Execute many tool calls in order."""
    return [await execute_tool_call(registry, call, ctx, state) for call in calls]


def _get_args_class(fn: ToolImpl) -> type[BaseModel]:
    """Return the Pydantic model class used as the tool's args parameter."""
    sig = inspect.signature(fn)
    params = [p for p in sig.parameters.values() if p.name not in ("ctx", "state")]
    args_param = params[0]
    hints = typing.get_type_hints(fn, globalns=fn.__globals__, localns=fn.__globals__)
    cls = hints[args_param.name]
    if not (isinstance(cls, type) and issubclass(cls, BaseModel)):
        raise TypeError(f"tool {fn.__name__} has no Pydantic args model")
    return cls
