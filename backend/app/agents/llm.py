"""Model-agnostic chat interface + the Gemini adapter.

The tool loop only knows `ChatModel`, so it is tested offline with a scripted fake. Messages use a neutral
format; the Gemini adapter converts them and keeps the raw model turn (needed for Gemini's thought signatures).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

from ..retry import is_transient, retry_transient

log = logging.getLogger("truecost.llm")
REQUEST_TIMEOUT_MS = 15_000     # per Gemini call; typical calls take 1 to 4 seconds


@dataclass
class ToolCall:
    name: str
    args: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    name: str
    output: Any


@dataclass
class Msg:
    role: str                                    # "user" | "model" | "tool"
    text: Optional[str] = None
    calls: List[ToolCall] = field(default_factory=list)       # role == "model"
    results: List[ToolResult] = field(default_factory=list)   # role == "tool"
    raw: Any = None                                           # adapter-private (e.g. Gemini Content)


@dataclass
class ModelTurn:
    text: Optional[str]
    calls: List[ToolCall]
    raw: Any = None


@dataclass
class ToolDecl:
    name: str
    description: str
    parameters: Dict[str, Any]                   # JSON-schema-like: {"type":"OBJECT","properties":{...}}


class ChatModel(Protocol):
    model_name: str

    def turn(self, system: str, messages: List[Msg], tools: List[ToolDecl]) -> ModelTurn: ...

    def final_json(self, system: str, prompt: str, schema: Dict[str, Any]) -> Dict[str, Any]: ...


class GeminiChat:
    """ChatModel backed by Gemini function calling. temperature 0; automatic function calling OFF (we run the loop)."""

    def __init__(self, api_key: str, model: str, client: Any = None, fallback_model: Optional[str] = None):
        self._primary = model
        self._fallback = fallback_model if fallback_model and fallback_model != model else None
        self._used: Optional[str] = None             # the model that actually answered last
        if client is None:
            from google import genai
            from google.genai import types
            # a stuck call must fail fast (and be retried) instead of hanging until a proxy cuts the connection
            client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS))
        self._client = client

    @property
    def model_name(self) -> str:
        return self._used or self._primary

    def _generate(self, **kwargs: Any) -> Any:
        """generate_content with retries for temporary errors (503 "high demand", 429), then once on a backup model."""
        models = [self._primary] + ([self._fallback] if self._fallback else [])
        last: Optional[BaseException] = None
        for i, name in enumerate(models):
            try:
                resp = retry_transient(lambda: self._client.models.generate_content(model=name, **kwargs),
                                       delays=(1.0, 2.5) if i == 0 else (1.0,))
                self._used = name
                return resp
            except Exception as e:  # noqa: BLE001
                if not is_transient(e):
                    raise
                last = e
                log.warning("model %s unavailable after retries (%s)%s", name, str(e)[:100],
                            "; trying the backup model" if i + 1 < len(models) else "")
        assert last is not None
        raise last

    def _contents(self, messages: List[Msg]):
        from google.genai import types
        out = []
        for m in messages:
            if m.role == "user":
                out.append(types.Content(role="user", parts=[types.Part.from_text(text=m.text or "")]))
            elif m.role == "model":
                if m.raw is not None:
                    out.append(m.raw)
                else:
                    parts = ([types.Part.from_text(text=m.text)] if m.text else []) + [
                        types.Part(function_call=types.FunctionCall(name=c.name, args=c.args)) for c in m.calls]
                    out.append(types.Content(role="model", parts=parts))
            else:
                out.append(types.Content(role="user", parts=[
                    types.Part.from_function_response(name=r.name, response={"result": r.output}) for r in m.results]))
        return out

    def turn(self, system: str, messages: List[Msg], tools: List[ToolDecl]) -> ModelTurn:
        from google.genai import types
        decls = [types.FunctionDeclaration(name=t.name, description=t.description, parameters=t.parameters) for t in tools]
        resp = self._generate(
            contents=self._contents(messages),
            config=types.GenerateContentConfig(
                system_instruction=system, temperature=0,
                tools=[types.Tool(function_declarations=decls)] if decls else None,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)))
        content = resp.candidates[0].content
        calls, texts = [], []
        for part in content.parts or []:
            if getattr(part, "function_call", None):
                calls.append(ToolCall(part.function_call.name, dict(part.function_call.args or {})))
            elif getattr(part, "text", None):
                texts.append(part.text)
        return ModelTurn(text="".join(texts) or None, calls=calls, raw=content)

    def final_json(self, system: str, prompt: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        from google.genai import types
        resp = self._generate(
            contents=prompt,
            config=types.GenerateContentConfig(system_instruction=system, temperature=0,
                                               response_mime_type="application/json", response_schema=schema))
        return json.loads(resp.text)
