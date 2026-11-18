# o-energy — Octopus Energy DE Dashboard (Schritt 1: Plan)

Eigene Web-App, die die Octopus-Energy-GraphQL-API (Kraken) pollt, Verbrauch,
Zählerstände, Preise und Kosten in SQLite persistiert und in einer schnellen
HTMX-WebUI darstellt.

---

## 1. Ausgangslage / Recherche-Ergebnisse

### 1.1 Quelle
Blogbeitrag: `https://octopusenergy.de/blog/tipps-tricks/auf-der-suche-nach-deutschen-energie-vorreitern`
→ Anbieter ist **Octopus Energy Germany GmbH** (München), Blog = "Constantine the Octopus".
Der Beitrag ist ein Marketing-Artikel über deutsche Energie-Vorreiter; die
technisch relevante Information ist der **Dynamic Octopus**-Tarif (15-Minuten-Preise
auf EPEX-Basis), der im Portal ein Dashboard mit „Aktuelle Preise, Verbrauch im
Dashboard & monatliche Abrechnung“ verspricht.

### 1.2 API (live introspektiert, `https://api.oeg-kraken.energy/v1/graphql/`)
GraphQL, **2472 Typen**, 228 Queries, 394 Mutations. Relevante Kernpunkte:

**Auth**
```graphql
mutation { obtainKrakenToken(input:{email:"…", password:"…"}) { token refreshToken refreshExpiresIn } }
```
- E-Mail/Passwort wird zur Laufzeit akzeptiert (im Introspection-Schema sind die
  Felder nicht sichtbar, der Server validiert sie aber → „KT-CT-1138 credentials incorrect“).
- Alternativ `input:{APIKey:"…"}` (Live Secret Key aus dem Portal) oder `refreshToken`.
- Danach Header `Authorization: <JWT>`; ohne Header: „KT-CT-1112 Authorization header not provided“.
- `obtainLongLivedRefreshToken(input:{krakenToken})` → Refresh-Token für den Dauerbetrieb.
- Portal selbst nutzt einen OAuth-Start (`/api/auth/kraken-oauth-start`) → **Fallback**, falls
  direkter Login blockiert wird.

**Preise / Tarif**
| Query | Zweck |
|---|---|
| `agreement(id: ID!)` | Tarif-Agreement: `unitRateInformation` (net/gross ct/kWh), `netMonthlyStandingCharge`, `unitRateForecast { validFrom validTo unitRateInformation }` → **Zukunftspreise**, `product { code isTimeOfUse … }` |
| `agreementRates(agreementId, activeAt)` | `ApplicableRateType { pricePerUnit unit validityPeriod{start end} variantProfile }` → zeitaufgelöste Einzelraten |
| `epexDayAheadPrices(periodStart, periodEnd)` | EPEX-Spotpreise (Day-Ahead) für DE |
| `activeTariffs` / `Product` | Tarifliste, `isTimeOfUse` erkennt dynamischen Tarif |
| `rateGroupTouScheme(specificationIdentifier, rateGroupCode)` | Zuordnung Zählzeitfenster → Tarif |

**Verbrauch / Zählerstände**
| Query | Zweck |
|---|---|
| `electricityMeters(accountNumber, meloNumber)` | Zähler-IDs, `number`, `meloNumber`, `hasSmartMeterGateway` |
| `electricityMeterReadings(accountNumber, meterId, readFrom, readTo, typesOfRead)` | Roh-Readings: `value`, `readAt`, `registerObisCode`, `typeOfRead`, `origin`, `status` |
| `electricityMeterReadingsByRegister(accountNumber, meterId)` | Register (OBIS-Codes) + `readings(...)` |
| `supplyPoint.readings(startAt, endAt, readingType, timeGranularity, timezone, units)` | Zeitreihe, Granularität `FIFTEEN_MIN` … `YEAR` |
| `supplyPointTimeOfUseReadings(externalIdentifier, marketName, …, timeGranularity)` | Zeitfenster-Verbrauch (dynamischer Tarif) |
| `estimatedSupplyPointReadings(...)` | Schätzungen (Fallback, `estimationStrategy`) |
| `estimatedElectricityConsumption(accountNumber, meloNumber, startDate, endDate)` | Summen je Zeitraum |

**Konto / Kosten**
- `viewer { email accounts { … } }`, `account(accountNumber)` → `balance`, `overdueBalance`,
  `bills`, `transactions`, `annualStatements`, `paymentSchedules`, `ledgers`, `properties`.
- `marketSupplyAgreements` / `supplyPointsInterface` → Agreement + SupplyPoint + Produkt.

**Rahmenbedingungen**
- FastAPI: `readings(startAt, endAt, …)` liefert Zeitreihen mit `timeGranularity` bis `FIVE_MIN`.
- Granularität im Dynamic-Tarif: 15 Minuten.
- Alle Queries außer `rateLimitInfo` erfordern ein gültiges Token.

### 1.3 Kritische Fachanforderung: 24-h-Latenz der Verbrauchsdaten
Der Zähleranbieter liefert Verbrauchswerte **erst ~24 h versetzt** (Werte des Vortages
ca. gegen **08:00 Uhr** des Folgetages). Konsequenzen, die im Design verankert sind:

1. **Tagesabschluss ist ein Ereignis, kein Datum.** Ein Tag gilt erst als *final*,
   wenn für ihn (a) die Rohdaten eingetroffen sind und (b) der erwartete
   Slot-/Messwertumfang plausibel ist.
