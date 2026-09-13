"""Sessionshaandtering mod Unikum.

Unikum bruger OAuth2 med et access_token der kun lever 15 minutter. Selve
login kraever BankID paa mobil og kan ikke automatiseres.

Strategien er derfor at lade Unikums egen frontend goere arbejdet: vi koerer
en rigtig browser med en *persistent profil*, saa sessionen overlever mellem
koersler. Naar vi skal bruge et token, aabner vi startsiden headless og lader
SPA'en forny sit eget token via sit refresh_token. Vi laeser saa resultatet
ud af localStorage.

Den store fordel er, at vi ikke skal reverse-engineere OAuth-klientens
credentials eller gaette paa refresh-endpointets kontrakt. Holder refresh op
med at virke, lander browseren paa login-siden, og vi beder brugeren om at
scanne en QR-kode med BankID.
"""
from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

from . import config

# Forny i god tid foer udloeb, saa et langt scrape ikke doer midtvejs.
EXPIRY_MARGIN_SECONDS = 120
# Rigeligt til at scanne en QR-kode med mobilen.
LOGIN_TIMEOUT_MS = 5 * 60 * 1000
# Hvor laenge frontenden faar lov at ligge paa login-siden og forsoege at
# forny sig selv, foer vi konkluderer at sessionen er vaek.
LOGIN_PAGE_GRACE_SECONDS = 25


class NeedsLogin(RuntimeError):
    """Sessionen er doed og kan kun genoprettes med BankID."""


@dataclass
class Tokens:
    access_token: str
    refresh_token: str
    expires_at: float
    blob: dict | None = None  # hele localStorage-objektet, som SPA'en skrev det

    @property
    def seconds_left(self) -> float:
        return self.expires_at - time.time()

    @property
    def is_usable(self) -> bool:
        return bool(self.access_token) and self.seconds_left > EXPIRY_MARGIN_SECONDS


