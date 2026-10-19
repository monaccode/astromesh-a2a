# astromesh-a2a

## Framework adapters: event loops

`agent.as_tool("<framework>")` gives a native tool. A `RemoteAgent` is bound to the
event loop of its first I/O. Sync tools (CrewAI `run`, smolagents, LangChain `invoke`...)
are dispatched to that loop when it runs in another thread, or run on a background loop
when the agent has none yet (which then binds it there). Do not block the agent's loop
while its tools run: calling a sync framework entry point (`crew.kickoff()`) inside
`async def main` deadlocks, so the dispatch gives up after `policy.timeout` + 5 s with
`LoopBindingError`. Use the async entry point (`kickoff_async`, `ainvoke`) or
`await asyncio.to_thread(crew.kickoff)`. From sync code, use
`run_sync(agent.card(), agent=agent)` and `run_sync(agent.aclose(), agent=agent)`.

## Serving an agent: sessions

`Incoming.context_id` is chosen by the caller and is not scoped: two callers can send the
same one. Key any memory or session state by `Incoming.session_key`
(`"<caller>:<context_id>"`), never by `context_id` alone.
