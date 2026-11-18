# Mitwirken

Danke, dass du dir o-energy anschaust! Das Projekt ist klein genug, dass ein
PR von Hand gut funktioniert – und die technischen Feinheiten sind in
**[AGENTS.md](AGENTS.md)** dokumentiert (auch für KI-Agenten).

## Setup

```bash
git clone https://github.com/<owner>/o-energy.git
cd o-energy
uv sync
uv run alembic upgrade head
uv run pytest
```

Für die Oberfläche ohne echte Zugangsdaten:

```bash
uv run oenergy seed --days 30
uv run oenergy serve
```

## Vor dem Absenden

```bash
uv run pytest              # alle Tests müssen grün sein
uv run ruff check .        # Linting
uv run ruff format .       # Formatierung
```

## Was zu einer Änderung gehört

| Änderung | dazu |
|---|---|
| Verhalten der App | ein Test in `tests/` |
| neue Konfiguration | `.env.example` **und** README-Konfigurationstabelle |
| neue API-Nutzung | Eintrag in `AGENTS.md` § 5 (live verifizierte Fakten) |
| sichtbares Feature | `CHANGELOG.md` unter `[Unreleased]` |
| Fehler | `CHANGELOG.md` unter `[Unreleased]` → *Behoben* |

Die Commit-Nachricht folgt dem Muster `feat:`, `fix:`, `docs:`, `refactor:` –
am besten auf Deutsch, ein Satz pro Zeile.

## Konventionen

* **Code und Kommentare auf Deutsch**, Bezeichner englisch.
* **Typannotationen** überall (Python 3.12).
* **loguru** statt `logging`, Formatierung mit `{}` (nicht `%s`).
* **Zeiten**: in der Datenbank naive UTC, in der Ausgabe lokal (`TIMEZONE`).
* **`services/` bleibt HTTP-frei** – dort steht die Fachlogik, ohne FastAPI und
  ohne HTML. Das macht sie ohne Mocking testbar.
* **Keine Geheimnisse** in den Commits.

## API-Anfragen

Die Octopus-API ist undokumentiert und authorizationsabhängig. Bevor du eine
neue Query verwendest:

```bash
uv run oenergy diagnose     # was liefert der Account tatsächlich?
uv run oenergy probe --out probe.json
```

Neue Queries bitte **live am Account prüfen** und das Ergebnis in
`AGENTS.md` § 5 festhalten – das spart allen nachfolgenden viel Zeit.

## Pull Requests

1. Branch vom `main` abzweigen (`feat/…`, `fix/…`)
2. Kleine, zusammenhängende Änderungen pro PR
3. Im PR-Beschreibung: **was** geändert wurde und **warum**, ggf. ein Screenshot
   bei UI-Änderungen
4. `CHANGELOG.md` nicht vergessen
