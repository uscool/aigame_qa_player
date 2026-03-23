import json
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


def _safe_float(x: Any) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None


def _parse_json_list(maybe_json: str) -> List[Any]:
    try:
        v = json.loads(maybe_json)
        if isinstance(v, list):
            return v
    except Exception:
        pass
    return []


@dataclass
class FrameSeries:
    frame_idx: np.ndarray
    t: np.ndarray
    dt: np.ndarray
    keys_down_counts: np.ndarray
    keys_down_raw: Optional[List[List[str]]] = None


@dataclass
class SpriteSeries:
    frame_idx: np.ndarray
    t: np.ndarray
    method: List[str]
    x: np.ndarray
    y: np.ndarray
    w: np.ndarray
    h: np.ndarray
    score: List[Optional[float]]

    @property
    def center_x(self) -> np.ndarray:
        return self.x + self.w / 2.0

    @property
    def center_y(self) -> np.ndarray:
        return self.y + self.h / 2.0


@dataclass
class SessionStats:
    session_dir: Path
    model_name: Optional[str]
    game_name: Optional[str]
    started_at_unix: Optional[float]
    ended_at_unix: Optional[float]
    duration_seconds: float

    avg_fps: float
    min_fps: float
    fps_drop_count: int
    glitch_count: int
    method_counts: Dict[str, int]


def load_meta(session_dir: Path) -> Dict[str, Any]:
    meta_path = session_dir / "meta.json"
    if not meta_path.exists():
        return {}
    return json.loads(meta_path.read_text(encoding="utf-8"))


def load_frames_csv(session_dir: Path) -> FrameSeries:
    frames_csv = session_dir / "frames.csv"
    if not frames_csv.exists():
        raise FileNotFoundError(f"Missing {frames_csv}")

    frame_idx: List[int] = []
    t: List[float] = []
    dt: List[float] = []
    keys_counts: List[int] = []

    with frames_csv.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            fi = int(row["frame_idx"])
            ts = _safe_float(row["t"])
            dts = _safe_float(row["dt"])
            if ts is None or dts is None:
                continue
            keys = _parse_json_list(row["keys_down"])
            frame_idx.append(fi)
            t.append(ts)
            dt.append(dts)
            keys_counts.append(len(keys))

    return FrameSeries(
        frame_idx=np.array(frame_idx, dtype=np.int64),
        t=np.array(t, dtype=np.float64),
        dt=np.array(dt, dtype=np.float64),
        keys_down_counts=np.array(keys_counts, dtype=np.int64),
        keys_down_raw=None,
    )


def load_sprite_jsonl(session_dir: Path) -> SpriteSeries:
    sprite_path = session_dir / "sprite_track.jsonl"
    if not sprite_path.exists():
        raise FileNotFoundError(f"Missing {sprite_path}")

    frame_idx: List[int] = []
    t: List[float] = []
    method: List[str] = []
    x: List[int] = []
    y: List[int] = []
    w: List[int] = []
    h: List[int] = []
    score: List[Optional[float]] = []

    with sprite_path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            evt = json.loads(line)
            frame_idx.append(int(evt["frame_idx"]))
            t.append(float(evt["t"]))
            method.append(str(evt.get("method", "unknown")))
            bbox = evt.get("bbox") or {}
            xv = bbox.get("x", 0)
            yv = bbox.get("y", 0)
            wv = bbox.get("w", 0)
            hv = bbox.get("h", 0)
            x.append(int(xv) if xv is not None else 0)
            y.append(int(yv) if yv is not None else 0)
            w.append(int(wv) if wv is not None else 0)
            h.append(int(hv) if hv is not None else 0)
            score.append(evt.get("score"))

    return SpriteSeries(
        frame_idx=np.array(frame_idx, dtype=np.int64),
        t=np.array(t, dtype=np.float64),
        method=method,
        x=np.array(x, dtype=np.int64),
        y=np.array(y, dtype=np.int64),
        w=np.array(w, dtype=np.int64),
        h=np.array(h, dtype=np.int64),
        score=score,
    )


def _compute_fps_stats(frames: FrameSeries, target_fps: Optional[float]) -> Tuple[float, float, int]:
    # FPS per frame from dt; ignore invalid dt <= 0
    valid = frames.dt > 0
    if not np.any(valid):
        return 0.0, 0.0, 0

    fps = 1.0 / frames.dt[valid]
    avg_fps = float(np.mean(fps))
    min_fps = float(np.min(fps))

    if target_fps is None or target_fps <= 0:
        # heuristic: "drop" if dt is in upper quartile
        thresh_dt = float(np.quantile(frames.dt[valid], 0.75)) * 1.5
        fps_drop_count = int(np.sum(frames.dt > thresh_dt))
        return avg_fps, min_fps, fps_drop_count

    # "drop" if fps falls below 80% of target
    fps_drop_count = int(np.sum(fps < (0.8 * float(target_fps))))
    return avg_fps, min_fps, fps_drop_count


