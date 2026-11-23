# Changelog

Alle wichtigen Änderungen an o-energy werden in dieser Datei dokumentiert.

Das Format basiert auf [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
und dieses Projekt folgt [Semantic Versioning](https://semver.org/lang/de/).

## [1.5.5] – 2026-11-23

### Hinzugefügt

- **README-Screenshots** aus der Demo-Datenbank für Desktop, Mobil und Light Mode.
- **GitHub-Link auf der System-Seite** inklusive Icon und Verweis auf Repository,
  Issues und Releases.

### Geändert

- **Material-You-3-Designsystem**: tonale Teal/Petrol-Farbrollen als CSS-Variablen,
  Light- und Dark-Mode, großzügige Formen, Surface-Container statt harter
  Schatten und tabellarische Ziffern für kWh-, Euro- und Prozentwerte.
- **Adaptive Navigation**: permanenter Navigation Drawer ab 1025 px,
  Navigation Rail von 600–1024 px und eine feste Bottom Navigation unter
  600 px (System bleibt über die Top App Bar erreichbar).
- **Charts im M3-Look**: Farben aus dem Token-System, sanfte Gradient-Flächen,
  abgerundete Balken, dezente Rasterlinien und M3-Tooltips.
- **PWA-Feinschliff**: Safe-Area-Abstände für Notch/Home-Bar, 48-px-Touchziele,
  `user-select: none` auf Bedienelementen und ein neuer Service-Worker-Cache
  (`oenergy-v3`), damit Styles und Offline-Seite sicher ausgetauscht werden.

### Behoben

- **Preis-Matrix auf Mobile**: Die 96 15-Minuten-Zellen sprengten die Seite
  (270 px horizontaler Überlauf). Auf Desktop passen alle 24 Stunden in die
  Karte, auf Tablet/Mobile scrollt die Matrix innerhalb ihres Panels.
- **Logout leert den PWA-Cache** jetzt auch über den mobilen Abmelde-Button.
- **Datenstand-Kachel** zeigt den letzten erfolgreichen Sync schon beim ersten
  Seitenaufbau (bisher erst nach dem HTMX-Refresh).

## [1.5.0] – 2026-11-18

### Hinzugefügt

- **MCP-Schnittstelle (nur lesend)**: FastMCP-Server unter `/mcp`
  (Streamable HTTP, stateless) im bestehenden Web-Prozess. Eigener Bearer-Token
  (`MCP_TOKEN`, `oenergy generate-mcp-token --write-env`), eigenes Rate- und
  Body-Limit; ohne Token antwortet der Pfad mit `401`. Acht Werkzeuge
  (`status`, `consumption`, `costs`, `prices`, `cheapest_windows`, `price_at`,
  `monthly_overview`, `account`) und drei Ressourcen (`energy://status`,
  `energy://today`, `energy://month`). Die Service-Schicht liefert aggregierte
  Werte ohne Kontonummer, Zähler-/MaLo-Nummer, E-Mail oder Namen; die
  MCP-Session lässt technisch nur `SELECT` zu. Das Extra `mcp` ist im
  Docker-Image installiert.

### Geändert

- **MCP-Endpunkt kanonisch ohne Schrägstrich**: `/mcp` ist der Endpunkt;
  `/mcp/` wird mit `307` (Methode und Rumpf bleiben erhalten) auf `/mcp`
  umgeleitet. Die Weiterleitung übernimmt `X-Forwarded-Proto` und
  `X-Forwarded-Host`, sodass hinter Tunnel/Proxy auch im `Location`-Header
  `https://…/mcp` steht.

### Sicherheit

Die Ergebnisse zweier unabhängiger Sicherheits- und Code-Reviews (siehe
[REVIEW_1.md](REVIEW_1.md) und [REVIEW_2.md](REVIEW_2.md)) sind umgesetzt:

- **Der Service Worker cacht keine authentifizierten Seiten mehr** – vorher
  überlebten Verbrauchs-, Kosten- und Kontodaten die Abmeldung im
  Browser-Cache. Beim Abmelden wird der Cache zusätzlich geleert.
- **Keine offene Weiterleitung in der Passkey-Anmeldung**: das `next`-Ziel wird
  jetzt mit derselben Regel geprüft wie beim Passwort-Login (nur relative
  Ziele innerhalb der App).
- **Sicherheits- und Cache-Header** für alle Antworten (`nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`, CSP) und
  `Cache-Control: no-store` für authentifizierte Seiten.
- **Limit für die Request-Größe** (256 KB, sonst 413) – ein anonymer Angreifer
  kann nicht mehr mit einem einzelnen Riesenkörper den Speicher belasten.
- **Rate-Limit für Anmeldeversuche**: nach 10 Fehlversuchen wird die Herkunft
  für 5 Minuten gesperrt.
- **Der Bypass-Token** wird zeitkonstant verglichen; doppelt gesetzte Header
  werden verworfen.
- **Sitzungen werden beim Abmelden serverseitig entwertet** (Sitzungs-Epoche) –
  ein kopierter Cookie-Wert ist danach wertlos.
- **Push-Abonnements** sind auf bekannte Push-Dienste beschränkt (kein SSRF
  über den Server).
- **Dateirechte**: Datenbank, WAL, Log und Backups werden mit `0600` angelegt.
- **CI**: alle Jobs bekommen `permissions: contents: read`, die Actions sind auf
  Commit-SHAs gepinnt.
- **Härtung des WebAuthn-Flows**: die Challenge hat ein Ablauffenster (2
  Minuten), die Benutzer-Verifikation (PIN/Biometrie) wird erzwungen.
- Die **Middleware-Reihenfolge** wurde korrigiert: die SessionMiddleware muss
  ausserhalb der Anmeldeprüfung liegen, sonst sieht diese kein dekodiertes
  Cookie (Regressionstest vorhanden).
- **Das `next`-Ziel wird im Login-Skript JSON-escaped** eingebettet – ein
  Backslash oder Zeilenumbruch kann den JavaScript-String nicht mehr
  aufbrechen (verhinderte bisher die Passkey-Anmeldung).
- **IPv6-Hosts werden korrekt erkannt**: `[::1]:8000` gilt als lokal,
  Userinfo-Tricks wie `evil.example@localhost` werden verworfen.

### Behoben

- `uvicorn` wertet jetzt `X-Forwarded-Proto`/`-Host` aus – hinter einem Tunnel
  stimmen damit Schema und Host (nötig für Passkeys und das Zugriffs-Log).
- Der Compose-escapte Passwort-Hash (`$$argon2id$$…`) wird beim Laden
  normalisiert – im Direktbetrieb war die Anmeldung sonst unmöglich.
- `/api/health` gibt den Datenbankpfad nicht mehr nach außen.
- Unbekannte Job-Namen beim manuellen Sync werden mit 400 abgewiesen.
- Geldbeträge liegen jetzt in ganzzahligen Cent (kein Rundungsdrift).
- **Die Lieferung der Zählerdaten muss nicht pünktlich sein**: bisher hing die
  Abholung am stündlichen Job und einem Sperrfenster von 60 Minuten – kam die
  Lieferung nach 08:00 Uhr, konnte der Vortag bis zur nächsten vollen Stunde
  unsichtbar bleiben. Das Sperrfenster ist jetzt 30 Minuten, und der
  *Tagesabschluss* (08:17) holt die Daten selbst ab, statt sich auf den
  stündlichen Job zu verlassen.
- Die Sync-Übersicht nennt die Jobs jetzt verständlich („Preise“, „Zählerdaten“,
  „Tagesabschluss“ …) und erklärt sie in einem Satz darunter.

### Dokumentation

- **AI.md**: wie das Projekt mit KI-Unterstützung entwickelt wurde und welche
  Leitplanken für Mitwirkende gelten (mit und ohne KI).

## [1.2.0] – 2026-11-15

Die App ist jetzt fürs Telefon gemacht: installierbar als progressive Web App,
optimiertes Layout und eine Push-Nachricht, sobald die Daten des Vortags da sind.

### Hinzugefügt

- **Progressive Web App**: Manifest, Icons, Service Worker und eine Offline-Seite –
  die App lässt sich auf dem Telefon installieren („Zum Home-Bildschirm“) und
  startet im eigenen Fenster. Die statischen Dateien werden gecacht, die
  API-Daten nie.
- **Mobile-optimiertes Layout**: Kacheln im Zweispalten-Raster, scrollbare
  Navigation, Tabellen waagerecht scrollbar, kleinere Diagramme, Safe-Area für
  Notch-Geräte.
- **Web-Push-Benachrichtigungen**: Meldet der Netzbetreiber die Daten des
  Vortags, schickt die App eine Nachricht an das Telefon
  („Neue Daten vom 14.11.: 8,5 kWh für 4,40 €“). Abonnement über die Seite
  *System*, Testnachricht per Button. Die VAPID-Schlüssel werden automatisch
  erzeugt und in der Datenbank abgelegt – Zustellung an Chrome/FCM und
  Firefox/Mozilla bestätigt.
- **Zugriff ohne Anmeldung für lokale Tests**: `AUTH_LOCAL_BYPASS` (Host und
  Quelle müssen lokal sein) oder das Secret-Header `X-OEnergy-Token`
  (`oenergy generate-token --write-env` – der Wert steht nur in der `.env`).

### Behoben

- Die Tabelle „Zählerstände“ auf der Verbrauchsseite war immer leer, weil sie
  die Rohzählerstände zeigte statt der tatsächlich gemessenen Intervalle.
- **Der Grundpreis (10,21 €/Monat) fehlte in allen Auswertungen**: die Kosten-
  berechnung nahm den zuletzt aktualisierten Vertrag – das ist oft der beendete
  „Light“-Tarif, der keinen Grundpreis hat. Jetzt wird der aktive Vertrag
  verwendet; der Grundpreis wird auch an Tagen ohne Verbrauchsdaten anteilig
  berechnet, weil die Grundgebühr ja täglich läuft.
- **Web-Push**: der VAPID-Schlüssel wurde als PEM gespeichert, pywebpush erwartet
  ihn aber als base64url-Rohwert (oder als Datei) – jede Nachricht scheiterte mit
  „Could not deserialize key data“. Außerdem trug pywebpush das `aud`-Claim des
  ersten Abonnements in das gemeinsame Claims-Dict ein und schickte es auch an
  das zweite → „aud claim … MUST include the origin“. Jetzt: eigener Schlüssel
  je Abo, Zustellung an beide Geräte bestätigt.
- **Veraltete Dateien im Browser/CDN-Cache**: der Service Worker lieferte die
  alte `app.js` aus seinem Cache aus, dadurch blieb eine veraltete
  Service-Worker-Registrierung aktiv und `navigator.serviceWorker.ready` löste
  nie auf (die Push-Anmeldung blieb ohne Wirkung). Jetzt: eigene Dateien werden
  netz-first geladen, alle Asset-URLs tragen einen Versionsparameter und der
  Service Worker wird ohne HTTP-Cache aktualisiert.

## [1.1.0] – 2026-11-15

Die App ist jetzt für den Betrieb im Internet gedacht: Anmeldung mit Passwort und
Passkey, dazu eine Arbeitsanleitung für Mitwirkende.

### Hinzugefügt

- **Anmeldung mit Passwort** (Argon2id über `pwdlib`, Sitzung in einem signierten
  Cookie, 14 Tage gültig). Es gibt nur einen Benutzer, deshalb wird **kein
  Benutzername** abgefragt. `oenergy set-password` erzeugt den Hash,
  `--write-env` schreibt ihn direkt in die `.env` (inklusive Escaping für Docker
  Compose). Ohne gesetzten Hash bleibt die App offen und weist in der Kopfzeile
  mit einem gelben Hinweis darauf hin.
- **Passkeys (WebAuthn)** über `py_webauthn`: Anmeldung per Fingerabdruck, Gesicht
  oder Geräte-PIN. Registrierung auf der Seite *System* (mit Namen und der
  Möglichkeit, einen Schlüssel wieder zu entfernen), Anmeldung über die
  Login-Seite. RP-ID und Origin kommen aus `WEBAUTHN_RP_ID`/`WEBAUTHN_ORIGIN` oder
  werden aus der Anfrage abgeleitet.
- **AGENTS.md**: vollständige Arbeitsanleitung für Menschen und KI-Agenten –
  Aufbau, Technologiestapel, Teststrategie, live verifizierte API-Fakten,
  Konventionen, Stolperfallen und Release-Checkliste.
- **Optionaler Cloudflare-Tunnel** als Compose-Profil `tunnel`.
- `oenergy token-info` und `oenergy whoami` zeigen die verbleibende Laufzeit der
  Tokens und den angemeldeten Benutzer.

### Behoben

- Das Passkey-Skript lag außerhalb der Jinja-Blöcke und wurde deshalb gar nicht
  ausgeliefert (die Liste blieb bei „Wird geladen …“, der Button tat nichts).
  `base.html` hat dafür einen eigenen `{% block scripts %}`.
- Die Passkey-Login-Endpunkte wurden von der Auth-Middleware auf die Login-Seite
  umgeleitet; das JavaScript bekam HTML statt JSON. Die Endpunkte sind jetzt
  öffentlich, und API-Aufrufe antworten bei fehlender Session mit JSON/401 statt
  einer Umleitung.
- **Docker Compose interpoliert die `.env`**: ein Argon2-Hash muss dort als
  `$$argon2id$$…` escaped werden, sonst kommt er zerstört im Container an und
  die Anmeldung schlägt immer fehl. `set-password --write-env` erledigt das
  automatisch.
- Die Tests hingen von der lokalen `.env` ab (Anmeldung, `COOKIE_SECURE`,
  WebAuthn-Einstellungen) – sie setzen die Variablen jetzt selbst.
- Die Scheduler-Startmeldung war noch im alten `%s`-Format und zeigte statt der
  Poll-Intervalle „prices every %s min“.

### Sicherheit

- Die Anmeldung schützt auch die API-Endpunkte (Diagramme, Export). Öffentlich
  sind nur `/login`, `/api/health`, die statischen Dateien und die
  Passkey-Anmeldung.

## [1.0.0] – 2026-11-15

Erste stabile Fassung. Die Beta 0.8.0 hat die Anbindung an die Octopus-API
 gebracht; danach wurden im Review die Bedienung, die Auswertungen und die
Datenpflege nachgeschärft.

### Hinzugefügt

- Seite „Verbrauch“: neues Diagramm „Verbrauch und Preis“ – die 15-Minuten-Slots
  der letzten zwei Tage als Balken mit zusätzlicher Preislinie (zweite Achse), so
  wird sichtbar, zu welchem Preis verbraucht wurde.
- Seite „Verbrauch“: darunter der Monatsverbrauch als Balkendiagramm mit
  Vor-/Zurück-Navigation über die Monate (endgültige Tage hell, provisorische
  dunkel). Tage ohne Zählerdaten werden als Lücke dargestellt, nicht als 0 kWh.
- **Backfill bis zum Ende der Historie**: die App holt die Historie schrittweise
  weiter zurück (30 Tage pro Lauf), bis die API keine Werte mehr liefert. Der
  Zustand wird am Zähler vermerkt (`history_start`, `history_exhausted`) – danach
  wird nicht mehr weitergefragt. Die verbleibende Historie steht auf der
  System-Seite.
- **System-Seite** zeigt jetzt die Daten aus der API: angemeldeter Nutzer,
  Konto mit Anbieter, Status und Saldo, Verträge (Produktcode, Preisart,
  Lieferpunkt, Grundpreis, USt), Lieferpunkt mit Adresse, MaLo- und
  Zählernummer sowie den Stand der Datenhistorie.
- **Export und Import**: der gesamte Datenbestand lässt sich als JSON exportieren
  (`oenergy export`) und wieder zurückspielen (`oenergy import`). Das Format ist
  schemaunabhängig und die Verarbeitung idempotent – ein Import ändert nichts
  doppelt. Im Web gibt es den Export als Download auf der System-Seite.
- Das Backup-Skript erstellt jetzt zusätzlich zum SQLite-Backup einen
  JSON-Export und behält die 10 neuesten Sicherungen.

### Geändert

- Dashboard: „Kosten heute“ und „Verbrauch heute“ ersetzt durch die Werte von
  gestern – die Daten des heutigen Tages liefert der Netzbetreiber erst am
  Folgetag.
- „Datenstand“ zeigt jetzt den letzten **erfolgreichen** Sync und je Job den
  Status (statt des zuletzt fehlgeschlagenen Laufs).
- Preis-Matrix nutzt die zusammengesetzten Preise (Börsenpreis + Basiskomponente)
  und färbt sie mit einem durchgehenden Verlauf von grün (günstig) über gelb
  (normal) bis rot (teuer); die Schwellen sind über `PRICE_CHEAP_LIMIT` und
  `PRICE_EXPENSIVE_LIMIT` konfigurierbar.
- Der Backfill fragt nur noch Zeitfenster ab, für die der Netzbetreiber die Werte
  überhaupt liefern konnte (Lieferung gegen 08:00 Uhr des Folgetages).
- Historie der Börsenpreise wird nachgeladen (`oenergy sync prices --history-days 30`),
  damit vergangene Kundenpreise exakt berechnet werden können.
- Logging auf **loguru** umgestellt (farbig, Datei-Sink mit Rotation, Standardbibliothek-
  Logs von uvicorn/httpx/SQLAlchemy werden abgefangen) – inklusive Umstellung der
  Logmeldungen auf den `{}`-Stil.
- Die entwertete Abfrage `supplyPoint.readings` wurde entfernt (die Verbrauchsdaten
  kommen aus `property.measurements`).

### Behoben

- **Zeitzone**: die Zeitstempel in der Oberfläche wurden in UTC statt in der
  lokalen Zeit angezeigt (Sync-Läufe wirkten eine Stunde zu früh). Alle
  Anzeigen werden jetzt nach `TIMEZONE` umgerechnet - auch die Ausgaben von
  `oenergy token-info`.
- **„Heute“ entfernt**: der heutige Tag kann keine Zählerdaten haben (Lieferung
  erst am Folgetag). Die Kacheln „Verbrauch heute“ und „Kosten heute“ sowie der
  heutige Tag in den Diagrammen (Verbrauchsprofil, Tageskosten) sind entfernt.
- **Diagnose ohne Passwort**: `oenergy diagnose` nutzt jetzt den gespeicherten
  Token und funktioniert auch ohne Zugangsdaten in der `.env`. `oenergy login`
  sagt klar, dass für eine Anmeldung das Passwort nötig ist.
- Die Monatsprognose war fehlerhaft: sie zählte die bereits angefallenen Kosten
  nicht mit und doppelte den heutigen Tag. Jetzt ist sie die erwarteten
  **Gesamtkosten des Monats** (bereits angefallen + offene Tage × Tagesschnitt
  der letzten 7 Tage) – mit Angabe der Unsicherheit (± Standardabweichung je Tag
  × Wurzel aus der Zahl der offenen Tage). Die Unsicherheit sinkt also, je näher
  das Monatsende rückt.

### Sicherheit

- Die Zugangs-Kachel auf der System-Seite zeigt den tatsächlichen Token-Status
  (Refresh-Token aktiv mit Restlaufzeit, Token aktiv, erneuern nötig) statt nur zu
  prüfen, ob das Passwort in der `.env` steht.
- Der letzte erfolgreiche Sync wird auch in älteren Läufen gesucht, damit die
  Kachel nicht fälschlich „noch kein Sync“ anzeigt.

## [0.8.0] – 2026-11-15 (Beta)

Erste öffentliche Fassung: die App holt die Octopus-Energy-DE-Daten selbst von der
Kraken-GraphQL-API ab, speichert sie in SQLite und zeigt sie in einer
HTMX-Web-Oberfläche.

### Hinzugefügt

**Datenquelle**

- Kraken-GraphQL-Client mit Token-Cache in der Datenbank, automatischem Refresh,
  Bearer-Fallback, Retry mit Backoff und Drosselung auf 1,2 Requests/s
  (die API ist stark ratenbegrenzt).
- Preise aus `agreement(id).unitRateForecast` (15-Minuten-Slots für heute und
  die Zukunft) und `epexDayAheadPrices`.
- Verbrauch aus `property(id).measurements(readingFrequencyType: RAW_INTERVAL)` –
  echte 15-Minuten-Intervalle des Smart-Meter-Gateways („Amphio“).
- Rechnungen und Kontostand aus `account.bills`.
- Diagnose-Kommando, das prüft, welche Daten ein Account tatsächlich liefert.

**Fachlogik**

- Preiszerlegung: eine Tarifzeit kann aus mehreren Raten bestehen (Basis- und
  variable Komponente); die App summiert sie zum echten Kundenpreis. Die
  Preishistorie wird aus EPEX + Basiskomponente rekonstruiert und ist damit exakt.
- Umgang mit dem Lieferverzug des Netzbetreibers (~24 h): jeder Tag hat den Status
  `final`, `provisorisch`, `ausstehend` oder `warten`; vollständige Tage werden
  erst nach der erwarteten Lieferung (08:00 Uhr plus Toleranz) abgeschlossen.
- Automatischer Backfill: bereits abgefragte Zeitfenster werden in
  `reading_windows` gemerkt, nachgefragt werden nur echte Lücken.
- Kosten je 15-Minuten-Slot inklusive Grundpreis, Tages-/Monats-/Jahreswerte,
  Monatsprognose und Abgleich mit den Rechnungen aus dem Kundenkonto.

**Oberfläche**

- Fünf Seiten: Dashboard, Verbrauch, Preise, Kosten, System.
- Live-Kacheln über HTMX (60-Sekunden-Refresh), Chart.js-Diagramme für Preis,
  Verbrauch und Kosten, Preis-Matrix (Tag × 15-Minuten-Slot) als CSS-Heatmap.
- Dark Mode, CSV-Export der Preise, Anzeige der aktuellen Preisbestandteile.

**Betrieb**

- Docker-Compose-Setup mit nicht-root Benutzer, Healthcheck, Volume für die
  SQLite-Datei und optionalem Sync-Worker ohne Web-Server.
- APScheduler-Jobs: Preise alle 15 Minuten, Zählerdaten stündlich, Tagesabschluss
  täglich 08:17 Uhr, Rechnungen täglich 06:12 Uhr, Konto alle 12 Stunden.
- Alembic-Migrationen, Backup-Skript, README mit Ersteinrichtung und
  Betriebsanleitung.

**CLI**

`serve`, `sync`, `login`, `token-info`, `whoami`, `diagnose`, `probe`, `seed`,
`reset`, `init-db`, `jobs`

### Geändert

- Verbrauchswerte werden dem Intervall zugeordnet, das mit dem späteren
  Zählerstand endet (vorher 15 Minuten versetzt).
- Zählregister werden nicht mehr überschrieben, sondern aktualisiert; nach
  gelieferten Daten werden die betroffenen Intervalle neu berechnet.
- Die USt wird normalisiert (die API liefert 19 statt 0,19).
- `refreshExpiresIn` wird als Millisekunden interpretiert (20 Tage statt 57 Jahre).
- Der Grundpreis wird als EUR- oder Cent-Wert erkannt (10,21 €/Monat).

### Lizenz

- Das Projekt steht unter der **MIT-Lizenz** (`LICENSE`, `project.license = "MIT"`).

### Sicherheit

- Der Container läuft als nicht-root Benutzer (uid/gid 1000).
- Zugangsdaten stehen ausschließlich in der `.env` (chmod 600) und sind nicht im
  Git-Repository; das Refresh-Token liegt nur in der lokalen SQLite-Datei.
- Es werden keine Zugangsdaten im Log ausgegeben.

### Bekannte Einschränkungen

- Ein länger gültiges Refresh-Token ist für Kundenkonten nicht freigeschaltet;
  nach 20 Tagen ist ein erneutes `oenergy login` nötig (oder das Passwort bleibt
  in der `.env`).
- Die Preisreihe `agreementRates` sowie die Abfragen `electricityMeters` und
  `electricityMeterReadings` sind für Kundenkonten gesperrt; die App nutzt die
  freigegebenen Alternativen.
- Die API unterstützt keine 15-Minuten-Aggregation – die Rohintervalle
  (`RAW_INTERVAL`) werden verwendet.
- Die Preis-Historie setzt voraus, dass die EPEX-Preise der App zur Verfügung
  stehen; für sehr alte Zeiträume liefert die API keine Börsenpreise mehr.
- Es ist nur Strom abgebildet; das Datenmodell ist auf weitere Tarifarten
  vorbereitet.

<!-- Die Vergleichslinks werden ergänzt, sobald das Remote-Repository steht. -->
