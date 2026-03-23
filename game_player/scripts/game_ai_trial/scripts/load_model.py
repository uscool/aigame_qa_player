import argparse
import json
import subprocess
import sys
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from stats_dashboard import find_latest_session, list_sessions, plot_session, summarize_session


def _now_ts() -> float:
    return time.time()


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


def _pick_from_list(label: str, options: list[str]) -> str:
    if not options:
        raise RuntimeError(f"No {label} options found.")
    print(f"\n{label}:")
    for i, opt in enumerate(options, start=1):
        print(f"{i}. {opt}")
    idx = _read_choice(len(options))
    return options[idx]


def _get_models_root() -> Path:
    # Keep consistent with scripts/new_model.py default output.
    return (Path(__file__).resolve().parent.parent / "models").resolve()


def _prompt_model_game(model_name_arg: Optional[str]) -> Tuple[str, str]:
    models_root = _get_models_root()

    model_name = model_name_arg
    if not model_name or model_name == "NULL":
        model_name = _pick_from_list(
            "Available models",
            [p.name for p in models_root.iterdir() if p.is_dir()],
        )
    model_dir = models_root / model_name
    if not model_dir.exists():
        raise RuntimeError(f"Model folder not found: {model_dir}")

    game_name = _pick_from_list(
        f"Available games for model '{model_name}'",
        [p.name for p in model_dir.iterdir() if p.is_dir()],
    )
    return model_name, game_name


def _prompt_model(model_name_arg: Optional[str]) -> str:
    models_root = _get_models_root()
    model_name = model_name_arg
    if not model_name or model_name == "NULL":
        model_name = _pick_from_list(
            "Available models",
            [p.name for p in models_root.iterdir() if p.is_dir()],
        )
    model_dir = models_root / model_name
    if not model_dir.exists():
        raise RuntimeError(f"Model folder not found: {model_dir}")
    return model_name


def _get_test_root() -> Path:
    return (Path(__file__).resolve().parent.parent / "test").resolve()


def _list_test_exes() -> List[Path]:
    test_root = _get_test_root()
    if not test_root.exists():
        return []

    exes = [p for p in test_root.rglob("*.exe") if p.is_file()]
    filtered: List[Path] = []
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


def _prompt_test_game() -> Tuple[str, Path]:
    exes = _list_test_exes()
    if not exes:
        raise RuntimeError("No .exe games found under `test/`.")

    test_root = _get_test_root()
    print("\nSelect a game executable under `test/`:")
    for i, exe in enumerate(exes, start=1):
        rel = exe.relative_to(test_root)
        print(f"{i}. {rel}")

    idx = _read_choice(len(exes))
    game_exe = exes[idx]
    rel = game_exe.relative_to(test_root)
    game_name = rel.parts[0] if rel.parts else game_exe.stem
    return game_name, game_exe


def _safe_summarize(session_dir: Path):
    try:
        return summarize_session(session_dir)
    except Exception as e:
        print(f"Could not summarize session {session_dir}: {e}")
        return None


def view_stats(model_name_arg: Optional[str]) -> None:
    models_root = _get_models_root()
    model_name, game_name = _prompt_model_game(model_name_arg)

    sessions = list_sessions(models_root, model_name, game_name)
    if not sessions:
        print("No sessions found for this model/game yet.")
        return

    print("\nSessions (oldest -> newest):")
    session_options = []
    for s in sessions:
        session_options.append(s.name)

    # Add quick option for "latest"
    print("1. Latest session")
    for i, name in enumerate(session_options, start=2):
        print(f"{i}. {name}")
    choice = _read_choice(len(session_options) + 1)
    if choice == 0:
        session_dir = sessions[-1]
    else:
        session_dir = sessions[choice - 1]

    stats = _safe_summarize(session_dir)
    if stats is None:
        return

    print("\nSession stats:")
    print(f"  Session: {session_dir.name}")
    print(f"  Recorded duration (sec; proxy for completion time): {stats.duration_seconds:.3f}")
    print(f"  Avg FPS: {stats.avg_fps:.2f}")
    print(f"  Min FPS: {stats.min_fps:.2f}")
    print(f"  FPS drop count: {stats.fps_drop_count}")
    print(f"  Glitch count (bbox jumps): {stats.glitch_count}")
    print("  Tracking method counts:")
    for k, v in sorted(stats.method_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"    - {k}: {v}")

    do_plot = input("\nShow graphs? (y/N): ").strip().lower() == "y"
    if do_plot:
        try:
            plot_session(session_dir)
        except Exception as e:
            print(f"Could not show graphs: {e}")


def _latest_or_none(models_root: Path, model_name: str, game_name: str) -> Optional[Path]:
    return find_latest_session(models_root, model_name, game_name)


