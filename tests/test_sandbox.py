"""Python sandbox tests (offline)."""

from __future__ import annotations

import pytest


async def test_safe_exec_runs_harmless_code():
    from backend.sandbox.python_sandbox import safe_exec

    result = await safe_exec("print(2+2)")
    assert result.success
    assert "4" in result.stdout


@pytest.mark.parametrize(
    "code",
    [
        "import os\nprint(os.getcwd())",
        "open('/etc/passwd').read()",
        "__import__('os').system('echo pwned')",
    ],
)
async def test_dangerous_code_rejected_without_executing(code):
    from backend.sandbox.python_sandbox import safe_exec

    result = await safe_exec(code)
    assert not result.success
    assert "pwned" not in getattr(result, "stdout", "")
