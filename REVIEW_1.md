# Review 1 – Ergebnis der Umsetzung

Das unabhängige Sicherheits- und Code-Review (`REVIEW_1.md`, durchgeführt von einem
anderen Modell) hat **keine kritischen und keine hohen Befunde** gefunden. Alle
2 MITTEL-, 8 NIEDRIG- und 4 INFO-Befunde sind behoben – dieser Abschnitt hält
fest, **was** geändert wurde und **woran** man die Umsetzung erkennt.

**Nachweis:** 105 Tests grün (davon 22 neue in `tests/test_security.py`),
`ruff check` und `ruff format --check` sauber, CI grün.

| Schwere | Befunde | Status |
|---|---|---|
| KRITISCH | 0 | – |
| HOCH | 0 | – |
| MITTEL | 2 | beide behoben |
| NIEDRIG | 8 | alle behoben |
| INFO | 4 (+1 Sammelbefund) | alle umgesetzt |

---

## MITTEL

### 1. Service Worker cachte authentifizierte Seiten dauerhaft → **behoben**

**Problem:** Jede HTML-Navigation wurde als Klone in den Cache Storage gelegt –
ungefiltert auch `/`, `/verbrauch`, `/kosten`, `/system` mit Verbrauchs-,
Kosten-, Konto-, Zähler- und MaLo-Nummern. Der Eintrag überdauerte die
Abmeldung. `SECURITY.md` behauptete das Gegenteil.

**Umsetzung:**

* `src/oenergy/static/sw.js`: nur eine Whitelist (`OFFLINE_PAGES = ["/login"]`)
  wird gecacht; der Offline-Fallback zeigt `offline.html` statt der zuletzt
  besuchten Seite.
* Neuer `message`-Handler im Service Worker: `clear-page-cache` leert den Cache.
* `src/oenergy/static/js/app.js`: `clearServiceWorkerCache()` wird beim
  Abmelden angestoßen (`#logout-link` in `base.html`).
* `SECURITY.md` beschreibt jetzt die tatsächliche (enge) Reichweite.

### 2. Offene Weiterleitung in der Passkey-Anmeldung → **behoben**

**Problem:** `next` wurde ungeprüft in der JSON-Antwort des
Passkey-Options-Endpunkts zurückgegeben und im Skript an
`window.location.href` übergeben → Post-Auth-Phishing möglich.

**Umsetzung:**

* Neue Funktion `safe_next()` in `src/oenergy/web/auth.py`: erlaubt sind nur
  relative Ziele, die mit einem einfachen `/` beginnen, nicht mit `//` oder
  `/\` beginnen und keine Steuerzeichen enthalten. Das Passwort-Login nutzt
  dieselbe Funktion.
* `src/oenergy/web/webauthn_routes.py`: das Ziel wird vor der Rückgabe geprüft.
* Nebeneffekt behoben: `{{ next }}` stand roh im JS-String-Kontext und konnte
  einen Syntax-Fehler auslösen (DoS über präparierten Link).

**Test:** `tests/test_security.py::test_safe_next_rejects_external_targets`
(6 Varianten), `::test_passkey_options_validate_the_next_target`.

---

## NIEDRIG

### 3. Keine Begrenzung der Request-Größe → **behoben**

Neue Middleware `BodyLimitMiddleware` (`src/oenergy/web/auth.py`): Bodies über
256 KB werden mit **413** abgewiesen – sowohl über `Content-Length` als auch
beim Lesen (ohne Header).

**Test:** `::test_oversized_body_is_rejected`. Live geprüft: HTTP 413.

### 4. Anmeldeversuche ohne Rate-Limit → **behoben**

`login_is_locked()` / `_register_failure()` in `src/oenergy/web/auth.py`: nach
**10 Fehlversuchen** wird die Herkunft für **5 Minuten** gesperrt
(`error=locked`). Die Zählung liegt prozessweit im Speicher und wird im Log
vermerkt.

**Test:** `::test_login_is_locked_after_repeated_failures`.

### 5. Abmeldung invalidiert die Sitzung nicht → **behoben**

* Serverseitige **Sitzungs-Epoche**: `bump_session_epoch()` beim Logout,
  `_is_valid_session()` bei jeder Anfrage. Ein kopierter Cookie-Wert ist nach
  dem Abmelden wertlos.
* `SECURITY.md` beschreibt das Verhalten.

**Test:** `::test_logout_invalidates_the_session`,
`::test_session_survives_and_is_invalidated_by_logout`.

> **Hinweis:** Bei der Umsetzung zeigte sich ein **Fehler in der
> Middleware-Reihenfolge** – die SessionMiddleware lag innen, wodurch die
> Anmeldeprüfung kein dekodiertes Cookie sah und **jede** Anmeldung abgewiesen
> wurde. Die Kette ist jetzt
> `Session → SecurityHeaders → BodyLimit → RequireLogin`; ein Regressionstest
> sichert die Reihenfolge ab
> (`::test_middleware_order_keeps_the_session_readable`).

### 6. Sensible Dateien mit 0644 → **behoben**

* `src/oenergy/db/base.py`: `_restrict_permissions()` setzt Datenbank, WAL und
  SHM auf `0600`.
* `src/oenergy/logging_config.py`: das Log-Verzeichnis wird `0700`, die
  Logdatei `0600`.
* `scripts/backup.sh`: `umask 077` und `chmod 600` für die Backups.
* `SECURITY.md` nennt die Empfehlung für `.env`, Datenbank und Backups.

### 7. Fehlende Security-/Cache-Header → **behoben**

Neue Middleware `SecurityHeadersMiddleware`: `X-Content-Type-Options: nosniff`,
`X-Frame-Options: DENY`, `Referrer-Policy: same-origin`, `Permissions-Policy`
und eine CSP (`frame-ancestors 'none'`, `form-action 'self'`). Für
authentifizierte Antworten zusätzlich `Cache-Control: no-store` und
`Pragma: no-cache`.

**Live geprüft** (produktiv, `https://strom.tbaer.de/login`): alle Header
gesetzt ✓.