def run_test_and_analyze(model_name_arg: Optional[str]) -> None:
    models_root = _get_models_root()
    model_name = _prompt_model(model_name_arg)
    game_name, game_exe = _prompt_test_game()

    baseline_dir = _latest_or_none(models_root, model_name, game_name)
    if baseline_dir is None:
        print("No baseline sessions found. We'll just record once and analyze that session.")

    duration_raw = input("\nTest recording duration seconds (0 = until ESC): ").strip()
    duration = 0.0
    try:
        duration = float(duration_raw)
    except Exception:
        duration = 0.0

    write_video = input("Write video.mp4? (y/N): ").strip().lower() == "y"

    print("\nStarting test recording. Controls:")
    print("  1) Press ALT+Tab to start capture")
    print("  2) Press ESC to stop")

    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parent / "new_model.py"),
        model_name,
        game_name,
        "--out",
        "models",
        "--game-exe",
        str(game_exe),
    ]
    if write_video:
        cmd.append("--write-video")
    if duration and duration > 0:
        cmd += ["--duration", str(duration)]

    # Note: new_model.py blocks until ESC or duration.
    subprocess.run(cmd, check=True)

    new_dir = _latest_or_none(models_root, model_name, game_name)
    if new_dir is None:
        print("Test completed but no new session was found (unexpected).")
        return
    if baseline_dir is not None and new_dir == baseline_dir:
        print("Warning: latest session did not change. You may have pressed ESC before ALT+Tab started recording.")

    print(f"\nAnalyzing new session: {new_dir}")
    new_stats = _safe_summarize(new_dir)
    if new_stats is None:
        return

    if baseline_dir is not None and baseline_dir.exists():
        base_stats = _safe_summarize(baseline_dir)
        if base_stats is not None:
            print("\nComparison vs baseline:")
            print(f"  Baseline session: {baseline_dir.name}")
            print(f"  Baseline duration (sec; proxy completion): {base_stats.duration_seconds:.3f}")
            print(f"  New duration (sec; proxy completion):      {new_stats.duration_seconds:.3f}")
            print(f"  Avg FPS:  {base_stats.avg_fps:.2f} -> {new_stats.avg_fps:.2f}")
            print(f"  Min FPS:  {base_stats.min_fps:.2f} -> {new_stats.min_fps:.2f}")
            print(f"  FPS drops: {base_stats.fps_drop_count} -> {new_stats.fps_drop_count}")
            print(f"  Glitches:  {base_stats.glitch_count} -> {new_stats.glitch_count}")

            issues: list[str] = []
            if new_stats.fps_drop_count > base_stats.fps_drop_count:
                issues.append("More FPS drop events than baseline (possible performance hitches).")
            if new_stats.glitch_count > base_stats.glitch_count:
                issues.append("More glitchy bbox jumps than baseline (possible tracker failure or visual stutter).")
            if not issues:
                issues.append("No obvious regressions detected vs baseline using current heuristics.")

            print("\nPotential issues spotted (heuristic):")
            for it in issues:
                print(f"  - {it}")

    do_plot = input("\nShow graphs for the new session? (y/N): ").strip().lower() == "y"
    if do_plot:
        try:
            plot_session(new_dir)
        except Exception as e:
            print(f"Could not show graphs: {e}")

    # "Play it using AI" (keystroke replay proxy with optional exploration)
    do_replay = input("\nReplay the new recording using AI (keystroke replay)? (Y/n): ").strip().lower()
    if do_replay == "" or do_replay == "y" or do_replay == "yes":
        mode_raw = input(
            "\nReplay mode: [1] deterministic, [2] explore (stochastic variations) (default 2): "
        ).strip()
        replay_mode = "explore"
        if mode_raw == "1":
            replay_mode = "deterministic"

        explore_intensity = 0.6
        if replay_mode == "explore":
            intensity_raw = input("Explore intensity (0.0 - 1.0, default 0.6): ").strip()
            if intensity_raw:
                try:
                    explore_intensity = float(intensity_raw)
                except Exception:
                    explore_intensity = 0.6
            explore_intensity = max(0.0, min(1.0, explore_intensity))

        replay_session_keystrokes(new_dir, replay_mode=replay_mode, explore_intensity=explore_intensity)


def replay_latest_session(model_name_arg: Optional[str]) -> None:
    models_root = _get_models_root()
    model_name = _prompt_model(model_name_arg)

    # We still pick a game from `test/` so the mapping to `models/<model>/<game>/` is consistent.
    game_name, _game_exe = _prompt_test_game()

    latest = _latest_or_none(models_root, model_name, game_name)
    if latest is None:
        print("No sessions found for that model/game yet. Record one first.")
        return

    print(f"\nLatest session: {latest}")
    mode_raw = input(
        "Replay mode: [1] deterministic, [2] explore (stochastic variations) (default 2): "
    ).strip()
    replay_mode = "explore"
    if mode_raw == "1":
        replay_mode = "deterministic"

    explore_intensity = 0.6
    if replay_mode == "explore":
        intensity_raw = input("Explore intensity (0.0 - 1.0, default 0.6): ").strip()
        if intensity_raw:
            try:
                explore_intensity = float(intensity_raw)
            except Exception:
                explore_intensity = 0.6
        explore_intensity = max(0.0, min(1.0, explore_intensity))

    replay_session_keystrokes(latest, replay_mode=replay_mode, explore_intensity=explore_intensity)


def _parse_pynput_key(key_name: str):
    """
    Convert our recorded `key` string (e.g. "Key.right", "Key.space", "a") into pynput keys.
    """
    from pynput.keyboard import Key  # local import to avoid requiring pynput for stats-only use

    if key_name.startswith("Key."):
        attr = key_name.split("Key.", 1)[1]
        if hasattr(Key, attr):
            return getattr(Key, attr)
    # Single-character keys come through as that character (e.g. "a", "w", etc.)
    if len(key_name) == 1:
        return key_name
    return None


