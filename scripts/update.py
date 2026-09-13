"""Indgang for den planlagte koersel.

Opgaveplanlaeggeren starter processen uden et bestemt arbejdskatalog, saa
"python -m unikum.cli" ville ikke kunne finde pakken. Her laegger vi selv
projektmappen paa importstien, saa scriptet kan startes fra hvor som helst.

Koeres med pythonw.exe, som ikke aabner et konsolvindue. Alt output gaar
derfor i data/logs/update.log.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from unikum import runner  # noqa: E402

if __name__ == "__main__":
    sys.exit(runner.run())
