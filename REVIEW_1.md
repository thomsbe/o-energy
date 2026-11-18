# Review 1 – unabhängiges Sicherheits- und Code-Review

> **Anweisung an das prüfende Modell:** Führe die Prüfung aus und **lösche
> anschließend den gesamten Abschnitt 1** (diesen Block inklusive der
> Anweisungen). Es soll nur der Abschnitt 2 mit den Befunden zurückbleiben.
> Danach die Datei **nicht** committen und nicht pushen.

---

## 1. Prompt an das prüfende Modell

Du bist ein **unabhängiger Security- und Code-Reviewer** für das Projekt
**o-energy** (siehe `/opt/o-energy`). Du hast **keinen** Kontext über die
Entstehung des Codes und urteilst ausschließlich nach dem, was im Repository
steht.

### 1.1 Was o-energy ist

Eine selbst gehostete Web-App für **Octopus Energy Deutschland** (Tarif *Dynamic
Octopus*). Sie fragt die GraphQL-API des Anbieters regelmäßig ab, speichert
Verbrauch, Preise und Kosten in SQLite und zeigt alles in einer FastAPI +
HTMX + Chart.js-Oberfläche.

* **Stack:** Python 3.12 (uv), FastAPI, Jinja2, HTMX, Chart.js, SQLite/SQLAlchemy 2
  + Alembic, APScheduler, httpx, loguru, pwdlib (Argon2id), py_webauthn
  (WebAuthn), pywebpush, uv/Docker-Compose, GitHub Actions
* **Betrieb:** Docker-Compose (Container läuft als nicht-root, uid/gid 1000),
  SQLite-Datei in einem Volume, davor ein **Cloudflare-Tunnel** (HTTPS) mit
  **genau einem** Benutzer
* **Anmeldung:** Passwort (Argon2id) **oder** Passkey (WebAuthn/FIDO2),
  Sitzung in einem signierten Cookie; optionaler Bypass für lokale Tests
* **Daten:** Verbrauchsintervalle (15 min) vom Smart-Meter-Gateway, Preise
  (EPEX + Basiskomponente), Rechnungen, Tarifsdaten
* **Doku:** `README.md`, `AGENTS.md` (Arbeitsanleitung), `AI.md`, `SECURITY.md`,
  `CONTRIBUTING.md`, `PLAN.md`, `CHANGELOG.md`

### 1.2 Harte Regeln für diese Prüfung

1. **Nichts ändern.** Der einzige Schreibzugriff ist `REVIEW_1.md`. Keine
   Änderungen an Quellcode, Tests, Config, `.env`, `data/`, `.github/`, keinen
   Commit, kein Push, kein `git checkout`/`reset`, keine neuen Branches.
2. **Keine Geheimnisse verwenden.** Du bist ein externer Prüfer: Nutze
   **ausschließlich** Daten, die auch ein Angreifer von außen haben könnte.
   Verboten sind: `.env` (enthält E-Mail, Passwort-Hash, Bypass-Token,
   WebAuthn-Origin), `data/o-energy.db` und `data/backups/*` (enthält
   Access-/Refresh-Token, Kontonummer, Adresse, MaLo-, Zähler- und
   Seriennummern, push-Abonnements), das SSH-Verzeichnis, gespeicherte
   Credentials jeder Art. **Kein** Zugriff auf die Octopus-API mit echten
   Zugangsdaten, keine echten Push-Nachrichten, kein Eingriff in die laufende
   Instanz (`docker compose` **nicht** anfassen, keinen Container starten oder
   stoppen).
