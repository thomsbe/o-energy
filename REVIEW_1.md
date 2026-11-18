# Review 1 – unabhängiges Sicherheits- und Code-Review

> Die Befunde wurden am 18.11.2026 ausgewertet und umgesetzt – siehe
> **[REVIEW_1_OK.md](REVIEW_1_OK.md)**. Das Original (der Prüfauftrag) steht in
> der Git-Historie.

## Befunde

### [MITTEL] Service Worker cached authentifizierte Seiten dauerhaft

* **Datei:** `src/oenergy/static/sw.js:53-63` (fetch-Handler, Zweig `request.mode === "navigate"`); Widerspruch zur Doku: `SECURITY.md:50-51`
* **Was:** Jede erfolgreiche HTML-Navigation wird als Klone in den Cache Storage gelegt –
  ungefiltert auch `/`, `/verbrauch`, `/kosten`, `/system`. Der Eintrag bleibt bis zum
  Wechsel des Cache-Namens (`oenergy-v2`) erhalten; die Abmeldung (`src/oenergy/web/auth.py:170`,
  `request.session.clear()`) löscht ihn nicht. Dynamisch geprüft am Code-Pfad; im Browser
  nachvollziehbar via DevTools → Application → Cache Storage oder durch Offline-Start der PWA.
* **Warum relevant:** Die Seiten enthalten Verbrauchs-, Kosten-, Konto-, Zähler- und
  MaLo-Nummern sowie die Octopus-E-Mail. Sie überdauern die Abmeldung und bleiben auf
  gemeinsam genutzten Geräten/Profilen lesbar (Offline-Fall der PWA zeigt die letzte
  Ansicht des Vornutzers). `SECURITY.md` behauptet das Gegenteil („cacht die statischen
  Dateien … die zwischengespeicherte Login-Seite enthält keine Geheimnisse“) – die Doku
  hält hier nicht, was der Code tut.
* **Einstufung:** Kein Remote-Angriff nötig, sondern lokaler Zugriff auf das
  Browser-Profil bzw. ein Szenario mit gemeinsam genutztem Profil; data outlives logout
  widerspricht der dokumentierten Erwartung → MITTEL, nicht HOCH.
* **Vorschlag:** In `sw.js` das Navigations-Caching auf eine Whitelist beschränken
  (`/login`, `/offline.html`); Offline-Fallback auf `offline.html` statt der zuletzt
  besuchten Seite. Optional beim Abmelden den Seiten-Cache leeren.
* **Aufwand:** klein
* **Sicherheit:** belegt (Code-Pfad eindeutig; Header-Verhalten der App separat getestet)

### [MITTEL] Offene Weiterleitung in der Passkey-Anmeldung (`next` unvalidiert)

* **Datei:** `src/oenergy/web/routes.py:45` (`"next": request.query_params.get("next", "/")` –
  ungeprüft in JSON), `src/oenergy/web/templates/login.html:92` (fetch) und `login.html:133`
  (`window.location.href = payload.next || "/"`). Gegenstück mit validierung:
  `src/oenergy/web/auth.py:182-186`.
* **Was:** Die Passkey-Options-Route gibt den `next`-Parameter ungeprüft zurück; nach
  erfolgreicher Passkey-Verifikation übernimmt das Login-Skript den Wert als Ziel-URL.
  Belegt an der isolierten Instanz (Dummy-Credential in der Test-DB):
  `POST /api/auth/passkey/login/options?next=https://evil.example/phish` →
  `"next": "https://evil.example/phish"`; ebenso `//evil.example`. Das Passwort-Login
  blockt dieselben Werte (belegt: alle Varianten einschließlich Backslash → `/` bzw.
  `/%5C…`, kein Redirect).
* **Warum relevant:** Ein Angreifer versendet den Link
  `https://strom.tbaer.de/login?next=https://evil.example/phish`. Das Opfer meldet sich
  mit seinem echten Passkey an und landet unmittelbar danach auf der Angreiferseite –
  klassisches Post-Auth-Phishing (CWE-601).
