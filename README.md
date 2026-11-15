# o-energy

> **Version 1.2.0** – Änderungen siehe [CHANGELOG.md](CHANGELOG.md). Lizenz: MIT.

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
  Zählerregister. Im Diagramm „Verbrauch und Preis“ liegt die Preislinie über dem
  Verbrauchsprofil der letzten zwei Tage – so sieht man, zu welchem Preis
  verbraucht wurde. Darunter lässt sich der Tagesverbrauch Monat für Monat
  durchblättern.
* **Kosten**: Energiekosten (Verbrauch × Zeitpreis) + Grundpreis, Tages-/Monats-
  /Jahreswerte, Monatsprognose, Abgleich mit den Rechnungen aus dem Kundenkonto.
* **24-h-Latenz**: Die Werte des Zähleranbieters kommen erst am Folgetag gegen
  08:00 Uhr. Die App kennzeichnet jeden Tag als `final`, `provisorisch`,
  `ausstehend` oder `warten` und rechnet nur vollständige Tage als endgültig.
* **Automatischer Backfill**: Die App merkt sich, welche Zeitfenster bereits
  abgefragt wurden, und fragt nur Lücken nach – inklusive automatischer
  Nachlieferung der Zählerdaten. Die Historie wird schrittweise bis zum Ende
  zurückgeholt; ist das Ende erreicht, wird es gemerkt und nicht weiter gefragt.

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

### Backup und Wiederherstellung

```bash
./scripts/backup.sh            # SQLite-Kopie + JSON-Export nach data/backups/
```

Der JSON-Export ist **schemaunabhängig**: er enthält Verbrauch, Preise,
Tageswerte, Rechnungen und Vertragsdaten als einfache Datensätze und kann
unabhängig von der Datenbankversion wieder eingespielt werden.

```bash
uv run oenergy export --out data/backups/sicherung.json.gz   # Export
uv run oenergy import data/backups/sicherung.json.gz          # Import
```

Der Import ist **idempotent**: vorhandene Datensätze werden anhand ihrer
Schlüssel aktualisiert, nichts wird doppelt angelegt. Danach werden Kosten und
Tagesstatus neu berechnet. Alternativ gibt es den Download direkt aus der
Oberfläche (Seite *System* → *Export herunterladen*).

### CLI im Überblick

| Befehl | Zweck |
|---|---|
| `oenergy serve` | Web-App starten |
| `oenergy login` | einmalig anmelden, Tokens speichern |
| `oenergy token-info` | Laufzeit der gespeicherten Tokens anzeigen |
| `oenergy sync [job\|all]` | Synchronisation ausführen (`account`, `prices`, `readings`, `finalize`, `bills`) |
| `oenergy diagnose` | Prüfen, welche Daten der Account liefert |
| `oenergy probe` | Rohantworten der API dumpen |
| `oenergy export --out DATEI` | Datenbestand als JSON exportieren |
| `oenergy import DATEI` | JSON-Export zurückspielen (idempotent) |
| `oenergy seed --days 30` | Demo-Daten erzeugen |
| `oenergy reset --yes` | Datenbank leeren |
| `oenergy init-db` | Schema anlegen |
| `oenergy jobs` | Job-Zeitplan anzeigen |

## Anmeldung

Die App ist **ohne Anmeldung** nutzbar – für den Betrieb auf einem offenen
Server sollte sie aber geschützt werden. Es genügt ein Benutzer:

```bash
uv run oenergy set-password            # fragt das Passwort ab und gibt den Hash aus
```

Den ausgegebenen Hash in die `.env` eintragen:

> **Wichtig beim Docker Compose:** Die `.env` wird von Compose interpoliert – ein
> Argon2-Hash enthält `$`-Zeichen und muss dort als `$$` escaped werden, sonst
> kommt er im Container zerstört an und die Anmeldung schlägt immer fehl:
>
> ```bash
> # in der .env
> AUTH_PASSWORD_HASH=$$argon2id$$v=19$$m=65536,t=3,p=4$$…$$…
> ```


```bash
AUTH_ENABLED=true
AUTH_USERNAME=admin
AUTH_PASSWORD_HASH=$argon2id$v=19$m=65536,t=3,p=4$...
```

Danach ist die App nur mit Anmeldung erreichbar (`/login`); die Sitzung liegt in
einem signierten Cookie (`oenergy_session`, 14 Tage). Das Secret zum Signieren
wird beim ersten Start erzeugt und in der Datenbank abgelegt – es bleibt also
über Container-Neustarts hinweg gültig.

| Variable | Standard | Bedeutung |
|---|---|---|
| `AUTH_ENABLED` | `true` | Anmeldung aktiv (wirkt nur, wenn ein Hash gesetzt ist) |
| `AUTH_USERNAME` | `admin` | Benutzername |
| `AUTH_PASSWORD_HASH` | – | Argon2id-Hash (siehe `oenergy set-password`) |
| `AUTH_SECRET` | – | optional: eigenes Secret für die Sitzungs-Cookies |
| `AUTH_LOCAL_BYPASS` | `true` | Zugriffe ohne Anmeldung, wenn **Host und Quelle** lokal sind |
| `AUTH_BYPASS_TOKEN` | – | optionales Secret-Header `X-OEnergy-Token` für Skripte |
| `SESSION_MAX_AGE_DAYS` | `14` | Gültigkeit der Sitzung |
| `COOKIE_SECURE` | `false` | auf `true` setzen, wenn die App nur über HTTPS erreichbar ist |

