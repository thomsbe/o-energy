# o-energy

> **Version 0.8.0 (Beta)** – Änderungen siehe [CHANGELOG.md](CHANGELOG.md). Lizenz: MIT.

Eigene Web-App für **Octopus Energy Germany** (Dynamic Octopus): Die App fragt die
Kraken-GraphQL-API regelmäßig ab, speichert Verbrauch, Zählerstände, Preise und
Kosten in SQLite und zeigt alles in einer schnellen HTMX-WebUI mit Chart.js.

```
┌────────────┐   GraphQL/REST    ┌──────────────┐   SQLite    ┌──────────────┐
│ Kraken API │ ◀───────────────▶ │ o-energy     │ ◀─────────▶ │ o-energy.db │
│ (Octopus)  │   alle 15 Min     │ (FastAPI)    │             │             │
└────────────┘                   └──────────────┘             └──────────────┘
```

## Funktionsumfang

* **Preise**: aktueller Kundenpreis (brutto), nächster Preis, EPEX-Spotpreis als
  Referenz, Preisverlauf 48 h, Preis-Matrix (Tag × 15-Minuten-Slot) mit Farbverlauf
  von grün (günstig) über gelb (normal) bis rot (teuer), günstigste Zeitfenster,
  CSV-Export.
  Beim Dynamic Octopus besteht der Arbeitspreis aus mehreren Raten je Zeitfenster
  (z. B. Basiskomponente + dynamische Komponente) – die App summiert sie zum
  tatsächlichen Kundenpreis und zeigt die Bestandteile an.
* **Verbrauch**: 15-Minuten-Intervalle, Tages-/Monatswerte, Zählerstände (roh),
  Zählerregister.
* **Kosten**: Energiekosten (Verbrauch × Zeitpreis) + Grundpreis, Tages-/Monats-
  /Jahreswerte, Monatsprognose, Abgleich mit den Rechnungen aus dem Kundenkonto.
* **24-h-Latenz**: Die Werte des Zähleranbieters kommen erst am Folgetag gegen
  08:00 Uhr. Die App kennzeichnet jeden Tag als `final`, `provisorisch`,
  `ausstehend` oder `warten` und rechnet nur vollständige Tage als endgültig.
* **Automatischer Backfill**: Die App merkt sich, welche Zeitfenster bereits
  abgefragt wurden, und fragt nur Lücken nach – inklusive automatischer
  Nachlieferung der Zählerdaten.

## Schnellstart (lokal)

```bash
# 1. Abhängigkeiten
uv sync

# 2. Konfiguration
cp .env.example .env
$EDITOR .env          # OCTOPUS_EMAIL / OCTOPUS_PASSWORD eintragen

# 3. Datenbank anlegen
uv run alembic upgrade head

# 4. Starten
uv run oenergy serve           # http://127.0.0.1:8000
```

Ohne echte Zugangsdaten lässt sich die Oberfläche mit Demo-Daten testen:

```bash
uv run oenergy seed --days 30
```

Vor dem ersten echten Sync sollten die Demo-Daten wieder entfernt werden:

```bash
uv run oenergy reset --yes
```

### Zugangsdaten und Token-Laufzeit

Die App meldet sich mit dem Portal-Login an:

```graphql
mutation { obtainKrakenToken(input: {email: "...", password: "..."}) { token refreshToken refreshExpiresIn } }
```

Im Kundenportal von Octopus Energy Germany gibt es **keinen API-Key** – dort wird
ein OAuth-Flow verwendet. Deshalb läuft die App über den Login:

```bash
uv run oenergy login        # einmalig anmelden
uv run oenergy token-info   # Laufzeiten anzeigen
```

* Das **Access-Token** ist ein JWT und **eine Stunde** gültig (`exp = iat + 3600`);
  die App liest das `exp`-Claim und erneuert das Token selbstständig vor Ablauf.
* Das **Refresh-Token** ist **20 Tage** gültig (`refreshExpiresIn` kommt in
  Millisekunden). Damit läuft die App ohne Passwort weiter.
* Ein *länger* gültiges Refresh-Token (`obtainLongLivedRefreshToken`) ist für
  Kundenkonten nicht freigeschaltet – die API antwortet hier mit „viewer is not
  authorized“. Die App versucht es einmalig und nutzt danach das 20-Tage-Token.
* `token-info` zeigt, wie lange das Access-Token und das Refresh-Token noch
  gültig sind. Läuft das Refresh-Token ab, genügt ein erneutes `oenergy login`.

**Nach dem ersten Login kann `OCTOPUS_PASSWORD` aus der `.env` entfernt werden** –
das Refresh-Token in der Datenbank übernimmt. Der Token liegt in der lokalen
SQLite-Datei (`data/o-energy.db`), also nur auf dem eigenen System.