2. **Rohdaten werden nie überschrieben, sondern upserted** (`meter_id, read_at` unique),
   damit nachlaufende Werte Lücken schließen statt Historie zu zerstören.
3. **Jedes Reading bekommt `received_at`** → wir können exakt sagen, *wann* ein Tag
   vollständig wurde, und das im UI anzeigen („final seit 08:14“).
4. **Backfill-Fenster**: jeder Sync lädt die letzten *N* Tage (Default 3, konfigurierbar)
   erneut → verspätete Werte landen automatisch in der DB.
5. **Provisorische vs. finale Werte**: API liefert `typeOfRead` (`BILLING`, `INTERIM`,
   `ESTIMATE`, …) und `origin` → wir markieren Schätz-/Interim-Werte und rechnen
   Kosten für unvollständige Tage als **vorläufig**.
6. **UI**: Tage/Gaps werden visuell als „wartet auf Zählerdaten (ab ca. 08:00)“
   gekennzeichnet; Kosten-/Verbrauchsgraphen haben eine sichtbare „Provisorisch“-Legende.

---

## 2. Architektur

### 2.1 Stack
- **Python 3.12+, `uv`** (Projektverwaltung, Lockfile, `uv run`, `uv sync`)
- **FastAPI** + **Uvicorn**, Jinja2-Templates (Server-Rendering), **HTMX** für Partial Updates
- **SQLAlchemy 2.x** (ORM) + **Alembic** (Migrationen), **SQLite** (WAL-Modus)
- **httpx** (async API-Client), **pydantic v2 / pydantic-settings** (Config)
- **APScheduler** (in-process) für wiederkehrende Jobs + **CLI** (`uv run oenergy sync`)
  für cron/systemd-Timer als Alternative
- **Charts**: Apache ECharts (schön, schnell, sehr gute Zeitreihen) — oder Chart.js
  (siehe offene Frage O5); statisch eingebunden (kein CDN, läuft offline)
- **CSS**: eigenes, modernes CSS (Design-Tokens, Dark Mode) — kein Tailwind-Build nötig

### 2.2 Projektstruktur
```
o-energy/
├── pyproject.toml            # uv-Projekt, Deps, Scripts
├── uv.lock
├── .env.example              # OCTOPUS_EMAIL, OCTOPUS_PASSWORD, DB_URL, POLL_*, ...
├── alembic.ini
├── alembic/versions/         # Migrationen
├── src/oenergy/
│   ├── __init__.py
│   ├── config.py             # Settings (pydantic-settings)
│   ├── main.py               # FastAPI-App, lifespan, Router
│   ├── cli.py                # `uv run oenergy sync|probe|prices`
│   ├── api/
│   │   ├── client.py         # httpx-Client, Retry/Backoff, Fehlernormalisierung
│   │   ├── auth.py           # obtainKrakenToken, Token-Cache, Refresh, 401-Recovery
│   │   ├── queries.py        # alle GraphQL-Dokumente (als Konstanten)
│   │   └── models.py         # pydantic-Modelle der API-Antworten
│   ├── db/
│   │   ├── base.py           # Engine/Session (SQLite WAL), SessionLocal
│   │   └── models.py         # SQLAlchemy-Modelle
│   ├── services/
│   │   ├── sync.py           # Orchestrierung: Preise/Meter/Statements
│   │   ├── prices.py         # Preis-Zeitreihe + „aktueller/zukünftiger Preis“
│   │   ├── consumption.py    # Readings → 15-min-Intervalle, Tagesaggregation
│   │   ├── completeness.py   # Latenz/Vollständigkeit, final/provisorisch
│   │   └── costs.py          # Kostenberechnung (Energie + Grundpreis)
│   ├── jobs.py               # Scheduler-Definition
│   ├── web/
│   │   ├── routes.py         # Seiten + HTMX-Partials + JSON-Endpunkte
│   │   └── templates/        # base.html, dashboard.html, partials/*
│   └── static/               # css, js, vendor/ (htmx, echarts)
└── tests/                    # pytest: Kostenrechnung, Vollständigkeit, Parsing
```

### 2.3 Datenmodell (SQLite)
| Tabelle | Zweck | Schlüssel |
|---|---|---|
| `accounts` | Konto-Nr., Brand, Kundenname, URN | `number` PK |
| `agreements` | Agreement-ID, Konto, Produkt-Code, `is_time_of_use`, melo/sp-Nr., SupplyPoint | `id` PK |
| `meters` | Zähler-ID, Nummer, meloNummer, OBIS-Register | `id` PK |
| `meter_readings` | **Rohdaten**: value, read_at, obis, type_of_read, origin, status, `received_at` | unique (`meter_id`, `read_at`, `register_obis_code`) |
| `intervals` | 15-min-Buckets: kWh (netto), quelle (`measured`/`estimated`), anzahl_messungen, `received_at` | unique (`meter_id`, `bucket_start`) |
| `daily_stats` | Tagesaggregation: kWh, kosten_ct, `is_final`, `finalized_at`, `expected_slots`, `received_slots` | unique (`meter_id`, `day`) |
| `prices` | Preis-Zeitreihe: `starts_at`, `ends_at`, `price_ct_per_kwh` (netto/brutto), `source` (`agreement_rate`/`epex`/`forecast`) | unique (`starts_at`, `source`, `meter_id`/`agreement_id`) |
| `tariff_rates` | Grundpreis/Arbeitspreis-Sätze je Tarif mit Gültigkeitszeitraum | unique (`agreement_id`, `valid_from`) |
| `bills` / `statements` | Rechnungen/Jahresabschlüsse aus dem Konto (Ist-Kosten zum Abgleich) | `id` PK |
| `sync_runs` | Log: Job, Start/Ende, Status, Trefferzahl, Fehlermeldung | id PK |

