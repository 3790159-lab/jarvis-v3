"""Tests for the reference-video frame slicer (Веха B, Task 1).

The cv2 boundary is the only external dependency. Following the pattern in
``test_video_face_swap_engine.py`` (which stubs the decode boundary), these
tests inject a fake cv2 module via ``monkeypatch.setattr(video_frames, "_cv2",
...)`` so the sampling math + file I/O are exercised without a real video.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.block_m2_video import video_frames


# --------------------------------------------------------------------------- #
# Fake cv2 boundary
# --------------------------------------------------------------------------- #
class _FakeCapture:
    def __init__(self, frame_count: int, fps: float = 30.0, opened: bool = True):
        self._frame_count = frame_count
        self._fps = fps
        self._opened = opened
        self._pos = 0
        self.read_indices: list[int] = []
        self.released = False

    def isOpened(self) -> bool:  # noqa: N802 (cv2 API name)
        return self._opened

    def get(self, prop):
        if prop == _FakeCv2.CAP_PROP_FPS:
            return self._fps
        if prop == _FakeCv2.CAP_PROP_FRAME_COUNT:
            return self._frame_count
        return 0

    def set(self, prop, value):  # noqa: A003
        if prop == _FakeCv2.CAP_PROP_POS_FRAMES:
            self._pos = int(value)
        return True

    def read(self):
        self.read_indices.append(self._pos)
        return True, object()  # (ok, non-None frame)

    def release(self) -> None:
        self.released = True


class _FakeCv2:
    CAP_PROP_FPS = 5
    CAP_PROP_FRAME_COUNT = 7
    CAP_PROP_POS_FRAMES = 1
    IMWRITE_JPEG_QUALITY = 1

    def __init__(self, capture: _FakeCapture):
        self._capture = capture
        self.imwrite_params: list[list] = []

    def VideoCapture(self, path):  # noqa: N802
        return self._capture

    def imwrite(self, path, frame, params=None):
        self.imwrite_params.append(params or [])
        Path(path).write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
        return True


def _install(monkeypatch, capture: _FakeCapture) -> _FakeCv2:
    fake = _FakeCv2(capture)
    monkeypatch.setattr(video_frames, "_cv2", lambda: fake)
    return fake


# --------------------------------------------------------------------------- #
# Pure sampling math
# --------------------------------------------------------------------------- #
def test_even_indices_includes_first_and_last():
    idx = video_frames._even_indices(frame_count=100, k=5)
    assert idx[0] == 0
    assert idx[-1] == 99
    assert idx == [0, 25, 50, 74, 99]


def test_even_indices_returns_all_when_fewer_frames_than_k():
    assert video_frames._even_indices(frame_count=3, k=7) == [0, 1, 2]


def test_frame_count_for_duration_is_round_plus_two():
    assert video_frames._frame_count_for_duration(5.0) == 7
    assert video_frames._frame_count_for_duration(15.0) == 17
    assert video_frames._frame_count_for_duration(0.4) == 2


# --------------------------------------------------------------------------- #
# slice_video_to_frames — behaviour
# --------------------------------------------------------------------------- #
def test_slices_k_evenly_spaced_frames(tmp_path, monkeypatch):
    # 150 frames @ 30fps => duration 5.0s => K = round(5)+2 = 7
    cap = _FakeCapture(frame_count=150, fps=30.0)
    _install(monkeypatch, cap)
    out = tmp_path / "frames"

    paths = video_frames.slice_video_to_frames(tmp_path / "ref.mp4", out)

    assert len(paths) == 7
    assert cap.read_indices == [0, 25, 50, 74, 99, 124, 149]
    assert cap.read_indices[0] == 0
    assert cap.read_indices[-1] == 149  # last frame included


def test_frame_files_written_zero_padded(tmp_path, monkeypatch):
    cap = _FakeCapture(frame_count=150, fps=30.0)
    _install(monkeypatch, cap)
    out = tmp_path / "frames"

    paths = video_frames.slice_video_to_frames(tmp_path / "ref.mp4", out)

    names = sorted(p.name for p in paths)
    assert names[0] == "frame_000.jpg"
    assert names[-1] == "frame_006.jpg"
    for p in paths:
        assert p.exists() and p.read_bytes()  # file actually written


def test_returns_all_frames_when_video_shorter_than_k(tmp_path, monkeypatch):
    # 2 frames @ 1fps => duration 2.0s => K = round(2)+2 = 4 > F => take all 2
    cap = _FakeCapture(frame_count=2, fps=1.0)
    _install(monkeypatch, cap)

    paths = video_frames.slice_video_to_frames(tmp_path / "ref.mp4", tmp_path / "f")

    assert len(paths) == 2
    assert cap.read_indices == [0, 1]


def test_unopenable_video_raises(tmp_path, monkeypatch):
    _install(monkeypatch, _FakeCapture(frame_count=100, opened=False))
    with pytest.raises(video_frames.VideoFramesError):
        video_frames.slice_video_to_frames(tmp_path / "ref.mp4", tmp_path / "f")


def test_empty_video_raises(tmp_path, monkeypatch):
    _install(monkeypatch, _FakeCapture(frame_count=0, fps=30.0))
    with pytest.raises(video_frames.VideoFramesError):
        video_frames.slice_video_to_frames(tmp_path / "ref.mp4", tmp_path / "f")


def test_old_frames_dir_wiped_before_slicing(tmp_path, monkeypatch):
    out = tmp_path / "frames"
    out.mkdir()
    stale = out / "frame_999_from_old_video.jpg"
    stale.write_bytes(b"old")
    cap = _FakeCapture(frame_count=150, fps=30.0)
    _install(monkeypatch, cap)

    video_frames.slice_video_to_frames(tmp_path / "ref.mp4", out)

    assert not stale.exists()
    assert all(p.name.startswith("frame_0") for p in out.iterdir())


def test_capture_released(tmp_path, monkeypatch):
    cap = _FakeCapture(frame_count=150, fps=30.0)
    _install(monkeypatch, cap)
    video_frames.slice_video_to_frames(tmp_path / "ref.mp4", tmp_path / "f")
    assert cap.released
