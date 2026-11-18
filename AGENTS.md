# AGENTS.md – Arbeitsanleitung für KI-Agenten (und Menschen)

> **Diese Datei wird von Agenten automatisch gelesen. Bitte vollständig lesen,
> bevor Code geändert wird.**
>
> **Context7 / eine Bibliotheks-Doku über MCP:** Wenn verfügbar, **immer** die
> aktuelle Doku der betroffenen Bibliothek befragen, bevor eine API verwendet
> oder eine Abhängigkeit gewählt wird. FastAPI, pydantic, loguru, py_webauthn
> und APScheduler entwickeln sich schnell – das Gedächtnis ist hier keine gute
> Grundlage.

---

## 0. Das Projekt in 60 Sekunden

**o-energy** = selbst gehostete Web-App für **Octopus Energy Deutschland**
(Tarif *Dynamic Octopus*). Sie pollt die GraphQL-API des Anbieters, speichert
Verbrauch, Preise und Kosten in SQLite und zeigt sie in einer HTMX-Oberfläche.

- **Python 3.12**, Paketverwaltung **uv**, Web-Framework **FastAPI**
- **Ein** Benutzer, **ein** Tarif, keine Multi-Tenant-Funktionen
- Alles liegt in `/opt/o-energy`, Paket in `src/oenergy`

```bash
uv sync                     # Abhängigkeiten installieren
uv run pytest               # Tests (Ziel: alle grün)
uv run ruff check .         # Linting (Ziel: keine Fehler)
uv run ruff format .        # Formatierung
uv run oenergy serve         # Web-App auf http://127.0.0.1:8000
```

---

## 1. Harte Regeln

1. **Keine Annahmen über Bibliotheks-APIs.** Erst Context7/Doku befragen, dann
   verwenden. Beispiel aus diesem Projekt: `py_webauthn` 3.x nutzt
   `parse_registration_credential_json` (nicht `..._response_json`) und
   `options_to_json_dict` (nicht `options_to_json`, das liefert einen String).
2. **`services/` bleibt HTTP-frei.** In `src/oenergy/services/` stehen die
   Fachlogiken. Sie dürfen weder FastAPI-Routen noch HTML kennen – dadurch sind
   sie ohne Mocking testbar.
3. **Loguru statt `logging`.** `from loguru import logger`. Formatierung mit
   `{}` – **niemals** `%s`, sonst steht die rohe Formatzeichenkette im Log.
4. **Zeiten:** in der Datenbank immer **naive UTC**. Erst in der Ausgabe nach
   `TIMEZONE` umrechnen (`to_local()` im Python-Code, `localtime()` im
   Template). In der Oberfläche wird **nie** eine UTC-Zeit angezeigt.
5. **Der heutige Tag hat nie Zählerdaten.** Der Netzbetreiber liefert mit
   ~24 Stunden Verzögerung. „Heute“ darf in **keiner** Kachel und **keinem**
   Diagramm auftauchen (Ausnahmen: reine Preise, die gültig sind).
6. **Keine Geheimnisse ins Repository.** `.env`, `data/` und `probe.json` sind
   ignoriert. Zugangsdaten ausschließlich in `.env`. **Achtung:** Docker Compose
   interpoliert die `.env` – ein Argon2-Hash muss dort als `$$argon2id$$…`
   escaped werden, sonst kommt er zerstört im Container an.
7. **Jede Verhaltensänderung braucht einen Test** in `tests/`. Die Tests dürfen
   nicht von der lokalen `.env` abhängen (in `tests/conftest.py` werden die
   relevanten Variablen explizit gesetzt).
8. **`CHANGELOG.md` pflegen** (Keep a Changelog), Versionen nach SemVer.
9. **Jinja: Inhalte außerhalb von `{% block %}` werden nicht gerendert.** Skripte
   gehören in den `{% block scripts %}` der Basisvorlage – sonst fehlen sie im
   Browser, ohne Fehler in der Konsole.

---

## 2. Wo ändere ich was?

| Aufgabe | Dateien |
|---|---|
| Neue API-Abfrage | `src/oenergy/api/queries.py` → dazu ein Eintrag in `AGENTS.md` § 5 |
| Preise, Kosten, Verbrauch | `src/oenergy/services/` |
| Neue Seite/Route | `src/oenergy/web/routes.py` + `web/templates/` |
| Datenmodell | `src/oenergy/db/models.py` **+ Migration** (`uv run alembic revision --autogenerate`) |
| Konfiguration | `src/oenergy/config.py` (Pydantic-Settings, Env-Variablen in GROSSBUCHSTABEN) |
| Jobs/Zeitplan | `src/oenergy/jobs.py` |
| CLI | `src/oenergy/cli.py` |
| Oberfläche | `src/oenergy/static/css/app.css`, `static/js/app.js`, `web/templates/` |
| Anmeldung/Passkeys | `src/oenergy/web/auth.py`, `web/webauthn_routes.py`, `services/webauthn_service.py` |
| Push-Benachrichtigungen | `src/oenergy/services/push.py`, `web/push_routes.py`, `static/sw.js` |
| Export/Import | `src/oenergy/services/transfer.py` |
| PWA (Manifest, Icons, Offline) | `src/oenergy/static/manifest.webmanifest`, `static/icons/`, `static/offline.html`, `scripts/make_icons.py` |

