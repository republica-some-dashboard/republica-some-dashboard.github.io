# Republica SoMe Dashboard

Henter tal for Republicas opslag på LinkedIn, Facebook og Instagram hver time og
udgiver dem som et dashboard online.

**Dashboardet:** <https://republica-some-dashboard.github.io>
(adressen står under Settings → Pages, når opsætningen er færdig)

---

## Sådan sættes det op

Regn med 30–40 minutter første gang. Bagefter skal du aldrig røre det igen.

### 1 · Lav et Meta-token der ikke udløber

Det token du henter i Graph API Explorer dør efter et par timer. Til automatisk
drift skal du have et systembruger-token.

1. Åbn <https://business.facebook.com/settings/system-users>
2. **Tilføj** → giv den navn, fx `Dashboard`, rolle **Administrator**
3. Vælg systembrugeren → **Tilføj aktiver** → tilføj tre ting:
   appen *Republica SoMe Dashboard*, Facebook-siden, Instagram-kontoen.
   Sæt fuld adgang på hver.
4. **Generér nyt token** → vælg appen → sæt flueben ved disse fem:
   ```
   pages_show_list   pages_read_engagement   read_insights
   instagram_basic   instagram_manage_insights
   ```
5. Under udløb vælg **Aldrig**
6. Kopiér tokenet. **Det vises kun én gang.**

### 2 · Find de to id'er

Åbn denne adresse i browseren, med dit token indsat til sidst:

```
https://graph.facebook.com/v26.0/me/accounts?fields=id,name,instagram_business_account{id,username}&access_token=DIT_TOKEN
```

Svaret er tekst. `id` er Facebook-sidens id. `instagram_business_account.id` er
Instagram-kontoens id. Gem dem.

### 3 · Opret repoet på GitHub

1. Opret en konto på <https://github.com> hvis du ikke har en
2. <https://github.com/new>
   - **Owner:** `republica-some-dashboard` (lad den stå)
   - **Repository name:** `republica-some-dashboard.github.io`

     Navnet skal være ejerens navn efterfulgt af `.github.io`. Så lander siden på
     den korte adresse `https://republica-some-dashboard.github.io` i stedet for
     med repo-navnet hængt på bagefter.
   - **Public**, README **Off**, ingen .gitignore, ingen license
   - **Create repository**
3. På den tomme side der kommer: klik **uploading an existing file** → træk hele
   den udpakkede mappe ind → **Commit changes**

Mappen skal indeholde `.github/workflows/opdater-dashboard.yml`. Kan du ikke se
`.github`-mappen når du trækker filer ind, så træk hele mappen ind i stedet for
filerne enkeltvis.

### 4 · Læg tokens ind som hemmeligheder

**Settings** → i venstre side under **Security**: **Secrets and variables** →
**Actions** → **New repository secret**. Opret disse, én ad gangen:

| Navn | Værdi |
|---|---|
| `META_TOKEN` | systembruger-tokenet fra trin 1 |
| `FB_PAGE_ID` | Facebook-sidens id fra trin 2 |
| `IG_USER_ID` | Instagram-kontoens id fra trin 2 |
| `LI_TOKEN` | LinkedIn-token — først når LinkedIn har godkendt adgangen |
| `LI_ORG_ID` | LinkedIn-organisationens id |

Mangler LinkedIn endnu, så opret de tre første. De to sidste kan tilføjes senere,
og LinkedIn springes over indtil da.

Hemmeligheder kan ikke læses igen bagefter — heller ikke af dig. De vises aldrig
i kørslerne eller i koden.

### 5 · Tænd for udgivelsen

**Settings** → **Pages** → under *Build and deployment* → Source:
**GitHub Actions**.

Vælg **ikke** "Deploy from a branch". Den mulighed opdaterer ikke pålideligt, når
det er en robot der lægger filerne op.

### 6 · Giv robotten skriverettigheder

**Settings** → **Actions** → **General** → nederst under *Workflow permissions*:
vælg **Read and write permissions** → **Save**.

Det er hvad der lader kørslen gemme historikken i `data/posts.json`.

### 7 · Kør den første gang

**Actions**-fanen → i venstre side **Opdater dashboard** → knappen
**Run workflow** → **Run workflow**.

Kørslen tager 1–3 minutter. Grønt flueben betyder færdig. Adressen på dashboardet
står derefter under **Settings → Pages**.

---

## Herefter

Den kører hver time af sig selv. Du gør ingenting.

**Alle med linket kan se dashboardet.** GitHub tilbyder ikke adgangskode på
gratis-planen — heller ikke på den betalte personlige. Der ligger ikke tokens i
repoet, men tallene er offentlige. Læg ikke andet fortroligt i mappen.

**Hvis den holder op med at køre:** GitHub slår planlagte kørsler fra, hvis der
ikke har været aktivitet i repoet i 60 dage. Du får en mail. Fix: **Actions** →
**Opdater dashboard** → **Enable workflow**. Ét klik.

**Når et token udløber:** LinkedIn-tokens holder 60 dage og skal fornyes. Meta-
tokenet holder for evigt. Fejler en kørsel, sender GitHub dig en mail — opdatér
hemmeligheden, og næste kørsel virker igen.

**Tidspunkter:** kørslen ligger kl. :17 i UTC. GitHub advarer selv om at planlagte
kørsler kan blive forsinkede ved spidsbelastning, og at nogle kan blive droppet.
Regn med "cirka hver time", ikke præcist.

**Historik:** hver kørsel gemmer et snapshot. Kurven "ny reach pr. dag" bygger på
forskellen mellem snapshots, så den starter tom og vokser. Den kan ikke hentes
bagud fra API'et.

Opslag ældre end 45 dage hentes ikke længere, men bliver bevaret i arkivet med
deres sidste kendte tal.

---

## Filerne

| Fil | Hvad den gør |
|---|---|
| `.github/workflows/opdater-dashboard.yml` | Timeplanen. Kører de to scripts og udgiver |
| `fetch_some_data.py` | Henter opslag, tal og billeder fra de tre platforme |
| `build_dashboard.py` | Bygger `index.html` ud fra `data/posts.json` |
| `dashboard_template.html` | Dashboardets udseende. Rediger her, hvis noget skal se anderledes ud |
| `data/posts.json` | Tal og historik. Skrives af robotten — rediger ikke i hånden |
