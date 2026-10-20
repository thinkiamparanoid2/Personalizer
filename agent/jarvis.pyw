"""Windowless launcher used by the Explorer context menu and Send To shortcuts."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.cli import main  # noqa: E402

sys.exit(main(sys.argv[1:]))
