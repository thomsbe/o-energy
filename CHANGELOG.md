# Changelog

Alle wichtigen Änderungen an o-energy werden in dieser Datei dokumentiert.

Das Format basiert auf [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
und dieses Projekt folgt [Semantic Versioning](https://semver.org/lang/de/).

## [Unreleased]

Nichts vorbereitet.

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

### Geändert (Review-Runde)

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

### Erweitert (Review-Runde 2)

- Seite „Verbrauch“: neues Diagramm „Verbrauch und Preis“ – die 15-Minuten-Slots
  der letzten zwei Tage als Balken mit zusätzlicher Preislinie (zweite Achse), so
  wird sichtbar, zu welchem Preis verbraucht wurde.
- Seite „Verbrauch“: darunter der Monatsverbrauch als Balkendiagramm mit
  Vor-/Zurück-Navigation über die Monate (endgültige Tage hell, provisorische
  dunkel). Tage ohne Zählerdaten werden als Lücke dargestellt, nicht als 0 kWh.

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
