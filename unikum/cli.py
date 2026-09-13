"""Kommandolinje: python -m unikum.cli <kommando>"""
from __future__ import annotations

import argparse
import json
import sys

from . import auth, db


def cmd_login(_args: argparse.Namespace) -> int:
    auth.login()
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    print(json.dumps(auth.status(), indent=2, ensure_ascii=False))
    return 0


def cmd_check(_args: argparse.Namespace) -> int:
    """Bevis at sessionen virker: forny token og hent beskedlisten."""
    from . import api, config

    token = auth.get_access_token(allow_login=False)
    print(f"Token hentet ({len(token)} tegn).")
    with api.Client() as client:
        entries = list(client.list_entries())
    print(f"{len(entries)} poster fra og med {config.SINCE}:\n")
    for e in entries:
        a = e.get("author") or {}
        who = f"{a.get('firstName', '')} {a.get('lastName', '')}".strip() or "-"
        print(f"  {e['published'][:10]}  {e['informationType']:<14}  {e['title'][:45]:<45}  {who}")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    from . import fetch

    stats = fetch.sync(since=args.since, refetch=args.refetch)
    print(f"Set {stats['set']} poster, hentede {stats['nye']} nye, {stats['bilag']} bilag.")
    return 0


def cmd_summarize(args: argparse.Namespace) -> int:
    from . import summarize

    stats = summarize.run(limit=args.limit)
    print(f"Opsummerede {stats['opsummeret']}, fejl: {stats['fejl']}.")
    return 0


def cmd_update(_args: argparse.Namespace) -> int:
    """Hele kaeden. Det er den, der koerer paa timer."""
    from . import runner

    code = runner.run()
    print(runner.read_state())
    return code


def cmd_deploy(_args: argparse.Namespace) -> int:
    from . import deploy

    result = deploy.deploy()
    for step in result["steps"]:
        print(f"  {step}")
    print()
    print("Saet denne linje i .env:")
    print(f"  CLOUD_URL={result['url']}")
    return 0


def cmd_publish(_args: argparse.Namespace) -> int:
    from . import config, publish

    if not config.CLOUD_URL:
        print("CLOUD_URL er ikke sat i .env - der publiceres ikke.")
        print("Se worker/README.md for opsaetning.")
        return 1
    for sent in publish.publish():
        print(f"  sendte {sent['navn']}: {sent['bytes']} bytes")
    check = publish.verify()
    print(f"Hentet tilbage: HTTP {check['status']}, {check['bytes']} bytes, "
          f"ETag {check['etag']}")
    print("Afviser forkert token:", "ja" if check["afviser_forkert_token"] else "NEJ - undersoeg!")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from . import config

    print(f"Feed:   {config.PUBLIC_BASE_URL}/feed.xml?token=...")
    print(f"Skaerm: {config.PUBLIC_BASE_URL}/display.json?token=...")
    if args.reload:
        print("Genindlaeser automatisk ved kodeaendringer.")
    uvicorn.run(
        "unikum.serve:app",
        host=args.host,
        port=args.port,
        log_level="info",
        reload=args.reload,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="unikum")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("login", help="Log ind med BankID og gem sessionen").set_defaults(fn=cmd_login)
    sub.add_parser("status", help="Vis sessionens tilstand").set_defaults(fn=cmd_status)
    sub.add_parser("check", help="Test at sessionen kan hente data").set_defaults(fn=cmd_check)

    p_sync = sub.add_parser("sync", help="Hent beskeder og bilag til databasen")
    p_sync.add_argument("--since", help="ISO-dato, fx 2026-08-01")
    p_sync.add_argument("--refetch", action="store_true", help="Hent alt igen")
    p_sync.set_defaults(fn=cmd_sync)

    p_sum = sub.add_parser("summarize", help="Opsummer poster der mangler resume")
    p_sum.add_argument("--limit", type=int, help="Kun de foerste N")
    p_sum.set_defaults(fn=cmd_summarize)

    sub.add_parser("update", help="Hent nyt og opsummer i ét hug").set_defaults(fn=cmd_update)

    sub.add_parser("deploy", help="Udrul workeren til Cloudflare").set_defaults(fn=cmd_deploy)

    sub.add_parser("publish", help="Push feedet til skyen").set_defaults(fn=cmd_publish)

    p_serve = sub.add_parser("serve", help="Start webserveren med feed og skaermdata")
    p_serve.add_argument("--host", default=None)
    p_serve.add_argument("--port", type=int, default=None)
    p_serve.add_argument("--reload", action="store_true",
                         help="Genindlaes koden ved aendringer (til udvikling)")
    p_serve.set_defaults(fn=cmd_serve)

    args = parser.parse_args(argv)
    if getattr(args, "host", None) is None and hasattr(args, "host"):
        from . import config
        args.host = config.HOST
    if getattr(args, "port", None) is None and hasattr(args, "port"):
        from . import config
        args.port = config.PORT
    db.init()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
