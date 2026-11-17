"""``python -m intake`` (and the packaged app): the tray app, or a command."""

import sys

from intake.entry import main

raise SystemExit(main(sys.argv[1:]))
