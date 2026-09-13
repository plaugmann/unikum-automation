"""Den planlagte koersel.

Koeres uovervaaget, saa alt skal ende i en log frem for paa en skaerm - ogsaa
det, der gaar galt. Tilstanden skrives desuden til en fil, saa baade
/healthz og e-ink-skaermen kan vise, om systemet stadig har fat i Unikum.
"""
from __future__ import annotations

import json
import traceback
from datetime import datetime, timezone

from . import config

LOG_DIR = config.DATA_DIR / "logs"
LOG_PATH = LOG_DIR / "update.log"
STATE_PATH = config.DATA_DIR / "last_run.json"
MAX_LOG_BYTES = 2_000_000


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(line: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    # Simpel rotation. En log der vokser i aarevis paa en Raspberry Pi med
    # SD-kort er en fejl der venter paa at ske.
    if LOG_PATH.exists() and LOG_PATH.stat().st_size > MAX_LOG_BYTES:
        LOG_PATH.replace(LOG_PATH.with_suffix(".log.1"))
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(f"{_stamp()}  {line}\n")


def _save_state(state: dict) -> None:
    state["tidspunkt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def read_state() -> dict:
    if not STATE_PATH.exists():
        return {"status": "aldrig koert"}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"status": "ulaeselig tilstandsfil"}


def run() -> int:
    """Hent, opsummer, publicer. Returnerer en exitkode.

    0 = alt vel, 2 = sessionen kraever BankID, 1 = noget andet gik galt.
    """
    from . import auth, fetch, summarize

    try:
        fstats = fetch.sync()
        sstats = summarize.run(verbose=False)
    except auth.NeedsLogin as exc:
        log(f"SESSION DOED: {exc}")
        _save_state({"status": "kraever login", "besked": str(exc)})
        return 2
    except Exception as exc:
        log(f"FEJL: {type(exc).__name__}: {exc}")
        log(traceback.format_exc())
        _save_state({"status": "fejl", "besked": f"{type(exc).__name__}: {exc}"})
        return 1

    state = {
        "status": "ok",
        "nye": fstats["nye"],
        "bilag": fstats["bilag"],
        "opsummeret": sstats["opsummeret"],
        "opsummeringsfejl": sstats["fejl"],
    }
    linje = (f"{fstats['nye']} nye, {fstats['bilag']} bilag, "
             f"{sstats['opsummeret']} opsummeret, {sstats['fejl']} fejl")

    # Publicering er adskilt: en fejl i skyen maa ikke se ud som om
    # hentningen fejlede, for de lokale data er opdaterede uanset hvad.
    if config.CLOUD_URL:
        from . import publish

        try:
            sendt = publish.publish()
            state["publiceret"] = len(sendt)
            linje += f", publiceret {len(sendt)}"
        except Exception as exc:
            state["status"] = "publicering fejlede"
            state["besked"] = f"{type(exc).__name__}: {exc}"
            linje += f", PUBLICERING FEJLEDE: {exc}"

    log(linje)
    _save_state(state)
    return 0
