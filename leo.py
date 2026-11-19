"""leo-sys command line: Claude Code's live status and more on a LEOBOG AMG65 keyboard (matrix and key lights).

Run `python leo.py` for the list of commands. The code lives in the leosys package (leosys/cli.py), which is also
what the installed leo-sys.exe runs.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # find leosys however Python is started

if __name__ == "__main__":
    from leosys.cli import main
    sys.exit(main(sys.argv[1:]))
