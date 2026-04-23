import sys
from pathlib import Path

# Ensure the project root is importable so `from src.x import ...`
# and `from pickles_transducer import ...` both work.
sys.path.insert(0, str(Path(__file__).parent.parent))