**Test:** `::test_security_headers_are_set`,
`::test_session_survives_and_is_invalidated_by_logout` (prüft `no-store`).

### 8. Bypass-Token nicht zeitkonstant verglichen → **behoben**

`secrets.compare_digest()` statt `==`; doppelt gesetzte Header werden über
`_collect_headers()` verworfen (bisher gewann der letzte).

**Test:** `::test_duplicate_bypass_headers_are_rejected`.

### 9. `set-password` schrieb immer den Compose-escapten Hash → **behoben**

* Die App normalisiert den Hash beim Laden (`Settings._unescape_hash`):
  `$$argon2id$$…` → `$argon2id$…`. Damit funktioniert **derselbe** Wert für
  Docker Compose **und** für den Direktbetrieb.
* `oenergy set-password` gibt **beide** Formen aus (unescaped für dotenv,
  escaped für Compose) und weist darauf hin.
* README: eigener Hinweis zum Direktbetrieb.

**Test:** `::test_compose_escaped_hash_is_normalized`; zusätzlich ein
Round-Trip über `set-password --write-env` → `Settings` → `verify_password`
geprüft.

### 10. Uvicorn ohne Proxy-Header → **behoben**

`uvicorn.run()` startet jetzt mit `proxy_headers=True` und
`forwarded_allow_ips` (Vorgabe `127.0.0.1`, überschreibbar über
`FORWARDED_ALLOW_IPS`). Hinter dem Tunnel stimmen damit Schema und Host – nötig
für Passkeys und für das Zugriffs-Protokoll.

*README*: neuer Abschnitt „Hinter einem Reverse-Proxy oder Tunnel“.

### 11. CI: Actions nicht gepinnt, Jobs ohne `permissions` → **behoben**

* Alle Jobs haben `permissions: contents: read`.
* Alle Actions sind auf **Commit-SHAs** gepinnt (mit Versionsangabe im
  Kommentar): `actions/checkout`, `actions/setup-python`, `astral-sh/setup-uv`,
  `gitleaks/gitleaks-action`.
* Dependabot erkennt die neuen Versionen weiterhin.

### 12. `set-password` / Direktbetrieb: siehe 9. → **behoben**

---

## INFO

### 13. Push-Abonnement: Server postete an beliebige URLs (SSRF) → **behoben**

`src/oenergy/services/push.py`: `_is_allowed_endpoint()` beschränkt Abonnements
auf **https** und die bekannten Push-Dienste (FCM, Mozilla autopush, Apple WNS).

**Test:** `::test_push_only_accepts_known_push_services`,
`::test_push_rejects_unknown_hosts`.

### 14. `/api/health` verrät den Datenbankpfad → **behoben**

Antwort ist jetzt nur noch `{"status": "ok"}`.

**Test:** `::test_health_endpoint_does_not_leak_the_database_path`.