**Nach einer Schema-Änderung immer:** `uv run alembic revision --autogenerate -m "…"`
und `uv run alembic upgrade head` prüfen.

---

## 3. Teststrategie

**Ziel:** Die *Fachlogik* testen, nicht die Octopus-API. Die API wird auf
HTTP-Ebene mit **respx** gemockt.

```bash
uv run pytest                      # alles
uv run pytest -q tests/test_costs.py
uv run pytest -k "backfill or pricing" -v
uv run pytest --lf                 # nur zuletzt Fehlgeschlagenes
```

| Testdatei | Thema |
|---|---|
| `test_consumption.py` | Intervalle aus Zählerständen, Einheit kWh/Wh, Zählerwechsel |
| `test_costs.py` | Kosten je Slot, Grundpreis, EPEX-Fallback |
| `test_pricing.py` | Preisklassifikation, Farbverlauf |
| `test_completeness.py` | Tagesstatus, Lieferzeitpunkt 08:00 |
| `test_backfill.py` | Gap-Erkennung, Historie, Retry-Fenster |
| `test_api.py` | Token, Refresh, Bearer-Fallback, Retry (respx) |
| `test_integration.py` | kompletter Sync gegen ein gemocktes Kraken |
| `test_dashboard.py` | Monatsprognose, Preisklassen |
| `test_transfer.py` | Export/Import, Idempotenz |
| `test_web.py` | Routen, Partials, Login, Export-Download |
| `test_webauthn.py` | Passkey-Optionen, Zugriffsschutz |
| `test_push.py` (in `test_webauthn.py`) | Push-Abonnement, Zustellung |

Fixtures: `tests/conftest.py` → `database`, `session`, `seeded`, `client`.
Bei Bedarf dort ergänzen statt zu duplizieren.

**Wichtig:** In Tests immer `api_min_interval_seconds=0.0` setzen – sonst
drosselt der Client die Requests und die Suite wird um Sekunden langsam.

---

## 4. Workflow für eine Änderung

```bash
git status                 # Ausgangslage prüfen
# ... Änderung ...
uv run pytest              # Tests
uv run ruff check .        # Linting
uv run ruff format .       # Formatierung
git add -A && git commit   # sprechende Nachricht: feat:/fix:/docs:/refactor:
```

**Vor einem Release zusätzlich:** `CHANGELOG.md` ergänzen, Version in
`pyproject.toml` **und** `src/oenergy/__init__.py` (`__version__`),
`uv.lock` aktualisieren, `uv build` prüfen.

**Nicht committen:** `.env`, `data/`, `probe.json`, `.venv/`, `__pycache__/`.

---

## 5. Octopus-API – live verifizierte Fakten

Die API (Kraken) ist undokumentiert und **authorizationsabhängig**. Diese
Erkenntnisse sind bereits im Code umgesetzt – **nicht neu erfinden**:

### Erlaubt

| Zweck | Query |
|---|---|
| Konto, Verträge, Lieferpunkt, Zähler | `viewer`, `account(...){properties{...}}` |
| Preise heute/Zukunft | `agreement(id).unitRateForecast` (15-Min-Slots) |
| Basiskomponente | `agreement(id).unitRateInformation.rates` |
| Börsenpreis | `epexDayAheadPrices` |
| **Verbrauch** | `property(id).measurements(utilityFilters:[{electricityFilters:{readingFrequencyType: RAW_INTERVAL}}])` |
| Rechnungen | `account.bills` |

### Gesperrt (Kundenkonten)

`electricityMeters`, `electricityMeterReadings`, `agreementRates`,
`estimatedSupplyPointReadings` → „The viewer is not authorized to execute the
query/mutation“.

### Fachregeln

- **Preis** = EPEX-Spot + **17,35 ct/kWh** Basiskomponente (netto; 20,65 brutto).
  Historie wird daraus rekonstruiert, heute/Zukunft kommt aus dem Forecast.
- **Grundpreis** 10,21 €/Monat: die API liefert `grossRate = 10.21258` in **Euro**
  (nicht Cent) – das wird erkannt und umgerechnet.
- **USt**: API liefert `19` (Prozent), nicht `0.19`.
- **Lieferverzug**: Zählerdaten ~24 h versetzt (Vortag gegen 08:00).
- **Token**: Access 1 h (JWT `exp`), Refresh **20 Tage**
  (`refreshExpiresIn` in **Millisekunden**), Long-Lived-Refresh gesperrt.
