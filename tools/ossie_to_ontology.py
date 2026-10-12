"""Thin wrapper: the implementation lives in ossie_hub/ontology.py.

    python tools/ossie_to_ontology.py <ossie.yaml> <ontology.yaml>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ossie_hub.ontology import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
