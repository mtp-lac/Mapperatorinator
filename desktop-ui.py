"""Launch the native Mapperatorinator desktop GUI."""

import sys


def main():
    try:
        from desktop.app import run
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("PyQt6"):
            print("The desktop GUI requires PyQt6. Install it in your inference environment:")
            print(f'  "{sys.executable}" -m pip install -r requirements-desktop.txt')
            return 1
        raise
    return run()


if __name__ == "__main__":
    sys.exit(main())
