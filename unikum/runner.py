"""Den planlagte koersel.

Koeres uovervaaget, saa alt skal ende i en log frem for paa en skaerm - ogsaa
det, der gaar galt. Tilstanden skrives desuden til en fil, saa baade
/healthz og e-ink-skaermen kan vise, om systemet stadig har fat i Unikum.
"""
from __future__ import annotations

import json
import socket
import time
import traceback
from datetime import datetime, timezone

import httpx

from . import config

LOG_DIR = config.DATA_DIR / "logs"
LOG_PATH = LOG_DIR / "update.log"
STATE_PATH = config.DATA_DIR / "last_run.json"
MAX_LOG_BYTES = 2_000_000

# Efter en genstart er DNS tit ikke oppe endnu, naar timeren fyrer, og et
# Wi-Fi-hikke ser ud paa samme maade. Det er ikke en fejl vaerd at give op
# paa - vi venter bare lidt og proever igen.
NETVAERKSFEJL = (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
                 httpx.RemoteProtocolError, socket.gaierror, OSError)
PAUSER = (15, 45, 120)


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


def _publish(state: dict) -> None:
    """Laeg det faerdige feed op. Tilstanden skal vaere gemt foerst.

    display.json henter sin status fra tilstandsfilen, saa publicerer vi
    inden vi gemmer, baerer skaermen altid den forrige koersels status.
    """
    if not config.CLOUD_URL:
        return
    from . import publish

    try:
        sendt = publish.publish()
        state["publiceret"] = len(sendt)
    except Exception as exc:
        # En fejl i skyen maa ikke se ud som om hentningen fejlede - de
        # lokale data er opdaterede uanset hvad.
        state["publicering"] = f"fejlede: {type(exc).__name__}: {exc}"
        log(f"PUBLICERING FEJLEDE: {type(exc).__name__}: {exc}")
    _save_state(state)


def _er_netvaerksfejl(exc: BaseException) -> bool:
    """Skeln mellem "nettet er ikke klar" og en rigtig fejl.

    OSError er bred, saa vi ser paa hele kaeden af aarsager: en DNS-fejl
    dukker op som gaierror dybt nede under httpx' egne undtagelser.
    """
    set_ = set()
    while exc is not None and id(exc) not in set_:
        set_.add(id(exc))
        if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout,
                            httpx.ReadTimeout, httpx.RemoteProtocolError,
                            socket.gaierror)):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def run() -> int:
    """Hent, opsummer, publicer. Returnerer en exitkode.

    0 = alt vel, 2 = sessionen kraever BankID, 1 = noget andet gik galt.
    """
    from . import auth, fetch, summarize

    try:
        # Baade hentning og opsummering er idempotente, saa et helt
        # genforsoeg er ufarligt - det springer selv over, hvad der allerede
        # er hentet og opsummeret.
        for forsoeg, pause in enumerate((*PAUSER, None), start=1):
            try:
                fstats = fetch.sync()
                sstats = summarize.run(verbose=False)
                break
            except Exception as exc:
                if pause is None or not _er_netvaerksfejl(exc):
                    raise
                log(f"netvaerket er ikke klar ({exc}) - forsoeg {forsoeg}, "
                    f"venter {pause}s")
                time.sleep(pause)
    except auth.NeedsLogin as exc:
        log(f"SESSION DOED: {exc}")
        state = {"status": "kraever login", "besked": str(exc)}
        _save_state(state)
        # Publicer alligevel, saa skaermen og feedet kan sige til. Det
        # kraever ingen Unikum-session - vi laeser kun vores egen database.
        _publish(state)
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
    # Gem foer vi publicerer, saa display.json baerer denne koersels status
    # og ikke den forrige.
    _save_state(state)
    _publish(state)

    log(f"{fstats['nye']} nye, {fstats['bilag']} bilag, "
        f"{sstats['opsummeret']} opsummeret, {sstats['fejl']} fejl"
        + (f", publiceret {state['publiceret']}" if "publiceret" in state else ""))
    return 0
