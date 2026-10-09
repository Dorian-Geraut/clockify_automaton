import sys

from .config import load_config
from .scheduler import run

USAGE = "Usage: python -m clockify_automaton [--dry-run] <config.json>"


def main() -> None:
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]
    if len(args) != 1:
        print(USAGE)
        sys.exit(1)

    try:
        config = load_config(args[0])
    except (ValueError, FileNotFoundError) as e:
        print(f"Config error: {e}")
        sys.exit(1)

    run(config, dry_run=dry_run)


if __name__ == "__main__":
    main()
