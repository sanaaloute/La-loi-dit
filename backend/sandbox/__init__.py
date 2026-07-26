"""Sandboxed code execution backends.

The default backend (:mod:`backend.sandbox.python_sandbox`) validates code
statically (AST allowlist) and then runs it in an isolated subprocess.
Pyodide and E2B adapters share the same interface and stay disabled until
explicitly configured.
"""

from backend.sandbox.python_sandbox import SandboxResult, safe_exec

__all__ = ["SandboxResult", "safe_exec"]
