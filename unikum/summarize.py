"""Opsummering af skolebeskeder med OpenAI.

Output holdes paa svensk, fordi kilden er svensk og familien laeser svensk
fint - en oversaettelse ville kun tilfoeje stoej og fejlkilder.

Vi beder om struktureret JSON frem for fritekst, fordi feedet skal kunne
sortere og filtrere paa kategori og deadline, og e-ink-skaermen skal have en
kort overskrift der passer i bredden.
"""
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI

from . import config, db

CATEGORIES = ["LEKTIE", "MØDE", "UDFLUGT", "SKEMA", "PRAKTISK", "INFO"]

SYSTEM_PROMPT = """Du sammenfatter beskeder fra en svensk folkeskole til danske forældre.

Kilden er på svensk. Skriv sammenfatningen på DANSK i et naturligt, korrekt
dansk med æ, ø og å.

Behold dog følgende på svensk, uoversat:
- Navne på personer, skoler, klasser og grupper: Glasbruksskolan, Kryssaren,
  4C, Jeanette Lilja.
- Navne på systemer og tjenester: Unikum, Skola24, Classroom, Teams.
- Skolens egne fagudtryk, fordi forældrene møder dem igen i skolens egen
  kommunikation: utvecklingssamtal, fritids, höstlov, studiedag, stängdag,
  förskoleklass, mellanstadiet, mentor, matsäck.
Oversæt altså den forbindende tekst, ikke skolens begreber.

Begynd aldrig en sætning med et svensk begreb skrevet med lille bogstav.
Omskriv hellere, så sætningen starter med et dansk ord - fx "Derefter tager
fritids over" frem for "fritids tager over".

Almindelige tidsudtryk er IKKE skolebegreber og skal oversættes: "vecka 39"
bliver til "uge 39", "måndag" til "mandag", "onsdag den 16 september" til
"onsdag den 16. september". Brug dansk datoformat i den løbende tekst.

Vær konkret og kortfattet - forælderen skal på få sekunder kunne se, om
beskeden kræver noget af dem.

Du skriver TO tekster af forskellig længde:

"summary" er den korte. Den vises i oversigten og på en lille skærm.
- To eller tre sætninger. Aldrig mere.
- Den skal altid være KORTERE end kildeteksten. Er kilden i forvejen kort,
  er én sætning nok - skriv aldrig mere end det, der står. At puste en kort
  besked op er en fejl.

"detail" er den lange. Den vises, når forælderen klikker beskeden op, og skal
kunne læses alene, uden at man først har set "summary".
- Tag ALT med, som en forælder kan få brug for: hver dato, hvert klokkeslæt,
  hvert sted, beløb, navn, frist og undtagelse. Det er her, detaljerne hører
  hjemme.
- Skriv sammenhængende afsnit i almindelig prosa, adskilt af en tom linje.
  Ét afsnit pr. emne.
- Brug KUN punktopstilling til egentlige lister: flere datoer, flere
  klokkeslæt, ting der skal medbringes, trin der skal følges. Sæt "- " forrest
  på hver linje. Lav aldrig en punktopstilling af sætninger, der hører
  naturligt sammen i et afsnit - det gør teksten sværere at læse, ikke
  lettere. Et afsnit med ét enkelt punkt skal altid være prosa.
- Du må gerne gentage det, "summary" siger - "detail" er den fulde udgave,
  ikke en fortsættelse.
- Længden skal følge kilden. Et langt brev eller en PDF på flere sider giver
  en fyldig tekst; en besked på to linjer giver ingenting. Er der ikke mere
  at sige end det, "summary" allerede siger, så sæt "detail" til null.
- Find aldrig på noget. Står det ikke i kilden, kommer det ikke med.

Regler:
- Tag alle datoer, klokkeslæt, steder og beløb med. Find aldrig på detaljer,
  der ikke står i teksten.
- Kræves der noget af forælderen (tilmelde, sende med, betale, svare,
  deltage), så skriv det i feltet "action". Ellers null.
- "action" må ALDRIG gentage det, sammenfatningen allerede siger. Den skal
  være en kort opfordring på én linje - udsagnsord først, ingen gentagne
  detaljer. Siger sammenfatningen "Tilmeld i Skola24 senest 18/9", skal
  "action" være null, fordi opfordringen allerede fremgår.
- "due_date" er sidste svarfrist eller den dag, noget sker, som ISO-dato
  (YYYY-MM-DD). Regn relative datoer ud fra den publiceringsdato, du får.
  Et informationsbrev, der nævner mange forskellige datoer, skal have null -
  ellers bliver det misvisende i kalenderen.
- "headline" er på dansk, højst 60 tegn, og vises på en lille skærm. Den skal
  kunne forstås helt uden sammenfatningen.
- Ligger hele indholdet i et vedhæftet billede, så læs billedet og behandl
  teksten i det som en del af beskeden.

Kategorier - vælg altid den mest specifikke, der passer:
- LEKTIE: lektier, gloser, prøver, læsning og andet, eleven skal lave hjemme.
- MØDE: utvecklingssamtal, forældremøder, foredrag, temaaftener.
- UDFLUGT: udflugter, rejser, friluftsdage, besøg uden for skolen.
- SKEMA: kun når beskeden i det væsentlige handler om tid - ferie,
  studiedage, lukkedage, ændrede tider, aflyst undervisning, forsinkelser.
  Et bredt brev, der blandt meget andet også nævner nogle datoer, er INFO.
- PRAKTISK: det forælderen skal ordne eller overholde - tilmeldinger, udstyr,
  tøj, betalinger, regler og politikker.
- INFO: bred eller blandet information, fx månadsbrev og breve fra rektor,
  der dækker flere emner på én gang. Brug også INFO, når ingen anden
  kategori passer.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "detail": {"type": ["string", "null"]},
        "category": {"type": "string", "enum": CATEGORIES},
        "action": {"type": ["string", "null"]},
        "due_date": {"type": ["string", "null"]},
        "headline": {"type": "string"},
    },
    "required": ["summary", "detail", "category", "action", "due_date", "headline"],
    "additionalProperties": False,
}

# Aendres prompten, skal gamle resumeer laves om. Hashen fanger det.
PROMPT_HASH = hashlib.sha256(
    (SYSTEM_PROMPT + json.dumps(SCHEMA, sort_keys=True) + config.OPENAI_MODEL).encode()
).hexdigest()[:16]

MAX_INPUT_CHARS = 60_000


def _client() -> OpenAI:
    if not config.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY mangler i .env")
    return OpenAI(api_key=config.OPENAI_API_KEY)


def build_input(entry: dict) -> str:
    """Saml beskedens tekst og alle bilagstekster til ét input."""
    parts = [
        f"Titel: {entry['title']}",
        f"Publicerat: {(entry.get('published') or '')[:10]}",
        f"Avsändare: {entry.get('author') or 'okänd'}",
        f"Avsett för: {entry.get('owner') or 'okänt'}",
    ]
    contexts = json.loads(entry.get("contexts") or "[]")
    if contexts:
        parts.append(f"Grupper: {', '.join(contexts)}")

    parts.append("\n--- Meddelandetext ---")
    parts.append(entry.get("body_text") or "(tom)")

    for att in db.attachment_text(entry["id"]):
        parts.append(f"\n--- Bilaga: {att['filename']} ---")
        parts.append(att["text"])

    return "\n".join(parts)[:MAX_INPUT_CHARS]


def build_user_message(entry: dict) -> list[dict]:
    """Tekst plus eventuelle billedbilag.

    Nogle laerere laegger hele beskeden op som et skaermbillede. Vi sender
    derfor billederne med, saa indholdet ikke gaar tabt.
    """
    content: list[dict] = [{"type": "text", "text": build_input(entry)}]
    for img in db.attachment_images(entry["id"]):
        path = Path(img["local_path"])
        if not path.exists():
            continue
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        data = base64.b64encode(path.read_bytes()).decode()
        marker = f"\n--- Bilaga (bild): {img['filename']} ---"
        content.append({"type": "text", "text": marker})
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{data}"},
        })
    return content


def summarize_entry(entry: dict, client: OpenAI | None = None) -> dict:
    client = client or _client()
    res = client.chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_message(entry)},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "sammanfattning", "schema": SCHEMA, "strict": True},
        },
    )
    data = json.loads(res.choices[0].message.content)
    data["model"] = config.OPENAI_MODEL
    data["prompt_hash"] = PROMPT_HASH
    data["created_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return data


def run(limit: int | None = None, verbose: bool = True) -> dict[str, int]:
    """Opsummer alt der mangler - eller hvis prompten er aendret, alt igen."""
    pending = db.entries_needing_summary(PROMPT_HASH)
    if limit:
        pending = pending[:limit]

    stats = {"opsummeret": 0, "fejl": 0}
    if not pending:
        return stats

    client = _client()
    for row in pending:
        entry = dict(row)
        try:
            data = summarize_entry(entry, client)
            db.save_summary(entry["id"], data)
            stats["opsummeret"] += 1
            if verbose:
                print(f"  [{data['category']:<10}] {entry['title'][:50]}")
        except Exception as exc:
            stats["fejl"] += 1
            if verbose:
                print(f"  FEJL ved {entry['title'][:40]}: {type(exc).__name__}: {exc}")
    return stats
