# KI-Unterstützung in diesem Projekt

## Kurzfassung

o-energy wurde mit Unterstützung von **KI-Agenten** entwickelt (Kilo Code mit dem
Modell *MiniMax 3.1 Flash*), **überwacht und verantwortet von Thomas Bär**
(Maintainer). Der Maintainer hat jeden Commit freigegeben, jeden PR reviewed und
die Veröffentlichung verantwortet.

## Was das bedeutet

* **Der Mensch ist der Autor.** Die KI war Werkzeug, nicht Urheber. Das Projekt
  steht unter der [MIT-Lizenz](LICENSE), und die Lizenz gilt für den
  veröffentlichten Code.
* **Jeder Commit ist von Hand freigegeben.** Die KI hat nie selbstständig in
  `main` geschrieben – Änderungen entstanden immer als Arbeitsstand, wurden
  geprüft und dann mit einer sprechenden Nachricht (`feat:`, `fix:`, `docs:`)
  committet.
* **Die CI ist das Sicherheitsnetz.** Bei jedem Push laufen Linting, Formatierung,
  81 Tests, der Paketbau und der Docker-Build. Ein Commit, der die CI nicht
  besteht, ist kein guter Commit – das hat sich in diesem Projekt mehrfach bewährt
  (die CI hat Linting-Fehler gefunden, die lokal übersehen wurden).
* **Die harten Fakten sind live verifiziert.** Die Angaben zur Octopus-API in
  [AGENTS.md](AGENTS.md) § 5 stammen aus Abfragen am echten Konto, nicht aus
  Annahmen. Das ist die wichtigste Barriere gegen „halluzinierte“ API-Nutzung.

## Leitplanken für Mitwirkende – mit und ohne KI

Diese Regeln gelten für jeden Beitrag, unabhängig davon, ob eine KI beteiligt
war:

1. **[AGENTS.md](AGENTS.md) lesen.** Dort stehen die Regeln, die Stolperfallen
   und die live verifizierten API-Fakten. Wer eine Bibliothek oder eine API
   annimmt, hat nicht gelesen.
2. **Keine Annahmen über Bibliotheks-APIs.** Erst die aktuelle Doku befragen
   (Context7 oder die Projektdoku), dann verwenden.
3. **Keine erfundenen API-Fakten.** Neue Queries werden live am Account geprüft
   (`oenergy diagnose`, `oenergy probe`) und das Ergebnis in AGENTS.md § 5
   festgehalten.
4. **Jede Verhaltensänderung braucht einen Test.** Tests prüfen die Fachlogik,
   nicht die Octopus-API (diese wird mit `respx` gemockt).
5. **Die CI muss grün sein.** Linting (`ruff check .`), Formatierung
   (`ruff format --check .`), Tests und Build.
6. **Keine Geheimnisse.** `.env`, `data/` und `probe.json` gehören nie ins
   Repository. Siehe [SECURITY.md](SECURITY.md).
7. **Der Maintainer reviewt jeden PR** – auch KI-generierten Code. Bei
   Zweifeln wird die betroffene Aussage am lebenden System geprüft statt
   geglaubt.

## Transparenz

Die Historie des Projekts ist die Zusammenarbeit selbst: die Commit-Nachrichten
beschreiben, was geändert wurde und warum, und die Changelog-Datei
([CHANGELOG.md](CHANGELOG.md) hält fest, welche Fehler aufgetreten sind. Bei
Beiträgen mit KI-Beteiligung ist es hilfreich, das im PR zu erwähnen – die
Entscheidung liegt beim Maintainer.

## Was hier bewusst nicht passiert

* Keine KI-generierten Inhalte ohne Gegenprüfung am laufenden System (Preisformel,
  Token-Laufzeiten, Lieferzeitpunkt, Anmeldefluss).
* Keine automatisch übernommenen Abhängigkeiten – jede Bibliothek ist mit einer
  Begründung in [CONTRIBUTING.md](CONTRIBUTING.md) und im Changelog vermerkt.
* Kein Zugriff der KI auf Geheimnisse: die `.env` wird nicht gelesen, Token
  erscheinen nicht im Log und nicht in Commits.
* Keine KI-generierten Veröffentlichungen: Der Maintainer veröffentlicht
  (`uv run oenergy publish`) und verantwortet den Inhalt.