Indizes auf `read_at`, `day`, `starts_at`; `PRAGMA journal_mode=WAL`, `foreign_keys=ON`.

### 2.4 Jobs (Scheduler)
| Job | Intervall | Inhalt |
|---|---|---|
| `sync_prices` | alle 15 min | `agreementRates` + `unitRateForecast` + EPEX → `prices`; Preis-Broadcast an UI |
| `sync_readings` | stündlich (zur vollen Stunde) | Meter-Readings der letzten 3 Tage (Backfill) → `intervals` → `daily_stats` |
| `sync_daily_finalize` | 08:15 Europe/Berlin | Tagesabschluss: Vortag finalisieren, wenn Daten vollständig; Kosten neu berechnen |
| `sync_bills` | täglich 06:00 | Rechnungen/Balance aktualisieren (Ist-vs.-Soll-Abgleich) |
| `sync_account` | 12 h | Agreement/Produkt/Grundpreis aktualisieren |

Alle Jobs: Timeout, Retry mit Backoff, Fehler landen in `sync_runs` + Log; die UI zeigt
„letzter Sync“ und Fehlerstatus.

### 2.5 Kostenberechnung
- **Energiekosten**: je 15-min-Intervall `kWh × Preis(ct/kWh)`; Preis aus `prices`
  (Vorrang: `agreement_rate` → `unitRateForecast` → EPEX + Aufschlag aus `tariff_rates`).
  Netto-Preise + Umsatzsteuer (19 %) bzw. direkt Brutto-Preise aus der API (`grossRateInformation`).
- **Grundpreis**: monatlicher Standing Charge aus `agreement.netMonthlyStandingCharge` /
  `tariff_rates` → Tagesanteil (365-Tage-Tarif) bzw. Monatsbuckets.
- **Kennzahlen**: Kosten heute/gestern/aktueller Monat/Jahr, Ø-Preis (€/kWh), günstigstes
  & teuerstes Zeitfenster des Tages, Hochrechnung Monatsende, **provisorisch/final**-Kennzeichnung.
- **Abgleich**: Soll-Kosten (berechnet) vs. Ist-Kosten (Rechnungen aus `bills`) als
  „Rechnung & Prognose“-Kachel.

### 2.6 Web-UI (FastAPI + Jinja2 + HTMX + ECharts)
Seiten:
1. **Dashboard** – Kacheln: aktueller Preis (ct/kWh, gültig bis …), nächste Preise,
   Verbrauch heute/gestern/lfd. Monat, Kosten heute/Monat/Jahr, Tarifname,
   „Datenstand“-Kachel (letzter Sync, Vollständigkeit gestern).
   Diagramme: 15-min-Preisverlauf (heute + morgen), Verbrauchsprofil 24 h,
   Kosten pro Tag (letzte 30 Tage, provisionär eingefärbt),
   Kosten-Heatmap (Tag × 15-min-Slot).
2. **Verbrauch** – Tabelle + Graph: Zählerstände (letzte 10), Tagesverbrauch,
   Zählzeitfenster-Auswertung (billigste Zeiten des Tages).
3. **Preise** – EPEX/Endkunden-Preisreihe, Zuschläge, Export CSV.
4. **Kosten/Abrechnung** – Rechnungen, Soll/Ist-Vergleich, Hochrechnung.
5. **Status/System** – Sync-Log, geplante Jobs, Konfiguration (ohne Secrets).

Technik:
- HTMX für Live-Tiles (`hx-get` + `hx-trigger="every 30s"`), Preis-/Verbrauchs-Charts
  per JSON-Endpoint + ECharts (clientseitig), Alpine.js sparsam für Tab-/Range-Switch.
- Vendor-Assets lokal (`static/vendor/`), keine CDN-Abhängigkeit.
- Dark Mode, responsives Grid, schnelle Server-Rendered-Shell + gezielte Partials.

---

## 3. Umsetzungsplan (Schritte)