def _jwt_expiry(token: str) -> float:
    """Laes 'exp' ud af et JWT uden at validere signaturen.

    Vi stoler ikke paa tokenet her - vi bruger kun udloebstiden til at vide,
    hvornaar vi skal forny. Serveren afviser alligevel et ugyldigt token.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return float(claims.get("exp", 0))
    except Exception:
        return 0.0


def _read_cache() -> Tokens | None:
    if not config.TOKEN_CACHE.exists():
        return None
    try:
        raw = json.loads(config.TOKEN_CACHE.read_text(encoding="utf-8"))
        return Tokens(
            raw["access_token"], raw.get("refresh_token", ""), raw["expires_at"],
            raw.get("blob"),
        )
    except Exception:
        return None


def _write_cache(tokens: Tokens) -> None:
    config.TOKEN_CACHE.write_text(
        json.dumps(
            {
                "access_token": tokens.access_token,
                "refresh_token": tokens.refresh_token,
                "expires_at": tokens.expires_at,
                "blob": tokens.blob,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    # Tokenet er en adgangsnoegle til barnets skoledata - hold filen for os selv.
    try:
        config.TOKEN_CACHE.chmod(0o600)
    except OSError:
        pass  # Windows-filsystemer uden POSIX-rettigheder


def _tokens_from_storage(page) -> Tokens | None:
    """Laes tokens ud af localStorage, hvis siden lige nu tillader det.

    To ting skal passe. For det foerste er localStorage bundet til origin, og
    under BankID-login staar browseren paa funktionstjanster.se, hvor Unikums
    token ikke findes. For det andet river en igangvaerende navigation
    evaluate-kaldet vaek under os. Begge dele er normale tilstande i et
    login-forloeb, ikke fejl - saa vi returnerer bare None og proever igen.
    """
    if not page.url.startswith(config.BASE):
        return None
    try:
        raw = page.evaluate("key => window.localStorage.getItem(key)", config.TOKEN_KEY)
    except Exception:
        return None
    if not raw:
        return None
    try:
        blob = json.loads(raw)
    except json.JSONDecodeError:
        return None
    access = blob.get("access_token")
    if not access:
        return None
    return Tokens(access, blob.get("refresh_token", ""), _jwt_expiry(access), blob)


# Unikums session-cookie er sat uden udloebstid, saa browseren smider den,
# naar den lukker. Vi gemmer den selv og giver den en eksplicit levetid, saa
# serversessionen overlever mellem koersler. Uden den redirecter startsiden
# til login, og SPA'en naar aldrig at forny sit token.
COOKIE_TTL_DAYS = 30
COOKIE_DOMAINS = ("unikum.net", "funktionstjanster.se")


def _save_cookies(ctx) -> int:
    try:
        cookies = ctx.cookies()
    except Exception:
        return 0
    keep = []
    horizon = time.time() + COOKIE_TTL_DAYS * 86400
    for c in cookies:
        if not any(d in c.get("domain", "") for d in COOKIE_DOMAINS):
            continue
        c = dict(c)
        # expires -1 betyder session-cookie. Vi giver den en rigtig dato,
        # ellers findes den ikke naeste gang.
        if c.get("expires", -1) in (-1, None) or c["expires"] < time.time():
            c["expires"] = horizon
        keep.append(c)
    if keep:
        config.COOKIE_CACHE.write_text(json.dumps(keep, indent=2), encoding="utf-8")
        try:
            config.COOKIE_CACHE.chmod(0o600)
        except OSError:
            pass
    return len(keep)


def _load_cookies(ctx) -> int:
    if not config.COOKIE_CACHE.exists():
        return 0
    try:
        cookies = json.loads(config.COOKIE_CACHE.read_text(encoding="utf-8"))
        ctx.add_cookies(cookies)
        return len(cookies)
    except Exception:
        return 0


def _seed_refresh_token(page) -> bool:
    """Giv SPA'en vores gemte refresh_token tilbage, foer den starter.

    Unikums session-cookie forsvinder, naar browseren lukker, saa en frisk
    proces lander paa login-siden og SPA'en rydder sit tomme token. Men
    OAuth-fornyelse bygger paa refresh_token, ikke paa cookien - saa hvis vi
    laegger vores gemte token tilbage i localStorage, inden startsiden loader,
    fornyer frontenden selv sessionen. Dermed slipper vi for at kende
    OAuth-klientens credentials.
    """
    cached = _read_cache()
    if not cached or not cached.blob or not cached.refresh_token:
        return False
    try:
        # localStorage er bundet til origin, saa vi skal staa paa Unikum
        # foerst. login.jsp er den letteste side der altid svarer.
        page.goto(f"{config.BASE}/unikum/login.jsp", wait_until="domcontentloaded", timeout=60_000)
        page.evaluate(
            "([key, value]) => window.localStorage.setItem(key, value)",
            [config.TOKEN_KEY, json.dumps(cached.blob)],
        )
        return True
    except Exception:
        return False


def _harvest(headless: bool, timeout_ms: int, start_url: str | None = None) -> Tokens:
    """Aabn Unikum i den persistente profil og hent et gyldigt token.

    Headless: SPA'en fornyer selv sit token, hvis refresh_token stadig duer.
    Headful: brugeren kan logge ind med BankID undervejs.
    """
    config.PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        indstillinger = {
            "user_data_dir": str(config.PROFILE_DIR),
            "headless": headless,
            "viewport": {"width": 1280, "height": 900},
            "locale": "sv-SE",
        }
        if config.CHROMIUM_PATH:
            # Paa ARM64 Linux findes Playwrights egen Chromium ikke.
            indstillinger["executable_path"] = config.CHROMIUM_PATH
        ctx = pw.chromium.launch_persistent_context(**indstillinger)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()

            if headless:
                _load_cookies(ctx)
                _seed_refresh_token(page)

            page.goto(start_url or config.START_URL, wait_until="domcontentloaded", timeout=60_000)

            started = time.time()
            deadline = started + timeout_ms / 1000
            while time.time() < deadline:
                if page.is_closed():
                    raise NeedsLogin("Browservinduet blev lukket, foer vi fik en session.")
                tokens = _tokens_from_storage(page)
                if tokens and tokens.is_usable:
                    # Startsiden skriver tokenet tidligt. Et kort ophold giver
                    # SPA'en ro til at skrive faerdig, foer vi lukker vinduet.
                    page.wait_for_timeout(1500)
                    tokens = _tokens_from_storage(page) or tokens
                    _write_cache(tokens)
                    _save_cookies(ctx)
                    return tokens
                # Havner vi paa login og bliver liggende, er sessionen doed.
                # Vi giver frontenden en frist foerst, for den kan naa at
                # forny sig og sende os videre ind i appen.
                if headless and "login" in page.url.lower():
                    if time.time() - started > LOGIN_PAGE_GRACE_SECONDS:
                        raise NeedsLogin("Unikum blev paa login-siden - sessionen er doed.")
                try:
                    page.wait_for_timeout(1000)
                except Exception:
                    time.sleep(1)

            raise NeedsLogin("Fik aldrig et brugbart token inden for tidsfristen.")
        finally:
            ctx.close()


def get_access_token(*, allow_login: bool = False) -> str:
    """Returner et gyldigt access_token, og forny eller log ind efter behov."""
    cached = _read_cache()
    if cached and cached.is_usable:
        return cached.access_token

    try:
        return _harvest(headless=True, timeout_ms=45_000).access_token
    except (NeedsLogin, PWTimeout) as exc:
        if not allow_login:
            raise NeedsLogin(
                "Sessionen er udloebet. Koer 'python -m unikum.cli login' og scan "
                "QR-koden med BankID."
            ) from exc

    return login().access_token


def login() -> Tokens:
    """Tving et interaktivt BankID-login og gem sessionen i profilen."""
    print("Aabner Unikum i et browservindue.")
    print("  1. Vaelg 'Malmoe stad foer vaardnadshavare' paa listen")
    print("  2. Klik 'BankID other device'")
    print("  3. Scan QR-koden med BankID paa mobilen")
    print("Vinduet lukker af sig selv, naar sessionen er i hus.")
    tokens = _harvest(headless=False, timeout_ms=LOGIN_TIMEOUT_MS, start_url=config.LOGIN_URL)
    print(f"Logget ind. Access token udloeber om {tokens.seconds_left / 60:.1f} minutter.")
    return tokens


def status() -> dict:
    """Et hurtigt overblik over sessionens tilstand - til fejlsoegning."""
    cached = _read_cache()
    return {
        "har_profil": config.PROFILE_DIR.exists(),
        "har_cached_token": cached is not None,
        "minutter_tilbage": round(cached.seconds_left / 60, 1) if cached else None,
        "brugbart": cached.is_usable if cached else False,
    }
