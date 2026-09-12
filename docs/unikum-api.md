# Unikum – kortlægning af API

Resultat af research-session 2026-09-12. Alt herunder er verificeret mod en
live, indlogget session for vårdnadshavare i Malmö stad.

## 1. Identiteter

| Enhed | ID |
|---|---|
| Sören Meyer Plaugmann (vårdnadshavare) | `11373201365` |
| Max Meyer Plaugmann (elev) | `10773547324` |
| Glasbruksskolan | `10916440412` |
| Glasbruksskolans fritidshem | `10917635976` |
| Modersmålsenheten grundskolan | `10595730187` |

`__pid` i URL'er = den kontekst siden vises for (bruger, gruppe eller skole).

## 2. Login

Interaktivt, kræver BankID på mobil. Reproducerbar sti:

1. `https://start.unikum.net/unikum/appLogin.ahtml`
2. Vælg **"Malmö stad för vårdnadshavare"**
   (IdP entityId: `https://skolfederation.malmo.se/idp`)
3. Redirect til `eid-connect.funktionstjanster.se`
4. Knap **"BankID other device"** → QR-kode, scannes med mobil
5. Retur til `start.unikum.net`, som afslutter et OAuth2 authorization-code flow:
   - `GET /unikum/auth/oauth2/tokenjshandover.ajax?code=…&state=…`
   - `POST /unikum/auth/oauth2/token`

Login-URL'er med `aid=`-parameter er **engangs** og kan ikke genbruges.

## 3. Tokens

Gemmes i `localStorage` under nøglen:

```
@unikum/oauth-tokens/unikum-client
```

```json
{ "access_token": "<JWT, 706 tegn>",
  "refresh_token": "<JWT, 743 tegn>",
  "expires_in": 899,
  "token_type": "bearer",
  "scope": "read write",
  "jti": "…",
  "sessionFingerprint": "<64 hex>" }
```

- **access_token: 15 minutters levetid** (`exp` − `iat` = 900 s)
- **refresh_token har intet `exp`-claim.** Verificeret: en åben fane fornyede
  sig selv hvert 15. minut i over 40 minutter uden BankID.
- `sessionFingerprint` sendes **ikke** som header ved API-kald.

## 4. GraphQL-API

```
POST https://clients.unikum.net/fam/v1/
```

Headers:

```
Authorization: Bearer <access_token>
X-App-Origin: Monolith
X-Language: sv
Content-Type: application/json
```

Body: `{"query": "...", "variables": {...}}`

- Arbitrære queries er tilladt (valideres mod skemaet, ikke en whitelist).
- **Introspektion er slået fra på serveren.** Men fejlbeskeder lækker
  feltforslag ("Did you mean …"), og frontenden har hele skemaet (912 typer)
  liggende klientside i en lokal Apollo-facade — nåbart via
  `window.__APOLLO_CLIENT__.link` → `.schema._typeMap` i en indlogget browser.

### Relevante felter på `User`

| Felt | Indhold | Bruges? |
|---|---|---|
| `informationEntriesByRole` | Meddelanden + ledelsesinfo | **Ja – primær kilde** |
| `plannings` | Pedagogiska planeringar | Ja (20 stk. for Max) |
| `blogEntries` | Opslag | Marginal – kun 1 post, fra 2024 |
| `calendarEntries` | Kalender | Endnu ikke undersøgt |
| `developmentPlans` | Utvecklingssamtal / IUP | Endnu ikke undersøgt |
| `guardianGroups` | Grupper (4C, Kryssaren) | Til filtrering/mærkning |

### Liste-query (bagkatalog + polling)

`User.informationEntriesByRole` tager bl.a.
`role: InformationEntryRoleEnum` (`GUARDIAN`/`STUDENT`/`STAFF`),
`sortOn: InformationEntrySortEnum`, `from: Date`, `to: Date`,
`first: Int`, `after: String`, `unread: Boolean`, `isUsersOwnPage: Boolean`.

