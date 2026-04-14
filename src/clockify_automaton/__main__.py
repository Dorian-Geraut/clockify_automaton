import sys

from .config import load_config
from .scheduler import run


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python -m clockify_automaton <config.json>")
        sys.exit(1)

    try:
        config = load_config(sys.argv[1])
    except (ValueError, FileNotFoundError) as e:
        print(f"Config error: {e}")
        sys.exit(1)

    run(config)


if __name__ == "__main__":
    main()
