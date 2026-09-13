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

```bash
npm install -g wrangler
wrangler login
```

Opret nøgle-værdi-lageret og skriv ID'et ind i `wrangler.jsonc`:

```bash
wrangler kv namespace create FEED
```

Sæt de to hemmeligheder. `FEED_TOKEN` skal være **samme værdi** som i
projektets `.env`, så det lokale og det offentlige feed bruger samme nøgle:

```bash
wrangler secret put FEED_TOKEN
```

```bash
wrangler secret put PUSH_TOKEN
```

Udrul:

```bash
wrangler deploy
```

Wrangler skriver adressen ud, fx `https://unikum-feed.<konto>.workers.dev`.
Den skal i projektets `.env` som `CLOUD_URL`.

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