| # | Schritt | Inhalt | Fertig, wenn |
|---|---|---|---|
| **S0** | Probe | `uv run oenergy probe` – mit echten Credentials: Login testen, `viewer`/`account`/`agreements` auslesen, **verfügbare Preis- und Reading-Queries empirisch ermitteln** (welche Query liefert beim Dynamic-Tarif die 15-min-Preise und die Intervalldaten?), erstes Reading-Format dokumentieren | JSON-Dump der Tarife/Preise/ersten Readings liegt vor; Preisquelle und Reading-Granularität sind entschieden |
| **S1** | Projekt-Init | `uv init`, `pyproject.toml` (FastAPI, uvicorn, httpx, sqlalchemy, alembic, apscheduler, pydantic-settings, jinja2, pytest, ruff), `src/`-Layout, `.env.example`, Lint/Format-Config, `make`-artige Task-Skripte | `uv sync` läuft, `uv run oenergy --help` und `uv run pytest` laufen |
| **S2** | DB-Schema | SQLAlchemy-Modelle + Alembic-Migration, SQLite WAL, Seeds/Indices | `alembic upgrade head` erzeugt Schema, `uv run oenergy db-check` grün |
| **S3** | API-Client & Auth | httpx-Client, `obtainKrakenToken`, Token-Cache in DB, Refresh, 401-Retry, Fehlernormalisierung, Rate-Limit-Bewusstsein, Retry/Backoff, Unit-Tests gegen Mock | `pytest` deckt Token-Refresh + Retry ab |
| **S4** | Preise | `sync_prices`: Agreement-Raten, Forecast, EPEX → `prices`; Services „aktueller Preis“, „nächster Preis“, „Preisreihe“; Preisfan-Logik bei 15-min-Slots | Preistabelle gefüllt; UI-Tile zeigt gültigen Current- & Future-Preis |
| **S5** | Verbrauch & Latenz | `sync_readings` mit Backfill (3 Tage), Upsert, `received_at`, 15-min-Buckets, Tagesaggregation, `completeness`-Service (`is_final`, erwartete vs. gelieferte Slots, `finalized_at`), **08:00-Finalisierung** | Testfall: Tag ohne Daten → „provisorisch“; nach Datenlieferung → `is_final=1`, Graph zeigt volle Werte |
| **S6** | Kosten | Kostenberechnung je Intervall/Tag/Monat, Grundpreis, Soll/Ist-Abgleich mit Rechnungen, Hochrechnung | Kosten summieren korrekt (Unit-Tests mit fixtures) |
| **S7** | Web-UI | FastAPI-Routen, Jinja-Basis, Dashboard mit Kacheln, 5 Unterseiten, HTMX-Live-Updates, ECharts-Diagramme, Dark Mode | `uv run oenergy serve` zeigt vollständiges Dashboard mit Live-Daten |
| **S8** | Betrieb | systemd-Unit + Timer (oder Docker-Compose), Log-Rotation, DB-Backup-Skript, `.env`-Handling, README (Betrieb, Kosten, Sicherheit) | `systemctl enable --now oenergy-sync` läuft, Backup-Restore getestet |
| **S9** | Qualität | pytest-Suite (API-Parsing, Kosten, Vollständigkeit, Routen-Smoke-Test), ruff, mypy, README/CHANGELOG | `uv run pytest` + `uv run ruff check` sauber |

Reihenfolge-Abhängigkeiten: S0 → (S1,S2) → S3 → S4/S5 → S6 → S7 → S8 → S9.

---

## 4. Risiken & Gegenmaßnahmen

| Risiko | Gegenmaßnahme |
|---|---|
| Kraken blockiert Login außerhalb des Portals (Captcha/OAuth) | Fallback: API-Key (Live Secret Key) bzw. OAuth-Start-Flow; in S0 empirisch klären |
| Dynamischer Tarif liefert Preise nur über `unitRateForecast`/`agreementRates` | In S0 alle Preisquellen testen; Preisdienst arbeitet mit Prioritätsliste + Fallback auf EPEX + Aufschlag |
| Zählerregister/OBIS und Zeitgranularität unbekannt | In S0 empirically ermitteln; Konfiguration `METER_OBIS`, `GRANULARITY` im `.env` |
| Sehr große Reading-Mengen (15-min seit Jahren) | nur rolling window abrufen, ab `2024-01-01`/konfigurierbar, Cursor-Pagination |
| EPEX-Daten kommen erst am Folgetag | `sync_prices` läuft häufig; fehlende Zeiträume werden nachgeholt (Backfill auch für Preise) |
| API-Änderungen | Schema-/Response-Validierung mit pydantic, Parser kapseln, Schema-Dump im Repo (`docs/kraken-schema.json`) |

---

## 5. Offene Fragen (bitte beantworten)

1. **Welcher Tarif?** Dynamic Octopus (15-min-EPEX-Preise) oder ein klassischer Festpreis-Tarif?
   (Beeinflusst, ob Preisreihen aus `agreementRates` oder Forecast/EPEX+Aufschlag kommen.)
2. **Zugangsdaten-Form:** E-Mail+Passwort (Portal-Login) oder habt ihr einen API-Key
   (Live Secret Key) im Kundenportal? Beides wird unterstützt.
3. **Umfang:** nur Strom oder auch Gas? Zähler-/MELO-Nummern liegen dann ggf. mehrere vor.
4. **Betrieb:** direkt auf dem Host (systemd-Service + Timer) oder in einem Docker-Container?
   Wo soll die SQLite-DB liegen, und soll ich ein Backup-Skript einbauen?
5. **Chart-Bibliothek:** Apache ECharts (mich empfohlen: schöner, sehr performant bei
   Zeitreihen) oder Chart.js (kleiner, etablierter)?
6. **UI-Sprache:** Deutsch oder Englisch?

---

