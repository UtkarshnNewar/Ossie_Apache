"""Thin wrapper: the implementation lives in ossie_hub/tmdl.py (importable library code).

    python tools/tmdl_to_ossie.py <tmdl file> <out.yaml> --name sales [--infer-relationships]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ossie_hub.tmdl import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
