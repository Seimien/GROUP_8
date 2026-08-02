# Live Demo Chaos Procedure

1. Instructor gives prompt (e.g. "primary throws 504, coverage -> 95%").
2. `git checkout -b chaos/<prompt-name>`
3. Edit relevant gateway mock / test to simulate the failure.
4. Bump `--cov-fail-under` in ci.yml if requested.
5. `git add -A && git commit -m "JOSEPH - chaos: <scenario> failure injection + coverage bump"`
6. `git push -u origin chaos/<prompt-name>` and open PR live.
7. Watch Actions tab; if a stage fails, read the log, patch, commit again, push.
8. Once green, merge; be ready for Q&A on why unit mocks can't catch DB/schema bugs.
