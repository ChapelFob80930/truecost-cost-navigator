"""The tool-use loop: allow-listed tools, hard cap on calls, graceful failure, full trail.

Rules enforced in code (not only in the prompt):
- only tools in the `tools` dict can run; anything else is refused and still counts against the cap,
- at most `max_calls` tool calls per request (default 5) - the loop STOPS and reports it,
- tool exceptions and bad arguments are returned to the model as an error result, never crash the request,
- a model/API failure ends the loop with `error` set, so the caller can fall back (cached mode or code-only output).
"""
from __future__ import annotations

import dataclasses
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel

from .llm import ChatModel, Msg, ToolDecl, ToolResult
from .schemas import ToolCallRecord

log = logging.getLogger("truecost.agent")
MAX_CALLS = 5
MAX_TURNS = 8


@dataclass
class ToolSpec:
    decl: ToolDecl
    fn: Callable[..., Any]
    source: Optional[str] = None


@dataclass
class LoopResult:
    trail: List[ToolCallRecord] = field(default_factory=list)
    calls_used: int = 0
    final_text: Optional[str] = None
    error: Optional[str] = None


def to_jsonable(x: Any) -> Any:
    if isinstance(x, BaseModel):
        return x.model_dump(mode="json")
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        return to_jsonable(dataclasses.asdict(x))
    if isinstance(x, dict):
        return {str(k): to_jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)):
        return [to_jsonable(v) for v in x]
    return x if isinstance(x, (str, int, float, bool)) or x is None else str(x)


def run_tool_loop(model: ChatModel, system: str, user_message: str, tools: Dict[str, ToolSpec],
                  max_calls: int = MAX_CALLS, max_turns: int = MAX_TURNS,
                  on_event: Optional[Callable[[str, Any], None]] = None) -> LoopResult:
    """`on_event("action", {step, tool, inputs, thought})` fires just BEFORE a tool runs and
    `on_event("observation", ToolCallRecord)` just AFTER, so a UI can show the agent's work as it happens.
    A failing callback is ignored: watching must never break the agent."""
    def emit(kind: str, payload: Any) -> None:
        if on_event is not None:
            try:
                on_event(kind, payload)
            except Exception:  # noqa: BLE001
                log.warning("on_event callback failed", exc_info=True)

    res = LoopResult()
    messages: List[Msg] = [Msg(role="user", text=user_message)]
    decls = [t.decl for t in tools.values()]

    for _ in range(max_turns):
        try:
            turn = model.turn(system, messages, decls)
        except Exception as e:  # noqa: BLE001 - API/network failure must degrade, not 500
            log.exception("model call failed")
            res.error = f"The AI model could not be reached ({type(e).__name__}). Showing results from code only."
            return res
        messages.append(Msg(role="model", text=turn.text, calls=turn.calls, raw=turn.raw))
        if not turn.calls:
            res.final_text = turn.text
            return res

        results: List[ToolResult] = []
        for idx, call in enumerate(turn.calls):
            if res.calls_used >= max_calls:
                res.error = f"Stopped: the agent reached its limit of {max_calls} tool calls."
                log.warning("tool cap reached")
                return res
            res.calls_used += 1
            # The model's stated reason: a `thought` argument on the call (reliable with function calling), or else the
            # text it wrote before its first call. The argument is removed so tools never receive it.
            args = dict(call.args or {})
            arg_thought = args.pop("thought", None)
            thought = (turn.text if idx == 0 and turn.text else None) or (arg_thought if isinstance(arg_thought, str) else None)
            emit("action", {"step": len(res.trail) + 1, "tool": call.name, "inputs": to_jsonable(args), "thought": thought})
            res.trail.append(_execute(len(res.trail) + 1, call.name, args, tools))
            rec = res.trail[-1]
            rec.thought = thought
            emit("observation", rec)
            results.append(ToolResult(call.name, rec.output if rec.ok else {"error": rec.error}))
        messages.append(Msg(role="tool", results=results))

    res.error = "Stopped: the agent took too many steps."
    return res


def _execute(step: int, name: str, args: Dict[str, Any], tools: Dict[str, ToolSpec]) -> ToolCallRecord:
    t0 = time.perf_counter()
    spec = tools.get(name)
    if spec is None:
        rec = ToolCallRecord(step=step, tool=name, inputs=to_jsonable(args), ok=False, error="This tool is not allowed.")
    else:
        allowed = set(spec.decl.parameters.get("properties", {}))
        clean = {k: v for k, v in (args or {}).items() if k in allowed}
        try:
            out = to_jsonable(spec.fn(**clean))
            rec = ToolCallRecord(step=step, tool=name, inputs=to_jsonable(clean), output=out, source=spec.source)
        except Exception as e:  # noqa: BLE001 - tool errors go back to the model
            rec = ToolCallRecord(step=step, tool=name, inputs=to_jsonable(clean), ok=False, error=f"{type(e).__name__}: {e}")
    rec.duration_ms = int((time.perf_counter() - t0) * 1000)
    log.info("tool=%s ok=%s ms=%d inputs=%s", rec.tool, rec.ok, rec.duration_ms, rec.inputs)
    return rec
