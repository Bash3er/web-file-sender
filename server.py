#!/usr/bin/env python3
"""Web File Sender: sender/server launcher."""
import os
import sys
from pathlib import Path

from core.server_app import main

if __name__ == "__main__":
    # Keep relative config/log paths stable when launched from a shortcut.
    if getattr(sys, "frozen", False):
        os.chdir(Path(sys.executable).resolve().parent)
    main()