def replay_session_keystrokes(session_dir: Path, replay_mode: str, explore_intensity: float) -> None:
    import time as _time
    from pynput.keyboard import Controller  # type: ignore

    session_dir = Path(session_dir)
    meta_path = session_dir / "meta.json"
    ks_path = session_dir / "keystrokes.jsonl"

    if not meta_path.exists() or not ks_path.exists():
        print(f"Missing required files for replay in: {session_dir}")
        return

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    started_at = meta.get("started_at_unix")
    if not isinstance(started_at, (int, float)):
        # Fallback: use first event timestamp as t0.
        started_at = None

    game_exe = meta.get("game_exe")
    game_args = meta.get("game_args") or None

    launch = input("\nLaunch the game again before replay? (Y/n): ").strip().lower()
    if launch == "" or launch == "y" or launch == "yes":
        if game_exe:
            try:
                cmd = [str(game_exe)]
                # We stored game_args as a string; keep simple: pass through as raw command line.
                if isinstance(game_args, str) and game_args.strip():
                    cmd = [str(game_exe)] + game_args.split(" ")
                proc = subprocess.Popen(cmd, cwd=str(Path(game_exe).parent))
                print(f"Launched game: {game_exe} (pid={proc.pid})")
            except Exception as e:
                print(f"Could not launch game for replay: {e}")
        else:
            print("No `game_exe` in meta.json; skipping launch.")

        delay_raw = input("Wait how many seconds before replaying? (default 2.0): ").strip()
        delay = 2.0
        try:
            if delay_raw:
                delay = float(delay_raw)
        except Exception:
            delay = 2.0
        _time.sleep(delay)

    ctrl = Controller()

    # Ignore "control" keys so we don't accidentally stop/alt-tab during replay.
    ignored = {
        "Key.esc",
        "esc",
        "Key.ESC",
        "Key.tab",
        "Key.alt_l",
        "Key.alt_r",
        "Key.alt_gr",
        "Key.alt",
    }

    # Load events and sort by absolute time.
    events = []
    with ks_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            evt = json.loads(line)
            events.append(evt)
    events.sort(key=lambda e: float(e.get("t", 0.0)))

    if not events:
        print("No keystroke events found to replay.")
        return

    t0 = float(events[0]["t"])
    if started_at is not None:
        t0 = float(started_at)

    speed = input("Replay speed (1.0 = realtime, 2.0 = faster): ").strip()
    replay_speed = 1.0
    try:
        if speed:
            replay_speed = float(speed)
    except Exception:
        replay_speed = 1.0
    if replay_speed <= 0:
        replay_speed = 1.0

    rng = random.Random()  # new randomness each run

    # Keys we are allowed to substitute during exploration.
    # We pick from observed keys in the log (excluding control keys).
    observed_key_names = sorted({str(e.get("key", "")) for e in events if str(e.get("key", ""))})
    observed_key_names = [k for k in observed_key_names if k not in ignored and _parse_pynput_key(k) is not None]

    timing_jitter_sec = 0.0
    substitute_key_prob = 0.0
    if replay_mode == "explore":
        # These are heuristics: higher intensity => more deviation from the recorded sequence.
        timing_jitter_sec = 0.015 + 0.075 * float(explore_intensity)
        substitute_key_prob = 0.05 + 0.30 * float(explore_intensity)

    print("\nReplaying keystrokes...")
    start_wall = _time.time()
    last_target_wall = start_wall
    # Map: "original key identity" -> "actual pynput key pressed" (used so release matches).
    sub_map: dict[str, object] = {}
    for evt in events:
        key_name = str(evt.get("key", ""))
        event_type = str(evt.get("event", ""))

        if key_name in ignored:
            continue

        rel_t = float(evt.get("t")) - t0
        if replay_mode == "explore" and timing_jitter_sec > 0:
            rel_t = rel_t + rng.uniform(-timing_jitter_sec, timing_jitter_sec)
            rel_t = max(0.0, rel_t)

        # Convert relative time using speed scale.
        target_wall = start_wall + (rel_t / replay_speed)
        if target_wall < last_target_wall:
            target_wall = last_target_wall  # keep replay monotonic in time

        now_wall = _time.time()
        sleep_for = target_wall - now_wall
        if sleep_for > 0:
            _time.sleep(sleep_for)

        parsed = _parse_pynput_key(key_name)
        if parsed is None:
            continue

        try:
            # During explore mode we may substitute which key is actually pressed,
            # while keeping the original event's "identity" for release pairing.
            if event_type == "press":
                if replay_mode == "explore" and observed_key_names and rng.random() < substitute_key_prob:
                    candidates = [k for k in observed_key_names if k != key_name]
                    if candidates:
                        alt_key_name = rng.choice(candidates)
                        alt_parsed = _parse_pynput_key(alt_key_name)
                        if alt_parsed is not None:
                            ctrl.press(alt_parsed)
                            # store substitution so release event targets the same physical key
                            # by using a per-original-key map on the Controller (closure local)
                            sub_map[key_name] = alt_parsed
                        else:
                            ctrl.press(parsed)
                    else:
                        ctrl.press(parsed)
                else:
                    ctrl.press(parsed)
            elif event_type == "release":
                actual = sub_map.pop(key_name, parsed)
                if actual is not None:
                    ctrl.release(actual)
        except Exception:
            # If a key can't be handled on this system, just skip it.
            continue
        last_target_wall = target_wall

    print("Replay finished.")