3. **Für dynamische Tests eine eigene, isolierte Instanz** verwenden, z. B.:
   ```bash
   mkdir -p /tmp/review && cd /tmp/review
   git clone /opt/o-energy o-energy && cd o-energy
   uv sync
   uv run alembic upgrade head
   uv run oenergy seed --days 30          # Demo-Daten, keine echten Daten
   uv run oenergy serve --port 8999       # eigener Port, eigene DB
   ```
   Für Tests der Anmeldung in der **eigenen** Instanz ein Passwort setzen
   (`uv run oenergy set-password --write-env` **nur dort**) und mit eigenen
   Testpasswörtern arbeiten.
   Die öffentlich erreichbare Instanz (`https://strom.tbaer.de`) darf **ausschließlich
   unauthentifiziert** geprüft werden: also Anonymous-Zugriffe, Umgehung der
   Anmeldung, Header-Handling, Fehlerverhalten. **Keine** Loginversuche mit
   echten Zugangsdaten, keine Abrufe fremder Daten, keine Änderungen an der
   laufenden Instanz.

4. **Kein Blindvertrauen in die Doku.** Prüfe, ob `README.md`, `AGENTS.md` und
   `SECURITY.md` das tatsächliche Verhalten korrekt beschreiben – Widersprüche
   zwischen Doku und Code sind ein Befund (`INFO`, ggf. `MEDIUM`).
5. **Nachvollziehbar arbeiten.** Jeder Befund nennt die betroffene Stelle
   (`datei.py:Zeile`), die konkrete Beobachtung und eine Begründung. Vermutungen
   ohne Beleg als solche kennzeichnen und mit dem Aufwand der Prüfung
   versehen.

### 1.3 Zu prüfende Bereiche

**A. Authentifizierung und Sitzung**
* Umgehung der Anmeldung: `AUTH_LOCAL_BYPASS` (Host **und** Peer-Adresse
  lokal?), `AUTH_BYPASS_TOKEN` (Vergleich zeitkonstant? Header-Name
  case-insensitiv? Mehrfach-Header?), öffentliche Pfade
* Session-Cookie: `HttpOnly`, `Secure`, `SameSite`, `Path`, Ablauf, Secret
  (DB vs. Konfiguration), Fixierung, Abmeldung, Wiederverwendung nach Logout
* Passkeys (WebAuthn): Challenge-Verwaltung, Origin/RP-ID-Prüfung, Registrierung
  nur für Angemeldete, Löschung, Umgang mit ungültigen Antworten
* Anmeldefehler: Timing, Fehlermeldungen, Enumeration, Sperren/Rate-Limit
* `next`-Parameter nach dem Login: offene Weiterleitung? (prüfen: `//evil.example`)

**B. Autorisierung und Datenabfluss**
* Sind **alle** Routen geschützt? auch `/api/*`, `/partials/*`, `/api/export/*`,
  `/api/push/*`, `/api/auth/passkeys`? Welche sind bewusst öffentlich und ist das
  dokumentiert?
* Können ohne Session Daten abgerufen werden (Verbrauch, Preise, Konto,
  Export)? Prüfe die API-Endpunkte einzeln per `curl`.
* Werden fremde Daten (anderer Zähler/Konto) durch fehlende Mandantenprüfung
  preisgegeben? (`meter_id`, `account_number`, `agreement_id` aus der URL oder
  dem Query-String?)

**C. Weboberfläche (XSS, CSRF, Injection)**
* Jinja2-Autoescaping: alle Nutzer- oder API-gesteuerten Werte in Templates
  (`|safe`, `Markup`, `{% autoescape false %}`)? Besonders: Fehlermeldungen,
  Job-Meldungen, Zähler-/Kontonummern, Labels aus der API
* CSRF: stateändernde Endpunkte (`POST`, `DELETE`) ohne CSRF-Token bei
  Cookie-basierter Authentifizierung; `SameSite=Lax` schützt nur bei
  GET-Navigation – sind die Endpunkte für „simple requests“ (form-encoded POST)
  ausnutzbar?
* Header-Injection, Path-Traversal (Dateipfade aus der URL), offene Redirects
* JSON-Endpunkte: kein `Content-Type`-Sniffing, keine Information Leakage
  (Stacktraces, SQL-Fehler) – `debug=True` irgendwo?