* **Einstufung:** Internet-erreichbarer Anmeldeprozess, kein Datenabfluss, Opfer-Interaktion
  erforderlich → MITTEL.
* **Vorschlag:** `next` vor der Rückgabe mit derselben Regel wie `redirect_to_login`
  prüfen (relativ, mit `/` beginnend, ohne `//`; idealerweise `urljoin`-basiert) – im
  Service oder in der Route. Nebenbei: `{{ next }}` steht roh im JS-String-Kontext
  (`login.html:92`); Backslashes/Zeilenümbrüche erzeugen einen Syntax-Fehler und legen die
  Passkey-Anmeldung lahm (DoS über präparierten Link, kein XSS – Anführungszeichen werden
  HTML-escaped und im `<script>` nicht dekodiert).
* **Aufwand:** klein
* **Sicherheit:** belegt (Echo dynamisch nachgestellt; Redirect-Ziel code-belegt)

### [NIEDRIG] Keine Begrenzung der Request-Größe auf öffentlichen Endpunkten

* **Datei:** `src/oenergy/web/webauthn_routes.py:51` (`await request.json()` vor
  Challenge-Prüfung), `src/oenergy/web/routes.py:115` (`request.form()` bei `/login`),
  `src/oenergy/web/push_routes.py:29,40`
* **Was:** Ein 20-MB-JSON an den **unauthentifizierten** Endpunkt
  `/api/auth/passkey/login/verify` wurde vollständig gelesen und geparst (Antwort 400
  erst nach dem Parsen, 0,1 s lokal). Nirgends im Stack existiert ein Limit
  (uvicorn/Starlette setzen standardmäßig keins).
* **Warum relevant:** Anonymer Memory-DoS: Der Cloudflare-Tunnel begrenzt Requests auf
  ~100 MB, aber der direkt veröffentlichte Port (`docker-compose.yml:17`, `8000:8000`)
  ist unbegrenzt; parallele große Bodies bedienen einen einzigen Uvicorn-Worker und
  können kleine Heimserver in Speichernot bringen.
* **Einstufung:** Verfügbarkeitsstörung ohne Rechteausweitung, braucht Durchhaltewillen
  des Angreifers → NIEDRIG.
* **Vorschlag:** Middleware mit Body-Limit (JSON z. B. 64 KB, Formular 4 KB) oder
  frühzeitiger Content-Length-Check; als Zweitlinie `docker-compose.yml` auf
  `127.0.0.1:8000:8000` beschränken, wenn der Tunnel genutzt wird.
* **Aufwand:** klein
* **Sicherheit:** belegt

### [NIEDRIG] Anmeldeversuche ohne Rate-Limit oder Sperre

* **Datei:** `src/oenergy/web/routes.py:112-120`
* **Was:** 10 schnelle Fehlanmeldungen → 10 × 303 ohne Verzögerung, Sperrfrist oder
  Zähler (belegt). Einziges Tempo-Argument ist die Argon2id-Kosten (~50–100 ms je
  Versuch).
* **Warum relevant:** Brute-Force über den Tunnel bleibt bei schwachem Passwort
  durchhaltbar; ein WAF/Rate-Limit gibt es auf der Cloudflare-Strecke nicht
  automatisch.
* **Einstufung:** NIEDRIG – Argon2id plus HTTPS und ein starkes Passwort senken das
  Risiko stark, härtungsbedürftig bleibt es.
* **Vorschlag:** Fehlschlag-Zähler mit ansteigender Verzögerung (prozessweit oder nach
  IP – hinter dem Tunnel nur eingeschränkt wirksam), Warnung im Log, Hinweis auf
  Passwörter ≥ 12 Zeichen in README/SECURITY.
* **Aufwand:** klein
* **Sicherheit:** belegt

### [NIEDRIG] Abmeldung invalideert die Sitzung nicht serverseitig

