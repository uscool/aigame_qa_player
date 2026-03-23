import os
import subprocess
import sys
from pathlib import Path

# this is intended to be the runner script. This will select and create models for new 
# games. Alternatively, it allows older game AI models to be loaded and used to run 
# automated testing. 

# Core functionality planned:
# 1. Create models for game training (call file)
# 2. Load existing models for automated testing (call file)
# 3. Pull existing dashboards for game performance. (Terminal based)
#     - Checkboxes recommended for which dashboards to pull (training, testing, etc.)
#     - Runtime dash
#     - Training dash

def _get_test_root() -> Path:
    return (Path(__file__).resolve().parent.parent / "test").resolve()


def _list_test_exes() -> list[Path]:
    test_root = _get_test_root()
    if not test_root.exists():
        return []

    exes = [p for p in test_root.rglob("*.exe") if p.is_file()]

    # Filter out common Unity helper tools so you can pick the actual game.
    filtered: list[Path] = []
    for p in exes:
        name = p.name.lower()
        rel = p.relative_to(test_root)
        parts = [x.lower() for x in rel.parts]
        if "library" in parts:
            continue
        if name in ("vswhere.exe", "comintegration.exe"):
            continue
        if "crashhandler" in name:
            continue
        filtered.append(p)

    return sorted(filtered, key=lambda p: str(p))


def _read_choice(num_options: int) -> int:
    while True:
        raw = input(f"Enter choice (1-{num_options}): ").strip()
        try:
            idx = int(raw)
        except ValueError:
            print("Invalid input. Enter a number.")
            continue
        if 1 <= idx <= num_options:
            return idx - 1
        print(f"Out of range. Enter a number between 1 and {num_options}.")


raw = input("Enter the model name (please enter NULL if it does not exist): ")
if raw == "NULL":
    model = input("Enter the new model name to create: ").strip()
    if not model:
        raise SystemExit("Model name cannot be empty.")

    print(f"Creating new model session: {model}")

    exes = _list_test_exes()
    if not exes:
        raise SystemExit("No .exe games found under `test/`.")

    print("\nSelect a game executable under `test/`:")
    for i, exe in enumerate(exes, start=1):
        rel = exe.relative_to(_get_test_root())
        print(f"{i}. {rel}")

    exe_choice = _read_choice(len(exes))
    game_exe = exes[exe_choice]
    rel = game_exe.relative_to(_get_test_root())
    game_name = rel.parts[0] if rel.parts else game_exe.stem

    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parent / "new_model.py"),
        model,
        game_name,
        "--game-exe",
        str(game_exe),
    ]
    subprocess.run(cmd, check=True)
else:
    model = raw
    print("Loading existing model...")
    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parent / "load_model.py"),
        model,
    ]
    subprocess.run(cmd, check=True)