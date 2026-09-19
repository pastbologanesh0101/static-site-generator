import sys
from pathlib import Path

# Make the project root (where build.py lives) importable as `build`,
# regardless of the directory tests are invoked from.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
