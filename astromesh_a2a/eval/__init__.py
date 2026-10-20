"""Attack eval: local malicious A2A servers ("traps") and how many the Policy stops.

``await run_traps(policy)`` runs every trap in ``traps.TRAPS`` against ``policy`` and
returns an ``EvalReport``; ``python -m astromesh_a2a.eval`` prints the table and exits
non-zero unless every trap was stopped. See ``docs/eval.md`` for what each trap does
and what "stopped" means for it.

The traps run on a local server (``127.0.0.1``): the eval harness opts its OWN agents
into loopback (see ``astromesh_a2a.eval.traps``). Needs the ``server`` extra (uvicorn,
Starlette, the SDK's HTTP server).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from astromesh_a2a.policy import Policy

__all__ = ["DEFAULT_POLICY", "EvalReport", "format_report", "run_traps"]

DEFAULT_POLICY = Policy(allowed=("127.0.0.1",))


@dataclass(frozen=True)
class EvalReport:
    """``stopped`` of ``total`` traps were stopped; ``by_trap`` and ``details`` per trap."""

    total: int
    stopped: int
    by_trap: dict[str, bool]
    details: dict[str, str] = field(default_factory=dict)

    @property
    def all_stopped(self) -> bool:
        return self.stopped == self.total


async def run_traps(policy: Policy = DEFAULT_POLICY) -> EvalReport:
    """Run every trap against ``policy`` (its own host added to ``allowed``, http allowed).

    An unexpected error inside a trap counts as NOT stopped (its detail says why).
    """
    from astromesh_a2a.adapters._common import require

    for module in ("uvicorn", "starlette", "sse_starlette"):
        require(module, "server")
    from astromesh_a2a.eval.traps import TRAPS, running_server

    by_trap: dict[str, bool] = {}
    details: dict[str, str] = {}
    async with running_server() as server:
        for name, trap in TRAPS.items():
            server.reset()
            try:
                outcome = await trap(server, policy)
            except Exception as exc:  # noqa: BLE001 - a broken trap is reported, not raised
                by_trap[name], details[name] = False, f"trap error: {type(exc).__name__}: {exc}"
            else:
                by_trap[name], details[name] = outcome.stopped, outcome.detail
    return EvalReport(
        total=len(by_trap), stopped=sum(by_trap.values()), by_trap=by_trap, details=details
    )


def format_report(report: EvalReport) -> str:
    """The report as a plain-text table (trap, stopped, detail)."""
    width = max([len("trap"), *map(len, report.by_trap)])
    lines = [f"{'trap':<{width}}  stopped  detail", f"{'-' * width}  -------  ------"]
    for name, stopped in report.by_trap.items():
        mark = "yes" if stopped else "NO"
        lines.append(f"{name:<{width}}  {mark:<7}  {report.details.get(name, '')}")
    lines.append(f"\n{report.stopped}/{report.total} traps stopped")
    return "\n".join(lines)
