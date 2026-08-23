"""Allow ``python -m patchcycle``."""

from patchcycle.cli import main

if __name__ == "__main__":  # pragma: no cover - interpreter entry guard
    raise SystemExit(main())