* **Datei:** `src/oenergy/web/auth.py:170-172` (`logout` → nur `request.session.clear()`)
* **Was:** Die Sitzung ist ein signiertes, selbsttragendes Cookie; nach `GET /logout`
  akzeptiert der Server den alten Cookie-Wert weiterhin (belegt: alte Cookie-Zeile nach
  Logout → HTTP 200). Kein serverseitiger Speicher, das Session-Secret bleibt stabil.
* **Warum relevant:** Ein kopierter/exfiltrierter Cookie-Wert bleibt bis zu 14 Tage
  nutzbar, obwohl der Nutzer „abgemeldet“ hat – Logout erweckt den Eindruck einer
  Sperrung, die nicht stattfindet.
* **Einstufung:** Missbrauch setzt einen bereits abgegriffenen Cookie voraus (kein
  XSS-Vektor gefunden, Autoescaping aktiv) → NIEDRIG.
* **Vorschlag:** Verhalten in SECURITY.md dokumentieren; optional kürzeres
  `SESSION_MAX_AGE_DAYS` für shared Devices oder Sitzungs-Epoche in der DB
  (Password-Wechsel rotiert). Ohne echtes Mehrbenutzer-Risiko keine höhere Dringlichkeit.
* **Aufwand:** klein bis mittel
* **Sicherheit:** belegt

### [NIEDRIG] Sensible Dateien mit 0644: Datenbank, WAL, Backups, Log

* **Datei:** `data/o-energy.db`, `data/o-energy.db-wal`, `data/o-energy.db-shm`
  (belegt: 644), `data/backups/*` (644/664, erzeugt von `scripts/backup.sh` ohne
  `umask`), `data/logs/o-energy.log` (644); Randfall `src/oenergy/services/account.py:27`
  loggt die Octopus-E-Mail.
* **Was:** In diesen Dateien liegen Access-/Refresh-Token (Tabelle `tokens`), der private
  VAPID-Schlüssel (`app_settings`), Konto-, Zähler- und MaLo-Nummern. Jedes lokale Konto
  und jeder unprivilegierte Prozess kann sie lesen – ohne `.env` anfassen zu müssen.
* **Warum relevant:** Auf Mehrbenutzer-Hosts (oder nach Kompromittierung eines anderen
  Dienstes mit niederen Rechten) ist der Octopus-Zugang direkt lesbar. `SECURITY.md:48`
  empfiehlt `chmod 600` nur für die `.env` und verschweigt DB/Backups/Log.
* **Einstufung:** Lokale Voraussetzung nötig, kein Remote-Weg → NIEDRIG.
* **Vorschlag:** `backup.sh`: `umask 077` und `chmod 600` nach dem Schreiben; App:
  nach der ersten DB-Erstellung `chmod 600` (z. B. in `db/base.py` beim
  `resolve_db_path`); Log-Datei unter restriktivem umask anlegen; Empfehlung in
  SECURITY.md ergänzen.
* **Aufwand:** klein
* **Sicherheit:** belegt (Dateirechte)

### [NIEDRIG] Fehlende Security-/Cache-Header auf HTML- und JSON-Antworten

* **Datei:** `src/oenergy/main.py` (keine Header-Middleware); belegt an der isolierten
  Instanz **und** produktiv (`https://strom.tbaer.de/login`): kein `Cache-Control`, kein
  `X-Content-Type-Options`, keine CSP / `X-Frame-Options`, keine `Referrer-Policy`.
* **Warum relevant:** (a) Authentifizierte Seiten sind heuristisch cachebar und werden –
  zusätzlich zum SW-Befund – vom Browser ohne `no-store` behandelt; (b) Clickjacking auf
  interaktiven Seiten bleibt möglich (`/actions/sync`-Knöpfe, `/logout` als GET); (c)
  `nosniff` fehlt als Basis-Härtung. Cloudflare cached HTML nicht (belegt:
  `cf-cache-status: DYNAMIC`), daher kein akuter Abfluss über den CDN.