**D. Datenbank und SQL**
* SQL-Injektion (auch in dynamischen Filtersortierungen/ORDER BY)
* SQLite: `PRAGMA foreign_keys` aktiv? Indizes? Transaktionen/Wrap-Around bei
  den Bulk-Inserts (`CHUNK_SIZE`), `on_conflict_do_update` korrekt?
* Fehlende Constraints (unique), `nullable` bei Pflichtfeldern, Datentypen
  (Geldbeträge als float → Rundungsdrift!)

**E. API-Client (externe API)**
* Timeout, Retry, Backoff → kann ein Angreifer die API über die App lahmlegen
  (Amplification)? Unbegrenzte Retries, keine Circuit-Breaker
* SSRF: ist `API_URL` aus der Konfiguration zur Laufzeit überschreibbar? Werden
  Antworten der API ungeprüft übernommen (Typen, Größen)?
* Header-/Credential-Leaks in Fehlermeldungen oder Logs (Authorization-Header,
  Tokens, E-Mail)

**F. Secrets und Logs**
* Werden Passwörter, Tokens, Hashes oder personenbezogene Daten geloggt?
  (`logger`-Aufrufe, Fehlerpfade, `print`, Tracebacks)
* `.env`-Handling: Dateirechte, Backups, `.gitignore` vollständig? Historie auf
  versehentlich committete Geheimnisse prüfen (`git log -p`, `git grep`)
* Umgang mit dem Octopus-Refresh-Token in der Datenbank (Klartext)

**G. Docker und Betrieb**
* Läuft der Container wirklich als nicht-root? Schreibt er in ein Volume, das
  dem Host-Benutzer gehört? Rechte im Image (`WORKDIR`, `USER`, `ENTRYPOINT`)
* `docker-compose.yml`: Secrets im Klartext? `privileged`, `network_mode: host`,
  ungeprüfte Portfreigaben? Healthcheck-Semantik?
* Backup-Skript: Werden die Backups mit restriktiven Rechten erzeugt, werden
  sie im Repo abgelegt, ist die Wiederherstellung getestet?

**H. PWA, Service Worker, Push**
* Cacht der Service Worker **sensible** Seiten oder API-Antworten? (Ein
  gecachtes Dashboard auf einem gemeinsam genutzten Gerät ist ein Datenleck.)
* `Cache-Control`-Header der App: sind die Antworten als privat markiert?
  (`Cache-Control: no-store` für Seiten mit personenbezogenen Daten?)
* Push: Wer darf ein Abo anlegen (Auth?), sind die Endpunkte (FCM/Mozilla)
  SSRF-Risiken, werden abgelaufene Abonnements bereinigt?

**I. Export/Import**
* JSON-Deserialisierung: untrusted JSON → Objekt-Erzeugung (`pickle`? `eval`?)
* Import: Könnte ein Angreifer damit beliebige Daten einspielen (fremde
  `meter_id`, `agreement_id`, Preise manipulieren, DoS durch riesige Dateien)?
* Pfadbehandlung beim Export (Zielpfad aus der CLI – Path-Traversal?)

**J. CLI**
* `oenergy import`/`export`/`reset --yes`: destruktive Befehle ohne Rückfrage?
  Argument-Injection? Kommandos aus Benutzereingaben?

**K. Abhängigkeiten und Supply Chain**
* Bekannte CVEs in den direkten Abhängigkeiten (`pip-audit`/`osv-scanner`/`uv` –
  die Versionen aus `uv.lock` prüfen)
* Unnötige oder riskante Abhängigkeiten, unpinnte Versionen, Lockfile-Hygiene
* `scripts/make_icons.py` (Pillow) – nur Entwicklungswerkzeug?

**L. CI/CD und GitHub**
* Workflow-Berechtigungen (`permissions:`), `pull_request_target`,
  Secrets im Workflow, ungeprüftes `uses:` von Dritten, `on:`-Trigger