def _prompt_ai_params() -> dict:
    print("\nAI exploration parameters (press Enter for defaults):")
    max_time_raw = input("Max time seconds (default 45): ").strip()
    max_time = 45.0
    if max_time_raw:
        try:
            max_time = float(max_time_raw)
        except Exception:
            max_time = 45.0
    epsilon_raw = input("Explore epsilon [0..1] (default 0.25): ").strip()
    epsilon = 0.25
    if epsilon_raw:
        try:
            epsilon = float(epsilon_raw)
        except Exception:
            epsilon = 0.25
    epsilon = max(0.0, min(1.0, epsilon))
    hold_raw = input("Key hold seconds (default 0.12): ").strip()
    hold = 0.12
    if hold_raw:
        try:
            hold = float(hold_raw)
        except Exception:
            hold = 0.12
    step_wait_raw = input("Step wait seconds (default 0.18): ").strip()
    step_wait = 0.18
    if step_wait_raw:
        try:
            step_wait = float(step_wait_raw)
        except Exception:
            step_wait = 0.18
    stuck_seconds_raw = input("Stuck timeout seconds (default 12): ").strip()
    stuck_seconds = 12.0
    if stuck_seconds_raw:
        try:
            stuck_seconds = float(stuck_seconds_raw)
        except Exception:
            stuck_seconds = 12.0
    progress_move_raw = input("Progress move threshold px (default 15): ").strip()
    progress_move_px = 15.0
    if progress_move_raw:
        try:
            progress_move_px = float(progress_move_raw)
        except Exception:
            progress_move_px = 15.0
    return {
        "max_time": max_time,
        "epsilon": epsilon,
        "hold": hold,
        "step_wait": step_wait,
        "stuck_seconds": stuck_seconds,
        "progress_move_px": progress_move_px,
    }


def _select_baseline_session(models_root: Path, model_name: str, game_name: str) -> Optional[Path]:
    base = find_latest_session(models_root, model_name, game_name)
    return base