def _compute_glitches(sprite: SpriteSeries) -> Tuple[int, List[int]]:
    # Glitch proxy: large jumps in bbox center between frames.
    cx = sprite.center_x
    cy = sprite.center_y
    if cx.shape[0] < 3:
        return 0, []

    dx = np.diff(cx)
    dy = np.diff(cy)
    dist = np.sqrt(dx * dx + dy * dy)  # pixels/frame

    # threshold using robust statistics
    med = float(np.median(dist))
    mad = float(np.median(np.abs(dist - med)))
    # avoid zero division / ultra-low MAD
    if mad < 1e-6:
        thresh = med + 50.0
    else:
        thresh = med + 6.0 * (1.4826 * mad)

    glitch_indices = [int(i + 1) for i, d in enumerate(dist) if d > thresh]
    return len(glitch_indices), glitch_indices


def summarize_session(session_dir: Path, glitch_threshold_hint: Optional[float] = None) -> SessionStats:
    session_dir = Path(session_dir)
    meta = load_meta(session_dir)

    frames = load_frames_csv(session_dir)
    sprite = load_sprite_jsonl(session_dir)

    target_fps = _safe_float(meta.get("fps_target"))
    avg_fps, min_fps, fps_drop_count = _compute_fps_stats(frames, target_fps)
    glitch_count, _glitch_indices = _compute_glitches(sprite)

    method_counts: Dict[str, int] = {}
    for m in sprite.method:
        method_counts[m] = method_counts.get(m, 0) + 1

    started_at_unix = meta.get("started_at_unix")
    ended_at_unix = meta.get("ended_at_unix")

    duration_seconds = float(meta.get("duration_seconds", 0.0) or 0.0)

    return SessionStats(
        session_dir=session_dir,
        model_name=meta.get("model_name"),
        game_name=meta.get("game_name"),
        started_at_unix=started_at_unix if isinstance(started_at_unix, (int, float)) else None,
        ended_at_unix=ended_at_unix if isinstance(ended_at_unix, (int, float)) else None,
        duration_seconds=duration_seconds,
        avg_fps=avg_fps,
        min_fps=min_fps,
        fps_drop_count=fps_drop_count,
        glitch_count=glitch_count,
        method_counts=method_counts,
    )


def plot_session(session_dir: Path, title: Optional[str] = None) -> None:
    """
    Plot a few basic graphs from a session.
    Requires `matplotlib` to be installed.
    """
    try:
        import matplotlib.pyplot as plt  # type: ignore
    except Exception:
        raise RuntimeError(
            "matplotlib is not installed. Install with: python -m pip install matplotlib"
        )

    meta = load_meta(session_dir)
    target_fps = _safe_float(meta.get("fps_target"))

    frames = load_frames_csv(session_dir)
    sprite = load_sprite_jsonl(session_dir)

    # Derived series
    valid = frames.dt > 0
    fps = np.full(frames.dt.shape, np.nan, dtype=np.float64)
    fps[valid] = 1.0 / frames.dt[valid]

    cx = sprite.center_x
    cy = sprite.center_y

    # glitch indices
    glitch_count, glitch_indices = _compute_glitches(sprite)

    if title is None:
        title = f"{meta.get('model_name','?')}/{meta.get('game_name','?')} - {session_dir.name}"

    fig = plt.figure(figsize=(12, 8))
    fig.suptitle(title)

    ax1 = plt.subplot(3, 1, 1)
    ax1.plot(frames.t, fps, linewidth=1)
    ax1.set_ylabel("FPS")
    if target_fps is not None:
        ax1.axhline(0.8 * float(target_fps), color="orange", linestyle="--", label="0.8 target")
        ax1.legend(loc="upper right")

    # mark glitches using bbox center jump indices (align by index)
    ax2 = plt.subplot(3, 1, 2)
    ax2.plot(cx, cy, linewidth=1)
    if glitch_indices:
        gx = cx[glitch_indices]
        gy = cy[glitch_indices]
        ax2.scatter(gx, gy, c="red", s=10, label=f"glitches={glitch_count}")
        ax2.legend(loc="upper right")
    ax2.set_xlabel("center_x (px)")
    ax2.set_ylabel("center_y (px)")

    ax3 = plt.subplot(3, 1, 3)
    ax3.plot(frames.t, frames.keys_down_counts, linewidth=1)
    ax3.set_ylabel("keys_down_count")
    ax3.set_xlabel("time (unix seconds)")

    plt.tight_layout()
    plt.show()


def list_sessions(models_root: Path, model_name: str, game_name: str) -> List[Path]:
    base = Path(models_root) / model_name / game_name
    if not base.exists():
        return []
    sessions = sorted(base.glob("session_*"), key=lambda p: p.stat().st_mtime)
    return sessions


def find_latest_session(models_root: Path, model_name: str, game_name: str) -> Optional[Path]:
    sessions = list_sessions(models_root, model_name, game_name)
    if not sessions:
        return None
    return sessions[-1]