* **Einstufung:** Defense in Depth → NIEDRIG.
* **Vorschlag:** Middleware, die für authentifizierte Antworten
  `Cache-Control: no-store`, global `X-Content-Type-Options: nosniff` und
  `X-Frame-Options: DENY` (bzw. CSP `frame-ancestors 'self'`) setzt; `/logout` auf POST
  umstellen oder per Header absichern.
* **Aufwand:** klein
* **Sicherheit:** belegt

### [NIEDRIG] Bypass-Token: Vergleich nicht zeitkonstant, Doppel-Header: letzter gewinnt

* **Datei:** `src/oenergy/web/auth.py:113-118`
* **Was:** Der Secret-Header wird mit `==` verglichen statt `secrets.compare_digest`.
  Bei mehreren `X-OEnergy-Token`-Headern gewinnt der **letzte** (belegt in beiden
  Reihenfolgen; Ursache: `dict(headers)` überschreibt). Header-Namen werden korrekt
  case-insensitiv behandelt (ASGI normalisiert).
* **Warum relevant:** Standard-Anforderung an Secret-Vergleiche; netzwerkseitiges
  Timing ist praktisch nicht auswertbar (Nanosekunden im Jitter), der Befund bleibt
  rechnerisch Härtung. Das Doppel-Header-Verhalten ist selbst nicht ausnutzbar, ohne
  das Token zu kennen.
* **Einstufung:** NIEDRIG (Härtung), Ausnutzbarkeit: Verdacht/theoretisch.
* **Vorschlag:** `secrets.compare_digest(token, header_value)`; erste Header-Instanz
  verwenden oder Duplikate zurückweisen.
* **Aufwand:** klein
* **Sicherheit:** belegt (Verhalten), theoretisch (Angriff)

### [NIEDRIG] `set-password` schreibt immer den Compose-escapten Hash – Direktbetrieb loggt dann niemanden ein

* **Datei:** `src/oenergy/cli.py:295-316` (`cmd_set_password`, Ausgabe **und**
  `--write-env`), `src/oenergy/services/transfer.py:362` (`$$`-Escape); Doku
  `README.md:224-239` behandelt nur den Compose-Fall.
* **Was:** Erzeugte Hashes erscheinen immer als `$$argon2id$$…`. Docker Compose
  interpoliert `$$`→`$` (funktioniert); bei direktem Betrieb mit `uv run oenergy serve`
  liest pydantic-settings den Wert roh aus der dotenv → ungültiges Hash-Format →
  **jede** Anmeldung schlägt fehl (belegt an der isolierten Instanz:
  `verify_password` False, `/login` → `error=1`). Keine Warnung in Ausgabe oder
  README für den Nicht-Compose-Pfad.
* **Warum relevant:** Betreiber sperrt sich aus; naheliegende „Reparatur“ wäre, den
  Hash wieder zu entfernen – dann läuft die App **offen im Internet**. Stilles
  Fehlverhalten mit sicherheitskritischer Fehlreaktion.
* **Einstufung:** NIEDRIG (Konfigurations-Fußangel mit dokumentiertem Ausweg).
* **Vorschlag:** Beide Formen ausgeben (roh für dotenv, `$$` für Compose) **oder**
  einen Normalisierungs-Validator in `Settings` (`$$`→`$` beim Laden); README-Absatz
  „Betrieb ohne Docker“ ergänzen.
* **Aufwand:** klein
* **Sicherheit:** belegt (an der Testinstanz nachgestellt)

### [NIEDRIG] Uvicorn ohne Proxy-Header hinter dem Tunnel: WebAuthn-Origin fällt auf `http://` zurück

* **Datei:** `src/oenergy/cli.py:29-35` (`uvicorn.run` ohne `proxy_headers`),
  `src/oenergy/services/webauthn_service.py:57-67` (`context_for` leitet Origin/RP-ID
  aus `request.base_url` ab, wenn `WEBAUTHN_ORIGIN`/`WEBAUTHN_RP_ID` fehlen);
  belegtes Echo: Options-Request mit `Host: evil.example` → `rpId: evil.example`.
