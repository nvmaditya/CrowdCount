"""Allow ``python -m crowdcount``."""

import sys

from crowdcount.cli import main

sys.exit(main())
