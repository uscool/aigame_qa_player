import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _require_deps() -> Tuple[Any, Any, Any]:
    try:
        import cv2  # type: ignore
    except Exception:
        cv2 = None

    try:
        import numpy as np  # type: ignore
    except Exception:
        np = None

    try:
        import mss  # type: ignore
    except Exception:
        mss = None

    try:
        from pynput import keyboard  # type: ignore
    except Exception:
        keyboard = None

    missing = []
    if cv2 is None:
        missing.append("opencv-python")
    if np is None:
        missing.append("numpy")
    if mss is None:
        missing.append("mss")
    if keyboard is None:
        missing.append("pynput")

    if missing:
        print("Missing dependencies for new_model.py.", file=sys.stderr)
        print("Install with:", file=sys.stderr)
        print(f'  python -m pip install {" ".join(missing)}', file=sys.stderr)
        raise SystemExit(2)

    return cv2, np, mss, keyboard


@dataclass
class SpriteTrack:
    method: str
    x: Optional[int] = None
    y: Optional[int] = None
    w: Optional[int] = None
    h: Optional[int] = None
    score: Optional[float] = None


def _now_ts() -> float:
    return time.time()


def _safe_key_name(key: Any) -> str:
    # pynput keys can be Key.space etc or have `.char`
    try:
        if hasattr(key, "char") and key.char is not None:
            return str(key.char)
    except Exception:
        pass
    return str(key)


def _is_alt_key_name(key_name: str) -> bool:
    # Examples from pynput: "Key.alt_l", "Key.alt_r"
    return key_name.startswith("Key.alt")


def _is_tab_key_name(key_name: str) -> bool:
    return key_name == "Key.tab"


def _is_esc_key_name(key_name: str) -> bool:
    return key_name in ("Key.esc", "esc", "Key.ESC")


def _get_test_root() -> Path:
    return (Path(__file__).resolve().parent.parent / "test").resolve()


def _find_exe_in_test(exe_filename: str) -> Path:
    """
    Find an executable by filename anywhere under `test/`.
    Example: exe_filename="SpaceWalk.exe"
    """
    exe_filename = exe_filename.strip().strip('"').strip("'")
    if not exe_filename:
        raise ValueError("exe_filename is empty")

    test_root = _get_test_root()
    if not test_root.exists():
        raise FileNotFoundError(f"`test/` directory not found: {test_root}")

    exe_filename_lower = exe_filename.lower()
    matches: List[Path] = []
    for p in test_root.rglob("*.exe"):
        if p.is_file() and p.name.lower() == exe_filename_lower:
            matches.append(p)

    if not matches:
        raise FileNotFoundError(f"Could not find `{exe_filename}` under `test/`.")
    if len(matches) > 1:
        raise RuntimeError(
            f"Multiple executables named `{exe_filename}` found under `test/`:\n"
            + "\n".join(str(m.relative_to(test_root)) for m in matches)
        )
    return matches[0]


def _cap_template_crop(cv2: Any, gray: Any, x: int, y: int, w: int, h: int, max_dim: int = 96) -> Any:
    """
    Crop and optionally downscale a template to keep matchTemplate fast/stable.
    """
    H, W = gray.shape[:2]
    x0 = max(0, min(W - 1, int(x)))
    y0 = max(0, min(H - 1, int(y)))
    x1 = max(0, min(W, int(x0 + w)))
    y1 = max(0, min(H, int(y0 + h)))
    if x1 <= x0 or y1 <= y0:
        return None

    crop = gray[y0:y1, x0:x1]
    ch, cw = crop.shape[:2]
    if max(ch, cw) <= max_dim:
        return crop

    scale = float(max_dim) / float(max(ch, cw))
    new_w = max(8, int(round(cw * scale)))
    new_h = max(8, int(round(ch * scale)))
    resized = cv2.resize(crop, (new_w, new_h), interpolation=cv2.INTER_AREA)
    return resized


