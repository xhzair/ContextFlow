"""Runner for ContextFlow."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Command-line restore: python run.py --restore "Workspace Name"
restore_name = None
if "--restore" in sys.argv:
    idx = sys.argv.index("--restore")
    if idx + 1 < len(sys.argv):
        restore_name = " ".join(sys.argv[idx + 1:]).strip('"').strip("'")

from contextflow.main import main

if __name__ == "__main__":
    main(restore_name=restore_name)
