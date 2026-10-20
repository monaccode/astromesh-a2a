"""``python -m astromesh_a2a.eval``: run the traps with the default policy.

Exit status: 0 when every trap was stopped, 1 when some were not, 2 when the ``server``
extra is missing.
"""

from __future__ import annotations

import asyncio
import sys

from astromesh_a2a.adapters._common import MissingExtraError
from astromesh_a2a.eval import format_report, run_traps


def main() -> int:
    try:
        report = asyncio.run(run_traps())
    except MissingExtraError as exc:
        print(exc, file=sys.stderr)
        return 2
    print(format_report(report))
    return 0 if report.all_stopped else 1


if __name__ == "__main__":
    sys.exit(main())
