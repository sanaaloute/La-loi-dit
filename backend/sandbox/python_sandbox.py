"""Two-stage sandboxed Python execution.

Stage 1 — static AST validation: only an allowlist of node types, names
and importable modules (math, statistics, datetime, json, csv, re,
collections, itertools, functools) is accepted. Dunder attribute access
and dangerous builtins (open, eval, exec, __import__, input, compile,
getattr, setattr, delattr, globals, locals) are rejected. Rejected code
raises :class:`SandboxError` and is NEVER executed.

Stage 2 — isolation: accepted code runs in a fresh subprocess
(``sys.executable -I``) with a scrubbed environment, a temporary working
directory and a hard timeout after which the process is killed.
"""

from __future__ import annotations

import ast
import asyncio
import base64
import os
import sys
import tempfile
from typing import Optional

from pydantic import BaseModel, Field

from backend.core.exceptions import SandboxError

ALLOWED_MODULES = frozenset(
    {"math", "statistics", "datetime", "json", "csv", "re", "collections", "itertools", "functools"}
)

# Names that may never appear as a call target or bare name.
FORBIDDEN_NAMES = frozenset(
    {
        "open", "eval", "exec", "__import__", "input", "compile",
        "getattr", "setattr", "delattr", "globals", "locals", "vars",
        "breakpoint", "exit", "quit", "memoryview", "bytearray", "object",
        "super", "type",
    }
)

# Allowlist of AST node types permitted in sandboxed code.
ALLOWED_NODES = (
    ast.Module, ast.Interactive, ast.Expression,
    # statements
    ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Return, ast.Delete,
    ast.Assign, ast.AugAssign, ast.AnnAssign, ast.For, ast.AsyncFor, ast.While,
    ast.If, ast.With, ast.AsyncWith, ast.Raise, ast.Try, ast.Assert, ast.Import,
    ast.ImportFrom, ast.Global, ast.Nonlocal, ast.Expr, ast.Pass, ast.Break,
    ast.Continue,
    # expressions
    ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.Lambda, ast.IfExp, ast.Dict, ast.Set,
    ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.Await,
    ast.Yield, ast.YieldFrom, ast.Compare, ast.Call, ast.FormattedValue,
    ast.JoinedStr, ast.Constant, ast.Attribute, ast.Subscript, ast.Starred,
    ast.Name, ast.List, ast.Tuple, ast.Slice,
    # operators / context / misc
    ast.Load, ast.Store, ast.Del, ast.And, ast.Or, ast.Add, ast.Sub, ast.Mult,
    ast.MatMult, ast.Div, ast.Mod, ast.Pow, ast.LShift, ast.RShift, ast.BitOr,
    ast.BitXor, ast.BitAnd, ast.FloorDiv, ast.Invert, ast.Not, ast.UAdd,
    ast.USub, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Is,
    ast.IsNot, ast.In, ast.NotIn,
    ast.comprehension, ast.ExceptHandler, ast.arguments, ast.arg, ast.keyword,
    ast.alias, ast.withitem,
)


class SandboxResult(BaseModel):
    """Outcome of a sandboxed execution."""

    stdout: str = ""
    stderr: str = ""
    success: bool = False
    error: Optional[str] = None


class _Validator(ast.NodeVisitor):
    """Reject anything outside the AST/name/module allowlists."""

    def generic_visit(self, node):  # noqa: D401 - part of NodeVisitor API
        if not isinstance(node, ALLOWED_NODES):
            raise SandboxError(f"disallowed syntax: {type(node).__name__}")
        super().generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("__"):
            raise SandboxError(f"dunder attribute access is forbidden: {node.attr}")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in FORBIDDEN_NAMES or (node.id.startswith("__") and node.id.endswith("__")):
            raise SandboxError(f"forbidden name: {node.id}")
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name.split(".")[0] not in ALLOWED_MODULES:
                raise SandboxError(f"module not allowed: {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        root = (node.module or "").split(".")[0]
        if root not in ALLOWED_MODULES:
            raise SandboxError(f"module not allowed: {node.module}")
        self.generic_visit(node)


def validate_code(code: str) -> ast.Module:
    """Parse and validate ``code``; raises SandboxError on any violation."""
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise SandboxError(f"syntax error: {exc}") from exc
    _Validator().visit(tree)
    return tree


def _scrubbed_env() -> dict[str, str]:
    """Minimal environment for the child process (keeps what Python needs
    to boot on Windows, drops everything else, notably credentials)."""
    env = {"PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
    for key in ("SystemRoot", "windir", "PATH", "TEMP", "TMP"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


async def safe_exec(code: str, timeout: Optional[float] = None) -> SandboxResult:
    """Validate then execute ``code`` in an isolated subprocess.

    Code failing static validation is rejected and reported in the returned
    SandboxResult (success=False) — it is never executed. ``timeout`` defaults
    to ``settings.sandbox_timeout_seconds`` when not given.
    """
    if timeout is None:
        from backend.core.config import get_settings

        timeout = get_settings().sandbox_timeout_seconds
    try:
        validate_code(code)
    except SandboxError as exc:
        return SandboxResult(success=False, error=str(exc))

    payload = base64.b64encode(code.encode("utf-8")).decode("ascii")
    runner = (
        "import base64,sys;"
        f"exec(compile(base64.b64decode('{payload}').decode('utf-8'),'<sandbox>','exec'))"
    )
    with tempfile.TemporaryDirectory(prefix="legal_ai_sandbox_") as workdir:
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-I", "-c", runner,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workdir,
                env=_scrubbed_env(),
            )
        except Exception as exc:
            return SandboxResult(success=False, error=f"failed to start sandbox subprocess: {exc}")
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            return SandboxResult(success=False, error=f"execution timed out after {timeout}s")

    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    if proc.returncode == 0:
        return SandboxResult(stdout=stdout, stderr=stderr, success=True)
    return SandboxResult(stdout=stdout, stderr=stderr, success=False, error=stderr.strip() or f"exit code {proc.returncode}")
