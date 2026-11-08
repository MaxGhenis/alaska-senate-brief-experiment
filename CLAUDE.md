# CLAUDE.md

A randomized test of what two blocks of a forecasting brief do to AI forecasters' conditional forecasts
at the cutoff, on Alaska's 2026 US Senate count. `README.md` registers the design, the measures, the
primary outcomes and the exclusions; read it first.

- Keep this repository private until `prereg/collected/` exists, then make it public. A forecaster
  that found the registration could learn what the study expects.
- Never add the Sol runs' session logs (`runs/*/codex-sessions/rollout-*.jsonl`) to git: they carry
  the lanes' account details. `.gitignore` keeps them out; keep a private copy outside the repository.
- Once `prereg/registration/` exists, leave the registered design alone: `README.md`'s design and
  analysis sections, `design/assignment.json`, the briefs, `graph/graph.yaml`, `context/` and the
  definitions in `src/i86/arms.py`. Record any later change as a dated deviation in
  `prereg/README.md`, with its reason, and never pool runs made with different brief hashes.
- Logged runs are append-only. Never edit `runs/*/response.md`, `response.json` or `meta.json` after
  `i86 collect` copies them in.
- Before registration, a change to `graph/graph.yaml`, `context/` or `src/i86/prompt.py` changes the
  briefs. Regenerate them with `.venv/bin/i86 prompt` and commit them.
- Dispatch each label in `design/assignment.json` once, in the file's order, only on Subfleet lanes:
  Opus 5.5 with `--tier standard -m opus`, GPT-6.1 Sol with `--tier hard`. Never on the OpenAI API.
- Never copy an answer into `runs/` while any run is still going; `i86 collect` enforces this.
- Every run must finish before the Division of Elections' count on 2026-11-10. Before the first
  dispatch, copy both jobs in `launchd/` to `~/Library/LaunchAgents/` and load each with `launchctl
  bootstrap gui/$(id -u) <plist>`. `scripts/deadline_guard.py` cancels runs still queued at 09:00Z
  and kills runs still going at 12:00Z, then marks them abandoned; `scripts/seal.py` seals the
  answers once every run has ended. Unload both after `scripts/seal.py` pushes `prereg/collected/`.
- Before `scripts/arms.py` runs, a separate model reviewer reads each flagged run's flag. Hide the
  run's label and arm from it, and cut any quote of either block from the flag's text, since a quote
  gives the arm away. Accept only the flags it clears, then run the `--accept-all-flags` and
  `--strict` sensitivity checks as well.
- The package keeps the name `i86` and the ballot-measure vocabulary of the DC map it descends from:
  `fail` ranges hold Sullivan wins, `pass` ranges hold Peltola wins, and the map's conditions say
  `passes` for a Peltola win.
- Tests: `.venv/bin/pytest -q -n 4` (at most 4 workers). Test answers carry the label MOCK. Most tests
  run on the DC map in `tests/fixtures/i86_graph.yaml`; the `tx_graph` fixture loads this map.
- Models produced these forecasts under stated scenarios. Never describe them as observations, or a
  jump as an observed causal effect, and post nothing in Max's name without his explicit OK.