Das Refresh-Token ist **20 Tage** gültig. Danach braucht die App einen neuen
Login:

| Variante | Verhalten |
|---|---|
| Passwort **in** der `.env` | läuft unbegrenzt weiter – die App meldet sich nach 20 Tagen selbst neu an |
| Passwort **raus** aus der `.env` | 20 Tage Ruhe, danach einmal `uv run oenergy login` (Passwort wird dann wieder gebraucht) |

Die verbleibende Laufzeit zeigt die Seite **System** (und `oenergy token-info`);
ist das Refresh-Token abgelaufen, warnt die App beim Start und im Log.

### Datenquellen (live verifiziert)

Für Kundenkonten sind nur wenige Queries freigeschaltet – die App nutzt genau diese:

| Daten | Query | Status |
|---|---|---|
| Konto, Tarif, Zähler | `viewer`, `account.properties` (MaLo + Meter) | frei |
| Aktueller/Zukunftspreis | `agreement(id).unitRateForecast` (15-Min-Slots) | frei |
| Basiskomponente | `agreement(id).unitRateInformation.rates` | frei |
| Börsenpreis | `epexDayAheadPrices` | frei |
| Verbrauch | `property(id).measurements(readingFrequencyType: RAW_INTERVAL)` | frei |
| Rechnungen | `account.bills` | frei |
| `electricityMeters`, `electricityMeterReadings`, `agreementRates` | – | **nicht autorisiert** |

Beim Dynamic Octopus gilt: **Kundenpreis = EPEX-Spotpreis + Basiskomponente**
(17,35 ct/kWh netto / 20,65 ct/kWh brutto). Die App rechnet die historischen
Preise deshalb exakt aus EPEX + Basiskomponente, während die Forecast-Slots für
heute und die Zukunft direkt übernommen werden.

### Erster Blick auf die API

```bash
uv run oenergy diagnose
```

Der Diagnose-Lauf meldet, was der Account tatsächlich liefert: Konto, Agreement
(Tarif, dynamisch/klassisch), Zähler (Nummer, Register), Verfügbarkeit von
`agreementRates`, `unitRateForecast` und EPEX, Anzahl der Zählerdaten, erkannte
Einheit (kWh/Wh) sowie den Status der letzten Tage. Die Rohdaten landen optional
mit `--out diagnose.json` als JSON.

Alternativ gibt `uv run oenergy probe --out probe.json` die kompletten
Rohantworten der API aus.

## Ersteinrichtung (Checkliste)

1. `cp .env.example .env` und Zugangsdaten eintragen (`OCTOPUS_EMAIL`, `OCTOPUS_PASSWORD`).
2. `chmod 600 .env` – die Datei enthält das Passwort.
3. `uv run alembic upgrade head` (lokal) bzw. `docker compose up -d` (Container).
4. `uv run oenergy login` – einmalig anmelden (danach kann das Passwort aus der `.env` raus).
5. `uv run oenergy diagnose` – prüfen, ob Konto, Tarif, Zähler und Preise geliefert werden.
6. `uv run oenergy sync all` – erster Abruf (30 Tage Historie + Preise).
7. `SCHEDULER_ENABLED=true` in der `.env` setzen, damit die Hintergrundjobs laufen.
8. Optional: `./scripts/backup.sh` in einen Cronjob aufnehmen.

## Betrieb mit Docker

```bash
cp .env.example .env && $EDITOR .env
docker compose up -d
docker compose logs -f
```

* Web-App: http://localhost:8000
* Datenbank: `./data/o-energy.db` (Volume `./data:/data`)
* Der Container läuft als nicht-root User (uid/gid 1000) - der Host-Ordner
  `./data` muss dem Benutzer 1000 gehören.
* Optionaler Worker ohne Web-Server:
  `docker compose --profile worker up -d oenergy-sync`

### Backup

```bash
./scripts/backup.sh            # erzeugt data/backups/o-energy-<zeit>.db
```

### CLI im Überblick

| Befehl | Zweck |
|---|---|
| `oenergy serve` | Web-App starten |
| `oenergy login` | einmalig anmelden, Tokens speichern |
| `oenergy token-info` | Laufzeit der gespeicherten Tokens anzeigen |
| `oenergy sync [job\|all]` | Synchronisation ausführen (`account`, `prices`, `readings`, `finalize`, `bills`) |
| `oenergy diagnose` | Prüfen, welche Daten der Account liefert |
| `oenergy probe` | Rohantworten der API dumpen |
| `oenergy seed --days 30` | Demo-Daten erzeugen |
| `oenergy reset --yes` | Datenbank leeren |
| `oenergy init-db` | Schema anlegen |
| `oenergy jobs` | Job-Zeitplan anzeigen |

## Jobs