## 6. Erste konkrete API-Aufrufe (Rohentwurf, in S0 zu verifizieren)
```graphql
# Login
mutation { obtainKrakenToken(input:{email:"$E", password:"$P"}) { token refreshToken refreshExpiresIn } }

# Konto + Tarife
{ viewer { email accounts { ... on AccountType {
    number status balance
    marketSupplyAgreements { id isActive agreedFrom
      supplyPoint { id externalIdentifier }
      product { code displayName isTimeOfUse } } } } } }

# Aktuelle + zukünftige Preise
{ agreement(id:"$AGREEMENT_ID") {
    netMonthlyStandingCharge
    unitRateInformation { ... on TimeOfUseProductUnitRateInformation {
        rates { timeslotName netUnitRateCentsPerKwh latestGrossUnitRateCentsPerKwh } } }
    unitRateForecast { validFrom validTo unitRateInformation {
        ... on SimpleProductUnitRateInformation { netUnitRateCentsPerKwh latestGrossUnitRateCentsPerKwh } } } } }

# Preise je Zeitfenster
{ agreementRates(agreementId:"$AGREEMENT_ID") { edges { node {
      pricePerUnit unit validityPeriod { start end } } } } }

# Verbrauch (24-h-versetzt!)
{ electricityMeters(accountNumber:"$ACC", meloNumber:"$MELO") { edges { node {
      id number meloNumber hasSmartMeterGateway } } } }
{ electricityMeterReadings(accountNumber:"$ACC", meterId:"$METER",
      readFrom:"2026-11-01T00:00:00Z", readTo:"2026-11-15T00:00:00Z", first:500) {
      edges { node { value readAt registerObisCode typeOfRead origin status } } } }
```

---

## 7. Getroffene Entscheidungen (Rückfragen beantwortet)

| Thema | Entscheidung |
|---|---|
| Tarif | **Dynamic Octopus** (dynamisch, 15-Minuten-Preise auf EPEX-Basis) |
| Zugangsdaten | **E-Mail + Passwort** (Portal-Login), API-Key als Fallback eingebaut |
| Umfang | **nur Strom** (Datenmodell ist aber auf Gas erweiterbar) |
| Betrieb | **Docker-Compose** (Web-Container + optionaler Sync-Worker, DB als Volume `./data`) |
| Chart-Bibliothek | **Chart.js** (lokal eingebunden, kein CDN) + Preis-Matrix als CSS-Heatmap |
| Sprache | **Deutsch** |

## 8. Umsetzungsstand

| Schritt | Status | Ergebnis |
|---|---|---|
| S0 API-Recherche | ✅ | Schema live introspektiert (2472 Typen) **und am echten Konto verifiziert**: freigegebene Queries ermittelt, Datenmodelle und Token-Laufzeiten bestätigt (Details unten) |
| S1 Projekt-Init | ✅ | `uv`-Projekt (Python 3.12), FastAPI/httpx/SQLAlchemy 2/Alembic/APScheduler/pydantic-settings, ruff + pytest |
| S2 DB-Schema | ✅ | 12 Tabellen inkl. `reading_windows` (Backfill-Memory) und `tokens`; 4 Alembic-Migrationen |
| S3 API-Client & Auth | ✅ | Token-Cache in der DB, JWT-Ablauf, Long-Lived-Refresh, 401-Recovery, Retry/Backoff – getestet mit `respx` |
| S4 Preise | ✅ | `agreementRates` + `unitRateForecast` + EPEX, Prioritätsliste, ct/kWh-Normalisierung, Grundpreis & USt aus `standingChargeGrossRateInformation` |
| S5 Verbrauch & Latenz | ✅ | Rohdaten-Upsert mit `received_at`, 15-Min-Buckets, Tagesstatus `final/provisorisch/ausstehend/warten`, Finalisierung nach 08:00 + Grace, optionale echte Intervallwerte über `supplyPoint.readings` |
| S6 Kosten | ✅ | Intervallkosten, Grundpreisanteil, Tages-/Monats-/Jahreswerte, Monatsprognose, Rechnungsabgleich |
| S7 Web-UI | ✅ | Dashboard, Verbrauch, Preise, Kosten, System; HTMX-Live-Kacheln, Chart.js-Diagramme, Preis-Matrix, Dark Mode |
| S8 Betrieb | ✅ | Dockerfile (uv-Basis, Healthcheck), `docker-compose.yml` mit optionalem Worker, Backup-Skript, README |
| S9 Qualität | ✅ | 81 Tests grün (inkl. End-to-End-Test der kompletten Pipeline gegen ein gemocktes Kraken), `ruff check`/`ruff format` sauber |
| **Backfill** | ✅ | Gap-Erkennung über Datenabdeckung + abgefragte Fenster, Tagesraster, Retry-Fenster; bereits abgerufene Bereiche werden nie erneut angefragt |
| **Diagnose** | ✅ | `oenergy diagnose` prüft live, ob Konto/Tarif/Zähler/Preisquellen liefern – Ausgabe: Konto, Agreement, Zähler, Preisquellen, Register, erkannte Einheit, Tagesstatus |

### Zusätzlich umgesetzt (über den Plan hinaus)

* **Automatischer Backfill** mit Fensterspeicher (`reading_windows`) – vom Nutzer angeregt.
* **Preis-Historie**: der Kundenpreis der Vergangenheit wird **exakt** rekonstruiert
  als EPEX + Basiskomponente (17,35 ct/kWh netto) – nicht als gemittelter Aufschlag.
* **Auth-Robustheit**: neben dem rohen JWT wird automatisch `Bearer <token>` probiert,
  erst danach wird das Token erneuert.
* **Verbrauchsdaten**: kommen aus `property(id).measurements(RAW_INTERVAL)` – echte
  15-Minuten-Intervalle des Smart-Meter-Gateways. Die Zählerregister werden nicht
  mehr ausgewertet.