def _load_template(cv2: Any, template_path: Optional[str]) -> Optional[Any]:
    if not template_path:
        return None
    p = Path(template_path)
    if not p.exists():
        print(f"Template image not found: {template_path}", file=sys.stderr)
        raise SystemExit(2)
    img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
    if img is None:
        print(f"Failed reading template image: {template_path}", file=sys.stderr)
        raise SystemExit(2)
    return img


def _track_sprite_template(
    cv2: Any,
    frame_bgr: Any,
    template_gray: Any,
    threshold: float,
) -> SpriteTrack:
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    res = cv2.matchTemplate(gray, template_gray, cv2.TM_CCOEFF_NORMED)
    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
    h, w = template_gray.shape[:2]
    if max_val < threshold:
        return SpriteTrack(method="template", score=float(max_val))
    x, y = max_loc
    return SpriteTrack(method="template", x=int(x), y=int(y), w=int(w), h=int(h), score=float(max_val))


def _track_sprite_optical_flow(
    cv2: Any,
    np: Any,
    prev_gray: Any,
    curr_bgr: Any,
    max_corners: int,
) -> Tuple[SpriteTrack, Any]:
    curr_gray = cv2.cvtColor(curr_bgr, cv2.COLOR_BGR2GRAY)
    p0 = cv2.goodFeaturesToTrack(
        prev_gray,
        maxCorners=max_corners,
        qualityLevel=0.01,
        minDistance=7,
        blockSize=7,
    )
    if p0 is None or len(p0) == 0:
        return SpriteTrack(method="optical_flow"), curr_gray

    p1, st, _err = cv2.calcOpticalFlowPyrLK(prev_gray, curr_gray, p0, None)
    if p1 is None or st is None:
        return SpriteTrack(method="optical_flow"), curr_gray

    good_new = p1[st == 1]
    if good_new is None or len(good_new) == 0:
        return SpriteTrack(method="optical_flow"), curr_gray

    xs = good_new[:, 0]
    ys = good_new[:, 1]
    x0, x1 = float(xs.min()), float(xs.max())
    y0, y1 = float(ys.min()), float(ys.max())
    # Bounding box of tracked points; this is a rough proxy for "sprite area".
    return (
        SpriteTrack(
            method="optical_flow",
            x=int(x0),
            y=int(y0),
            w=int(max(1.0, x1 - x0)),
            h=int(max(1.0, y1 - y0)),
            score=None,
        ),
        curr_gray,
    )


