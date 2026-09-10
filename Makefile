# Removes what a development run leaves behind: bytecode, the tool caches, coverage data and
# build output. `-prune` both stops find descending into a directory it has just deleted and
# keeps it out of the virtualenvs, whose bytecode caches belong to installed packages.
#
# The virtualenv itself is deliberately left alone: `uv sync` maintains it, and rebuilding one
# costs minutes, which is too much for a stray `make clean`. Remove it by hand when you mean to.
clean:
	find . -type d -name '.venv*' -prune -o -type d -name '__pycache__' -prune -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache .ruff_cache .hypothesis
	rm -rf .coverage htmlcov build dist
	rm -rf *.egg-info

# Linting runs first and formatting second, so formatting has the last word. `ruff check --fix`
# rewrites code — reordering imports, dropping unused ones — and what it leaves can itself need
# reformatting. Formatting first would let a fix undo it, leaving `lint-and-format-check`
# rejecting what `lint-and-format` had just produced.
#
# Both run the same rule set, the first fixing what it can and the second only reporting.
lint-and-format:
	uv run ruff check --line-length 100 --select I,N,F401 --fix
	uv run ruff format --line-length 100

lint-and-format-check:
	uv run ruff check --line-length 100 --select I,N,F401
	uv run ruff format --line-length 100 --check

mypy-typecheck:
	uv run mypy --ignore-missing-imports --check-untyped-defs .

ty-typecheck:
	uv run ty check --python-version 3.12 --ignore unused-type-ignore-comment .

test:
	uv run pytest --doctest-modules .

test-coverage:
	uv run pytest --doctest-modules --cov=anki_note_tooling .

check: lint-and-format-check mypy-typecheck ty-typecheck test

install-local:
	uv tool install --python 3.12 --force --reinstall .
