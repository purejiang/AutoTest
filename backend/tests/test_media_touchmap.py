"""无真机测试：TouchMapper 旋转时间线 + 最小二乘标定 + 持久化。"""

from __future__ import annotations

import pytest

from app import config
from app.android import touchmap
from app.android.touchmap import TouchMapper
from app.errors import ApiError


# ---------------------------------------------------------------- 旋转数学
@pytest.mark.parametrize(
    ("rotation", "point", "expected"),
    [
        (0, (0.25, 0.5), (0.25, 0.5)),
        (90, (0.25, 0.5), (0.5, 0.25)),
        (180, (0.25, 0.5), (0.75, 0.5)),
        (270, (0.25, 0.5), (0.5, 0.75)),
        (0, (0.0, 0.0), (0.0, 0.0)),
        (90, (0.0, 0.0), (1.0, 0.0)),
        (180, (0.0, 0.0), (1.0, 1.0)),
        (270, (0.0, 0.0), (0.0, 1.0)),
    ],
)
def test_rotate_normalized(rotation: int, point: tuple[float, float], expected: tuple[float, float]) -> None:
    assert touchmap.rotate_normalized(point[0], point[1], rotation) == pytest.approx(expected)


def test_rotate_normalized_accepts_quarter_turns() -> None:
    assert touchmap.rotate_normalized(0.25, 0.5, 1) == pytest.approx((0.5, 0.25))
    assert touchmap.rotate_normalized(0.25, 0.5, 2) == pytest.approx((0.75, 0.5))
    assert touchmap.rotate_normalized(0.25, 0.5, 3) == pytest.approx((0.5, 0.75))
    with pytest.raises(ValueError):
        touchmap.rotate_normalized(0.1, 0.2, 45)


# ---------------------------------------------------------------- 标定
def test_calibrate_two_points_linear() -> None:
    mapper = TouchMapper(serial="fake-1", screen_width=1080, screen_height=2400)
    calibration = mapper.calibrate(
        [(0.1, 0.1), (0.9, 0.9)],
        [(0.2, 0.2), (1.0, 1.0)],
    )
    assert calibration.ax == pytest.approx(1.0)
    assert calibration.bx == pytest.approx(0.1)
    assert calibration.ay == pytest.approx(1.0)
    assert calibration.by == pytest.approx(0.1)
    assert mapper.calibration.apply(0.5, 0.5) == pytest.approx((0.6, 0.6))


def test_calibrate_two_points_scale() -> None:
    mapper = TouchMapper(screen_width=1000, screen_height=2000)
    mapper.calibrate(
        [(0.0, 0.0), (1.0, 1.0)],
        [(0.1, 0.2), (0.9, 0.8)],
    )
    assert mapper.calibration.ax == pytest.approx(0.8)
    assert mapper.calibration.bx == pytest.approx(0.1)
    assert mapper.calibration.ay == pytest.approx(0.6)
    assert mapper.calibration.by == pytest.approx(0.2)


def test_calibrate_one_point_is_offset_only() -> None:
    mapper = TouchMapper(screen_width=1080, screen_height=2400)
    calibration = mapper.calibrate([(0.5, 0.5)], [(0.55, 0.45)])
    assert calibration.ax == pytest.approx(1.0)
    assert calibration.bx == pytest.approx(0.05)
    assert calibration.ay == pytest.approx(1.0)
    assert calibration.by == pytest.approx(-0.05)


def test_calibrate_validation() -> None:
    mapper = TouchMapper(screen_width=1080, screen_height=2400)
    with pytest.raises(ApiError) as missing:
        mapper.calibrate([], [])
    assert missing.value.code == "INVALID_ARGUMENT"
    with pytest.raises(ApiError) as mismatch:
        mapper.calibrate([(0.1, 0.1)], [(0.1, 0.1), (0.2, 0.2)])
    assert mismatch.value.code == "INVALID_ARGUMENT"


# ---------------------------------------------------------------- apply（旋转 + 标定）
def test_apply_rotation_zero() -> None:
    mapper = TouchMapper(screen_width=1080, screen_height=2400)
    assert mapper.apply(0.0, 0.0) == (0, 0)
    assert mapper.apply(1.0, 1.0) == (1079, 2399)
    assert mapper.apply(0.5, 0.5) == (round(0.5 * 1079), round(0.5 * 2399))


