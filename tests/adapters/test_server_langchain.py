import pytest

pytest.importorskip("langchain_core")

from langchain_core.messages import AIMessage
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnableLambda

from astromesh_a2a import Incoming, Origin, Outgoing
from astromesh_a2a.adapters.server_langchain import handler_from_runnable


def _incoming(text="hola"):
    return Incoming(
        text=text,
        data=[],
        origin=Origin.AGENT,
        context_id="c1",
        task_id="t1",
        session_key="alice:c1",
    )


async def test_handler_from_runnable():
    seen = {}

    async def run(value, config):
        seen["value"], seen["config"] = value, config
        return AIMessage(
            content=[{"type": "text", "text": "hi "}, {"type": "text", "text": "there"}]
        )

    out = await handler_from_runnable(RunnableLambda(run))(_incoming())
    assert out == Outgoing(answer="hi there")
    assert seen["value"] == "hola"  # untyped input: the text itself
    assert seen["config"]["configurable"]["thread_id"] == "alice:c1"
    assert seen["config"]["metadata"]["a2a_untrusted"] is True


async def test_handler_maps_input_shapes():
    prompt = PromptTemplate.from_template("Q: {question}")
    out = await handler_from_runnable(prompt | RunnableLambda(lambda p: p.to_string()))(
        _incoming("why?")
    )
    assert out.answer == "Q: why?"

    got = []
    explicit = RunnableLambda(lambda v: got.append(v) or {"output": "done"})
    assert (await handler_from_runnable(explicit, input_key="input")(_incoming())).answer == "done"
    assert got == [{"input": "hola"}]
    as_text = RunnableLambda(lambda v: {"temp": 21})
    assert (await handler_from_runnable(as_text, input_key=None)(_incoming())).data == [
        {"temp": 21}
    ]


async def test_handler_messages_graph_shape():
    class Graph:
        def get_input_jsonschema(self):
            return {"type": "object", "properties": {"messages": {}}, "required": ["messages"]}

        async def ainvoke(self, value, config=None):
            assert value == {"messages": [{"role": "user", "content": "hola"}]}
            return {"messages": [("user", "hola"), AIMessage(content="buenas")]}

    assert (await handler_from_runnable(Graph())(_incoming())).answer == "buenas"


def test_handler_needs_ainvoke():
    with pytest.raises(TypeError, match="ainvoke"):
        handler_from_runnable(object())