def ai_explore_played_game(model_name_arg: Optional[str]) -> None:
    models_root = _get_models_root()
    model_name, game_name = _prompt_model_game(model_name_arg)
    baseline = _select_baseline_session(models_root, model_name, game_name)
    if baseline is None:
        print("No recorded sessions found for that model/game. Record first using new_model.")
        return

    baseline_meta_path = baseline / "meta.json"
    if not baseline_meta_path.exists():
        print(f"Missing meta.json in baseline session: {baseline}")
        return
    baseline_meta = json.loads(baseline_meta_path.read_text(encoding="utf-8"))

    exe_filename = baseline_meta.get("exe_filename")
    if not exe_filename and baseline_meta.get("game_exe"):
        exe_filename = Path(baseline_meta["game_exe"]).name
    if not exe_filename:
        print("Baseline meta.json missing `exe_filename` (can't launch for AI).")
        return

    # Learn artifacts
    sprite_template_path = baseline_meta.get("sprite_template_path")
    if not sprite_template_path:
        # common default in our recorder
        candidate = baseline / "sprite_template.png"
        if candidate.exists():
            sprite_template_path = str(candidate)
    template_threshold = float(baseline_meta.get("template_threshold", 0.7) or 0.7)
    key_effects_path = baseline_meta.get("key_effects_path")
    if not key_effects_path:
        candidate = baseline / "key_effects.json"
        if candidate.exists():
            key_effects_path = str(candidate)

    key_effects = {}
    if key_effects_path and Path(key_effects_path).exists():
        key_effects = json.loads(Path(key_effects_path).read_text(encoding="utf-8"))
    key_effect_map = key_effects.get("key_effects", {}) if isinstance(key_effects, dict) else {}

    def _load_aligned_baseline_state_sequence(baseline_dir: Path) -> Tuple[List[int], List[Tuple[float, float]], List[List[str]], float, float, float, Tuple[float, float, float, float]]:
        """
        Build an aligned time series using:
          - frames.csv (t, dt, keys_down)
          - sprite_track.jsonl (bbox -> sprite center)

        Returns:
          states: list[state_id]
          centers: list[(cx,cy)]
          actions: list[keys_down_list] for each aligned frame
          moving_threshold: float
          cx_min/max, cy_min/max used for discretization
          raw_bounds: (cx_min,cx_max,cy_min,cy_max)
        """
        import csv as _csv

        frames_csv = baseline_dir / "frames.csv"
        sprite_jsonl = baseline_dir / "sprite_track.jsonl"

        # frame_idx -> keys_down list and timestamp
        frame_rows: List[Dict[str, Any]] = []
        with frames_csv.open("r", encoding="utf-8") as f:
            reader = _csv.DictReader(f)
            for row in reader:
                try:
                    fi = int(row["frame_idx"])
                    ts = float(row["t"])
                    keys_down = json.loads(row["keys_down"])
                    if not isinstance(keys_down, list):
                        keys_down = []
                    frame_rows.append({"frame_idx": fi, "t": ts, "keys_down": keys_down})
                except Exception:
                    continue

        if not frame_rows:
            raise RuntimeError(f"Baseline missing/invalid frames.csv: {frames_csv}")

        # frame_idx -> bbox center
        centers_by_idx: Dict[int, Tuple[float, float]] = {}
        with sprite_jsonl.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                evt = json.loads(line)
                try:
                    fi = int(evt["frame_idx"])
                    bbox = evt.get("bbox") or {}
                    x = bbox.get("x")
                    y = bbox.get("y")
                    w = bbox.get("w")
                    h = bbox.get("h")
                    if x is None or y is None or w is None or h is None:
                        continue
                    cx = float(x) + float(w) / 2.0
                    cy = float(y) + float(h) / 2.0
                    centers_by_idx[fi] = (cx, cy)
                except Exception:
                    continue

        # Align on common frame_idx; preserve ordering from frames.csv
        aligned: List[Tuple[int, float, List[str], Tuple[float, float]]] = []
        for r in frame_rows:
            fi = r["frame_idx"]
            if fi in centers_by_idx:
                aligned.append((fi, r["t"], r["keys_down"], centers_by_idx[fi]))

        if len(aligned) < 10:
            raise RuntimeError("Not enough aligned frame data to build state model.")

        centers = [a[3] for a in aligned]
        actions = [a[2] for a in aligned]
        xs = [c[0] for c in centers]
        ys = [c[1] for c in centers]
        cx_min, cx_max = float(min(xs)), float(max(xs))
        cy_min, cy_max = float(min(ys)), float(max(ys))
        if cx_max - cx_min < 1e-6:
            cx_max = cx_min + 1.0
        if cy_max - cy_min < 1e-6:
            cy_max = cy_min + 1.0

        # Moving threshold from baseline center deltas
        dists: List[float] = []
        for i in range(1, len(centers)):
            dx = centers[i][0] - centers[i - 1][0]
            dy = centers[i][1] - centers[i - 1][1]
            dists.append(float((dx * dx + dy * dy) ** 0.5))
        if not dists:
            moving_threshold = 5.0
        else:
            # robust-ish threshold
            d_sorted = sorted(dists)
            q75 = d_sorted[int(0.75 * (len(d_sorted) - 1))]
            moving_threshold = float(max(3.0, q75 * 0.6))

        grid_cols = 12
        grid_rows = 9

        def quant_state(cx: float, cy: float, moving: bool) -> int:
            # normalize -> cell
            fx = (cx - cx_min) / (cx_max - cx_min)
            fy = (cy - cy_min) / (cy_max - cy_min)
            ix = int(fx * grid_cols)
            iy = int(fy * grid_rows)
            ix = max(0, min(grid_cols - 1, ix))
            iy = max(0, min(grid_rows - 1, iy))
            m = 1 if moving else 0
            # compact encoding
            return (iy * grid_cols + ix) * 2 + m

        # Compute per-aligned frame states
        states: List[int] = []
        prev_c = centers[0]
        states.append(quant_state(prev_c[0], prev_c[1], moving=False))
        for i in range(1, len(centers)):
            cx, cy = centers[i]
            dx = cx - centers[i - 1][0]
            dy = cy - centers[i - 1][1]
            dist = float((dx * dx + dy * dy) ** 0.5)
            moving = dist >= moving_threshold
            states.append(quant_state(cx, cy, moving=moving))

        raw_bounds = (cx_min, cx_max, cy_min, cy_max)
        return states, centers, actions, moving_threshold, cx_min, cx_max, cy_min, cy_max, raw_bounds

    def _build_transition_model_from_baseline(baseline_dir: Path):
        """
        Build: transitions[(state_id, action_key)] -> {next_state_id: count}
        and compute which states can reach the baseline goal state.
        """
        ignored = {
            "Key.esc",
            "esc",
            "Key.ESC",
            "Key.tab",
            "Key.alt_l",
            "Key.alt_r",
            "Key.alt_gr",
            "Key.alt",
        }

        states, centers, actions, moving_threshold, cx_min, cx_max, cy_min, cy_max, raw_bounds = _load_aligned_baseline_state_sequence(baseline_dir)

        # transition counts
        transitions: Dict[Tuple[int, str], Dict[int, int]] = {}
        adjacency: Dict[int, set[int]] = {}
        # Build action_key at each frame: choose first non-control key if present.
        action_keys_seq: List[Optional[str]] = []
        for keys_down in actions:
            ak = None
            if isinstance(keys_down, list):
                for k in keys_down:
                    if isinstance(k, str) and k not in ignored and not k.startswith("Key.esc"):
                        ak = k
                        break
            action_keys_seq.append(ak)

        for i in range(len(states) - 1):
            ak = action_keys_seq[i]
            if not ak:
                continue
            s = states[i]
            sn = states[i + 1]
            key = (s, ak)
            transitions.setdefault(key, {})
            transitions[key][sn] = transitions[key].get(sn, 0) + 1
            adjacency.setdefault(s, set()).add(sn)

        goal_state = states[-1]

        # Reverse adjacency for reachability to goal
        rev_adj: Dict[int, set[int]] = {}
        for s, ns_set in adjacency.items():
            for sn in ns_set:
                rev_adj.setdefault(sn, set()).add(s)

        reachable_to_goal: set[int] = set()
        queue = [goal_state]
        reachable_to_goal.add(goal_state)
        while queue:
            cur = queue.pop()
            for prev in rev_adj.get(cur, set()):
                if prev not in reachable_to_goal:
                    reachable_to_goal.add(prev)
                    queue.append(prev)

        state_bounds = {
            "moving_threshold": moving_threshold,
            "cx_min": cx_min,
            "cx_max": cx_max,
            "cy_min": cy_min,
            "cy_max": cy_max,
            "grid_cols": 12,
            "grid_rows": 9,
            "goal_state": goal_state,
        }
        return transitions, reachable_to_goal, state_bounds

    try:
        transition_model, reachable_to_goal, state_bounds = _build_transition_model_from_baseline(baseline)
    except Exception as e:
        print(f"[state-model] Could not build baseline state model, falling back to key-effects reward only: {e}")
        transition_model, reachable_to_goal, state_bounds = {}, set(), {}

    params = _prompt_ai_params()

    # Prepare output dir
    from datetime import datetime

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    ai_dir = models_root / model_name / game_name / f"ai_run_{run_id}"
    ai_dir.mkdir(parents=True, exist_ok=True)

    ai_meta = {
        "model_name": model_name,
        "game_name": game_name,
        "run_id": run_id,
        "learned_from_session": baseline.name,
        "exe_filename": exe_filename,
        "template_threshold": template_threshold,
        "fps_target": float(baseline_meta.get("fps_target", 30.0) or 30.0),
        "ai_params": params,
        "completed": False,
        "completed_via_exit": False,
        "ended_at_unix": None,
    }
    (ai_dir / "meta.json").write_text(json.dumps(ai_meta, indent=2), encoding="utf-8")

    # Dynamic imports from recorder so we can reuse tracking logic.
    from new_model import _find_exe_in_test, _require_deps, _load_template, _track_sprite_template, _track_sprite_optical_flow, SpriteTrack

    cv2, np, mss, keyboard = _require_deps()
    # Prefer the exact executable path saved in meta.json to avoid filename collisions.
    game_exe_path = None
    if isinstance(baseline_meta.get("game_exe"), str) and baseline_meta["game_exe"].strip():
        try:
            game_exe_path = Path(baseline_meta["game_exe"]).resolve()
        except Exception:
            game_exe_path = None
    if game_exe_path is None:
        game_exe_path = _find_exe_in_test(str(exe_filename))

    # Launch game
    game_proc = subprocess.Popen([str(game_exe_path)], cwd=str(game_exe_path.parent))
    ai_meta["game_proc_pid"] = int(game_proc.pid)

    # ESC stop listener (optional)
    stop_flag = {"stop": False}

    def on_release(key: Any) -> Optional[bool]:
        name = str(key)
        # pynput keys are stringified like "Key.esc"
        if name in ("Key.esc", "Key.ESC", "esc"):
            stop_flag["stop"] = True
            return False
        return None

    listener = keyboard.Listener(on_release=on_release)
    listener.start()

    # Load template if available
    template_gray = None
    if sprite_template_path:
        template_gray = _load_template(cv2, sprite_template_path)

    frames_csv_path = ai_dir / "frames.csv"
    sprite_path = ai_dir / "sprite_track.jsonl"
    keystrokes_path = ai_dir / "keystrokes.jsonl"
    frames_csv_path.write_text("frame_idx,t,dt,keys_down\n", encoding="utf-8")

    ai_keystrokes: List[dict] = []

    # Choose action set
    action_keys: List[str] = []
    for k, v in key_effect_map.items():
        try:
            c = int(v.get("count", 0))
            dist = float(v.get("avg_dist", 0.0))
        except Exception:
            continue
        if c >= 2 and abs(dist) >= 8.0:
            action_keys.append(str(k))
    if not action_keys:
        action_keys = ["Key.left", "Key.right", "Key.up", "Key.down", "Key.space", "a", "d", "w", "s"]

    # Convert to pynput key objects (skip unknown keys)
    def parse_key_name(key_name: str):
        from pynput.keyboard import Key as _Key  # type: ignore

        if key_name.startswith("Key."):
            attr = key_name.split("Key.", 1)[1]
            if hasattr(_Key, attr):
                return getattr(_Key, attr)
        if len(key_name) == 1:
            return key_name
        return None

    parsed_actions: List[Tuple[str, Any]] = []
    for k in action_keys:
        pk = parse_key_name(k)
        if pk is not None:
            parsed_actions.append((k, pk))

    if not parsed_actions:
        print("Could not map any learned action keys to pynput keys. Aborting AI run.")
        return

    # State-based policy:
    # - current discrete state is matched to baseline discretization
    # - choose action expected to transition to new/less-visited states
    # - strongly prefer actions that lead to states that can reach the baseline goal state
    state_visit_counts: Dict[int, int] = {}

    def _quantize_current_state(cx: float, cy: float, prev_c: Optional[Tuple[float, float]], cur_c: Optional[Tuple[float, float]]) -> int:
        if not state_bounds:
            # fallback: single dummy state
            return 0
        cx_min = float(state_bounds["cx_min"])
        cx_max = float(state_bounds["cx_max"])
        cy_min = float(state_bounds["cy_min"])
        cy_max = float(state_bounds["cy_max"])
        grid_cols = int(state_bounds["grid_cols"])
        grid_rows = int(state_bounds["grid_rows"])
        moving_threshold = float(state_bounds["moving_threshold"])

        fx = (cx - cx_min) / (cx_max - cx_min + 1e-9)
        fy = (cy - cy_min) / (cy_max - cy_min + 1e-9)
        ix = int(fx * grid_cols)
        iy = int(fy * grid_rows)
        ix = max(0, min(grid_cols - 1, ix))
        iy = max(0, min(grid_rows - 1, iy))

        moving = False
        if prev_c is not None and cur_c is not None:
            dx = cur_c[0] - prev_c[0]
            dy = cur_c[1] - prev_c[1]
            dist = float((dx * dx + dy * dy) ** 0.5)
            moving = dist >= moving_threshold

        m = 1 if moving else 0
        return (iy * grid_cols + ix) * 2 + m

    def choose_action(curr_state: int) -> Tuple[str, Any]:
        # epsilon-greedy exploration
        if random.random() < params["epsilon"] or not transition_model:
            return random.choice(parsed_actions)

        best = None  # (score, key, pk)
        for key_name, pk in parsed_actions:
            next_counts = transition_model.get((curr_state, key_name), None)
            if not next_counts:
                # unknown transition: treat as low-confidence
                expected_novelty = 0.0
                expected_reach = -1.0
            else:
                # Expected novelty over next states
                total = sum(next_counts.values())
                expected_novelty = 0.0
                expected_reach = 0.0
                for sn, c in next_counts.items():
                    prob = float(c) / float(total)
                    visits = state_visit_counts.get(sn, 0)
                    novelty = 1.0 / (1.0 + float(visits))
                    reachable_bonus = 1.0 if (reachable_to_goal and sn in reachable_to_goal) else -1.0
                    expected_novelty += prob * novelty
                    expected_reach += prob * reachable_bonus

            # Combine: prefer reachable-to-goal paths + novelty
            score = (2.0 * expected_reach) + (1.5 * expected_novelty)
            if best is None or score > best[0]:
                best = (score, key_name, pk)

        if best is None:
            return random.choice(parsed_actions)
        _score, key_name, pk = best
        return key_name, pk

    # Capture loop
    frame_period = 1.0 / max(1.0, float(ai_meta["fps_target"]))
    prev_gray = None
    prev_t = _now_ts()
    frame_idx = 0

    prev_center = None
    curr_state_id = 0
    last_progress_wall = _now_ts()
    glitch_count = 0
    fps_drop_count = 0
    min_fps = float("inf")
    ended_reason = "unknown"
    dead_end_frame_count = 0
    dead_end_start_wall = None

    action_in_progress = False
    action_key_name = ""
    action_key_obj = None
    action_start_wall = 0.0
    action_end_wall = 0.0
    action_center_start = None
    action_pressed = False

    # Next action scheduling
    next_action_wall = _now_ts() + 0.5
    rng = random.Random()

    start_wall = _now_ts()

    try:
        from pynput.keyboard import Controller as _Controller  # type: ignore
        ctrl_cached = _Controller()
        with mss.mss() as sct:
            monitors = sct.monitors
            if int(baseline_meta.get("monitor", 1)) < 1 or int(baseline_meta.get("monitor", 1)) >= len(monitors):
                print("AI capture: invalid monitor index, using 1.")
                monitor_idx = 1
            else:
                monitor_idx = int(baseline_meta.get("monitor", 1))
            mon = monitors[monitor_idx]
            if baseline_meta.get("region"):
                # For now: use full monitor unless region is explicitly stored as bbox.
                # (You can extend this later to carry region through meta.)
                bbox = {"left": mon["left"], "top": mon["top"], "width": mon["width"], "height": mon["height"]}
            else:
                bbox = {"left": mon["left"], "top": mon["top"], "width": mon["width"], "height": mon["height"]}

            w = int(bbox["width"])
            h = int(bbox["height"])

            while True:
                now_wall = _now_ts()
                if stop_flag["stop"]:
                    ended_reason = "esc_stop"
                    break
                if now_wall - start_wall >= float(params["max_time"]):
                    ended_reason = "max_time"
                    break
                if game_proc.poll() is not None:
                    ai_meta["completed"] = True
                    ai_meta["completed_via_exit"] = True
                    ended_reason = "process_exit"
                    break

                # pace capture loop
                sleep_for = frame_period - (now_wall - prev_t)
                if frame_idx > 0 and sleep_for > 0:
                    time.sleep(sleep_for)
                t = _now_ts()
                dt = t - prev_t
                prev_t = t

                shot = sct.grab(bbox)
                frame = np.array(shot)  # BGRA
                frame_bgr = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

                # sprite tracking
                track: SpriteTrack
                if template_gray is not None:
                    track = _track_sprite_template(cv2, frame_bgr, template_gray, template_threshold)
                else:
                    if prev_gray is None:
                        prev_gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
                        track = SpriteTrack(method="optical_flow")
                    else:
                        track, prev_gray = _track_sprite_optical_flow(cv2, np, prev_gray, frame_bgr, max_corners=200)

                cx = (float(track.x) + float(track.w) / 2.0) if track.x is not None and track.w is not None else None
                cy = (float(track.y) + float(track.h) / 2.0) if track.y is not None and track.h is not None else None

                if cx is not None and cy is not None:
                    cur_center = (cx, cy)
                    prev_center_before = prev_center
                    if prev_center is not None:
                        dist = ((cx - prev_center[0]) ** 2 + (cy - prev_center[1]) ** 2) ** 0.5
                        if dist > float(params["progress_move_px"]) * 1.5:
                            glitch_count += 1
                        if dist > float(params["progress_move_px"]):
                            last_progress_wall = t
                    curr_state_id = _quantize_current_state(cx, cy, prev_center_before, cur_center)
                    prev_center = cur_center
                    state_visit_counts[curr_state_id] = state_visit_counts.get(curr_state_id, 0) + 1
                    if reachable_to_goal and curr_state_id not in reachable_to_goal:
                        dead_end_frame_count += 1
                        if dead_end_start_wall is None:
                            dead_end_start_wall = t
                    else:
                        dead_end_frame_count = 0
                        dead_end_start_wall = None

                    # If we remain in unreachable states for a sustained period,
                    # treat this as "completion not possible" and stop.
                    if dead_end_start_wall is not None and (t - dead_end_start_wall) >= float(params["stuck_seconds"]):
                        ended_reason = "dead_end_unreachable"
                        break

                # FPS stats (from dt)
                if dt > 0:
                    fps = 1.0 / dt
                    min_fps = min(min_fps, fps)
                    fps_target = float(ai_meta.get("fps_target", 30.0) or 30.0)
                    if fps < 0.8 * fps_target:
                        fps_drop_count += 1

                # Write sprite evt
                sprite_evt = {
                    "t": t,
                    "frame_idx": frame_idx,
                    "method": track.method,
                    "bbox": {"x": track.x, "y": track.y, "w": track.w, "h": track.h},
                    "score": track.score,
                }
                with sprite_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(sprite_evt) + "\n")

                # Write frames row (AI keys held are only during action hold).
                keys_down = []
                if action_in_progress and action_key_name:
                    keys_down = [action_key_name]
                with frames_csv_path.open("a", encoding="utf-8") as f:
                    f.write(f"{frame_idx},{t:.6f},{dt:.6f},{json.dumps(keys_down)}\n")

                # Start action
                if not action_in_progress and t >= next_action_wall:
                    action_key_name, action_key_obj = choose_action(curr_state_id)
                    action_in_progress = True
                    action_pressed = False
                    action_start_wall = t
                    action_center_start = prev_center
                    action_end_wall = t + float(params["hold"])

                # Release and finish action bookkeeping
                if action_in_progress and t >= action_end_wall:
                    # Release
                    try:
                        ctrl_cached.release(action_key_obj)  # type: ignore[arg-type]
                    except Exception:
                        pass
                    ai_keystrokes.append({"t": t, "event": "release", "key": action_key_name})
                    action_in_progress = False
                    action_pressed = False
                    # Step gap before next action.
                    next_action_wall = t + float(params["step_wait"])

                    action_key_name = ""
                    action_key_obj = None

                # Press action at the beginning of hold window
                if action_in_progress and (not action_pressed) and t >= action_start_wall:
                    try:
                        ctrl_cached.press(action_key_obj)  # type: ignore[arg-type]
                        ai_keystrokes.append({"t": t, "event": "press", "key": action_key_name})
                    except Exception:
                        pass
                    action_pressed = True

                # Stall detection
                if t - last_progress_wall >= float(params["stuck_seconds"]):
                    ended_reason = "stuck_no_progress"
                    break

                frame_idx += 1
    except Exception as e:
        print(f"AI exploration failed: {e}")

    # Write AI keystrokes
    with keystrokes_path.open("w", encoding="utf-8") as f:
        for evt in ai_keystrokes:
            f.write(json.dumps(evt) + "\n")

    # Finalize meta
    ended = _now_ts()
    ai_meta["ended_at_unix"] = ended
    ai_meta["ended_reason"] = ended_reason
    ai_meta["dead_end_frame_count"] = dead_end_frame_count
    ai_meta["dead_end_start_wall"] = dead_end_start_wall
    ai_meta["glitch_count"] = glitch_count
    ai_meta["fps_drop_count"] = fps_drop_count
    if min_fps != float("inf"):
        ai_meta["min_fps"] = min_fps
    (ai_dir / "meta.json").write_text(json.dumps(ai_meta, indent=2), encoding="utf-8")

    # Quick summary
    print("\n=== AI Exploration Results ===")
    print(f"Run dir: {ai_dir}")
    print(f"Completed (process exit): {ai_meta.get('completed_via_exit')}")
    try:
        stats = summarize_session(ai_dir)
        print(f"Avg FPS: {stats.avg_fps:.2f}, Min FPS: {stats.min_fps:.2f}, FPS drop events: {stats.fps_drop_count}")
        print(f"Glitch count (bbox jumps): {stats.glitch_count}")
    except Exception as e:
        print(f"Could not compute stats summary: {e}")
    print("Potential issues were detected via glitch/stall/FPS heuristics (see stats/plots if needed).")


def main() -> int:
    parser = argparse.ArgumentParser(description="Load recorded sessions and analyze stats.")
    parser.add_argument("model_name", nargs="?", default=None, help="Model name (or NULL for prompt).")
    args = parser.parse_args()

    while True:
        print("\n=== Load Model Menu ===")
        print("1. Run AI exploration (classical deviation/exploration) on a played game")
        print("2. Exit")
        choice = _read_choice(2)
        if choice == 0:
            ai_explore_played_game(args.model_name)
        else:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
