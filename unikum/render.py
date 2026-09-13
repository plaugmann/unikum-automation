"""Bygger feedets to udgaver ud fra databasen.

Laa foer inde i webserverens endpoints, men skal kunne koeres uden en server,
fordi vi ogsaa pusher resultatet til skyen. Derfor er genereringen skilt ud
her, og baade serve.py og publish.py bruger den samme kode - saa den lokale
og den offentlige udgave aldrig kan komme til at afvige.
"""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import format_datetime
from html import escape
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from . import config, db

CATEGORY_LABEL = {
    "LEKTIE": "Lektie",
    "MØDE": "Møde",
    "UDFLUGT": "Udflugt",
    "SKEMA": "Skema",
    "PRAKTISK": "Praktisk",
    "INFO": "Info",
}

BULLET_PREFIXES = ("- ", "* ", "• ")


def code_version() -> str:
    """Tidsstempel for den kode, der faktisk koerer.

    Uvicorn indlaeser modulerne én gang ved opstart. Redigerer man feedet og
    glemmer at genstarte, serveres den gamle udgave videre - og det ligner en
    fejl i indholdet i stedet for en gammel proces. Stemplet i <generator>
    goer forskellen synlig med det samme.
    """
    mtime = Path(__file__).stat().st_mtime
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")


def _parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


def entry_url(item: dict) -> str:
    """Dybt link tilbage til beskeden i Unikum."""
    return (
        f"{config.BASE}/unikum/infodok/messages.html"
        f"?__pid={config.GUARDIAN_PID}#/message/{item['id']}"
    )


def item_link(item: dict) -> str:
    """Hvor feedets <link> skal pege hen.

    Foer pegede den paa Unikum, men Unikum kraever BankID, saa et klik paa
    telefonen endte paa login-siden. Nu peger den paa vores egen side.

    Én side pr. besked, ikke et anker i en samlet side: RSS-laesere kaster
    tit fragmentet vaek og viser hele dokumentet, og saa faar man alle
    beskeder i stedet for den, man klikkede paa.
    """
    base = (config.CLOUD_URL or config.PUBLIC_BASE_URL).rstrip("/")
    return f"{base}/item/{item['id']}.html?token={config.FEED_TOKEN}"


def text_to_html(text: str) -> str:
    """Modellen skriver ren tekst med tomme linjer og "- " som punkttegn.

    Vi oversaetter til HTML her frem for at bede modellen om markup - saa kan
    den koncentrere sig om indholdet, og vi slipper for at stole paa markup
    fra en generativ model i et feed.

    Et afsnit blander gerne prosa og punkter, fx en indledende linje
    efterfulgt af en liste. Derfor behandles linjerne enkeltvis, og en tom
    linje afslutter det, der er i gang.
    """
    blocks: list[str] = []
    paragraph: list[str] = []
    bullets: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append("<p>" + escape(" ".join(paragraph)) + "</p>")
            paragraph.clear()

    def flush_bullets() -> None:
        if bullets:
            items = "".join(f"<li>{escape(b)}</li>" for b in bullets)
            blocks.append(f"<ul>{items}</ul>")
            bullets.clear()

    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            flush_bullets()
            flush_paragraph()
        elif line.startswith(BULLET_PREFIXES):
            # En liste afslutter den prosa, der stod foran den.
            flush_paragraph()
            bullets.append(line[2:].strip())
        else:
            flush_bullets()
            paragraph.append(line)

    flush_bullets()
    flush_paragraph()
    return "".join(blocks)


def description(item: dict) -> str:
    # Den lange tekst er skrevet til at staa alene, saa den erstatter den
    # korte - ellers ville laeseren se det samme sagt to gange.
    parts = [text_to_html(item.get("detail") or item["summary"])]
    if item.get("action"):
        parts.append(f"<p><strong>Skal gøres:</strong> {escape(item['action'])}</p>")
    if item.get("due_date"):
        parts.append(f"<p><strong>Dato:</strong> {escape(item['due_date'])}</p>")

    meta = []
    if item.get("title") and item["title"].strip() != (item.get("headline") or "").strip():
        meta.append(f"Emne: {item['title']}")
    if item.get("author"):
        meta.append(f"Fra: {item['author']}")
    if item.get("owner"):
        meta.append(f"Gruppe: {item['owner']}")
    names = [a["filename"] for a in item.get("attachments", [])]
    if names:
        meta.append(f"Bilag: {', '.join(names)}")
    if meta:
        parts.append("<p><small>" + " &middot; ".join(meta) + "</small></p>")
    return "".join(parts)


