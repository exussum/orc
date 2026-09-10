import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "examples" / "plugin"))

import orc
from orc import model as m

orc.config.load(m.Secrets(), {})
