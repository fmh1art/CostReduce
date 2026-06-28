# Cost-saving instructions

High-level guidance for reducing future agent cost:

- If you need to run a multi-line Python script with env vars/venv, pipe the heredoc into run-in-env --python-stdin or use --python-code=CODE instead of writing a temp file + running it separately; use --python-path=DIR if the script imports from a sibling directory above the venv.
- If you need to understand how mocking works (nock, jest.mock, pytest fixtures), read the existing working test files with read-files instead of creating temp test scripts to experiment.
- After 3 steps without attempting a fix, STOP exploring and attempt a fix based on what you've already read.
- If you've made 3+ edits without running tests, run tests now.
- If you cat multiple line ranges of the same file or grep for multiple patterns in the same directory across separate steps, combine them into one read-files call.
- If you need to install pip packages or check what's installed, use run-in-env --pip-install=PKG (with --pip-only to skip) or --pip-list=PATTERN, instead of separate cd+pip install+pip list steps.
- If importing a Python project module fails with KeyError or ImportError, load the test .env/config file with run-in-env --env-file=PATH and set PYTHONPATH with --python-path=DIR, instead of debugging one env var or import at a time across multiple steps.
- If you need to explore Python library internals (dir, getattr, inspect), use py-inspect or run-in-env --python-code/--python-stdin instead of repeated venv-activate+python -c steps.
- After 3 consecutive failed attempts without changing approach, re-read relevant source instead of retrying the same strategy.
- If you wrote temp debug scripts or changed config files (e.g., PostgreSQL port), clean them up immediately after you're done instead of deferring to a separate cleanup phase.