* **Was:** Hinter cloudflared sieht die App Schema `http` und Client `127.0.0.1`.
  Ohne konfigurierte Origin wird der WebAuthn-Origin `http://strom.tbaer.de`, während
  der Browser `https://…` meldet → Passkey-Login schlägt fehl. `AGENTS.md` §7
  („automatisch vom Host abgeleitet“) beschreibt das unzureichend. Das ungeprüfte
  Host-Echo auf dem öffentlichen Options-Endpunkt ist für sich harmlos (der Browser
  erzwingt die RP-ID-Konsistenz), belegt aber die Header-Abhängigkeit.
* **Warum relevant:** Konfigurationsrisiko: Passkeys brechen still → Nutzer bleibt auf
  Passwort-Login beschränkt; Zugriffs-Log der App zeigt nur 127.0.0.1 (keine
  Angreifer-IPs für Forensik).
* **Einstufung:** NIEDRIG (funktional/config; Passkey-Versagen „wahrscheinlich“, nicht
  im echten Browser vollzogen).
* **Vorschlag:** `serve` mit `--proxy-headers` und `--forwarded-allow-ips` starten oder
  `WEBAUTHN_ORIGIN`/`WEBAUTHN_RP_ID` als Pflicht hinter dem Tunnel dokumentieren.
* **Aufwand:** klein
* **Sicherheit:** belegt (Echo, uvicorn-Aufruf); Versager-Modus wahrscheinlich

### [NIEDRIG] CI: Third-Party-Actions nach mutablem Tag gepinnt, Jobs teils ohne `permissions`

* **Datei:** `.github/workflows/ci.yml` – `uses:` überwiegend `@v7`/`@v2` (inkl.
  `gitleaks/gitleaks-action@v2`); nur der Job `secrets` setzt `permissions:
  contents: read`, `test` und `docker` haben keinen Block.
* **Was:** Tags sind veränderbar – ein kompromittiertes Action-Repository läuft mit den
  Rechten des Standard-Tokens. Dependabot aktualisiert Actions monatlich (vorhanden ✓),
  ersetzt aber keine SHA-Pinnsung.
* **Warum relevant:** Supply-Chain-Basismaßnahme; Schadensfall unwahrscheinlich, Wirkung
  hoch (Repo-Kontrolle).
* **Einstufung:** NIEDRIG.
* **Vorschlag:** Für alle Jobs `permissions: contents: read` setzen; Third-Party-Actions
  auf Committ-SHA pinnen (z. B. gitleaks), ersteigene auf Versions-Tag belastbar.
* **Aufwand:** klein
* **Sicherheit:** belegt (Manifest)

### [INFO] Push-Abonnement: Server POSTet an beliebige URLs (nur für Angemeldete)

* **Datei:** `src/oenergy/services/push.py` (`subscribe`/`send`), `web/push_routes.py`
* **Was:** Belegt: Abonnement mit `endpoint=http://127.0.0.1:8998/push-target` erzeugt,
  `/api/push/test` löste einen POST des Servers auf den internen Listener aus
  (`POST /push-target aes128gcm len 199`). Unauthentifiziert: 401 (belegt).
* **Warum relevant / Einstufung:** Im Single-User-Modell kann nur der Eigentümer das
  auslösen, der den Container ohnehin kontrolliert → INFO. Erst nach Account-Takeover
  wird die App zum Sondenwerkzeug ins interne Netz.
* **Vorschlag:** Optional: Endpoint auf `https://` und bekannte Push-Domains (FCM,
  Mozilla autopush) beschränken; fehlgeschlagene Abonnements werden bereits gezählt
  und bereinigt (`failures`-Zähler vorhanden).
* **Aufwand:** klein
* **Sicherheit:** belegt

### [INFO] `/api/health` verrät öffentlich den Datenbank-URL-String

* **Datei:** `src/oenergy/web/routes.py:363-366`
* **Was:** Belegt produktiv: `{"status":"ok","db":"sqlite:////data/o-energy.db"}` –
  Pfadangabe ohne Geheimnis; Healthchecks (Docker, Monitoring) brauchen nur `status`.
