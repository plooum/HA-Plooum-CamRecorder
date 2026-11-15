"""Where photos and videos go, how they are named, and which ones the retention removes.

No Home Assistant import: tested in isolation (tests/test_files.py).

    <folder>/<camera>/photos/<YYYY-MM-DD>/<HH-MM-SS>[-<n>].<jpg|png>
    <folder>/<camera>/videos/<YYYY-MM-DD>/<start epoch>-<end epoch>.mp4

<camera> is the object id of the camera entity, dates and times are in Home Assistant's time zone.
The videos follow a layout the gallery card of HA-Plooum-Cards reads. While a video is being
recorded it is <start epoch>-recording.mp4 (Home Assistant writes <that>.tmp first).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
import os
import re

PHOTOS = "photos"
VIDEOS = "videos"

DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PHOTO_RE = re.compile(r"^\d{2}-\d{2}-\d{2}(?:-\d+)?\.(?:jpg|png)$")
VIDEO_RE = re.compile(r"^(\d{9,11})-(\d{9,11})\.mp4$")
PARTIAL_RE = re.compile(r"^(\d{9,11})-recording\.mp4(?:\.tmp)?$")
PARTIAL_MAX_AGE = 86400  # s: a partial video older than this was interrupted (restart, crash)


def camera_slug(entity_id: str) -> str:
    """camera.garden -> garden"""
    return entity_id.split(".", 1)[-1]


def camera_dir(folder: str, entity_id: str) -> str:
    return os.path.join(folder, camera_slug(entity_id))


def photo_path(folder: str, entity_id: str, when: datetime, ext: str, exists: Callable[[str], bool]) -> str:
    """A free path for a photo taken at `when` (local time); -2, -3... for several in a second."""
    day = os.path.join(camera_dir(folder, entity_id), PHOTOS, when.strftime("%Y-%m-%d"))
    base = when.strftime("%H-%M-%S")
    path = os.path.join(day, f"{base}.{ext}")
    n = 2
    while exists(path):
        path = os.path.join(day, f"{base}-{n}.{ext}")
        n += 1
    return path


def partial_video_path(folder: str, entity_id: str, start: datetime) -> str:
    """Path of a video while it is recorded; `start` is aware (local time)."""
    day = os.path.join(camera_dir(folder, entity_id), VIDEOS, start.strftime("%Y-%m-%d"))
    return os.path.join(day, f"{int(start.timestamp())}-recording.mp4")


def final_video_path(partial: str, start: float, end: float) -> str:
    """The name of a finished video, <start>-<end>.mp4 (epochs), next to its partial file."""
    if not PARTIAL_RE.match(os.path.basename(partial)):
        raise ValueError(f"Not a partial video: {partial}")
    start_i = int(round(start))
    return os.path.join(os.path.dirname(partial), f"{start_i}-{max(start_i, int(round(end)))}.mp4")


@dataclass(frozen=True)
class Capture:
    path: str
    kind: str  # "photo", "video" or "partial"
    size: int
    time: float  # epoch: the start of a video, the modification time of a photo


def scan(folder: str, slugs: Iterable[str] | None = None) -> list[Capture]:
    """Every photo, video and partial video under `folder` (only those cameras when `slugs`)."""
    out: list[Capture] = []
    try:
        cameras = sorted(slugs) if slugs is not None else sorted(os.listdir(folder))
    except FileNotFoundError:
        return out
    for cam in cameras:
        for sub in (PHOTOS, VIDEOS):
            base = os.path.join(folder, cam, sub)
            try:
                days = os.listdir(base)
            except (FileNotFoundError, NotADirectoryError):
                continue
            for day in days:
                if not DAY_RE.match(day):
                    continue
                day_dir = os.path.join(base, day)
                try:
                    names = os.listdir(day_dir)
                except (FileNotFoundError, NotADirectoryError):
                    continue
                for name in names:
                    path = os.path.join(day_dir, name)
                    kind = None
                    start: float | None = None
                    if sub == PHOTOS and PHOTO_RE.match(name):
                        kind = "photo"
                    elif sub == VIDEOS and (m := VIDEO_RE.match(name)):
                        kind, start = "video", float(m.group(1))
                    elif sub == VIDEOS and (m := PARTIAL_RE.match(name)):
                        kind, start = "partial", float(m.group(1))
                    if kind is None:
                        continue
                    try:
                        st = os.stat(path)
                    except FileNotFoundError:
                        continue
                    out.append(Capture(path, kind, st.st_size, start if start is not None else st.st_mtime))
    return out


def to_purge(
    captures: Iterable[Capture], now: float, keep_days: float, max_bytes: int, keep: Iterable[str] = ()
) -> list[Capture]:
    """The captures the retention removes: older than `keep_days` (0: no age limit), then the
    oldest ones until the total is under `max_bytes` (0: no size limit). Partial videos are only
    removed once interrupted (older than a day), and never those in `keep` (being recorded)."""
    keep = set(keep)
    removed: list[Capture] = []
    kept: list[Capture] = []
    for c in captures:
        if c.path in keep:
            continue
        if c.kind == "partial":
            if now - c.time > PARTIAL_MAX_AGE:
                removed.append(c)
            continue
        if keep_days > 0 and now - c.time > keep_days * 86400:
            removed.append(c)
        else:
            kept.append(c)
    if max_bytes > 0:
        total = sum(c.size for c in kept)
        for c in sorted(kept, key=lambda c: c.time):
            if total <= max_bytes:
                break
            removed.append(c)
            total -= c.size
    return removed


def remove(captures: Iterable[Capture]) -> int:
    """Delete these files, then the day folders they leave empty. Returns how many went."""
    count = 0
    days: set[str] = set()
    for c in captures:
        try:
            os.remove(c.path)
            count += 1
        except FileNotFoundError:
            pass
        days.add(os.path.dirname(c.path))
    for day in days:
        try:
            os.rmdir(day)  # only when empty
        except OSError:
            pass
    return count


def latest(captures: Iterable[Capture], kind: str) -> Capture | None:
    found = [c for c in captures if c.kind == kind]
    return max(found, key=lambda c: (c.time, c.path)) if found else None


def media_content_id(path: str, media_dirs: dict[str, str]) -> str | None:
    """media-source:// id of a file inside one of Home Assistant's media folders, else None."""
    real = os.path.realpath(path)
    for key, base in media_dirs.items():
        root = os.path.realpath(base)
        if real == root or real.startswith(root + os.sep):
            rel = os.path.relpath(real, root).replace(os.sep, "/")
            return f"media-source://media_source/{key}/{rel}"
    return None
