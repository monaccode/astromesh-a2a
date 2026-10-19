"""Serve a LangChain runnable as an A2A agent: ``handler_from_runnable(runnable)``.

>>> serve(handler_from_runnable(chain), card=CardSpec(...), auth=api_key_auth("KEY"))

The handler awaits ``runnable.ainvoke(input, config)``.

**Input** (``input_key``): ``"auto"`` (default) reads the runnable's input schema: a
string schema gets the text itself; an object schema with ``messages`` (a LangGraph /
``create_agent`` graph) gets ``{"messages": [{"role": "user", "content": text}]}``; an
object schema with exactly one required field gets ``{<that field>: text}``; anything
else gets ``{"input": text}``. ``input_key=None`` always sends the text itself;
``input_key="question"`` always sends ``{"question": text}``. Only ``Incoming.text``
reaches the runnable (data parts do not), and it was written by another program:
treat it as untrusted in your prompt.

**Config:** ``configurable.thread_id`` is ``Incoming.session_key`` (scoped per caller,
see ``Incoming``), so a checkpointed graph keeps one thread per caller conversation;
``metadata`` carries the A2A task id and ``a2a_untrusted=True``.

**Output:** an ``Outgoing`` passes through; a ``str`` is the answer; a message
(anything with ``.content``) gives its text; a dict with ``messages`` gives the last
message's text, one with ``output`` that value; any other dict becomes ``data``; anything
else ``str()``. A runnable cannot ask for confirmation through this handler: wrap it in
your own handler to return ``Outgoing(needs_confirmation=...)``.

Needs only ``langchain-core`` at call time (the ``langchain`` extra); serving needs the
``server`` extra.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from astromesh_a2a.types import Incoming, Outgoing

__all__ = ["handler_from_runnable"]

_AUTO = "auto"


def _input_builder(runnable: Any, input_key: str | None) -> Callable[[str], Any]:
    if input_key == _AUTO:
        input_key = _auto_key(runnable)
    if input_key is None:
        return lambda text: text
    if input_key == "messages":
        return lambda text: {"messages": [{"role": "user", "content": text}]}
    key = input_key
    return lambda text: {key: text}


def _auto_key(runnable: Any) -> str | None:
    try:
        schema = runnable.get_input_jsonschema()
    except Exception:  # noqa: BLE001 - any failure: fall back to the common default
        return "input"
    if schema.get("type") == "string":
        return None
    props = schema.get("properties") or {}
    if "messages" in props:
        return "messages"
    required = schema.get("required") or []
    if len(required) == 1:
        return required[0]
    if not props and schema.get("type") != "object":
        return None  # untyped (Any) input: the text itself
    return "input"


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # content blocks
        texts = [
            b if isinstance(b, str) else b.get("text", "")
            for b in content
            if isinstance(b, str) or (isinstance(b, dict) and b.get("type", "text") == "text")
        ]
        return "".join(texts)
    return str(content)


def _message_text(message: Any) -> str:
    if isinstance(message, dict):
        return _text(message.get("content", ""))
    if isinstance(message, (tuple, list)) and len(message) == 2:
        return _text(message[1])
    return _text(getattr(message, "content", message))


def _to_outgoing(value: Any) -> Outgoing:
    if isinstance(value, Outgoing):
        return value
    if isinstance(value, str):
        return Outgoing(answer=value)
    if hasattr(value, "content"):
        return Outgoing(answer=_message_text(value))
    if isinstance(value, dict):
        messages = value.get("messages")
        if isinstance(messages, list) and messages:
            return Outgoing(answer=_message_text(messages[-1]))
        if "output" in value:
            return _to_outgoing(value["output"])
        try:
            data = json.loads(json.dumps(value, allow_nan=False))
        except (TypeError, ValueError):
            return Outgoing(answer=str(value))
        return Outgoing(data=[data])
    return Outgoing(answer=str(value))


def handler_from_runnable(
    runnable: Any, *, input_key: str | None = _AUTO
) -> Callable[[Incoming], Awaitable[Outgoing]]:
    """A ``serve``/``create_app`` handler that runs ``runnable`` (see the module docs)."""
    if not callable(getattr(runnable, "ainvoke", None)):
        raise TypeError("runnable must have an async ainvoke() (a LangChain Runnable)")
    build = _input_builder(runnable, input_key)

    async def handler(incoming: Incoming) -> Outgoing:
        config = {
            "configurable": {"thread_id": incoming.session_key or f":{incoming.context_id}"},
            "metadata": {"a2a_task_id": incoming.task_id, "a2a_untrusted": True},
        }
        return _to_outgoing(await runnable.ainvoke(build(incoming.text), config=config))

    return handler