def _newest(items: list) -> datetime:
    """Nyeste besked, ikke "lige nu".

    Bruger vi klokken, aendrer feedet sig ved hver koersel, ETag'en skifter,
    og telefonerne henter alle 33 KB igen - ogsaa naar intet er sket. Med
    nyeste beskeds dato er feedet uaendret, indtil der faktisk er noget nyt.
    """
    datoer = [_parse_dt(i["published"]) for i in items if i.get("published")]
    return max(datoer) if datoer else datetime.now(timezone.utc)


def build_feed(limit: int = 60) -> bytes:
    """RSS 2.0 til en almindelig laeser."""
    items = db.feed_items(limit=limit)

    rss = ET.Element("rss", {"version": "2.0",
                             "xmlns:dc": "http://purl.org/dc/elements/1.1/"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "Unikum – Max"
    ET.SubElement(channel, "link").text = f"{config.BASE}/unikum/start.html"
    ET.SubElement(channel, "description").text = (
        "Beskeder fra Glasbruksskolan og Kryssaren, opsummeret på dansk"
    )
    ET.SubElement(channel, "language").text = "da-DK"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(_newest(items))
    ET.SubElement(channel, "generator").text = f"unikum-automation (kode: {code_version()})"

    for item in items:
        node = ET.SubElement(channel, "item")
        prefix = CATEGORY_LABEL.get(item["category"], item["category"] or "")
        # Laerernes egne emnelinjer er ofte intetsigende ("utv.samtal"), saa
        # vi viser LLM'ens overskrift. Originaltitlen staar i metalinjen.
        headline = item.get("headline") or item["title"]
        ET.SubElement(node, "title").text = f"[{prefix}] {headline}".strip()
        ET.SubElement(node, "link").text = item_link(item)
        # Stabil guid: samme post skal aldrig dukke op som ny i laeseren.
        guid = ET.SubElement(node, "guid", {"isPermaLink": "false"})
        guid.text = f"unikum-{item['id']}"
        ET.SubElement(node, "pubDate").text = format_datetime(_parse_dt(item["published"]))
        if item.get("category"):
            ET.SubElement(node, "category").text = item["category"]
        if item.get("author"):
            ET.SubElement(node, "dc:creator").text = item["author"]
        ET.SubElement(node, "description").text = description(item)

    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


def build_display(count: int = 5, maxlen: int = 120) -> dict[str, Any]:
    """Fladtrykt udgave til e-ink-skaermen.

    ESP32'en skal kunne noejes med ét HTTP-kald og en simpel JSON-parse, saa
    alt er forkortet og formateret paa forhaand her i stedet for paa devicet.
    """
    out = []
    for item in db.feed_items(limit=count):
        line = item.get("action") or item["summary"]
        if len(line) > maxlen:
            line = line[: maxlen - 1].rstrip() + "…"
        out.append({
            "id": item["id"],
            "date": (item["published"] or "")[:10],
            "category": CATEGORY_LABEL.get(item["category"], item["category"] or ""),
            "headline": item.get("headline") or item["title"],
            "line": line,
            "due": item.get("due_date"),
            "action": bool(item.get("action")),
        })

    from . import runner

    state = runner.read_state()
    return {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # Skaermen i bryggerset skal kunne vise, at systemet har mistet
        # sessionen - ellers ser gamle beskeder ud som om alt er i orden.
        "status": state.get("status", "ukendt"),
        "last_run": state.get("tidspunkt"),
        "needs_login": state.get("status") == "kraever login",
        "count": len(out),
        "items": out,
    }


# --- Laesbar HTML-side ----------------------------------------------------
# Feedets <link> pegede foer paa Unikum, men Unikum kraever BankID, saa paa
# en telefon endte man bare paa login-siden. Vi serverer derfor selv siden.
# Alle beskeder samles paa én side med et anker pr. post, saa publicering
# koster én skrivning i stedet for én pr. besked.

PAGE_CSS = """
:root { color-scheme: light dark; --bg:#fff; --fg:#1a1a1a; --muted:#666;
        --line:#e3e3e3; --chip:#eef1f4; --accent:#1a5fb4; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#16181c; --fg:#e6e6e6; --muted:#9aa0a6; --line:#2c2f36;
          --chip:#23262d; --accent:#7aa9f7; }
}
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
       font:16px/1.6 -apple-system, "Segoe UI", Roboto, sans-serif;
       padding:1rem; max-width:44rem; margin-inline:auto; }
h1 { font-size:1.3rem; margin:0 0 .25rem; }
.sub { color:var(--muted); font-size:.85rem; margin-bottom:2rem; }
article { border-top:1px solid var(--line); padding:1.75rem 0; }
article:target { background:var(--chip); margin-inline:-.75rem;
                 padding-inline:.75rem; border-radius:.5rem; }
h2 { font-size:1.1rem; margin:0 0 .5rem; }
.meta { color:var(--muted); font-size:.8rem; margin-bottom:1rem; }
.chip { background:var(--chip); border-radius:1rem; padding:.15rem .6rem;
        font-size:.75rem; margin-right:.5rem; white-space:nowrap; }
.todo { border-left:3px solid var(--accent); padding:.5rem .9rem;
        margin:1rem 0; background:var(--chip); border-radius:0 .3rem .3rem 0; }
ul { padding-left:1.2rem; }
a { color:var(--accent); }
.kilde { font-size:.8rem; color:var(--muted); margin-top:1rem; }
"""


def build_page(limit: int = 60) -> bytes:
    """Oversigt med alle beskeder. Bruges som indeks bag de enkelte sider."""
    items = db.feed_items(limit=limit)
    seneste = _newest(items).strftime("%d-%m-%Y")
    body = [
        "<h1>Unikum – Max</h1>",
        f'<p class="sub">Glasbruksskolan og Kryssaren · seneste besked {seneste}</p>',
    ]
    body.extend(_item_html(item, standalone=False) for item in items)
    return _shell("Unikum – Max", "".join(body))


def _item_html(item: dict, *, standalone: bool) -> str:
    """Én besked som HTML. Bruges baade paa oversigten og paa sin egen side."""
    headline = escape(item.get("headline") or item["title"])
    label = CATEGORY_LABEL.get(item["category"], item["category"] or "")
    meta = [(item["published"] or "")[:10]]
    if item.get("author"):
        meta.append(escape(item["author"]))
    if item.get("owner"):
        meta.append(escape(item["owner"]))

    tag = "h1" if standalone else "h2"
    parts = [
        f'<article id="item-{escape(str(item["id"]))}">',
        f"<{tag}>{headline}</{tag}>",
        # Adskillelsen skal vaere tegn og ikke CSS-margin: mange RSS-laesere
        # fjerner stylesheetet, og saa loeb kategori og dato sammen.
        f'<p class="meta"><span class="chip">{escape(label)}</span> '
        + " · ".join(meta)
        + "</p>",
        text_to_html(item.get("detail") or item["summary"]),
    ]

    if item.get("action"):
        due = f" (senest {escape(item['due_date'])})" if item.get("due_date") else ""
        parts.append(
            f'<p class="todo"><strong>Skal gøres:</strong> '
            f"{escape(item['action'])}{due}</p>"
        )

    kilde = [f'<a href="{escape(entry_url(item))}">Åbn i Unikum</a> (kræver login)']
    if item.get("title"):
        kilde.append(f"Oprindeligt emne: {escape(item['title'])}")
    names = [escape(a["filename"]) for a in item.get("attachments", [])]
    if names:
        kilde.append(f"Bilag: {', '.join(names)}")
    parts.append('<p class="kilde">' + " · ".join(kilde) + "</p>")
    parts.append("</article>")
    return "".join(parts)


def _shell(title: str, body: str) -> bytes:
    return "".join([
        "<!doctype html><html lang=da><meta charset=utf-8>",
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<meta name="robots" content="noindex,nofollow">',
        f"<title>{escape(title)}</title>",
        f"<style>{PAGE_CSS}</style>",
        body,
        "</html>",
    ]).encode("utf-8")


def build_item_page(item: dict) -> bytes:
    """Én besked alene.

    RSS-laesere aabner det link, feedet angiver, og viser hele dokumentet.
    Derfor skal hver besked have sin egen side - ellers ser man alle 17.
    """
    base = (config.CLOUD_URL or config.PUBLIC_BASE_URL).rstrip("/")
    index = f'{base}/items.html?token={config.FEED_TOKEN}'
    body = _item_html(item, standalone=True)
    body += f'<p class="kilde"><a href="{escape(index)}">Alle beskeder</a></p>'
    return _shell(item.get("headline") or item["title"], body)
