"""NetSim MVP launcher.

    python main.py

PyQt6 is imported here and nowhere below the GUI package, which is what makes
the "engine does not depend on Qt" rule easy to check.
"""

from __future__ import annotations

import sys


def main() -> int:
    try:
        from netsim.gui.main_window import run
    except ImportError as exc:
        print("NetSim could not start: %s" % exc)
        print()
        print("Install the dependencies first:")
        print("    python -m pip install -r requirements.txt")
        return 1
    return run()


if __name__ == "__main__":
    sys.exit(main())
