import sys
from pathlib import Path

# Make `system_testing` importable as a package from tests
sys.path.insert(0, str(Path(__file__).parent))