def test_apply_rotation_90_swaps_display_size() -> None:
    mapper = TouchMapper(screen_width=1080, screen_height=2400)
    mapper.add_rotation(90, at_ts=100.0)
    assert mapper.display_size() == (2400, 1080)
    # raw (0.25, 0.5) → 显示 (0.5, 0.25) → (1200, 270)
    assert mapper.apply(0.25, 0.5, at_ts=150.0) == (1200, 270)
    assert mapper.apply(0.0, 0.0, at_ts=150.0) == (2399, 0)


def test_apply_rotation_180_and_270() -> None:
    mapper = TouchMapper(screen_width=1080, screen_height=2400)
    mapper.add_rotation(180, at_ts=0.0)
    assert mapper.apply(0.25, 0.5, at_ts=10.0) == (round(0.75 * 1079), round(0.5 * 2399))
    mapper.add_rotation(270, at_ts=20.0)
    assert mapper.display_size() == (2400, 1080)
    assert mapper.apply(0.25, 0.5, at_ts=30.0) == (round(0.5 * 2399), round(0.75 * 1079))


def test_rotation_timeline_lookup() -> None:
    mapper = TouchMapper(screen_width=1080, screen_height=2400)
    mapper.add_rotation(0, at_ts=100.0)
    mapper.add_rotation(90, at_ts=200.0)
    mapper.add_rotation(180, at_ts=300.0)
    assert mapper.rotation_at(50.0) == 0
    assert mapper.rotation_at(150.0) == 0
    assert mapper.rotation_at(200.0) == 90
    assert mapper.rotation_at(250.0) == 90
    assert mapper.rotation_at(999.0) == 180
    # 同一时刻覆盖
    mapper.add_rotation(270, at_ts=250.0)
    assert mapper.rotation_at(260.0) == 270


def test_apply_uses_calibration_before_rotation() -> None:
    mapper = TouchMapper(screen_width=1000, screen_height=2000)
    mapper.calibrate([(0.1, 0.1), (0.9, 0.9)], [(0.2, 0.2), (1.0, 1.0)])
    mapper.add_rotation(90, at_ts=0.0)
    # calibrated (0.5,0.5) → 0.6,0.6 → rotation90 → (0.4,0.6) of 2000x1000
    assert mapper.apply(0.5, 0.5, at_ts=1.0) == (round(0.4 * 1999), round(0.6 * 999))


def test_apply_requires_screen_size() -> None:
    mapper = TouchMapper()
    with pytest.raises(ApiError) as exc:
        mapper.apply(0.5, 0.5)
    assert exc.value.code == "INVALID_ARGUMENT"
    with pytest.raises(ApiError):
        mapper.apply(float("nan"), 0.5)


# ---------------------------------------------------------------- 持久化
def test_to_dict_from_dict_roundtrip() -> None:
    mapper = TouchMapper(serial="serial:1", screen_width=1080, screen_height=2400)
    mapper.add_rotation(90, at_ts=10.0)
    mapper.add_rotation(180, at_ts=20.0)
    mapper.calibrate([(0.0, 0.0), (1.0, 1.0)], [(0.1, 0.1), (0.9, 0.9)])
    data = mapper.to_dict()
    assert data["version"] == touchmap.MAP_VERSION
    restored = TouchMapper.from_dict(data)
    assert restored.screen_width == 1080
    assert restored.screen_height == 2400
    assert restored.rotation_timeline == [(10.0, 90), (20.0, 180)]
    assert restored.calibration.ax == pytest.approx(0.8)
    assert restored.calibration.bx == pytest.approx(0.1)
    assert restored.apply(0.5, 0.5, at_ts=15.0) == mapper.apply(0.5, 0.5, at_ts=15.0)


def test_save_and_load(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    mapper = TouchMapper(serial="emulator-5554", screen_width=1080, screen_height=2400)
    mapper.add_rotation(90, at_ts=5.0)
    mapper.calibrate([(0.2, 0.2)], [(0.25, 0.25)])
    path = mapper.save()
    assert path == tmp_path / "touchmap_emulator-5554.json"
    assert path.exists()
    loaded = TouchMapper.load("emulator-5554")
    assert loaded.screen_width == 1080
    assert loaded.rotation_timeline == [(5.0, 90)]
    assert loaded.calibration.bx == pytest.approx(0.05)
    assert loaded.calibration.by == pytest.approx(0.05)


def test_load_missing_returns_empty_mapper(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    mapper = TouchMapper.load("does-not-exist")
    assert mapper.serial == "does-not-exist"
    assert mapper.screen_width == 0
    assert mapper.calibration.is_identity


def test_load_corrupt_file_returns_identity(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    (tmp_path / "touchmap_bad.json").write_text("{not json", encoding="utf-8")
    mapper = TouchMapper.load("bad")
    assert mapper.calibration.is_identity