* **Vorschlag:** Nur `{"status":"ok"}` zurückgeben.
* **Aufwand:** klein
* **Sicherheit:** belegt

### [INFO] Kostenfelder teils Float statt Integer-Cents

* **Datei:** `src/oenergy/db/models.py` – `DailyStat.cost_cents/energy_cost_cents/standing_cost_cents`
  und `Bill.total_cents` als `Float`, während `Account.balance_cents` korrekt `Integer` ist.
* **Warum relevant:** Mischbetrieb; Float-Cents können Rundungsdrift in Anzeige und
  Monatsprognose erzeugen (Korrektheit, keine Sicherheit).
* **Vorschlag:** Neuen Spalten Integer-Cents geben; bestehende beim nächsten
  Schema-Wechsel migrieren.
* **Aufwand:** mittel
* **Sicherheit:** belegt (Schema)

### [INFO] `POST /actions/sync` führt unbekannte Job-Namen als Fehlerlauf aus

* **Datei:** `src/oenergy/web/routes.py` (`action_sync`) + `services/sync.py`
  (`run_job`: `JOBS[job_name]` → KeyError läuft in den Fehlerzweig).
* **Was:** `job=beliebig` erzeugt eine SyncRun-Fehlerzeile inkl. nutzerkontrolliertem
  String in Log und Anzeige statt 400. Keine Injection (dict-Lookup), aber
  Protokollzeilen sind per Newline fälschbar (loguru escapet nicht).
* **Vorschlag:** `job` gegen `JOBS` validieren, unbekannte mit 400 abweisen.
* **Aufwand:** klein
* **Sicherheit:** belegt

### [INFO] WebAuthn: User-Verification nicht erzwungen; Challenge ohne Ablauf

* **Datei:** `src/oenergy/services/webauthn_service.py:171`
  (`require_user_verification=False`), Challenge lebt in der Session bis zur
  Verwendung (max. Sitzungsdauer, kein Zeitstempel).
* **Was:** Ein Passkey genügt ohne PIN/Biometrie-Abfrage (Options sagen nur
  „preferred“). Replay eines abgefangenen Assertions ist nur möglich, solange
  dieselbe Challenge ungenutzt in der Session liegt.
* **Warum relevant:** Für ein Single-User-Gerät vertretbar; auf geteilten Geräten
  wäre UV eine sinnvolle harte Barriere.
* **Vorschlag:** `require_user_verification=True` setzen, wenn alle Passkeys UV
  unterstützen; Challenge mit Zeitstempel + 2-Minuten-Fenster.
* **Aufwand:** klein
* **Sicherheit:** belegt (Code)

### [INFO] Kosmetik und Betriebs­empfehlungen

* **Datei:** `src/oenergy/web/auth.py:38,48` – `" [::1] "` in `LOCAL_HOSTS` ist
  tot: `Host: [::1]:8999` → `split(":")[0] == "["` matcht nie (Bypass verwehrt, nur der
  Eintrag ist wirkungslos).
* **Datei:** `src/oenergy/web/auth.py:31` – `PUBLIC_PATHS` enthält `/favicon.ico`,
  dafür existiert keine Route (404; die Favicons kommen als Data-URI aus den
  Templates).
* **Datei:** `docker-compose.yml:16-17` – `8000:8000` veröffentlicht den App-Port auf
  allen Interfaces. Die Auth-Middleware hält LAN-Zugriffe ab (Bypass verlangt Host
  **und** Peer lokal – belegt am Codepfad, `is_local_request` prüft beide), aber ohne
  Tunnel-Bedarf genügt `127.0.0.1:8000:8000`.
* **Vorschlag:** Einzelheiten je Budget aufräumen; Port-Mapping auf Loopback, wenn
  der Tunnelprofil läuft.
* **Aufwand:** klein
* **Sicherheit:** belegt

---

### Zusammenfassung

| Schwere | Anzahl |
|---|---|
| KRITISCH | 0 |
| HOCH | 0 |
| MITTEL | 2 |
| NIEDRIG | 8 |
| INFO | 4 (plus Sammelbefund Kosmetik) |