* **Anmeldung, Passkeys, PWA und Web-Push** – siehe `CHANGELOG.md` 1.1.0/1.2.0.
* **Container läuft als nicht-root User** (uid/gid 1000) mit Healthcheck.
* **Preiszusammensetzung**: Beim Dynamic Octopus besteht der Arbeitspreis aus
  mehreren Raten je Zeitfenster (Basiskomponente 20,65 ct/kWh + dynamische
  Komponente). Die API liefert sie einzeln; die App speichert jede Rate
  (`rate_name`) und summiert sie zum effektiven Kundenpreis. Grundpreis: 10,21 €/Monat
  kommen aus `standingChargeGrossRateInformation` (EUR- oder Cent-Werte werden
  erkannt).
* **Token-Lebenszyklus**: `oenergy login` meldet einmalig an und legt ein lang
  gültiges Refresh-Token ab – danach kann das Passwort aus der `.env` raus.
  `oenergy token-info` zeigt die Laufzeiten (JWT-`exp` + `refreshExpiresIn`).
  Im deutschen Portal gibt es keinen API-Key (dort wird OAuth genutzt).
* **Build-Backend** explizit auf `module-name = "oenergy"` gesetzt (uv_build leitet sonst
  `o_energy` ab).
* **Demo-Daten** lassen sich mit `oenergy reset --yes` entfernen; Dashboard und
  Kostenrechnung filtern immer auf den aktiven Zähler.
* **UI bei fehlenden Daten**: keine erfundenen 0,00 kWh, sondern „– kWh“ mit
  Hinweis auf den Lieferverzug; Dashboard zeigt bei leerer Datenbank einen
  Einrichtungs-Hinweis.
* **Container-Compose**: `SCHEDULER_ENABLED` kommt aus der `.env` (Default: an),
  `DB_URL` zeigt immer auf das Volume.

### S0 – Live-Befunde am echten Konto (2026-11-15)

| Frage | Befund |
|---|---|
| Welche Queries sind für Kundenkonten freigegeben? | `viewer`, `account.properties`, `agreement(id)`, `epexDayAheadPrices`, `property(id).measurements`, `account.bills`. **Nicht** autorisiert: `electricityMeters`, `electricityMeterReadings`, `agreementRates`, `estimatedSupplyPointReadings` → die App nutzt die freigegebenen Alternativen |
| Wo kommen die Verbrauchsdaten her? | `property(id).measurements(utilityFilters: [{electricityFilters: {readingFrequencyType: RAW_INTERVAL}}])` liefert echte 15-Minuten-Intervalle (Quelle „Amphio“, Einheit kWh) – 15-Min-Aggregation ist nicht unterstützt, `RAW_INTERVAL` schon |
| Wo kommen die Preise her? | `agreement(id).unitRateForecast` (15-Min-Slots, heute + Zukunft) und `unitRateInformation.rates` (Basiskomponente). `agreementRates` ist gesperrt |
| Wie hängt der Preis zusammen? | **Kundenpreis = EPEX + 17,35 ct/kWh netto** (Basiskomponente, brutto 20,65 ct/kWh) – exakt in jedem Slot verifiziert. So lassen sich die historischen Preise aus EPEX rekonstruieren |
| Grundpreis | `standingChargeGrossRateInformation.grossRate` = 10,21258 **EUR**/Monat → 1021 ct (Werte können EUR oder Cent sein) |
| Token-Laufzeit | Access-Token = JWT, **1 Stunde** (`exp = iat + 3600`); Refresh-Token = **20 Tage** (`refreshExpiresIn` in Millisekunden); Long-Lived-Refresh-Token ist für Kundenkonten nicht freigeschaltet |
| API-Key | Gibt es im deutschen Portal nicht – dort läuft ein OAuth-Flow (`/api/auth/kraken-oauth-start`). Einmalig `oenergy login`, danach reicht das Refresh-Token |
| API-Limit | Rate Limit aktiv („too many requests“): Client drosselt auf 1,2 s/Request und wartet bei „internal error“ bis zu 60 s |

### Erledigt (die offenen Fragen sind beantwortet)

1. **Zugangsdaten** stehen in der `.env`; `oenergy diagnose` prüft live, was der
   Account liefert. Das Passwort wird nach dem ersten `oenergy login` nicht mehr
   gebraucht (20-Tage-Refresh-Token in der Datenbank).
2. **Preisreihe**: `agreementRates` ist gesperrt → `unitRateForecast` (heute/Zukunft)
   plus EPEX für die Historie (rekonstruiert als EPEX + Basiskomponente).
3. **Register/Einheit**: die Intervalle kommen direkt in kWh aus dem Gateway
   („Amphio“), ein Register ist nicht nötig – `ENERGY_UNIT` bleibt auf `auto`.
4. **MELO/ACCOUNT**: der Lieferpunkt und die MaLo kommen aus `account.properties`;
   `ACCOUNT_NUMBER`/`MELO_NUMBER` werden nur gebraucht, wenn mehrere Lieferpunkte
   im Konto liegen.

### Nachtrag (18.11.2026) – Betrieb im Alltag

Nach drei Tagen Dauerbetrieb: die Zählerdaten kommen gegen **08:00 Uhr**, der
stündliche Job holt sie um **08:07**, die Push-Nachricht geht **6 Sekunden**
später raus. Keine Fehler in den Sync-Läufen. Die Tageswerte sind konsistent
(Intervall-Summe = Tageswert, 96/96 Slots, als endgültig markiert).

---

