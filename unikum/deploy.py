"""Udruller workeren til Cloudflare via REST API'et - uden wrangler.

Wrangler traekker over 100 MB binaerer med for at udrulle 95 linjers kode,
og det faldt gentagne gange over en langsom forbindelse. REST-API'et goer
det samme med nogle faa HTTP-kald, og vi har httpx i forvejen. Det virker
ogsaa uaendret paa en Raspberry Pi, hvor nodes binaerer er en ekstra
forhindring.

Kraever en API-token med rettighederne:
  - Workers Scripts: Edit
  - Workers KV Storage: Edit
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

from . import config

API = "https://api.cloudflare.com/client/v4"
WORKER_DIR = Path(__file__).resolve().parent.parent / "worker"
SCRIPT_PATH = WORKER_DIR / "src" / "index.js"
WRANGLER_PATH = WORKER_DIR / "wrangler.jsonc"
MODULE_NAME = "index.js"


class DeployError(RuntimeError):
    pass


def _settings() -> dict:
    """Laes navn og kompatibilitetsdato fra wrangler.jsonc.

    Filen bliver liggende som dokumentation af opsaetningen, ogsaa selv om
    vi ikke bruger wrangler til at udrulle. Saa er der ét sted at rette.
    """
    raw = WRANGLER_PATH.read_text(encoding="utf-8")
    # JSONC tillader kommentarer; json goer ikke.
    stripped = re.sub(r"^\s*//.*$", "", raw, flags=re.MULTILINE)
    data = json.loads(stripped)
    return {
        "name": data["name"],
        "compatibility_date": data["compatibility_date"],
        "kv_binding": data["kv_namespaces"][0]["binding"],
    }


ACCOUNT_ID_PATTERN = re.compile(r"[0-9a-f]{32}")


def resolve_account_id(token: str) -> str:
    """Slaa konto-ID'et op, hvis det ikke staar korrekt i .env.

    Cloudflare viser baade en konto-mail og et konto-ID, og de er nemme at
    forveksle. ID'et er 32 hex-tegn. Ser vaerdien forkert ud, spoerger vi
    bare API'et i stedet for at fejle paa noget, vi selv kan finde ud af.
    """
    if ACCOUNT_ID_PATTERN.fullmatch(config.CF_ACCOUNT_ID):
        return config.CF_ACCOUNT_ID

    res = httpx.get(
        f"{API}/accounts", headers={"Authorization": f"Bearer {token}"}, timeout=60.0
    )
    accounts = _result(res, "kunne ikke hente kontoen") or []
    if not accounts:
        raise DeployError("Tokenen har ikke adgang til nogen konto.")
    if len(accounts) > 1:
        navne = ", ".join(f"{a['name']} ({a['id']})" for a in accounts)
        raise DeployError(
            f"Tokenen har adgang til flere konti - saet CF_ACCOUNT_ID i .env: {navne}"
        )
    return accounts[0]["id"]


def _client() -> httpx.Client:
    if not config.CF_API_TOKEN:
        raise DeployError(
            "CF_API_TOKEN mangler i .env. Opret en token med "
            "Workers Scripts: Edit og Workers KV Storage: Edit."
        )
    account_id = resolve_account_id(config.CF_API_TOKEN)
    return httpx.Client(
        base_url=f"{API}/accounts/{account_id}",
        headers={"Authorization": f"Bearer {config.CF_API_TOKEN}"},
        timeout=120.0,
    )


def _result(res: httpx.Response, hvad: str):
    if res.status_code >= 400:
        try:
            fejl = "; ".join(e.get("message", "?") for e in res.json().get("errors", []))
        except Exception:
            fejl = res.text[:300]
        raise DeployError(f"{hvad}: HTTP {res.status_code} - {fejl}")
    body = res.json()
    if not body.get("success", True):
        fejl = "; ".join(e.get("message", "?") for e in body.get("errors", []))
        raise DeployError(f"{hvad}: {fejl}")
    return body.get("result")


def ensure_namespace(client: httpx.Client, title: str) -> tuple[str, bool]:
    """Find lageret, eller opret det. Returnerer (id, blev_oprettet)."""
    found = _result(
        client.get("/storage/kv/namespaces", params={"per_page": 100}),
        "kunne ikke liste KV-lagre",
    )
    for ns in found or []:
        if ns.get("title") == title:
            return ns["id"], False

    created = _result(
        client.post("/storage/kv/namespaces", json={"title": title}),
        "kunne ikke oprette KV-lager",
    )
    return created["id"], True


def upload_script(client: httpx.Client, settings: dict, namespace_id: str) -> None:
    """Laeg koden op med bindinger og hemmeligheder.

    Bindinger erstattes helt ved hver upload, saa hemmelighederne skal med
    hver gang. De laeses fra .env, som i forvejen er sandheden.
    """
    if not config.FEED_TOKEN:
        raise DeployError("FEED_TOKEN mangler i .env")
    if not config.CLOUD_PUSH_TOKEN:
        raise DeployError("CLOUD_PUSH_TOKEN mangler i .env")

    metadata = {
        "main_module": MODULE_NAME,
        "compatibility_date": settings["compatibility_date"],
        "bindings": [
            {"type": "kv_namespace", "name": settings["kv_binding"], "namespace_id": namespace_id},
            {"type": "secret_text", "name": "FEED_TOKEN", "text": config.FEED_TOKEN},
            {"type": "secret_text", "name": "PUSH_TOKEN", "text": config.CLOUD_PUSH_TOKEN},
        ],
    }

    res = client.put(
        f"/workers/scripts/{settings['name']}",
        files={
            "metadata": (None, json.dumps(metadata), "application/json"),
            MODULE_NAME: (
                MODULE_NAME,
                SCRIPT_PATH.read_bytes(),
                "application/javascript+module",
            ),
        },
    )
    _result(res, "kunne ikke laegge workeren op")


def enable_subdomain(client: httpx.Client, name: str) -> str:
    """Slaa workers.dev-adressen til og returner den faerdige URL."""
    _result(
        client.post(f"/workers/scripts/{name}/subdomain", json={"enabled": True}),
        "kunne ikke slaa workers.dev til",
    )
    sub = _result(client.get("/workers/subdomain"), "kunne ikke hente kontoens subdomaene")
    return f"https://{name}.{sub['subdomain']}.workers.dev"


def deploy() -> dict:
    settings = _settings()
    title = f"{settings['name']}-kv"
    steps = []

    with _client() as client:
        namespace_id, created = ensure_namespace(client, title)
        steps.append(f"KV-lager {title}: {'oprettet' if created else 'fandtes'} ({namespace_id})")

        upload_script(client, settings, namespace_id)
        steps.append(f"worker {settings['name']}: lagt op ({SCRIPT_PATH.stat().st_size} bytes)")

        url = enable_subdomain(client, settings["name"])
        steps.append(f"adresse: {url}")

    return {"url": url, "namespace_id": namespace_id, "steps": steps}