### 15. Unbekannte Job-Namen als Fehlerlauf → **behoben**

`/actions/sync?job=beliebig` wird mit **400** abgewiesen, statt einen
SyncRun-Fehler mit nutzerkontrolliertem String anzulegen.

**Test:** `::test_unknown_sync_job_is_rejected`.

### 16. WebAuthn: User-Verification nicht erzwungen, Challenge ohne Ablauf → **behoben**

* `require_user_verification=True` (konfigurierbar über
  `WEBAUTHN_REQUIRE_USER_VERIFICATION`, Standard `true`) – auch die
  Registrierung verlangt jetzt UV.
* Die Challenge hat ein **Ablauffenster von 2 Minuten** (`CHALLENGE_TTL`) und
  wird mit Zeitstempel in der Sitzung gehalten (`store_challenge()` /
  `load_challenge()` / `clear_challenge()`).

### 17. Geldfelder als Float statt Integer-Cents → **behoben**

* `daily_stats.cost_cents`, `energy_cost_cents`, `standing_cost_cents` und
  `bills.total_cents` sind jetzt `Integer`.
* Migration `ce975c5c8f2f` (mit `CAST(... AS INTEGER)`).
* Die Kostenberechnung rundet auf ganze Cent.

### 18. Kosmetik und Betrieb → **umgesetzt**

* `LOCAL_HOSTS`: der tote Eintrag `"[::1]"` ist entfernt (nach `split(":")`
  konnte er nie matchen).
* `PUBLIC_PATHS`: `/favicon.ico` ist entfernt – es gibt keine solche Route
  (die Favicons kommen als Data-URI aus den Templates).
* `docker-compose.yml`: Der Port bleibt auf allen Interfaces, weil der
  Cloudflare-Tunnel als eigener Container über das Docker-Netz arbeitet. Ein
  Versuch mit `127.0.0.1:8000:8000` führte prompt zu **502** – der Kommentar
  im Compose-Datei erklärt das und nennt die Alternative für einen Tunnel auf
  dem Host.
* `CLAUDE`/`AGENT`-Hinweise: `AGENTS.md` wurde um die Hinweise „Context7
  befragen“, „Inhalte außerhalb von `{% block %}` werden nicht gerendert“ und
  die Datei-Übersicht ergänzt.

---

## Nicht umgesetzt (bewusst)

| Punkt | Begründung |
|---|---|
| **Sitzungen in der Datenbank** statt selbsttragendem Cookie | Der Aufwand steht in keinem Verhältnis zum Nutzen: der Nutzer meldet sich ab, Cookie und Session sind in einem Browserprofil; die Epoche beim Logout deckt den Kopier-Angriff ab. Für ein Mehrbenutzer-Modell wäre das der richtige Weg – das ist im README als „Grenze des Projekts“ vermerkt. |
| **Passwort-Änderung per Passkey** (Sitzungs-Epoche bei Passwortwechsel) | Es gibt nur ein Passwort und keine UI dafür; die Epoche beim Logout deckt den praktischen Fall ab. |
| **Externe Rate-Limit-Infrastruktur** (WAF, Redis) | Für eine Einzelinstanz zu Hause ist das in-process-Rate-Limit angemessen; hinter dem Tunnel wäre es die richtige Stelle, ist aber Betreibersache. |
| **`PRICE_CHEAP_LIMIT`/`PRICE_EXPENSIVE_LIMIT` als Sicherheitsgrenze** | Sie sind reine **Anzeige**schwellen für die Preis-Matrix; für die Kostenrechnung zählt der aus der API gelesene Tarif. Das ist jetzt in `SECURITY.md` ausdrücklich klargestellt. |

---

## Was das Review bestätigt hat (unverändert gut)

* Die **Authentifizierungs-Middleware** wirkt lückenlos – alle Routen antworten
  ohne Anmeldung mit 303 (Seiten) bzw. 401-JSON (API).
* **Passwort-Login**: Argon2id, identische Fehlerantwort, Cookie mit
  `HttpOnly`, `SameSite=Lax`, `Path=/`, `Max-Age`.
* **Kein XSS**, **keine SQL-Injektion**, **keine Secrets in der Historie**,
  **keine bekannten CVEs** im Lockfile, **nicht-root Container**, **Path-Traversal
  abgewehrt**, **Export ohne Secrets**.

Diese Punkte wurden beim Umsetzen nicht angetastet – die Änderungen sind
additiv und die vorhandenen Tests laufen unverändert grün.
