from pathlib import Path

from app.services.block_m2_face_swap.result_delivery import (
    build_result_zips,
    chunk_photos,
)


def test_chunk_photos_groups_by_ten():
    items = [Path(f"{i}.jpg") for i in range(23)]
    chunks = chunk_photos(items, size=10)
    assert [len(c) for c in chunks] == [10, 10, 3]


def test_build_result_zips_splits_by_size(tmp_path):
    # 5 files of ~30 KB each, cap 50 KB → multiple parts.
    files = []
    for i in range(5):
        p = tmp_path / f"r{i}.jpg"
        p.write_bytes(b"\x00" * 30_000)
        files.append(p)
    out_dir = tmp_path / "zips"
    zips = build_result_zips(files, out_dir, max_bytes=50_000)
    assert len(zips) >= 2
    assert all(z.exists() and z.suffix == ".zip" for z in zips)


def test_build_result_zips_single_part_when_small(tmp_path):
    files = []
    for i in range(3):
        p = tmp_path / f"r{i}.jpg"
        p.write_bytes(b"\x00" * 1000)
        files.append(p)
    zips = build_result_zips(files, tmp_path / "zips", max_bytes=10_000_000)
    assert len(zips) == 1
