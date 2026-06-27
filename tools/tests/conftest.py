"""Pytest config for `Docs/tools/` packages.

Makes the sibling `nocturnation_orchestrator` and `nocturnation_dmx`
packages importable when pytest is invoked from `Docs/tools/`.
"""

import sys
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
