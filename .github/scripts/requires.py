"""Print the installed astromesh-a2a's requirements, one per line, from its metadata.

    python requires.py           # base dependencies
    python requires.py server    # the dependencies the 'server' extra adds

Used by the release smoke so it installs exactly what the published wheel declares.
Standard library only (it runs in a venv that has nothing but the package).
"""

import re
import sys
from importlib.metadata import requires

extra = sys.argv[1] if len(sys.argv) > 1 else None
for req in requires("astromesh-a2a") or []:
    spec, _, marker = req.partition(";")
    names = re.findall(r"""extra\s*==\s*["']([^"']+)["']""", marker)
    if (extra is None and not names) or (extra is not None and names == [extra]):
        rest = re.sub(r"""\s*(and\s+)?extra\s*==\s*["'][^"']+["']""", "", marker).strip()
        print(spec.strip() + (f"; {rest}" if rest else ""))
