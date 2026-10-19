# astromesh-a2a

## Serving an agent: sessions

`Incoming.context_id` is chosen by the caller and is not scoped: two callers can send the
same one. Key any memory or session state by `Incoming.session_key`
(`"<caller>:<context_id>"`), never by `context_id` alone.
