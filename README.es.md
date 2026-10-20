<p align="center">
  <img src="https://raw.githubusercontent.com/monaccode/astromesh-a2a/main/assets/logo.png" alt="Astromesh A2A" width="640">
</p>

# astromesh-a2a

**Que tu agente hable con otros agentes sin confiar en ellos.**

[![astromesh-runtime](https://img.shields.io/badge/astromesh-runtime-f472b6)](https://monaccode.github.io/astromesh/#ecosystem)
[![Release](https://img.shields.io/github/v/release/monaccode/astromesh-a2a?label=release)](https://github.com/monaccode/astromesh-a2a/releases)
[![CI](https://github.com/monaccode/astromesh-a2a/actions/workflows/ci.yml/badge.svg)](https://github.com/monaccode/astromesh-a2a/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/astromesh-a2a?label=PyPI)](https://pypi.org/project/astromesh-a2a/)
[![Python](https://img.shields.io/pypi/pyversions/astromesh-a2a)](https://pypi.org/project/astromesh-a2a/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/monaccode/astromesh-a2a/blob/main/LICENSE)

*[Read in English](https://github.com/monaccode/astromesh-a2a/blob/main/README.md)*

[A2A (Agent2Agent)](https://a2a-protocol.org) es el protocolo abierto con el que los
agentes trabajan entre sí: un agente publica una tarjeta (card) que dice qué sabe hacer, y
otros agentes le mandan tareas por JSON-RPC, reciben respuestas, siguen el progreso en
streaming y contestan sus preguntas.

`astromesh-a2a` es un cliente y un servidor de A2A 1.x, construido sobre el
[SDK oficial de A2A](https://github.com/a2aproject/a2a-python). Mete un agente remoto
dentro de tu framework de agentes como una tool más (nueve frameworks), sirve tu propio
agente por A2A y trata todo lo que cruza el cable como lo que es: código que no controlás.

## Por qué no alcanza con el SDK

El SDK habla el protocolo. No decide a qué remotos podés llegar, cuánto de su salida
aceptás ni qué hace tu modelo con ella. Un agente remoto es un programa no confiable en el
servidor de otro; esta librería es la capa entre él y tu agente.

| Riesgo | Qué hace `astromesh-a2a` |
|---|---|
| Una card o una redirección mandan a tu cliente a `10.0.0.7`, `169.254.169.254` o `localhost` (SSRF) | **Salida de red fijada.** Cada request pasa por un transporte custodiado: sólo http(s), sólo direcciones públicas, cada host se resuelve una vez y la conexión queda fijada a la IP verificada (sin ventana de DNS rebinding), nunca sigue redirecciones. |
| Cualquier URL es alcanzable; una card anuncia su endpoint RPC en otro host | **Política de remotos.** `Policy.allowed` lista hosts o URLs de card; no se permite nada hasta que lo declares. La librería trae y revisa la card (host, sólo interfaz JSON-RPC A2A 1.x, interfaz en un host permitido) antes de que el SDK la vea. **Cards firmadas** opcionales, con claves fijadas por `kid` (nunca se descarga una URL `jku`). |
| El texto del remoto le dice a tu modelo "ignorá las instrucciones anteriores" | **Salida no confiable envuelta.** Las tools devuelven las palabras del remoto dentro de `<remote-agent-output untrusted="true" ...>`, con `<`, `>`, `&` y comillas escapados para que el bloque no se pueda cerrar ni falsificar. |
| El remoto pide "respondé sí para confirmar la transferencia" | **Nunca autoconfirma.** Una tarea `input_required` vuelve como pregunta pendiente; sólo un `reply()` explícito (del usuario, a través de tu agente) la continúa. El servidor marca todo mensaje entrante como `Origin.AGENT` y no confiable. |
| Un artefacto de 2 GB, un stream que nunca termina, un remoto que te come el presupuesto | **Presupuestos, timeouts y topes de tamaño.** `max_bytes` por cuerpo y parte, `max_stream_bytes` por stream, `timeout` por llamada (reloj de pared), `max_events` por stream, `Budget(max_calls, max_seconds)` por remoto. Las partes de archivo se descartan salvo que las aceptes. |
| No ves qué pasó, o tus logs filtran la API key | **Traza por etapa con redacción.** Cada llamada devuelve una traza (`card`, `send`, `status`, `artifact`, `message`, `rejection`, `result`) con claves, tokens, credenciales en URLs y firmas de URLs prefirmadas tachadas, y un `traceparent` W3C. Las credenciales salen sólo de variables de entorno o callables. |

No son promesas: un [eval de ataques](https://github.com/monaccode/astromesh-a2a/blob/main/docs/eval.md)
corre nueve remotos maliciosos contra la librería en cada push.

## Instalación

```bash
pip install astromesh-a2a                # cliente
pip install 'astromesh-a2a[server]'      # + servir tu propio agente (Starlette, uvicorn)
pip install 'astromesh-a2a[langchain]'   # + un agente remoto como tool de LangChain
```

Python 3.10+. Cada framework es un extra opcional (tabla más abajo); `signing` agrega PyJWT
para cards firmadas, `all` instala todo.

## Llamar a un agente remoto

```python
from astromesh_a2a import Auth, Policy, RemoteAgent

policy, auth = Policy(allowed=("trips.example.com",)), Auth.bearer("TRIPS_TOKEN")
async with RemoteAgent("https://trips.example.com", policy=policy, auth=auth) as trips:
    result = await trips.send("Buscame un vuelo a Lima para el viernes")
print(result.status, result.answer, result.trace)
```

`result.status` es `completed`, o `input_required` con la pregunta del remoto en
`result.pending`; `reply(task_id, text)` la contesta, `stream(text)` entrega eventos,
`cancel(task_id)` corta una tarea y `card()` devuelve la card revisada. Los errores tienen
tipo (`PolicyRejected`, `BlockedDestination`, `TaskFailed`, `A2ATimeout`,
`UnsupportedVersion`, todos `A2AError`). Guía: [docs/client.md](https://github.com/monaccode/astromesh-a2a/blob/main/docs/client.md).

## Servir tu agente

```python
from astromesh_a2a import CardSpec, Incoming, Outgoing, Skill, api_key_auth, serve


async def handle(msg: Incoming) -> Outgoing:  # msg.text viene de otro programa: no confiable
    return Outgoing(answer=await my_agent.run(msg.text, session=msg.session_key))


skill = Skill(id="book", name="Book trips", description="Flights and hotels.", tags=("travel",))
card = CardSpec("trips", "Books trips.", "1.0.0", "https://trips.example.com", [skill])
serve(handle, card, host="0.0.0.0", port=8000, auth=api_key_auth("TRIPS_API_KEY"))
```

La card se sirve en `/.well-known/agent-card.json` y JSON-RPC (bloqueante y SSE) en `/a2a`.
La autenticación es obligatoria: sin `auth=` el servidor no arranca salvo que pases
`insecure=True`. `create_app(...)` devuelve la app Starlette para montarla en tu propio
servidor. Guía: [docs/server.md](https://github.com/monaccode/astromesh-a2a/blob/main/docs/server.md).

Probá las dos puntas en tu máquina, sin red:

```bash
git clone https://github.com/monaccode/astromesh-a2a && cd astromesh-a2a
uv run python examples/hello_local.py
```

Se corre desde un checkout (los ejemplos no van en el wheel). La guardia de salida
rechaza `127.0.0.1`, así que el ejemplo habilita loopback sólo para su propio agente con
`astromesh_a2a.testing.allow_loopback(agent)`: sólo para desarrollo local y tests, nunca
en producción ([docs/policy.md](https://github.com/monaccode/astromesh-a2a/blob/main/docs/policy.md#loopback-local-development-and-tests-only)).

## Un agente remoto como tool de tu framework

```python
async with RemoteAgent("https://trips.example.com", policy=policy) as trips:
    await trips.card()  # el nombre y la descripción de la tool salen de la card
    tool = trips.as_tool("langchain")  # pasásela a tu agente: tools=[tool]
    print(await tool.ainvoke({"query": "Buscame un vuelo a Lima para el viernes"}))
```

La tool recibe un único `query`, guarda la conversación (y una tarea que espera respuesta)
en el objeto tool, devuelve la salida envuelta como no confiable y reporta los errores como
espera cada framework.

| Framework | `as_tool(...)` | Extra | Devuelve |
|---|---|---|---|
| LangChain / LangGraph | `"langchain"` | `langchain` | `StructuredTool` (sync + async) |
| CrewAI | `"crewai"` | `crewai` | `BaseTool` |
| Google ADK | `"adk"` | `adk` | `FunctionTool` |
| OpenAI Agents SDK | `"openai-agents"` | `openai-agents` | `FunctionTool` |
| PydanticAI | `"pydantic-ai"` | `pydantic-ai` | `Tool` (Python 3.11+) |
| LlamaIndex | `"llamaindex"` | `llamaindex` | `FunctionTool` (sync + async) |
| smolagents | `"smolagents"` | `smolagents` | `Tool` |
| Microsoft Agent Framework | `"agent-framework"` | `agent-framework` | `FunctionTool` |
| MCP | `"mcp"` | `mcp` | `Tool` de FastMCP; `adapters.mcp.build_server` para un servidor entero |

Al revés, `adapters.server_langchain.handler_from_runnable(runnable)` sirve un runnable de
LangChain o un grafo de LangGraph como agente A2A. Un ejemplo por framework:
[docs/frameworks.md](https://github.com/monaccode/astromesh-a2a/blob/main/docs/frameworks.md).

### Sync y async: event loops

Un `RemoteAgent` queda atado al event loop de su primera E/S (sus conexiones le pertenecen)
y rechaza cualquier otro con `LoopBindingError`. Las tools async funcionan sin más. Las
tools sync (`run` de CrewAI, smolagents, `invoke` de LangChain...) se despachan al loop del
agente cuando corre en otro hilo, o corren en un loop de fondo cuando el agente todavía no
tiene uno. No bloquees el loop del agente mientras corren sus tools: llamar a un punto de
entrada sync (`crew.kickoff()`) dentro de `async def main` se trabaría, así que el despacho
se rinde a los `policy.timeout` + 5 s con `LoopBindingError`. Usá el punto de entrada async
(`kickoff_async`, `ainvoke`) o `await asyncio.to_thread(crew.kickoff)`. Desde código sync,
usá `run_sync(agent.card(), agent=agent)` y `run_sync(agent.aclose(), agent=agent)`
(`from astromesh_a2a.adapters import run_sync`). Un `RemoteAgent` por loop.

## Sesiones

`Incoming.context_id` lo elige quien llama y no tiene alcance: dos llamadores pueden mandar
el mismo. Guardá memoria o estado de sesión por `Incoming.session_key`
(`"<caller>:<context_id>"`, donde caller es la identidad que devuelve tu `auth`), nunca sólo
por `context_id`. Del lado cliente, pasá `context_id=` a `send()` para mantener varios
mensajes en una misma conversación remota.

## El eval

Nueve remotos maliciosos locales: una card falsificada, una redirección a una dirección
interna, un nombre público que resuelve a `10.0.0.7`, un artefacto de 32 MiB, una parte de
archivo, inyección de prompt que intenta escaparse del bloque no confiable, un remoto que
pide que lo confirmen, un stream que nunca termina y un agente que sólo habla 0.3.

```bash
python -m astromesh_a2a.eval   # necesita el extra server
```

| Política | Trampas frenadas |
|---|---|
| Por defecto (`Policy(allowed=(host,))`) | **9/9** |
| Laxa (acepta archivos, cuerpos de 64 MiB) | 7/9 (pasan `huge_artifact` y `file_part`) |

Las otras siete se sostienen diga lo que diga la política: guardia de salida, revisión de
la card, el bloque no confiable y la regla de no autoconfirmar. CI falla si un cambio deja
pasar una trampa. Trampas, reglas de "frenada" y controles negativos:
[docs/eval.md](https://github.com/monaccode/astromesh-a2a/blob/main/docs/eval.md).

## Política

```python
from astromesh_a2a import Budget, Policy

policy = Policy(
    allowed=("trips.example.com", "https://hotels.example.org/.well-known/agent-card.json"),
    timeout=30.0,
    budget=Budget(max_calls=20, max_seconds=120),
)
```

Cada campo, su valor por defecto y por qué: [docs/policy.md](https://github.com/monaccode/astromesh-a2a/blob/main/docs/policy.md).

## En el ecosistema Astromesh

`astromesh-a2a` es la capa A2A de [Astromesh](https://github.com/monaccode/astromesh), como
[astromesh-rag](https://github.com/monaccode/astromesh-rag) es su capa de RAG y
[astromesh-okf](https://github.com/monaccode/astromesh-okf) su formato de conocimiento.
Planeado (todavía no publicado):

- **Core de Astromesh:** una tool `type: a2a` en el manifiesto del agente (`card_url`,
  `auth_env`, política, timeout) sobre esta librería, con `input_required` devuelto al
  modelo como observación; los turnos de agentes remotos cuentan como turnos de agente,
  nunca humanos.
- **ADK de Astromesh:** `remote_a2a()` para usar un agente A2A remoto desde agentes Python.
- **Nexus:** los agentes publicados se sirven por A2A (opt-in por agente), con conformidad
  probada contra este cliente.

Detalle: [docs/ecosystem.md](https://github.com/monaccode/astromesh-a2a/blob/main/docs/ecosystem.md).

## Contribuir

Issues y pull requests son bienvenidos:
[CONTRIBUTING.md](https://github.com/monaccode/astromesh-a2a/blob/main/CONTRIBUTING.md).

## Licencia

[Apache-2.0](https://github.com/monaccode/astromesh-a2a/blob/main/LICENSE). Parte del
ecosistema [Astromesh](https://monaccode.github.io/astromesh/).
