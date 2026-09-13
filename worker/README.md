# Unikum-feed i skyen

En lille Cloudflare Worker, der gemmer det færdige feed og serverer det til
telefonerne. Den genererer ingenting selv.

## Hvorfor push og ikke tunnel

Hentningen fra Unikum kan ikke flytte i skyen — BankID-sessionen lever i en
browser på maskinen derhjemme. Men det færdige feed er en statisk fil på
omkring 30 KB, og den kan sagtens ligge ude.

Maskinen derhjemme **pusher** til workeren. Der åbnes altså ikke for
indgående trafik til hjemmenettet, og skyen kender hverken BankID-sessionen,
Unikum-tokens eller OpenAI-nøglen. Går workeren ned, virker den lokale
server stadig.

## To nøgler

| Nøgle | Adgang | Hvor den bruges |
|---|---|---|
| `FEED_TOKEN` | læse | I URL'en, som RSS-apps og ESP32'en henter |
| `PUSH_TOKEN` | skrive | Kun i `.env` på maskinen derhjemme |

De er adskilt med vilje: deler du feed-URL'en med nogen, kan de læse, men
ikke overskrive.

## Opsætning

Vi udruller med Cloudflares REST API fra Python, ikke med wrangler.
Wrangler trækker over 100 MB binærer med for at udrulle 95 linjers kode, og
det faldt gentagne gange over en langsom forbindelse. `wrangler.jsonc`
bliver liggende som dokumentation af opsætningen — udrulleren læser navn og
`compatibility_date` derfra, så der er ét sted at rette.

### 1. Hent konto-ID og lav en API-token

I Cloudflares kontrolpanel:

- **Konto-ID** står i højre side under Workers & Pages.
- **API-token** oprettes under My Profile → API Tokens → Create Custom Token
  med netop disse to rettigheder:
  - `Account` → `Workers Scripts` → `Edit`
  - `Account` → `Workers KV Storage` → `Edit`

Sæt dem i projektets `.env`:

```
CF_ACCOUNT_ID=...
CF_API_TOKEN=...
```

De bruges **kun** til udrulning, ikke i daglig drift.

### 2. Udrul

```bash
.\.venv\Scripts\python.exe -m unikum.cli deploy
```

Den opretter KV-lageret, hvis det mangler, lægger koden op med bindingerne,
slår workers.dev-adressen til og skriver den URL ud, du skal sætte som
`CLOUD_URL` i `.env`.

`FEED_TOKEN` og `PUSH_TOKEN` sendes med som secrets ved hver udrulning,
læst fra `.env`. Dermed kan de to udgaver af feedet ikke komme til at bruge
forskellige nøgler.

### 3. Publicér

```bash
.\.venv\Scripts\python.exe -m unikum.cli publish
```

Herefter publicerer `update` automatisk, hver gang den kører.

## Test

Workerens logik kan afprøves uden at udrulle — routing, adgangskontrol og
betinget hentning køres mod en stub af KV:

```bash
node worker/test.mjs
```

## Endpoints

| Metode | Sti | Adgang |
|---|---|---|
| `GET` | `/feed.xml?token=…` | `FEED_TOKEN` |
| `GET` | `/display.json?token=…` | `FEED_TOKEN` |
| `PUT` | `/feed.xml` | `Authorization: Bearer <PUSH_TOKEN>` |
| `PUT` | `/display.json` | `Authorization: Bearer <PUSH_TOKEN>` |

Læsninger svarer `304 Not Modified`, hvis intet er ændret siden sidst.
Afsenderen sender en hash med, som bliver til `ETag` — så sparer telefonerne
data og batteri, når de henter ofte.

## Forbrug

Gratis-niveauet dækker 100.000 læsninger og 1.000 skrivninger i døgnet.
Ved kørsel hver time skriver vi 48 gange dagligt (to filer), og to telefoner
der henter hvert kvarter læser under 200 gange. Der er rigelig plads.