## 9. Plan: MCP-Schnittstelle (FastMCP) – *noch nicht umgesetzt*

> **Stand 18.11.2026:** Idee vom Maintainer, hier als Plan festgehalten. Es ist
> **noch nichts implementiert** – der nächste Schritt ist die Entscheidung
> (siehe 9.1) und danach die Umsetzung in den Schritten 9.3–9.7.

### 9.1 Die Idee und meine Bewertung

**Idee:** Eine MCP-Schnittstelle (Model Context Protocol) auf Basis von **FastMCP**,
damit KI-Agenten den Datenstand abfragen können: Verbrauch, Kosten, Preise,
Konto, Zähler, „wie viel habe ich letzten Monat verbraucht?", „wann war der
teuerste Slot?", „was kostet mich der Strom diesen Monat?".

**Bewertung: gute Idee, mit Auflagen umgesetzt.** Die App hält die Fachlogik
bereits sauber in `services/` (ohne HTTP-Bezug) – das ist genau die Schicht, die
ein MCP-Server aufrufen kann. Es kommt also **keine** doppelte Logik dazu, nur
eine zweite Tür zur vorhandenen.

Auflagen aus der Sicherheitsperspektive (das Projekt ist inzwischen geprüft,
siehe `REVIEW_1.md`/`REVIEW_1_OK.md`):

1. **Der MCP-Server gibt Daten preis, die die Web-Oberfläche schützt.** Er
   braucht daher ein **eigenes, strengeres Auth-Verfahren** – nicht die
   Web-Sitzung, sondern einen **eigenen Token** (Bearer), der nur für den
   MCP-Zugang gilt.
2. **Read-only.** Der MCP-Server darf **ausschließlich lesen** – keine Syncs
   auslösen, keine Daten ändern, keine Preise zurückschreiben. Sonst wäre er
   ein Weg, den Web-Zugang zu umgehen.
3. **Keine Geheimnisse im Antworttext.** Kein Access-/Refresh-Token, kein
   Session-Secret, keine vollständige E-Mail-Adresse, keine Kontonummer/
   Seriennummern im Klartext. Aggregierte Werte ja, Identifikatoren nur
   maskiert.
4. **Isolierter Pfad.** Der MCP-Endpunkt läuft im Web-Prozess, aber unter
   einem eigenen Pfad (`/mcp`) mit **eigener Middleware** (eigener Token, eigenes
   Rate-Limit, eigenes Body-Limit) – getrennt vom Web-Passwort und vom
   `X-OEnergy-Token`. FastMCP lässt sich dafür per `app.mount()` in die
   bestehende App hängen; ein eigener Prozess/Port bleibt als Alternative
   möglich (Ingress-Regel im Tunnel).
5. **Rate-Limit und Body-Limit** wie beim Web-Teil.
6. **Opt-in.** Ohne gesetzten Token läuft der MCP-Server gar nicht (kein
   versehentlich offener Zugang).

### 9.2 Technische Eckpunkte (mit Context7 geprüft)

* **FastMCP 4.2.1** (Apache-2.0, Python ≥ 3.11, 9 Abhängigkeiten) – aktuell
  unter `prefecthq/fastmcp`; die `gofastmcp`-Doku ist die ältere Linie.
* **Transport:** `mcp.run(transport="http", stateless_http=True)` – der
  Streamable-HTTP-Transport. **Stateless** ist hier richtig: kein
  Session-Affinität, skaliert hinter einem Proxy, einfacher zu betreiben.
* **Eigene Routen** sind mit `@mcp.custom_route` möglich (z. B. für einen
  Healthcheck) – die Auth-Middleware läuft als Starlette-Middleware **vor** der
  MCP-App.
* **Tools** mit `@mcp.tool`, **Ressourcen** mit `@mcp.resource` (statisch) und
  `@mcp.resource("scheme://{param}")` (Template). Für Zeitreihen sind Tools
  mit Parametern die bessere Wahl; Resources eignen sich für den aktuellen
  Stand.
* **`Context`** erlaubt Logging und Progress-Meldungen an den Client.
* **Wichtig:** FastMCP 4.x hat **keine eingebaute Bearer-Token-Authentifizierung**
  für den Server – die Absicherung muss über eine **eigene Starlette-Middleware**
  erfolgen (Header `Authorization: Bearer <token>`), analog zum
  `X-OEnergy-Token`-Mechanismus der Web-App.

### 9.3 Geplante Tools (erster Wurf)

| Tool | Frage, die sie beantwortet |
|---|---|
| `status()` | Wie aktuell sind die Daten? Letzter Sync je Job, Datenstand, offene Lücken |
| `consumption(days=7)` | Verbrauch je Tag (kWh), endgültig/provisorisch, mit und ohne Grundpreis |
| `costs(days=30)` | Kosten je Tag und Summe, Grundpreis, Monatsprognose mit Unsicherheit |
| `prices(hours=48)` | Preisverlauf (brutto), aktueller und nächster Slot, EPEX-Referenz |
| `cheapest_windows(days=7)` | Die günstigsten Zeitfenster (Tag × 15-Minuten-Slot) – die klassische Frage |
| `price_at(when)` | Der Preis zu einem bestimmten Zeitpunkt |
| `monthly_overview(month=…)` | Verbrauch, Kosten, Durchschnittspreis eines Monats |
| `account()` | Tarif, Grundpreis, USt, Kontostand – **ohne** Kontonummer und Adressen |

