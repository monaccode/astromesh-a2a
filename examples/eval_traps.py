"""Run the attack eval with your own policy and print the table.

    uv run python examples/eval_traps.py

The traps are local servers; the eval adds each trap's host to ``allowed`` and allows
plain http for them, and keeps everything else from the policy you pass.
"""

from __future__ import annotations

import asyncio

from astromesh_a2a import Policy
from astromesh_a2a.eval import format_report, run_traps


async def main() -> None:
    strict = Policy(allowed=("127.0.0.1",))
    print("Default policy\n" + format_report(await run_traps(strict)))

    lax = Policy(
        allowed=("127.0.0.1",),
        accept_parts=frozenset({"text", "data", "file"}),
        max_bytes=64 * 1024 * 1024,
        max_stream_bytes=64 * 1024 * 1024,
    )
    print("\nLax policy (files accepted, 64 MiB bodies)\n" + format_report(await run_traps(lax)))


if __name__ == "__main__":
    asyncio.run(main())
