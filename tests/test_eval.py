"""The attack eval: the default policy stops every trap; a lax one does not."""

from __future__ import annotations

import pytest

from astromesh_a2a import BlockedDestination, Policy, RemoteAgent, client
from astromesh_a2a.eval import DEFAULT_POLICY, EvalReport, format_report, run_traps, traps
from astromesh_a2a.eval import __main__ as cli
from astromesh_a2a.eval.traps import TRAPS, running_server

LAX = Policy(
    allowed=("127.0.0.1",),
    accept_parts=frozenset({"text", "data", "file"}),
    max_bytes=64 * 1024 * 1024,
    max_stream_bytes=64 * 1024 * 1024,
)


async def test_politica_por_defecto_detiene_todas():
    report = await run_traps()
    assert report.total == len(TRAPS) == 9
    assert report.stopped == report.total, format_report(report)
    assert set(report.by_trap) == set(TRAPS)


async def test_politica_laxa_deja_pasar_alguna():
    report = await run_traps(LAX)
    assert report.stopped < report.total
    assert report.by_trap["file_part"] is False
    assert report.by_trap["huge_artifact"] is False
    # What the policy does not govern still holds under a lax one.
    for name in ("dns_private", "redirect_internal", "self_confirm", "injection_text"):
        assert report.by_trap[name], report.details[name]


async def test_eval_no_abre_loopback_fuera_del_harness():
    original = client._http_client
    await run_traps()
    assert client._http_client is original  # no module-wide patch
    agent = RemoteAgent(
        "http://127.0.0.1:9/", policy=Policy(allowed=("127.0.0.1",), allow_insecure_http=True)
    )
    async with agent:
        with pytest.raises(BlockedDestination):
            await agent.card()


def test_cli_sale_distinto_de_cero_si_alguna_pasa(monkeypatch, capsys):
    async def fake(policy=None):
        return EvalReport(
            total=2, stopped=1, by_trap={"a": True, "b": False}, details={"a": "ok", "b": "leaked"}
        )

    monkeypatch.setattr(cli, "run_traps", fake)
    assert cli.main() == 1
    out = capsys.readouterr().out
    assert "leaked" in out
    assert "NO" in out
    assert "1/2 traps stopped" in out


def test_cli_cero_si_todas_paradas(monkeypatch, capsys):
    async def fake(policy=None):
        return EvalReport(total=1, stopped=1, by_trap={"a": True}, details={"a": "ok"})

    monkeypatch.setattr(cli, "run_traps", fake)
    assert cli.main() == 0
    assert "1/1 traps stopped" in capsys.readouterr().out


# ---- negative controls: a broken library must make the trap report NOT stopped ----


async def _run_one(trap) -> object:
    async with running_server() as server:
        return await trap(server, DEFAULT_POLICY)


async def test_control_wrapper_sin_escapar_no_detiene_injection(monkeypatch):
    from astromesh_a2a.adapters import _common

    monkeypatch.setattr(_common, "_attr", lambda text: text)  # attribute quotes unescaped
    outcome = await _run_one(traps.injection_text)
    assert outcome.stopped is False, outcome.detail
    assert "wrapper broken" in outcome.detail


async def test_control_autoconfirmacion_no_detiene_self_confirm(monkeypatch):
    from astromesh_a2a.adapters._common import RemoteTool

    original = RemoteTool.acall

    async def auto_yes(self, query):
        out = await original(self, query)
        if self.pending_task_id is not None:  # a library that answers for the user
            out = await original(self, "yes")
        return out

    monkeypatch.setattr(RemoteTool, "acall", auto_yes)
    outcome = await _run_one(traps.self_confirm)
    assert outcome.stopped is False, outcome.detail
    assert "RPCs sent: 2" in outcome.detail