Als **Ressourcen** zusätzlich: `energy://status`, `energy://today`, `energy://month`.

### 9.4 Umsetzungsschritte

| # | Schritt | Inhalt | Fertig, wenn |
|---|---|---|---|
| **M0** | Entscheidung | Transport, Token-Verfahren, Werkzeugkatalog, Freigabe durch den Maintainer | dieser Plan ist abgenommen |
| **M1** | Abhängigkeit | `fastmcp` als optionale Extra (`[project.optional-dependencies] mcp`), damit der Kern schlank bleibt | `uv sync --extra mcp` läuft, Kern ohne fastmcp installierbar |
| **M2** | Service-Schicht | `services/mcp_tools.py`: reine Funktionen, die `dashboard`/`costs`/`pricing` aufrufen und **maskierte**, aggregierte Rückgaben liefern (kein HTTP, keine MCP-Typen) | Unit-Tests ohne MCP-Abhängigkeit grün |
| **M3** | MCP-Server | `src/oenergy/mcp_server.py`: FastMCP-Instanz, die Tools und Ressourcen aus M2 registriert | `uv run oenergy mcp` startet, `tools/list` zeigt die Katalog |
| **M4** | Authentifizierung | Starlette-Middleware: `Authorization: Bearer <MCP_TOKEN>` – zeitkonstanter Vergleich, 401 ohne Token, Rate-Limit | ohne Token 401, mit Token 200 (auch im Test) |
| **M5** | Betrieb | MCP läuft **im Web-Prozess** unter `/mcp` (`app.mount`); eigener Token in der `.env` (`MCP_TOKEN`, per `oenergy generate-mcp-token`); optional ein separates Compose-Profil `mcp` mit eigenem Port, falls der MCP-Zugang doch getrennt laufen soll | `/mcp` antwortet mit Token, ohne Token 401 |
| **M6** | Tests | Tests für die Werkzeugfunktionen (M2) und für die Auth-Middleware (M4), ein Test mit einem echten MCP-Client (FastMCP-Client gegen den Server) | `pytest` grün |
| **M7** | Doku | README-Abschnitt „MCP-Schnittstelle“, SECURITY.md (Was der MCP-Zugang darf), AGENTS.md | Doku beschreibt den Ist-Zustand |
| **M8** | Veröffentlichung | Der Tunnel braucht **keine** zusätzliche Regel – `/mcp` liegt auf demselben Host wie die Web-App. Optional eine eigene Subdomain (`mcp.strom.tbaer.de`) per Ingress-Regel | von außen erreichbar, mit Token |

### 9.5 Offene Punkte für die Entscheidung

* **Port/Pfad:** **entschieden: `/mcp` unter dem bestehenden Port.** Der
  Cloudflare-Tunnel kann zwar auch andere Ports veröffentlichen (Ingress-Regeln
  mit `service: http://localhost:8001`), aber der MCP-Endpunkt lässt sich
  **auch im selben Web-Prozess** isoliert betreiben: FastMCP liefert mit
  `mcp.http_app()` eine ASGI-App, die per `app.mount("/mcp", mcp_app)` in die
  bestehende FastAPI-App gehängt wird (mit `lifespan=mcp_app.lifespan`, sonst
  initialisiert der Session-Manager nicht).
  *Vorteil:* ein Prozess, eine SQLite-Verbindung, keine doppelte
  Scheduler-/DB-Logik, und der Tunnel braucht keine zusätzliche Regel.
  *Damit die Isolation bleibt*, bekommt der MCP-Pfad eine **eigene
  Middleware**: eigener Bearer-Token, eigener Rate-Limit, eigenes Body-Limit –
  getrennt vom Web-Passwort und vom `X-OEnergy-Token`.
  *Hinweis:* FastMCP 4.x bringt keine eingebaute Bearer-Authentifizierung mit –
  die Absicherung kommt aus der eigenen Middleware (siehe 9.2).
* **Wie kommt der Agent an den Token?** Per `.env` (Datei) oder über einen
  MCP-Client mit eigener Anmeldung? (Empfehlung: `.env` – passend zum
  `AUTH_BYPASS_TOKEN`-Muster.)
* **Soll der MCP-Server die Daten bei Bedarf aktualisieren** (also einen Sync
  auslösen)? (Empfehlung: **nein** – read-only; die Daten sind spätestens
  15 Minuten alt.)
* **Ein Token oder mehrere?** (Empfehlung: **einer**, mit Rotation über
  `oenergy generate-mcp-token`.)

### 9.6 Risiken

| Risiko | Gegenmaßnahme |
|---|---|
| Der MCP-Zugang wird zum Datenabfluss (Adresse, Kontonummer) | Maskierung in der Service-Schicht (M2), keine Identifikatoren in den Antworten |
| Der MCP-Pfad umgeht die Web-Anmeldung | eigene Middleware für `/mcp` mit eigenem Token – der Web-Passwort-Login gilt dort nicht |
| Der MCP-Server umgeht die Web-Anmeldung | eigener Token, eigener Prozess, eigener Port, Rate-Limit |
| Der MCP-Server wird zum Schreibpfad | strikt read-only, keine Tools mit Schreibwirkung |
| Der Tunnel macht den MCP-Port öffentlich | Token-Pflicht, Rate-Limit, Healthcheck ohne Daten |
| Abhängigkeit `fastmcp` bläht das Projekt auf | als **optionales Extra**, nicht als Pflicht |