def _compute_key_effects(
    session_dir: Path,
    frames_csv_path: Path,
    sprite_track_path: Path,
    keystrokes_path: Path,
) -> Dict[str, Any]:
    """
    Infer which keys move the tracked sprite by correlating key press/release times
    with changes in the sprite center position.
    """
    # Read frames times
    import csv as _csv

    frame_times: Dict[int, float] = {}
    with frames_csv_path.open("r", encoding="utf-8") as f:
        reader = _csv.DictReader(f)
        for row in reader:
            try:
                fi = int(row["frame_idx"])
                frame_times[fi] = float(row["t"])
            except Exception:
                continue

    if not frame_times:
        return {"key_effects": {}, "note": "No frame times found; cannot infer key effects."}

    # Read sprite centers (aligned by frame_idx)
    sprite_centers: Dict[int, Tuple[float, float]] = {}
    with sprite_track_path.open("r", encoding="utf-8") as f:
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
                x = float(x)
                y = float(y)
                w = float(w)
                h = float(h)
                sprite_centers[fi] = (x + w / 2.0, y + h / 2.0)
            except Exception:
                continue

    if not sprite_centers:
        return {"key_effects": {}, "note": "No sprite centers found; cannot infer key effects."}

    # Build arrays for nearest-frame lookup
    frame_idx_sorted = sorted(sprite_centers.keys())
    t_sorted = [frame_times.get(fi) for fi in frame_idx_sorted]
    cx_sorted = [sprite_centers[fi][0] for fi in frame_idx_sorted]
    cy_sorted = [sprite_centers[fi][1] for fi in frame_idx_sorted]

    # Filter out frames missing timestamps
    t2: List[float] = []
    cx2: List[float] = []
    cy2: List[float] = []
    for fi, tt, x, y in zip(frame_idx_sorted, t_sorted, cx_sorted, cy_sorted):
        if isinstance(tt, (int, float)):
            t2.append(float(tt))
            cx2.append(float(x))
            cy2.append(float(y))

    if len(t2) < 2:
        return {"key_effects": {}, "note": "Not enough aligned frame data for key effects."}

    import bisect as _bisect

    def nearest_frame_time(t: float) -> int:
        # Return index into cx2/cy2 arrays
        idx = _bisect.bisect_left(t2, t)
        if idx <= 0:
            return 0
        if idx >= len(t2):
            return len(t2) - 1
        # pick closer of idx-1 and idx
        if abs(t2[idx] - t) < abs(t2[idx - 1] - t):
            return idx
        return idx - 1

    ignored_keys = {
        "Key.esc",
        "esc",
        "Key.ESC",
        "Key.tab",
        "Key.alt_l",
        "Key.alt_r",
        "Key.alt_gr",
        "Key.alt",
    }

    press_times: Dict[str, float] = {}
    press_centers: Dict[str, Tuple[float, float]] = {}
    effects_acc: Dict[str, Dict[str, Any]] = {}

    # Parse keystrokes as press/release pairs
    with keystrokes_path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            evt = json.loads(line)
            key_name = str(evt.get("key", ""))
            event_type = str(evt.get("event", ""))
            t = evt.get("t")
            if not isinstance(t, (int, float)):
                continue
            if key_name in ignored_keys:
                continue
            if event_type == "press":
                press_times[key_name] = float(t)
                idx = nearest_frame_time(float(t))
                press_centers[key_name] = (cx2[idx], cy2[idx])
            elif event_type == "release" and key_name in press_times and key_name in press_centers:
                idx = nearest_frame_time(float(t))
                end_cx = cx2[idx]
                end_cy = cy2[idx]
                start_cx, start_cy = press_centers[key_name]
                dx = float(end_cx - start_cx)
                dy = float(end_cy - start_cy)
                dist = float((dx * dx + dy * dy) ** 0.5)

                acc = effects_acc.get(key_name)
                if acc is None:
                    acc = {"count": 0, "sum_dx": 0.0, "sum_dy": 0.0, "sum_dist": 0.0}
                    effects_acc[key_name] = acc
                acc["count"] += 1
                acc["sum_dx"] += dx
                acc["sum_dy"] += dy
                acc["sum_dist"] += dist

                # clear pair
                press_times.pop(key_name, None)
                press_centers.pop(key_name, None)

    # Finalize
    key_effects: Dict[str, Any] = {}
    for k, acc in effects_acc.items():
        c = int(acc["count"])
        if c <= 0:
            continue
        key_effects[k] = {
            "count": c,
            "avg_dx": acc["sum_dx"] / c,
            "avg_dy": acc["sum_dy"] / c,
            "avg_dist": acc["sum_dist"] / c,
        }

    return {"key_effects": key_effects, "note": "Key effects inferred from sprite center delta at nearest frames."}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a new gameplay model dataset by recording frames + keystrokes.",
    )
    parser.add_argument("model_name", help="Name/id for the model to create")
    parser.add_argument("game_name", help="Game name for this recording session")
    parser.add_argument(
        "--out",
        default="models",
        help="Output root directory (default: models/)",
    )
    parser.add_argument(
        "--monitor",
        type=int,
        default=1,
        help="Monitor index to capture (1-based, default: 1)",
    )
    parser.add_argument(
        "--region",
        default="",
        help='Capture region as "left,top,width,height" (default: full monitor)',
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=30.0,
        help="Target capture FPS (default: 30)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=0.0,
        help="Record for N seconds then stop (default: 0 = until you press ESC)",
    )
    parser.add_argument(
        "--exe-filename",
        default="",
        help="Game executable filename to find under `test/` (e.g. SpaceWalk.exe).",
    )
    parser.add_argument(
        "--exe-args",
        default="",
        help="Optional command-line arguments for the executable (string).",
    )
    parser.add_argument(
        "--game-exe",
        default="",
        help="Optional direct path to a game executable to launch (overrides --exe-filename).",
    )
    parser.add_argument(
        "--game-args",
        default="",
        help="Deprecated alias for --exe-args.",
    )
    parser.add_argument(
        "--launch-delay",
        type=float,
        default=2.0,
        help="Seconds to wait after launching the game executable before starting recording (default: 2.0).",
    )
    parser.add_argument(
        "--template",
        default="",
        help="Optional path to a sprite template image for tracking (grayscale recommended).",
    )
    parser.add_argument(
        "--template-threshold",
        type=float,
        default=0.70,
        help="Template matching acceptance threshold (default: 0.70)",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Show a preview window while recording (press ESC to stop).",
    )
    parser.add_argument(
        "--write-video",
        action="store_true",
        help="Write captured frames to video.mp4 (larger disk usage).",
    )
    args = parser.parse_args()

    cv2, np, mss, keyboard = _require_deps()

    out_root = Path(args.out).resolve()
    session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir = out_root / args.model_name / args.game_name / f"session_{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)

    meta = {
        "model_name": args.model_name,
        "game_name": args.game_name,
        "session_id": session_id,
        "session_created_at_unix": _now_ts(),
        "started_at_unix": None,
        "exe_filename": args.exe_filename or None,
        "game_exe": args.game_exe or None,  # resolved later from --exe-filename if provided
        "game_args": (args.exe_args or args.game_args) or None,
        "fps_target": args.fps,
        "monitor": args.monitor,
        "region": args.region or None,
        "template": args.template or None,
        "template_threshold": args.template_threshold,
        "format": {
            "frames_csv": "frames.csv",
            "keystrokes_jsonl": "keystrokes.jsonl",
            "sprite_jsonl": "sprite_track.jsonl",
            "video": "video.mp4" if args.write_video else None,
        },
    }
    (session_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    # Keystroke logging (async)
    keystrokes_path = session_dir / "keystrokes.jsonl"
    key_events: List[Dict[str, Any]] = []
    pressed: set[str] = set()
    stop_flag = {"stop": False}
    recording_active = {"active": False}

    def _maybe_write_meta() -> None:
        (session_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    def on_press(key: Any) -> None:
        name = _safe_key_name(key)
        if recording_active["active"]:
            pressed.add(name)
            key_events.append({"t": _now_ts(), "event": "press", "key": name})

    def on_release(key: Any) -> Optional[bool]:
        name = _safe_key_name(key)
        if _is_esc_key_name(name):
            stop_flag["stop"] = True
            return False

        if recording_active["active"]:
            if name in pressed:
                pressed.remove(name)
            key_events.append({"t": _now_ts(), "event": "release", "key": name})
        return None

    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.start()

    # Resolve and launch the game executable (by filename under test/).
    game_proc = None
    exe_filename = (args.exe_filename or "").strip()
    try:
        game_exe_path: Optional[Path] = None
        if exe_filename:
            game_exe_path = _find_exe_in_test(exe_filename)
        elif args.game_exe:
            game_exe_path = Path(args.game_exe).resolve()
        if game_exe_path is None:
            raise ValueError("Provide --exe-filename or --game-exe to launch a game.")

        meta["exe_filename"] = game_exe_path.name
        meta["game_exe"] = str(game_exe_path)

        game_args_list: List[str] = []
        if (args.exe_args or args.game_args).strip():
            # Use non-posix parsing to be friendlier to Windows-style quotes.
            game_args_list = shlex.split((args.exe_args or args.game_args).strip(), posix=False)

        cmd = [str(game_exe_path)] + game_args_list
        game_cwd = str(game_exe_path.parent)
        game_proc = subprocess.Popen(cmd, cwd=game_cwd)
        meta["game_proc_pid"] = int(game_proc.pid)
        meta["started_by"] = "manual_recording"
        _maybe_write_meta()
        print(f"Launched game: {game_exe_path} (pid={game_proc.pid})")

        if args.launch_delay and args.launch_delay > 0:
            time.sleep(float(args.launch_delay))
    except Exception as e:
        print(f"Failed to launch game: {e}", file=sys.stderr)
        return 2

    # Start recording right after the launch delay.
    meta["started_at_unix"] = _now_ts()
    pressed.clear()
    recording_active["active"] = True
    _maybe_write_meta()

    template_gray = _load_template(cv2, args.template) if args.template else None
    template_saved_path = None

    # Screen capture setup
    frames_csv = session_dir / "frames.csv"
    sprite_path = session_dir / "sprite_track.jsonl"

    frames_csv.write_text("frame_idx,t,dt,keys_down\n", encoding="utf-8")

    with mss.mss() as sct:
        monitors = sct.monitors  # 0 is "all"; 1..n are individual
        if args.monitor < 1 or args.monitor >= len(monitors):
            print(f"Invalid --monitor={args.monitor}. Available monitors: 1..{len(monitors)-1}", file=sys.stderr)
            return 2

        mon = monitors[args.monitor]
        if args.region:
            try:
                left_s, top_s, w_s, h_s = [x.strip() for x in args.region.split(",")]
                bbox = {
                    "left": int(left_s),
                    "top": int(top_s),
                    "width": int(w_s),
                    "height": int(h_s),
                }
            except Exception:
                print('Invalid --region. Expected "left,top,width,height".', file=sys.stderr)
                return 2
        else:
            bbox = {"left": mon["left"], "top": mon["top"], "width": mon["width"], "height": mon["height"]}

        w = int(bbox["width"])
        h = int(bbox["height"])
        if w <= 0 or h <= 0:
            print("Capture region has non-positive width/height.", file=sys.stderr)
            return 2

        video_writer = None
        if args.write_video:
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            video_writer = cv2.VideoWriter(str(session_dir / "video.mp4"), fourcc, float(args.fps), (w, h))

        prev_gray = None
        frame_idx = 0
        start = _now_ts()
        prev_t = start
        frame_period = 1.0 / max(1.0, float(args.fps))

        try:
            while True:
                now = _now_ts()
                if args.duration and (now - start) >= float(args.duration):
                    break
                if stop_flag["stop"]:
                    break

                # pacing
                if frame_idx > 0:
                    sleep_for = frame_period - (now - prev_t)
                    if sleep_for > 0:
                        time.sleep(sleep_for)
                t = _now_ts()
                dt = t - prev_t
                prev_t = t

                shot = sct.grab(bbox)
                frame = np.array(shot)  # BGRA
                frame_bgr = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

                if video_writer is not None:
                    video_writer.write(frame_bgr)

                # sprite tracking
                track: SpriteTrack
                if template_gray is not None:
                    track = _track_sprite_template(cv2, frame_bgr, template_gray, float(args.template_threshold))
                else:
                    if prev_gray is None:
                        prev_gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
                        track = SpriteTrack(method="optical_flow")
                    else:
                        track, prev_gray = _track_sprite_optical_flow(cv2, np, prev_gray, frame_bgr, max_corners=200)

                    # Auto-create a template once we have a "reasonable" moving bbox.
                    if template_gray is None and template_saved_path is None:
                        try:
                            if track.x is not None and track.y is not None and track.w is not None and track.h is not None:
                                min_dim = 16
                                max_dim = 160
                                if track.w >= min_dim and track.h >= min_dim and track.w <= max_dim and track.h <= max_dim:
                                    frame_gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
                                    tmpl = _cap_template_crop(cv2, frame_gray, int(track.x), int(track.y), int(track.w), int(track.h), max_dim=96)
                                    if tmpl is not None and tmpl.size > 0 and tmpl.shape[0] >= 8 and tmpl.shape[1] >= 8:
                                        template_gray = tmpl
                                        template_saved_path = session_dir / "sprite_template.png"
                                        cv2.imwrite(str(template_saved_path), template_gray)
                                        meta["sprite_template_path"] = str(template_saved_path)
                                        meta["sprite_template_created_by"] = "optical_flow_bbox"
                                        _maybe_write_meta()
                        except Exception:
                            # Template creation should never crash recording.
                            pass

                sprite_evt = {
                    "t": t,
                    "frame_idx": frame_idx,
                    "method": track.method,
                    "bbox": {"x": track.x, "y": track.y, "w": track.w, "h": track.h},
                    "score": track.score,
                }
                with sprite_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(sprite_evt) + "\n")

                # frames index + keys snapshot
                keys_down = sorted(pressed)
                with frames_csv.open("a", encoding="utf-8") as f:
                    f.write(f"{frame_idx},{t:.6f},{dt:.6f},{json.dumps(keys_down)}\n")

                if args.show:
                    try:
                        vis = frame_bgr.copy()
                        if track.x is not None and track.y is not None and track.w is not None and track.h is not None:
                            cv2.rectangle(
                                vis,
                                (int(track.x), int(track.y)),
                                (int(track.x + track.w), int(track.y + track.h)),
                                (0, 255, 0),
                                2,
                            )
                            if track.score is not None:
                                cv2.putText(
                                    vis,
                                    f"{track.method} {track.score:.2f}",
                                    (10, 30),
                                    cv2.FONT_HERSHEY_SIMPLEX,
                                    1,
                                    (0, 255, 0),
                                    2,
                                    cv2.LINE_AA,
                                )
                            else:
                                cv2.putText(
                                    vis,
                                    f"{track.method}",
                                    (10, 30),
                                    cv2.FONT_HERSHEY_SIMPLEX,
                                    1,
                                    (0, 255, 0),
                                    2,
                                    cv2.LINE_AA,
                                )
                        cv2.imshow("new_model recording (ESC to stop)", vis)
                        if cv2.waitKey(1) & 0xFF == 27:
                            break
                    except Exception as e:
                        # OpenCV may be built without HighGUI (common with headless wheels).
                        print(f"--show disabled (OpenCV HighGUI not available): {e}", file=sys.stderr)
                        args.show = False

                frame_idx += 1

        finally:
            if video_writer is not None:
                video_writer.release()
            if args.show:
                try:
                    cv2.destroyAllWindows()
                except Exception:
                    pass

    # Flush any collected keystrokes
    with keystrokes_path.open("a", encoding="utf-8") as f:
        for evt in key_events:
            f.write(json.dumps(evt) + "\n")

    # Infer key effects (which keys move the tracked sprite).
    try:
        key_effects = _compute_key_effects(session_dir, frames_csv, sprite_path, keystrokes_path)
        effects_path = session_dir / "key_effects.json"
        effects_path.write_text(json.dumps(key_effects, indent=2), encoding="utf-8")
        meta["key_effects_path"] = str(effects_path)
        key_effects_count = 0
        try:
            key_effects_count = int(len(key_effects.get("key_effects", {})))
        except Exception:
            key_effects_count = 0
        meta["num_keys_with_effects"] = key_effects_count
        _maybe_write_meta()
    except Exception as e:
        print(f"Could not infer key effects: {e}", file=sys.stderr)

    ended = _now_ts()
    meta["ended_at_unix"] = ended
    started_at = meta.get("started_at_unix")
    if isinstance(started_at, (int, float)):
        meta["duration_seconds"] = float(ended) - float(started_at)
    else:
        meta["duration_seconds"] = 0.0
    (session_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(f"Saved new model session to: {session_dir}")
    print("Files:")
    print(f"  - {frames_csv.name} (frame index + keys_down snapshots)")
    print(f"  - {keystrokes_path.name} (key press/release events)")
    print(f"  - {sprite_path.name} (sprite bbox estimates per frame)")
    if args.write_video:
        print("  - video.mp4 (screen recording)")
    print("Press ESC during capture to stop.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
