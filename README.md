# unikum-automation

Samler skolebeskeder fra [unikum.net](https://start.unikum.net) ét sted,
opsummerer dem med en LLM og udstiller dem som et RSS-feed.

Baggrunden er, at lærerne på Glasbruksskolan bruger vidt forskellige kanaler:
nogle skriver direkte i beskeden, andre vedhæfter en PDF, andre igen lægger et
skærmbillede op. Beskederne blander lektier, forældremøder, ekskursioner,
lukkedage og praktisk information. Det er svært at holde overblik.

## Sådan virker det

```
Unikum GraphQL  ─►  fetch  ─►  SQLite  ─►  summarize (gpt-5.4)  ─►  RSS + JSON
                      │                          ▲
                      └── bilag: PDF/Word/txt ────┘
                          billeder sendes til modellen som billeder
```

- **Ingen HTML-scraping.** Unikum har et rigtigt GraphQL-API, der accepterer
  vores egne queries. Se [docs/unikum-api.md](docs/unikum-api.md).
- **Idempotent.** `sync` henter kun det, den ikke allerede har, så polling er
  billigt og genkørsler ufarlige.
- **Dansk ud, svensk ind.** Resuméerne skrives på dansk, men skolens egne
  begreber og navne bevares på svensk — `utvecklingssamtal`, `fritids`,
  `höstlov`, `Skola24` — så de stadig matcher det, skolen selv skriver.
- **Rå data og resuméer holdes adskilt**, så prompten kan ændres og alt køres
  om uden at hente fra Unikum igen. En hash af prompten styrer det automatisk.

## Kom i gang

```bash
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
cp .env.example .env      # udfyld OPENAI_API_KEY og FEED_TOKEN
```

## Kommandoer

| Kommando | Hvad den gør |
|---|---|
| `login` | Åbner en browser til BankID. Kræver din mobil. |
| `status` | Viser sessionens tilstand. |
| `check` | Tester at sessionen kan hente data. |
| `sync` | Henter beskeder og bilag til databasen. |
| `summarize` | Opsummerer poster uden resumé. |
| `update` | `sync` + `summarize`. Den, der skal køre på timer. |
| `publish` | Pusher feedet til skyen og henter det tilbage som kontrol. |
| `serve` | Starter webserveren. |

```bash
.\.venv\Scripts\python.exe -m unikum.cli update
.\.venv\Scripts\python.exe -m unikum.cli serve
```

Serveren indlæser `serve.py` én gang ved opstart. Ændrer du feedets udseende,
skal den genstartes — eller startes med `serve --reload` under udvikling.
Resuméerne læses derimod fra databasen ved hvert kald og er altid friske.

## Lokalt eller i skyen

Hentningen kan ikke flytte i skyen — BankID-sessionen lever i en browser på
maskinen derhjemme. Men det færdige feed er en statisk fil på ca. 30 KB, og
den kan sagtens ligge ude, så telefonerne kan nå den hjemmefra.

```
        HJEMME                         SKYEN
  Unikum ─► sync ─► SQLite                  ┌─ telefon
              └─► summarize ─┐              │
                             ├─ render ─► Worker + KV ─┤
        lokal server ────────┘   (push)                └─ ESP32
```

Vi **pusher** frem for at åbne hul ind til hjemmenettet. Skyen kender
hverken BankID-sessionen, Unikum-tokens eller OpenAI-nøglen — kun det
færdige feed. Går workeren ned, virker den lokale server uændret.

Sæt `CLOUD_URL` og `CLOUD_PUSH_TOKEN` i `.env`, så publicerer `update`
automatisk. Lad dem stå tomme for ren lokal drift. Se
[worker/README.md](worker/README.md).

## Udgange

Begge kræver `?token=<FEED_TOKEN>`.

- `GET /feed.xml` – RSS til en almindelig læser. Ét punkt pr. besked,
  sorteret efter dato, med kategori i titlen.
- `GET /display.json` – fladtrykt udgave til en ESP32 med e-ink-skærm.
  `?count=5&maxlen=120` styrer, hvor meget der skal være plads til.
- `GET /healthz` – sessionens tilstand.

## Sessionen og BankID

Unikum kræver BankID, og det kan ikke automatiseres. Til gengæld fornyer
Unikums egen frontend sit OAuth-token hvert 15. minut, så længe den kører.

Appen udnytter det. For at kunne genstarte og stadig have en session gemmer
den to ting, som begge er nødvendige:

1. OAuth-blobben fra `localStorage` (med `refresh_token`)
2. Unikums cookies — især SAML-sessionen `_shibsession_…`

Cookierne er session-cookies uden udløbstid, så browseren kasserer dem ved
lukning. Derfor gemmes de eksplicit i `data/cookies.json` med 30 dages
levetid. Mangler de, redirecter startsiden til login, og det *ligner* at
fornyelsen er død — men det er kun cookien, der mangler.

Med begge dele fornyer appen sig selv headless på ca. 10 sekunder.
Går det alligevel galt, siger den til, og du logger ind igen med BankID.

## Status

Virker: login, selvfornyende session, hentning, bilag (PDF/Word/txt/billeder),
opsummering på dansk med kort og lang tekst, RSS, JSON, publicering til
Cloudflare.

Mangler: planlagt kørsel, ESP32-klienten, flytning til Raspberry Pi.
