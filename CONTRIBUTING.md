# Contributing

Thank you for helping. pi-guard is used in homes of people who will never read
this file, so changes are judged by one question first: does this make the
system more reliable, simpler, or safer for them?

## Development setup

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests
.venv/bin/mypy
```

FFmpeg must be installed for the tests marked `ffmpeg`. Tests marked
`hardware` need real devices and are excluded by default:

```bash
TEST_MICROPHONE=plughw:CARD=WEBCAM,DEV=0 .venv/bin/pytest -m hardware
```

To run the service locally, see "Manual installation" in
[docs/installation.md](docs/installation.md).

## Guidelines

- Recording must never depend on messaging, MQTT, presence or detection.
  New long-running parts are supervised components; new event consumers get a
  bounded queue.
- Every user-facing string goes into both `i18n/en.json` and `i18n/de.json`
  (the tests enforce identical keys and placeholders). Write for someone who
  is not technical: short sentences, no jargon, no emoji.
- New chat commands are registered once in the `Registry`, with English and
  German aliases, and get a test for their aliases and arguments.
- Privacy-relevant features are off by default and documented in
  `docs/privacy.md`.
- No new dependency without a clear reason. No placeholder code.
- Mock hardware and network in tests. Do not claim hardware support you have
  not tested; update the table in `docs/hardware.md` when you have.

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/):
`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `perf:`, `chore:`.
One logical change per commit; tests pass on every commit. Never commit
`.env` files, tokens, recordings, databases or model files.

## Hardware reports

Reports from real installations are especially valuable. Please include the
board, OS version, camera model, `pi-guard doctor` output (it contains no
secrets) and what worked or did not.
