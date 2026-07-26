"""Safe arithmetic calculator.

Expressions are evaluated with a strict AST interpreter (no ``eval``):
numbers, + - * / // % ** , parentheses, unary +/- and a small set of math
functions/constants. Anything else raises ValueError.
"""

from __future__ import annotations

import ast
import math
import operator
from typing import Any

TOOL_SPEC = {
    "name": "calculator",
    "description": "Evaluate a safe arithmetic expression (e.g. '655.957 * 12 / (1 + 0.18)').",
    "parameters": {
        "type": "object",
        "properties": {
            "expression": {"type": "string", "description": "Arithmetic expression to evaluate."},
        },
        "required": ["expression"],
    },
}

_BIN_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {
    "abs": abs, "round": round, "min": min, "max": max,
    "sqrt": math.sqrt, "log": math.log, "log10": math.log10, "exp": math.exp,
    "floor": math.floor, "ceil": math.ceil, "pow": pow,
}
_CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau}


def _eval(node: ast.AST) -> Any:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"unsupported constant: {node.value!r}")
    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"unsupported operator: {type(node.op).__name__}")
        return op(_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"unsupported unary operator: {type(node.op).__name__}")
        return op(_eval(node.operand))
    if isinstance(node, ast.Name):
        if node.id in _CONSTS:
            return _CONSTS[node.id]
        raise ValueError(f"unknown name: {node.id}")
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
            raise ValueError("only whitelisted math functions are allowed")
        return _FUNCS[node.func.id](*[_eval(a) for a in node.args])
    raise ValueError(f"unsupported expression element: {type(node).__name__}")


def evaluate(expression: str) -> float:
    """Parse and evaluate an arithmetic expression safely."""
    tree = ast.parse(expression, mode="eval")
    return _eval(tree)


async def run(expression: str) -> dict:
    """TOOL entrypoint: evaluate ``expression`` and return the result."""
    try:
        result = evaluate(expression)
    except Exception as exc:
        return {"success": False, "error": str(exc), "expression": expression}
    return {"success": True, "result": result, "expression": expression}
