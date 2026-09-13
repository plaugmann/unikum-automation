# Raspberry Pi

Pi'en overtager hentningen og opsummeringen. Cloudflare bliver ved med at
servere feedet, så telefonerne mærker ingen forskel — kun afsenderen flytter.

## Hvad der er anderledes end på Windows

**Playwright har ingen Chromium til ARM64 Linux.** Kør derfor *ikke*
`playwright install chromium`; det fejler eller henter en binær, der ikke kan
køre. Vi bruger systemets i stedet og peger på den med `CHROMIUM_PATH` i
`.env`. `install-pi.sh` ordner det.

**systemd i stedet for Opgavestyring.** En timer frem for cron, fordi vi så
får `Persistent=true` — har Pi'en været slukket, hentes kørslen ind ved
opstart i stedet for bare at blive sprunget over.

**Første login kræver, at du kan se en QR-kode.** Se nedenfor.

## Installation

```bash
git clone <repo> unikum-automation
cd unikum-automation
bash deploy/install-pi.sh
```

Udfyld derefter `.env`. Tag `FEED_TOKEN` og `CLOUD_PUSH_TOKEN` med fra
Windows-maskinen, ellers holder feedet op med at virke for telefonerne.

`OPENAI_API_KEY` og `CLOUD_URL` skal også med. `CF_API_TOKEN` behøver du kun,
hvis du vil kunne udrulle workeren fra Pi'en.

## Databasen

Du kan enten starte forfra — `sync` henter alt fra `UNIKUM_SINCE` igen, og
opsummeringen koster nogle få kroner — eller kopiere `data/unikum.db` med
fra Windows. Det sidste er hurtigere og bevarer historikken.

Kopiér **ikke** `browser_profile/` med. Den er bundet til den maskine og det
styresystem, den blev lavet på. Log ind på ny i stedet.

## Første login

BankID kræver, at du kan se en QR-kode, og `login` åbner et browservindue.

- **Pi med skærm:** kør `./.venv/bin/python -m unikum.cli login` på skrivebordet.
- **Pi uden skærm:** to muligheder —
  - kobl en skærm på én gang ved opsætningen, eller
  - kør kommandoen over `ssh -X` med X-videresendelse fra en maskine med skærm.

Begge dele er engangsarbejde, indtil sessionen udløber.

## Drift

```bash
systemctl list-timers unikum-update.timer
```

```bash
journalctl -u unikum-update.service -n 50
```

```bash
tail -f data/logs/update.log
```

Exitkode `2` betyder, at sessionen kræver BankID igen.

## Strøm og SD-kort

Loggen roterer ved 2 MB, og bilag gemmes i `data/attachments/`. Ved få
beskeder om ugen vokser det ubetydeligt, men det er værd at vide, at det
ligger på kortet.

Chromium starter kun, når access-tokenet skal fornyes — cirka hver time i
nogle få sekunder. På en Pi 4 er det uproblematisk. På en Pi Zero er det
tungt, og da er det værd at overveje at droppe Playwright helt.