Die drei wichtigsten Befunde:

1. **Service Worker cached authentifizierte Seiten dauerhaft** – persönliche Daten
   überdauern die Abmeldung im Cache Storage; die Doku behauptet das Gegenteil.
2. **Offene Weiterleitung in der Passkey-Anmeldung** – `next` wird ungeprüft an
   `window.location.href` durchgereicht; das Passwort-Login blockt denselben Angriff.
3. **`set-password` schreibt den Compose-escapten Hash** – im Direktbetrieb ohne
   Docker ist damit kein Login möglich; die naheliegende „Reparatur“ (Hash entfernen)
   öffnet die App im Internet. Dazu passend: DB, Backups und Log liegen mit 0644 statt
   0600 auf der Platte.

Kein KRITISCH/HOCH-Befund: alle Routen sind geschützt, kein SQL/HTML/JS-Injektionsweg
gefunden, keine Secrets in der Historie, keine bekannten CVEs im Lockfile. Die
Schwachstellen härten das System ab oder brauchen Vorbedingungen (lokaler Zugriff,
Opfer-Interaktion, schwaches Passwort).

### Was ist ausdrücklich in Ordnung

* **Authentifizierungs-Middleware wirkt lückenlos.** Alle 35 Routen wurden ohne
  Anmeldung geprüft (isoliente Instanz + produktiv): Seiten → 303 auf `/login?next=…`,
  `/api/*` → 401-JSON. Öffentlich sind exakt die dokumentierten Pfade (`/login`,
  `/api/health`, `/sw.js`, `/favicon.ico`, `/static/*`, Passkey-Login-Endpunkte).
* **Passwort-Login solide:** Argon2id über pwdlib, identische Fehlerantwort (keine
  Enumeration), Cookie mit `HttpOnly`, `SameSite=Lax`, `Path=/`, `Max-Age=14 Tage`
  (belegt am Set-Cookie; `COOKIE_SECURE` korrekt konfigurierbar).
* **Kein Open Redirect beim Passwort-Login:** `next=https://…`, `//…`, `/\…` werden
  geblockt bzw. durch URL-Quoting (`%5C`) entschärft (belegt; der Backslash-Bypass
  trifft hier nicht).
* **Kein XSS gefunden:** Jinja-Autoescaping aktiv (`select_autoescape`, an der
  venv-Version verifiziert); `error`/`next` dynamisch escaped getestet; kein
  `|safe`/`Markup` in den Templates; `innerHTML` nur mit konstanten Strings; kein
  `eval`/`Function` in `app.js`.
* **Keine SQL-Injektion:** ausschließlich ORM mit gebundenen Parametern, kein `text()`
  mit Interpolation im Code; `PRAGMA foreign_keys=ON`; Bulk-Inserts mit
  `on_conflict_do_update` und Blockgröße (`CHUNK_SIZE`).
* **`AUTH_LOCAL_BYPASS` korrekt implementiert:** Host-Header **und** Peer-Adresse
  müssen lokal sein (auth.py:41-58) – ein Cloudflare-Tunnel (Peer 127.0.0.1, Host
  strom.tbaer.de) erhält keinen Bypass.
* **CSFR-Fläche klein:** `SameSite=Lax` + kein CORS-Middleware + keine
  zustandsändernden GETs (Ausnahme `/logout`, dokumentiert als NIEDRIG-Befund);
  Cross-Site-POSTs tragen kein Cookie.
* **Export enthält keine Secrets:** `EXPORT_TABLES` ohne `tokens`/`app_settings` –
  der Anspruch in AGENTS.md § 8 (kein Sitzungs-Secret im Export) stimmt; Web-Import
  existiert nicht.
* **Keine Geheimnisse in der Git-Historie:** Scan aller Commits nach
  Argon2-/Private-Key-/Passwort-Mustern – nur Platzhalter aus `.env.example`/README/CLI.
  CI führt gitleaks mit voller Historie; `.gitignore` deckt `.env`, `data/`, `*.db`,
  `probe.json` ab.