`from`/`to` filtrerer serverside — brug `from: "2026-08-01"` til backfill.

```graphql
query Feed($pid: ID!, $first: Int!, $cursor: String, $from: Date) {
  User(id: $pid) {
    informationEntriesByRole(
      role: GUARDIAN, sortOn: PUBLISHED, isUsersOwnPage: true,
      first: $first, after: $cursor, from: $from
    ) {
      pageInfo { hasNextPage endCursor }
      edges { node {
        _id title informationType published from to important
        author { firstName lastName }
      } }
    }
  }
}
```

Verificeret: `{pid: "11373201365", first: 100, from: "2026-08-01"}` giver
**17 poster** fra 2026-08-10 til 2026-09-11, `hasNextPage: false`.
Typer: `MESSAGE` (16), `MANAGEMENTINFO` (1).

> **Vigtigt:** listen udfylder *ikke* `attachments` og `owner`.
> Begge kræver et detaljekald pr. post.

### Detalje-query

```graphql
query Entry($id: ID!) {
  InformationEntry(id: $id) {
    _id title body published lastEdited informationType important
    from to recipients owningSchoolId
    author { firstName lastName }
    owner { __typename ... on School { _id fullName } ... on Group { _id fullName } }
    recipientContexts { edges { node { __typename
      ... on School { _id fullName } ... on Group { _id fullName } } } }
    attachments { _id path }
  }
}
```

`body` er HTML fra en draft-editor. Bilag ligger som et indlejret element
*og* i `attachments`:

```html
<div data-custom-block-type="atomic">
  <a data-custom-entity-type="INTERNAL_ATTACHMENT"
     data-file-name="Brev från rektor37.pdf"
     data-file-size="683860"
     data-file-url="/unikum/content/content/4cc0/14729854889-….pdf">…</a>
</div>
```

### Bilag

`attachments[].path` er fx `/content/4cc0/14729854889-….pdf`.
Fuld URL: `https://start.unikum.net/unikum` + `path`
→ `https://start.unikum.net/unikum/content/content/4cc0/….pdf`

Download kræver session (ikke verificeret om bearer-token er nok, eller om
den HttpOnly-cookie skal med).

## 5. Sessionens levetid — løst

Fornyelsen sker i Unikums egen frontend. For at kunne genstarte processen og
stadig have en session skal **to** ting gemmes:

1. **localStorage-blobben** under `@unikum/oauth-tokens/unikum-client`
   (indeholder `refresh_token`).
2. **Cookies for `start.unikum.net`** — især `_shibsession_…` (SAML-sessionen
   mod Malmö stads IdP), `JSESSIONID` og `UNIHZSESSID`.

Cookierne sættes **uden udløbstid**, altså som session-cookies. Playwright
kasserer dem, når browseren lukker — også med en persistent profil. De skal
derfor gemmes eksplicit og få en rigtig udløbsdato ved genindlæsning.

Gemmer man kun tokenet og ikke cookierne, redirecter `start.html` til
`login.jsp`, SPA'en starter aldrig, og den rydder sit eget token. Det ser ud
som om refresh ikke virker — men årsagen er den manglende cookie.

Med begge dele på plads fornyer en headless browser sessionen på ~11 sekunder
uden BankID.

## 6. Åbne punkter

- [ ] Hvor længe holder SAML-sessionen i praksis? Malmö stads IdP kan have en
      maksimal levetid, der tvinger nyt BankID uanset aktivitet.
- [ ] Hvilken auth kræver bilags-download? (Virker med bearer-token i dag.)
- [ ] `Planning` har intet `published`-felt, kun `lastEdited` — find ud af
      hvilken dato der skal bruges i feedet.
- [ ] `calendarEntries` og `developmentPlans`: relevante for feedet?
- [ ] Kryssaren (fritidshem) — kommer dens beskeder med i
      `informationEntriesByRole`, eller kræver de et opslag pr. `guardianGroup`?
