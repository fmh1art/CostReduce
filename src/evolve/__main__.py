"""CLI entry — forwards to `run_evolve` so `python -m src.evolve ...` works."""

from .run_evolve import main

if __name__ == "__main__":
    main()