| Job | Rhythmus | Aufgabe |
|---|---|---|
| `prices` | alle 15 min | Kundenpreise (`agreementRates`, `unitRateForecast`) + EPEX |
| `readings` | stündlich (Minute 7) | Zählerdaten, nur für offene Lücken |
| `finalize` | täglich 08:17 | Tagesabschluss nach der Lieferung um ~08:00 |
| `bills` | täglich 06:12 | Rechnungen und Kontostand |
| `account` | alle 12 h | Konto, Tarif, Zähler |

Manuell starten:

```bash
uv run oenergy sync all
uv run oenergy sync readings
uv run oenergy sync prices --history-days 30   # Börsenpreise für die Historie nachladen
```

## Konfiguration (`.env`)

| Variable | Standard | Bedeutung |
|---|---|---|
| `OCTOPUS_EMAIL` / `OCTOPUS_PASSWORD` | – | Portal-Login |
| `OCTOPUS_API_KEY` | – | Alternative zum Login |
| `API_URL` | `https://api.oeg-kraken.energy/v1/graphql/` | Kraken-Endpunkt |
| `DB_URL` | `sqlite:///./data/o-energy.db` | SQLite-Datei |
| `TIMEZONE` | `Europe/Berlin` | Zeitzone der Auswertung |
| `POLL_INTERVAL_MINUTES` | `15` | Intervall der Preisabfrage |
| `READINGS_BACKFILL_DAYS` | `3` | Wie weit zurück nachgeladen wird |
| `READINGS_HISTORY_DAYS` | `30` | Historie beim ersten Abruf |
| `READINGS_MAX_WINDOW_DAYS` | `7` | Maximale Größe einer Einzelabfrage |
| `GAP_RETRY_MINUTES` | `60` | Wartezeit, bevor eine leere Lücke erneut geprüft wird |
| `SLOT_MINUTES` | `15` | Länge eines Verbrauchsintervalls |
| `ENERGY_UNIT` | `auto` | `auto`, `kwh` oder `wh` (Zählregister) |
| `API_MIN_INTERVAL_SECONDS` | `1.2` | Mindestabstand zwischen zwei API-Requests (Rate-Limit) |
| `LOG_LEVEL` | `INFO` | Logstufe (`DEBUG`, `INFO`, `WARNING`, …) |
| `LOG_FILE` | – | zusätzlich in eine Datei schreiben (mit Rotation) |
| `LOG_ROTATION` / `LOG_RETENTION` | `10 MB` / `14 days` | Rotation der Logdatei |
| `PRICE_CHEAP_LIMIT` | `26.0` | ct/kWh brutto – bis hierher ist der Preis „günstig“ (grün) |
| `PRICE_EXPENSIVE_LIMIT` | `40.0` | ct/kWh brutto – ab hier ist der Preis „teuer“ (rot) |
| `SCHEDULER_ENABLED` | `true` | Hintergrundjobs im Web-Prozess |

## Logging

Die App nutzt [loguru](https://github.com/Delgan/loguru): farbige Ausgabe auf der
Konsole, optional eine rotierende Logdatei und ein einheitliches Format – auch
für die Logs von uvicorn, httpx, SQLAlchemy und APScheduler (diese werden
abgefangen und weitergeleitet).

```
14:02:36 | INFO     | oenergy.services.prices:_sync_epex:307 - Stored 136 EPEX prices
```

Im Docker-Container landet das Log zusätzlich in `/data/logs/o-energy.log`
(im Volume, mit Rotation).

## Projektstruktur

```
src/oenergy/
├── api/          # GraphQL-Client, Token-Management, Queries
├── db/           # SQLAlchemy-Modelle, Session
├── services/     # sync, preise, verbrauch, kosten, backfill, dashboard
├── web/          # FastAPI-Routen, Jinja-Templates
├── static/       # CSS, JS (Chart.js), Vendor-Assets (htmx, Chart.js)
├── jobs.py       # Scheduler
├── cli.py        # oenergy serve|sync|login|token-info|whoami|diagnose|probe|seed|reset|jobs
└── main.py       # FastAPI-App
```

## Entwicklung

```bash
uv run pytest            # Testsuite
uv run ruff check .      # Linting
uv run ruff format .     # Formatierung
uv run alembic revision --autogenerate -m "…"   # Migration
```

## Lizenz

MIT – siehe [LICENSE](LICENSE).

## Hinweise

* Der Kraken-Endpunkt ist nicht öffentlich dokumentiert; die verwendeten Queries
  wurden live per GraphQL-Introspection verifiziert (siehe `PLAN.md`).
* Zählerdaten, Preise und Kosten sind berechnete Werte – Abweichungen zur
  Rechnung des Versorgers sind möglich (Rundung, Ablesung, Steuern).
* Die Zugangsdaten gehören in die `.env` (nicht ins Repository).