- **Rate-Limit**: „too many requests“; Client wartet 1,2 s/Request und bis zu
  60 s bei „internal error“.

### Diagnose

```bash
uv run oenergy diagnose              # liefert der Account die Daten?
uv run oenergy probe --out probe.json  # Rohantworten ansehen
```

---

## 6. Stolperfallen (bitte nicht wiederholen)

| Stolperstein | Korrekt |
|---|---|
| `logger.info("x %s", y)` | `logger.info("x {}", y)` – loguru formatiert nicht mit `%s` |
| `datetime.utcnow()` in der Anzeige | erst speichern (UTC), dann mit `to_local()`/`localtime()` umrechnen |
| Heutige Werte in Diagrammen | entfernen – die Daten kommen erst am Folgetag |
| `session.get("session")` | `request.session` |
| SQLite: viele Parameter je Statement | Blöcke verwenden (siehe `services/transfer.py`, `CHUNK_SIZE`) |
| Passwort-Login mit Benutzername | Es gibt nur einen Benutzer – nur das Passwort |
| Bulk-Insert ohne `on_conflict_do_update` | immer mit Konfliktbehandlung, sonst doppelte Zeilen |

---

## 7. Betrieb und Konfiguration

```bash
docker compose up -d          # Web-App + Scheduler (genügt)
docker compose logs -f
./scripts/backup.sh           # SQLite-Backup + JSON-Export
```

- Jobs: Preise 15 min, Zählerdaten stündlich, Tagesabschluss 08:17,
  Rechnungen 06:12, Konto 12 h.
- Das Compose-Profil `worker` ist eine **Alternative**, nie zusätzlich
  (doppelte Abfragen, Rate-Limit).
- Ohne `AUTH_PASSWORD_HASH` ist die App **offen** (Hinweis in der Kopfzeile).
  Für lokale Tests gibt es zwei Ausnahmen: `AUTH_LOCAL_BYPASS` (nur wenn **Host**
  *und* Peer-Adresse lokal sind – ein Tunnel kommt ebenfalls von 127.0.0.1!) und
  das optionale Secret-Header `X-OEnergy-Token` (`AUTH_BYPASS_TOKEN`, erzeugt mit
  `oenergy generate-token --write-env` – **niemals** im Quellcode).
  Für den Betrieb im Internet: `uv run oenergy set-password` und den Hash in
  die `.env` eintragen; bei reinem HTTPS `COOKIE_SECURE=true`.
- Passkeys: Registrierung auf der Seite *System*, RP-ID/Origin aus
  `WEBAUTHN_ORIGIN`/`WEBAUTHN_RP_ID` oder automatisch vom Host abgeleitet.
  **WebAuthn braucht einen sicheren Kontext** (https oder localhost) – über
  `http://homeservices:8000` bietet der Browser keine Passkeys an.

Konfiguration komplett in `src/oenergy/config.py`; alle Variablen in
`.env.example` dokumentiert.

---

## 8. Grenzen des Projekts

- Nur Strom, ein Tarif, ein Benutzer.
- 15-Minuten-Aggregation bietet die API nicht – die Rohintervalle werden genutzt.
- Für sehr alte Zeiträume liefert die API keine Börsenpreise mehr.
- Der Export enthält kein Secret für die Sitzungs-Cookies (nach einem Restore
  einmal neu anmelden).
- Kein Mehrbenutzer-Modus, keine Rollen.

---

## 9. KI-Unterstützung

Dieses Projekt wurde **mit** KI-Agenten entwickelt (Kilo Code, MiniMax 3.1 Flash),
überwacht und verantwortet vom Maintainer. Die Leitplanken für Mitwirkende –
mit und ohne KI – stehen in [AI.md](AI.md); für dich als Agent ist das
Wichtigste: **diese Datei (AGENTS.md) lesen und die Regeln in § 1 einhalten.**
Verifiziere jede Aussage über die API oder eine Bibliothek, bevor du sie
verwendest (Context7, Projektdoku, `oenergy diagnose`).

---

## 10. Wenn etwas unklar ist

1. **Context7/Doku befragen** (Pflicht, wenn verfügbar).
2. `README.md` (Bedienung), `PLAN.md` (Architektur und API-Recherche),
   `CHANGELOG.md` (was warum geändert wurde) lesen.
3. `uv run oenergy diagnose` / `probe` – die API fragen statt raten.
4. Bei Unklarheit in der Anleitung: **diese Datei aktualisieren** – sie ist die
   Schnittstelle zu allen, die nach dir am Projekt arbeiten.
5. `AI.md` enthält die Leitplanken für KI-gestützte Beiträge.
