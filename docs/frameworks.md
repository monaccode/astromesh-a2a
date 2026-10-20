# Frameworks

`agent.as_tool(framework)` turns a `RemoteAgent` into a native tool of your framework. Each
adapter imports its framework on first use; a missing one raises `MissingExtraError` (an
`ImportError`) naming the extra to install.

Every example starts the same way:

```python
from astromesh_a2a import Auth, Policy, RemoteAgent

trips = RemoteAgent(
    "https://trips.example.com",
    policy=Policy(allowed=("trips.example.com",)),
    auth=Auth.bearer("TRIPS_TOKEN"),
)
await trips.card()  # name and description come from the card, or pass name=/description=
```

## The tool contract (all frameworks)

- **One input, `query: str`.** The first call sends it. When the previous call on the SAME
  tool object came back waiting for input, the next call replies to that task instead.
  A tool object holds one conversation: build one per conversation or user, never share one
  across users.
- **The output** is the remote's words inside
  `<remote-agent-output untrusted="true" agent="..." status="...">`, with `<`, `>`, `&` and
  quotes escaped. A pending task adds its `task_id` and a note, outside the block, telling
  the model that only the user can answer the remote's question.
- **Errors** follow each framework's tool-error convention with a short, safe message (no
  stack, no credentials). `RemoteTool.last_result` keeps the last `Result`.
- **Name and description** default to the card. The name (from the card, or your own
  `name=`) is sanitized for every framework (`[a-z0-9_]`, a Python identifier, at most 64
  characters); the remote's description is quoted as untrusted under the library's own
  framing.

Writing your own tool around a `RemoteAgent` (a framework not listed, your own loop)?
Reuse the same safety text from `astromesh_a2a.adapters`: `tool_description(card_name,
card_description)` (the default description: the remote's output is untrusted data, a
confirmation or approval is never answered on the user's behalf, and the remote's
self-description is quoted as untrusted), `wrap_untrusted(result, agent=...)` (the output block) and `PENDING_NOTE` (the
note it appends when the remote waits for input).

```python
from astromesh_a2a.adapters import PENDING_NOTE, tool_description, wrap_untrusted

card = await trips.card()
description = tool_description(card.name, card.description)
result = await trips.send(query)
output = wrap_untrusted(result, agent=card.name)  # ends with PENDING_NOTE if pending
```

| Framework | Returns | Tool errors |
|---|---|---|
| `langchain` | `StructuredTool` (sync + async) | `ToolException` (handled: the model sees the message) |
| `crewai` | `BaseTool` (`run` + `arun`) | exception |
| `adk` | `FunctionTool` (async) | `{"error": ...}` |
| `openai-agents` | `FunctionTool` (async) | `failure_error_function` |
| `pydantic-ai` | `Tool` (async) | `ModelRetry` only on `A2ATimeout`; other errors are the tool result |
| `llamaindex` | `FunctionTool` (sync + async) | exception |
| `smolagents` | `Tool` (sync) | exception |
| `agent-framework` | `FunctionTool` (async) | exception |
| `mcp` | FastMCP `Tool` (async) | `ToolError` (`isError` result) |

## LangChain / LangGraph

`pip install 'astromesh-a2a[langchain]'` (the tool needs only `langchain-core`; `create_agent`
comes from the `langchain` package)

```python
from langchain.agents import create_agent

agent = create_agent("openai:gpt-5-mini", tools=[trips.as_tool("langchain")])
state = await agent.ainvoke({"messages": [{"role": "user", "content": "A flight to Lima"}]})
```

## CrewAI

`pip install 'astromesh-a2a[crewai]'`

```python
from crewai import Agent, Crew, Task

planner = Agent(
    role="Travel planner",
    goal="Plan the trip",
    backstory="Delegates bookings to the trips agent.",
    tools=[trips.as_tool("crewai")],
)
task = Task(description="Book a flight to Lima", expected_output="A booking", agent=planner)
result = await Crew(agents=[planner], tasks=[task]).kickoff_async()
```

Inside `async` code use `kickoff_async()` (or `await asyncio.to_thread(crew.kickoff)`):
a sync `kickoff()` on the agent's loop would block it (see the README, "Sync and async").

## Google ADK

`pip install 'astromesh-a2a[adk]'`

```python
from google.adk.agents import Agent

planner = Agent(
    name="planner",
    model="gemini-2.5-flash",
    instruction="Use the trips agent for bookings.",
    tools=[trips.as_tool("adk")],
)
```

## OpenAI Agents SDK

`pip install 'astromesh-a2a[openai-agents]'`

```python
from agents import Agent, Runner

planner = Agent(name="planner", instructions="Plan trips.", tools=[trips.as_tool("openai-agents")])
result = await Runner.run(planner, "A flight to Lima on Friday")
```

## PydanticAI

`pip install 'astromesh-a2a[pydantic-ai]'` (Python 3.11+)

```python
from pydantic_ai import Agent

planner = Agent("openai:gpt-5-mini", tools=[trips.as_tool("pydantic-ai")])
result = await planner.run("A flight to Lima on Friday")
```

## LlamaIndex

`pip install 'astromesh-a2a[llamaindex]'`

```python
from llama_index.core.agent.workflow import FunctionAgent

planner = FunctionAgent(tools=[trips.as_tool("llamaindex")], llm=llm)  # llm: your LlamaIndex LLM
response = await planner.run("A flight to Lima on Friday")
```

## smolagents

`pip install 'astromesh-a2a[smolagents]'`

```python
import asyncio

from smolagents import InferenceClientModel, ToolCallingAgent

# `trips` as above (card already loaded). InferenceClientModel reads HF_TOKEN.
planner = ToolCallingAgent(tools=[trips.as_tool("smolagents")], model=InferenceClientModel())
answer = await asyncio.to_thread(planner.run, "A flight to Lima on Friday")
```

smolagents is sync: from `async` code, run it in a thread so the agent's loop stays free.

## Microsoft Agent Framework

`pip install 'astromesh-a2a[agent-framework]'`

```python
from agent_framework import Agent

planner = Agent(client, instructions="Plan trips.", tools=[trips.as_tool("agent-framework")])
response = await planner.run("A flight to Lima on Friday")  # client: your chat client
```

## MCP

`pip install 'astromesh-a2a[mcp]'`

```python
from mcp.server.fastmcp import FastMCP

from astromesh_a2a.adapters.mcp import build_server

server = FastMCP("travel", tools=[trips.as_tool("mcp")])  # into your own server
server = build_server(trips)  # or a server with only this tool; server.run() (stdio)
```

A FastMCP server may serve several sessions, but the tool keeps one conversation with the
remote: build one server per user.

## Serving a LangChain runnable

The other way round, a LangChain runnable or LangGraph graph served as an A2A agent:

```python
from astromesh_a2a import api_key_auth, serve
from astromesh_a2a.adapters.server_langchain import handler_from_runnable

serve(handler_from_runnable(graph), card, auth=api_key_auth("TRIPS_API_KEY"))
```

See [server.md](server.md) and `examples/serve_langchain.py`.

## Sync frameworks and event loops

A `RemoteAgent` is bound to the event loop of its first I/O. Sync tool calls are dispatched
to that loop when it runs in another thread, or run on a background loop when the agent has
none yet. From plain sync code:

```python
from astromesh_a2a.adapters import run_sync

trips = RemoteAgent("https://trips.example.com", policy=policy)
run_sync(trips.card(), agent=trips)
tool = trips.as_tool("crewai")
print(tool.run(query="A flight to Lima on Friday"))
run_sync(trips.aclose(), agent=trips)
```
