# Sicherheitsrichtlinie

## Einhaltung

**o-energy** ist ein privates Selbstbedienungswerkzeug: Es läuft auf einem eigenen
Rechner, greift mit den Zugangsdaten des Nutzers auf das Octopus-Portal zu und
zeigt Verbrauchs-, Preis- und Kontodaten. Es ist **kein Mehrbenutzer-Produkt**.

| Version | Unterstützt |
|---|---|
| 1.2.x | ✅ |
| < 1.2 | – |

## Eine Schwachstelle melden

Bitte **nicht** über ein öffentliches Issue melden. Schreib stattdessen eine
privat Nachricht an den Maintainer (siehe die Kontaktadresse im Repository) mit:

* Beschreibung der Schwachstelle und der betroffenen Version
* Schritten zur Reproduktion
* Einschätzung der Auswirkung (welche Daten wären betroffen?)

Du bekommst eine Rückmeldung, ob das Ticket angenommen wird. Eine Veröffentlichung
erfolgt erst, wenn ein Fix verfügbar ist.

## Sicherheitsmodell

### Was die App schützt

* **Anmeldung**: Argon2id-Hash in der `.env`, Sitzung in einem **signierten**
  Cookie (`SameSite=Lax`, 14 Tage). Optional `COOKIE_SECURE=true` für reinen
  HTTPS-Betrieb.
* **Passkeys**: WebAuthn/FIDO2 – der Schlüssel verlässt das Gerät nie.
* **Der Datenbestand** liegt in `data/o-energy.db` (SQLite) auf dem eigenen
  Rechner; das Refresh-Token des Octopus-Zugangs wird dort abgelegt.
* **MCP-Zugang**: eigener Bearer-Token (`MCP_TOKEN`) nur für `/mcp`,
  zeitkonstanter Vergleich, eigenes Rate- und Body-Limit. Der Zugang ist strikt
  lesend (Session mit ausschließlich `SELECT`) und liefert keine Kontonummer,
  Zähler-/MaLo-Nummer, E-Mail-Adresse oder Namen. Web-Passwort und
  `X-OEnergy-Token` gelten dort nicht – und umgekehrt.

### MCP-Zugang – was er darf

* **Erlaubt:** aggregierte Verbrauchs-, Kosten-, Preis- und Kontodaten lesen
  (Tarif, Grundpreis, USt, Kontostand) – ohne Adresse, Kontonummer,
  Zähler-/MaLo-Nummer, E-Mail oder Namen.
* **Nicht möglich**: einen Sync auslösen, Daten importieren, ändern oder
  löschen. Die Werkzeuge erhalten eine Session, die nur `SELECT` zulässt
  (`readonly_session`); ein Schreibversuch bricht mit `ReadOnlyViolation` ab.
* **Token**: `MCP_TOKEN` steht in der `.env` und ist wie ein Passwort zu
  behandeln. Bei Verdacht neu erzeugen (`oenergy generate-mcp-token --write-env`)
  und den Dienst neu starten.

### Was die App ausdrücklich nicht schützt

* **Es gibt genau einen Benutzer.** Es gibt keine Rollen, keine Rechte und keine
  Mandantenfähigkeit. Wer angemeldet ist, sieht alles.
* **Ohne `AUTH_PASSWORD_HASH` ist die App offen.** Das ist für den lokalen
  Betrieb gewollt und wird in der Kopfzeile gewarnt. Für einen Betrieb im Internet
  (Cloudflare-Tunnel, Portfreigabe) **muss** ein Passwort gesetzt sein, sonst sind
  Verbrauchsdaten, Konto-, Zählernummer und Adresse für jeden mit der URL
  sichtbar.
* **Die Zugangsdaten zur Octopus-API** (E-Mail, Passwort, Access- und
  Refresh-Token) liegen im Klartext in der `.env` bzw. in der Datenbank. Beides
  gehört auf den eigenen Rechner – `.env` mit `chmod 600`, Datenbankverzeichnis
  nicht öffentlich, beides nicht ins Repository (siehe `.gitignore`).
* **Der Service Worker** cacht die statischen Dateien, aber **keine
  authentifizierten Seiten und keine API-Antworten** – nur die Login-Seite
  (ohne Daten). Beim Abmelden wird der Cache zusätzlich geleert.
* **Sicherheits-Header**: alle Antworten tragen `X-Content-Type-Options`,
  `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy` und eine CSP;
  authentifizierte Seiten zusätzlich `Cache-Control: no-store`.
* **Rate-Limit**: Nach 10 fehlgeschlagenen Anmeldeversuchen wird die Herkunft
  für 5 Minuten gesperrt.
* **Request-Größe**: Bodies über 256 KB werden mit 413 abgewiesen.
* **Sitzungen**: Das Cookie ist selbsttragend, wird aber beim Abmelden serverseitig
  entwertet (Sitzungs-Epoche) – ein kopierter Cookie-Wert ist danach wertlos.
* **Push-Abonnements** sind auf die bekannten Push-Dienste beschränkt (FCM,
  Mozilla, Apple) – der Server postet nicht an beliebige URLs.
* **Dateirechte**: Datenbank, WAL, Log und Backups werden mit `0600` angelegt
  (nur für den Eigentümer lesbar).

### Empfehlungen für den Betrieb im Internet

1. Passwort setzen: `uv run oenergy set-password --write-env`
2. `COOKIE_SECURE=true` setzen (die App läuft dann ausschließlich über HTTPS)
3. Passkeys einrichten (Seite *System*) – dann genügt das Gerät
4. Optional eine zusätzliche Absicherung vor der App (Cloudflare Access,
   VPN, nginx mit Basic Auth)
5. `data/` nicht in ein öffentlich erreichbares Verzeichnis legen; Backups
   enthält sensible Daten

## Kein Secret im Repository

`.env`, `data/`, `probe.json` und `*.db` sind in `.gitignore`. Wer Zugangsdaten
versehentlich committet hat, sollte sie beim Anbieter widerrufen und das
Refresh-Token ungültig machen (ein neuer Login erzeugt ein neues Token).

## Gefundene Probleme

Sicherheitsprobleme, die uns gemeldet wurden, werden im
[CHANGELOG](CHANGELOG.md) unter „Sicherheit“ der jeweiligen Version vermerkt.