* **Abhängigkeiten sauber:** `pip-audit` (OSV) über den kompletten Lockfile-Bestand
  (1724 Zeilen resolved): keine bekannten Schwachstellen (Stand 2026-11-18); Pillow
  nur Dev-Werkzeug für ein Offline-Skript.
* **Docker:** Nicht-Root (uid 1000), Healthcheck, `--no-dev`, Abhängigkeiten vor Code;
  Compose-Profil `worker` ist dokumentierte Alternative, kein Doppelbetrieb.
* **Path-Traversal abgewehrt:** `/static/../`, `..%2f`, `%2e%2e`-Varianten → 404
  (belegt, StaticFiles-Schutz).
* **WebAuthn-Umfeld:** Challenge 32 Byte `secrets.token_bytes`, sessiongebunden und
  nach Verwendung entfernt; Registrierung nur für Angemeldete (401 belegt);
  py_webauthn 3.x-API korrekt verwendet (AGENTS.md-Fakten bestätigt).
* **API-Client:** Timeouts (30 s), begrenzte Retries (6) mit Backoff, keine
  Token/E-Mail-Ausgaben im Log, Fehlerausschnitte auf 200 Zeichen gekürzt.
* **Werkzeugkette funktioniert wie dokumentiert:** Quickstart in der isolierten Kopie
  vollständig nachgefahren (uv sync → alembic → seed → serve); Job-Plan stimmt mit
  AGENTS.md überein (15 min/08:17/06:12/12 h); 81 Tests grün, ruff clean, Formatierung
  clean.

### Getestet wurde

**Statisch:** alle 30 Python-Module unter `src/oenergy/` inkl. `services/`
(HTTP-Freiheit bestätigt – kein FastAPI/Starlette-Import), alle Templates und
Partials, `sw.js`, `app.js`, `offline.html`, `manifest.webmanifest`,
Dockerfile/docker-compose/backup.sh, `.github/` (CI, Dependabot, CODEOWNERS),
`.gitignore`, `.env.example`, README/SECURITY/AGENTS/AI/CHANGELOG, uv.lock
(Versionsauflösung), alembic-Migrationen, git-Historie (Geheimnis-Scan).

**Dynamisch, isolierte Instanz** (`/tmp/review/o-energy`, eigener Klon, Port 8999,
Demo-Daten via `seed`, eigenes Test-Passwort, `AUTH_LOCAL_BYPASS=false`,
SCHEDULER aus, Dummy-Passkey-Zeile für den Passkey-Pfad): Routen-Sweep ohne
Anmeldung; Login korrekt/falsch; Cookie-Flags; Open-Redirect-Varianten (6); XSS-Escaping
von `error`/`next`; Bypass-Token (richtig/falsch/Groß-Klein/Doppel-Header);
Logout + Wiederverwendung; Cache-/Security-Header; Push-SSRF gegen lokalen Listener;
Host-Header→RP-ID-Echo; 20-MB-Body; Path-Traversal; `pip-audit`; `pytest -q` (81
bestanden); `ruff check/format`.

**Produktive Instanz** `https://strom.tbaer.de` – ausschließlich unauthentifiziert:
`/`, `/login`, `/system`, `/api/state` (401), `/api/health` (leakt DB-URL),
`/sw.js`, Header-Bild, Cloudflare-Verhalten. Keine Loginversuche, keine Datenabrufe,
kein Eingriff in den laufenden Container.

**Nicht prüfbar / nicht geprüft:** echte WebAuthn-Zeremonie im Browser (kein Passkey
verfügbar; Passkey-Pfad per Code + Options-Echo bewertet), Timing-Angriffe auf den
`==`-Vergleich (nicht messbar), Lasttests/DoS nur als Einzelrequest demonstriert,
Betriebsdaten (`data/`, `.env`) wurden auf Befundprüfung nie gelesen – nur
Dateirechte (Metadaten).

**Blocker:** keine.