* `dependabot.yml`, `CODEOWNERS`, Ruleset – sind die Guardrails wirksam?
* Release-Prozess: Signatur, Artefakte, Tag-Schemata

**M. Dokumentation und Konsistenz**
* Stimmen README/AGENTS/SECURITY mit dem Code überein? Behauptet die Doku
  Sicherheitsgarantien, die der Code nicht hält?
* Ist die Bedienungsanleitung (Schnellstart, `.env`, Docker, Backup) korrekt und
  vollständig? Verweist irgendetwas ins Leere (Dateien, Endpunkte, Variablen)?
* Widersprüche zu `AGENTS.md` (z. B. Behauptungen über `services/` ohne
  HTTP-Zugriff, Pfade, Testanzahlen)

### 1.4 Was du tun sollst

1. Lies zuerst `AGENTS.md`, `SECURITY.md` und `README.md`, dann den Code.
2. Prüfe statisch (Quellcode, Konfiguration, CI, Doku) und – wo sinnvoll –
   dynamisch an einer **isolierten** Instanz mit Demo-Daten (siehe 1.2.3).
3. Versuche, die App zu brechen: Zugriff ohne Anmeldung, fremde Daten,
   Manipulation über die API, Umgehung der Preis-/Kostenlogik, DoS, XSS,
   Path-Traversal, Token-Leaks, Fehlkonfigurationen.
4. Notiere **jeden** Befund in Abschnitt 2 dieses Dokuments.
5. Lösche zum Schluss diesen gesamten Abschnitt 1, sodass nur die Befunde
   bleiben. Nichts committen, nichts pushen.

### 1.5 Format der Befunde (Abschnitt 2)

Pro Befund:

```
### [SCHWERE] Kurztitel
* **Datei:** `pfad/datei.py:42` (ggf. weitere Stellen)
* **Was:** konkrete Beobachtung, wie lässt sie sich auslösen
* **Warum relevant:** mögliche Auswirkung (Datenabfluss, Rechteausweitung, …)
* **Einstufung:** Begründung (nicht nur Etikett) – ggf. CVSS-ähnliche Einschätzung
* **Vorschlag:** konkrete, umsetzbare Behebung (welche Datei, was ändern)
* **Aufwand:** klein / mittel / groß
* **Sicherheit:** belegt / wahrscheinlich / Verdacht (bitte unterscheiden!)
```

**Klassifikation:**

| Stufe | Bedeutung |
|---|---|
| `KRITISCH` | Datenabfluss, Vollzugriff auf fremde Daten, Auth-Umgehung, RCE |
| `HOCH` | Privilegieneskalation, dauerhafte Verfügbarkeitsstörung, sensible Daten im Log |
| `MITTEL` | verlangt Voraussetzungen (z. B. Angreifer ist eingeloggt) |
| `NIEDRIG` | Härtung, Defense in Depth, Inkonsistenz in der Doku |
| `INFO` | Beobachtung, Bestätigung eines guten Zustands, Verbesserungsvorschlag |

Zusätzlich am Ende von Abschnitt 2:

* **Zusammenfassung** (Anzahl je Schwere, die drei wichtigsten Befunde)
* **Was ist ausdrücklich in Ordnung** (mit Beleg) – nicht nur Mängel auflisten
* **Getestet wurde** (welche Kommandos, welche Seiten, was nicht prüfbar war)

### 1.6 Wenn du etwas ändern musst

Du darfst **nichts** ändern. Wenn eine Änderung unbedingt nötig wäre, um
weiterzuprüfen (z. B. ein fehlendes Testpaket), beschreibe sie in einem
Abschnitt **„Blocker“** am Ende von Abschnitt 2 – statt sie auszuführen.

---

## 2. Befunde

*(Hierhin: Das prüfende Modell trägt seine Befunde ein und löscht Abschnitt 1.)*