Solange kein Hash gesetzt ist, bleibt die App offen und weist in der Kopfzeile
mit einem gelben Hinweis darauf hin. **Für einen Betrieb im Internet
(Cloudflare Tunnel, Portfreigabe) muss ein Passwort gesetzt sein** – sonst sind
Verbrauchsdaten, Konto- und Zählernummer sowie die Adresse für jeden erreichbar,
der die URL kennt.

### Benachrichtigungen (Web Push)

Auf der Seite *System* lässt sich eine Push-Benachrichtigung aktivieren. Meldet
der Netzbetreiber die Daten des Vortages, schickt die App eine Nachricht an das
Telefon („Neue Daten vom 14.11.: 8,5 kWh für 4,40 €“). Der Button *Testnachricht
senden* prüft die Einrichtung.

Voraussetzung ist ein **sicherer Kontext** – also `https://` (der Tunnel) oder
`localhost`. Auf iOS müssen die Mitteilungen für die zum Home-Bildschirm
hinzugefügte App in den iOS-Einstellungen erlaubt werden.

### Zugriff ohne Anmeldung (für Tests)

```bash
uv run oenergy generate-token --write-env   # schreibt AUTH_BYPASS_TOKEN in die .env
curl -H "X-OEnergy-Token: $(grep BYPASS_TOKEN .env | cut -d= -f2-)" http://127.0.0.1:8000/
```

Der Token steht **nur** in der `.env` (im Repository ignoriert) und wird nie
ausgegeben. Alternativ greift `AUTH_LOCAL_BYPASS`: Aufrufe, bei denen **sowohl**
der Host als auch die Quelle lokal sind (`localhost`/`127.0.0.1`), kommen ohne
Anmeldung durch – ein Aufruf über den Cloudflare-Tunnel also **nicht**, weil
dort der Host die öffentliche Adresse ist.

### Passkeys (WebAuthn)

Zusätzlich zum Passwort gibt es **Passkeys**: ein Schlüssel, der im Browser oder
auf dem Gerät liegt und per Fingerabdruck, Gesicht oder PIN bestätigt wird.
Registrierung passiert auf der Seite *System* (angemeldet), die Anmeldung über
den Button auf der Login-Seite – ohne Benutzername, es gibt nur einen Benutzer.

Für den Browser-/Server-Abgleich ist `WEBAUTHN_ORIGIN` wichtig (die Origin, unter
der die App erreichbar ist); die RP-ID wird daraus abgeleitet oder explizit
gesetzt:

```bash
WEBAUTHN_ORIGIN=https://strom.example.de
WEBAUTHN_RP_ID=strom.example.de
```

Die Umsetzung nutzt `py_webauthn` (WebAuthn/FIDO2) ohne Cloudflare-Zwang.

> **Wichtig:** WebAuthn funktioniert nur in einem sicheren Kontext – also über
> `https://` oder `localhost`. Über `http://homeservices:8000` bietet der Browser
> keine Passkeys an; dort bleibt nur das Passwort. Mit dem Cloudflare-Tunnel
> (`https://…`) funktionieren die Passkeys.

## Wann kommen die Daten?

Der Netzbetreiber liefert die Zählerdaten des Vortags **gegen 08:00 Uhr** – aber
nicht garantiert pünktlich. Die App ist darauf eingerichtet:

| Zeitpunkt | Was passiert |
|---|---|
| vor 08:00 | Der Tag steht auf *warten* (`awaiting`) |
| ab 08:00 | Der stündliche Job holt die Daten, sobald sie da sind (Sperrfenster 30 Minuten) |
| 08:17 | Der *Tagesabschluss* holt die Daten selbst ab, rechnet Kosten und Tagesstatus neu und schickt die Push-Nachricht |
| 10:00 | Spätestens jetzt wird ein Tag auch ohne vollständige Daten als endgültig markiert (2 Stunden Toleranz) |

Kommt die Lieferung erst um 11 Uhr, tauchen die Daten beim nächsten stündlichen
Lauf (11:07) auf und werden dann endgültig markiert.

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
| `READINGS_HISTORY_CHUNK_DAYS` | `30` | wie weit ein Lauf die Historie zurückgreift |
| `READINGS_HISTORY_MAX_DAYS` | `0` | Limit für die Historie (0 = unbegrenzt) |
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

## Mitwirken

**[AGENTS.md](AGENTS.md)** enthält die vollständige Arbeitsanleitung: Aufbau,
Technologiestapel, Teststrategie, die live verifizierten Fakten zur
Octopus-API, Konventionen und Stolperfallen. Sie richtet sich an Menschen wie an
KI-Agenten – wer mitwirkt, sollte sie lesen.

```bash
uv run pytest            # Tests
uv run ruff check .      # Linting
uv run ruff format .     # Formatierung
```

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
