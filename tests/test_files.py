"""Tests of the file layout and the retention (files.py), without Home Assistant.

Run: python -m unittest discover tests
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import os
import sys
from pathlib import Path
import tempfile
import unittest

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "plooum_camrecorder" / "files.py"
spec = importlib.util.spec_from_file_location("camrecorder_files", SRC)
files = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = files  # for its dataclass
spec.loader.exec_module(files)

TZ = timezone(timedelta(hours=1))
DAY = 86400


def touch(path: str, size: int = 10, mtime: float | None = None) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class Naming(unittest.TestCase):
    def test_photo_path_and_duplicates(self):
        when = datetime(2026, 11, 15, 14, 30, 5, tzinfo=TZ)
        taken = {"/m/garden/photos/2026-11-15/14-30-05.jpg", "/m/garden/photos/2026-11-15/14-30-05-2.jpg"}
        self.assertEqual(
            files.photo_path("/m", "camera.garden", when, "jpg", lambda p: False),
            "/m/garden/photos/2026-11-15/14-30-05.jpg",
        )
        self.assertEqual(
            files.photo_path("/m", "camera.garden", when, "jpg", taken.__contains__),
            "/m/garden/photos/2026-11-15/14-30-05-3.jpg",
        )

    def test_video_paths_match_the_gallery_layout(self):
        start = datetime(2026, 11, 15, 23, 59, 50, tzinfo=TZ)
        partial = files.partial_video_path("/m", "camera.garden", start)
        ts = int(start.timestamp())
        self.assertEqual(partial, f"/m/garden/videos/2026-11-15/{ts}-recording.mp4")
        final = files.final_video_path(partial, ts - 2.4, ts + 41.6)
        self.assertEqual(final, f"/m/garden/videos/2026-11-15/{ts - 2}-{ts + 42}.mp4")
        # The day is the day of the start, even past midnight.
        self.assertTrue(files.VIDEO_RE.match(os.path.basename(final)))
        self.assertFalse(files.VIDEO_RE.match(os.path.basename(partial)))

    def test_media_content_id(self):
        with tempfile.TemporaryDirectory() as media:
            path = os.path.join(media, "plooum_camrecorder", "garden", "photos", "2026-11-15", "14-30-05.jpg")
            self.assertEqual(
                files.media_content_id(path, {"local": media}),
                "media-source://media_source/local/plooum_camrecorder/garden/photos/2026-11-15/14-30-05.jpg",
            )
            self.assertIsNone(files.media_content_id("/elsewhere/x.jpg", {"local": media}))


class DefaultFolder(unittest.TestCase):
    def pick(self, media_dirs, fallback="/media"):
        return files.default_folder(media_dirs, fallback, "plooum_camrecorder")

    def test_a_media_folder_of_its_own(self):
        dirs = {"tapo_entree": "/media/tapo/entree", "plooum_camrecorder": "/media/plooum_camrecorder"}
        self.assertEqual(self.pick(dirs), "/media/plooum_camrecorder")
        self.assertEqual(self.pick({"recordings": "/mnt/nas/plooum_camrecorder/"}), "/mnt/nas/plooum_camrecorder")

    def test_local_media_folder(self):
        self.assertEqual(self.pick({"local": "/media"}), "/media/plooum_camrecorder")

    def test_never_in_another_media_folder(self):
        # A camera's own recordings folder is not taken: the fallback is
        self.assertEqual(self.pick({"tapo": "/media/tapo/entree"}), "/media/plooum_camrecorder")
        self.assertEqual(self.pick({}, "/config/media"), "/config/media/plooum_camrecorder")


class Retention(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.now = 1_800_000_000.0

    def tearDown(self):
        self.tmp.cleanup()

    def video(self, cam, age_days, size=10, partial=False):
        start = int(self.now - age_days * DAY)
        day = datetime.fromtimestamp(start, TZ).strftime("%Y-%m-%d")
        name = f"{start}-recording.mp4" if partial else f"{start}-{start + 30}.mp4"
        return touch(os.path.join(self.root, cam, "videos", day, name), size)

    def photo(self, cam, age_days, size=10):
        t = self.now - age_days * DAY
        day = datetime.fromtimestamp(t, TZ).strftime("%Y-%m-%d")
        return touch(os.path.join(self.root, cam, "photos", day, datetime.fromtimestamp(t, TZ).strftime("%H-%M-%S.jpg")), size, t)

    def purged(self, keep_days, max_bytes, keep=()):
        return sorted(c.path for c in files.to_purge(files.scan(self.root), self.now, keep_days, max_bytes, keep))

    def test_age(self):
        old_v, old_p = self.video("garden", 40), self.photo("garden", 31)
        self.video("garden", 2)
        self.photo("porch", 29)
        self.assertEqual(self.purged(30, 0), sorted([old_v, old_p]))
        self.assertEqual(self.purged(0, 0), [])

    def test_size_removes_the_oldest_first(self):
        a = self.video("garden", 5, 100)
        b = self.photo("porch", 4, 100)
        self.video("garden", 1, 100)
        self.assertEqual(self.purged(0, 250), [a])
        self.assertEqual(self.purged(0, 150), sorted([a, b]))

    def test_other_files_are_never_touched(self):
        touch(os.path.join(self.root, "garden", "videos", "2020-01-01", "notes.txt"))
        touch(os.path.join(self.root, "garden", "other", "2020-01-01", "12-00-00.jpg"))
        touch(os.path.join(self.root, "readme.md"))
        self.assertEqual(self.purged(1, 1), [])

    def test_partial_videos(self):
        stale = self.video("garden", 2, partial=True)
        recent = self.video("garden", 0.01, partial=True)
        self.assertEqual(self.purged(30, 0), [stale])
        self.assertNotIn(recent, self.purged(0.001, 1))
        self.assertEqual(self.purged(30, 0, keep=[stale]), [])

    def test_remove_deletes_empty_day_folders(self):
        v = self.video("garden", 40)
        keep = self.video("garden", 40.0001)  # same day, kept
        files.remove([c for c in files.scan(self.root) if c.path == v])
        self.assertFalse(os.path.exists(v))
        self.assertTrue(os.path.isdir(os.path.dirname(keep)))
        files.remove([c for c in files.scan(self.root) if c.path == keep])
        self.assertFalse(os.path.exists(os.path.dirname(keep)))

    def test_latest(self):
        self.video("garden", 3)
        newest = self.video("garden", 1)
        self.assertEqual(files.latest(files.scan(self.root), "video").path, newest)
        self.assertIsNone(files.latest(files.scan(self.root), "photo"))


if __name__ == "__main__":
    unittest.main()
