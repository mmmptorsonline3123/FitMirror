"""Pose-rule unit tests on synthetic landmarks, plus one smoke test of the real MediaPipe model."""
import numpy as np
import pytest
from app.pose import _FAKE, _FAKE_SIDE, Landmark, MediaPipeDetector
from app.validation import PhotoRejected, check_pose

W, H = 600, 1000


def person(**overrides):
    pts = {i: [*_FAKE.get(i, (0.5, 0.3)), 0.99] for i in range(33)}
    for k, v in overrides.items():
        pts[int(k[1:])] = list(v) + [0.99] if len(v) == 2 else list(v)
    return [Landmark(*pts[i]) for i in range(33)]


def code(people, view="front"):
    with pytest.raises(PhotoRejected) as e:
        check_pose(people, view, W, H)
    return e.value.code


def side_person(**kw):
    return person(**{f"i{k}": v for k, v in _FAKE_SIDE.items()}, **kw)


def test_ideal_poses_pass():
    check_pose([person()], "front", W, H)
    check_pose([person()], "back", W, H)
    check_pose([side_person()], "side", W, H)


def test_rejections():
    assert code([]) == "no_person"
    assert code([person(), person()]) == "multiple_people"
    assert code([person(i0=(0.5, -0.05, 0.2))]) == "head_cut_off"
    assert code([person(i27=(0.55, 1.05, 0.1), i28=(0.45, 1.05, 0.1))]) == "feet_cut_off"
    assert code([person(i23=(0.55, 0.52, 0.1), i24=(0.45, 0.52, 0.1))]) == "body_hidden"
    far = {f"i{i}": (x, 0.4 + (y - 0.12) * 0.4) for i, (x, y) in _FAKE.items()}
    assert code([person(**far)]) == "too_far"
    assert code([person(i15=(0.7, 0.05), i16=(0.3, 0.05))]) == "arms_raised"
    assert code([person(i25=(0.55, 0.5), i26=(0.45, 0.5))]) == "not_standing"
    assert code([person(i23=(0.75, 0.52), i24=(0.65, 0.52))]) == "not_upright"
    assert code([side_person()], "front") == "wrong_view"
    assert code([person()], "side") == "wrong_view"
    assert code([person(i11=(0.36, 0.25), i12=(0.64, 0.25))], "front") == "wrong_view"  # facing away


def test_real_mediapipe_model_loads_and_finds_nobody_in_blank_image():
    try:
        det = MediaPipeDetector("models/pose_landmarker_full.task")
    except OSError as e:  # e.g. Windows Smart App Control blocking the native DLL; runs on Linux CI/Render
        pytest.skip(f"MediaPipe native library could not load here: {e}")
    try:
        assert det.detect(np.full((1000, 600, 3), 220, np.uint8)) == []
        assert det.segment(np.full((1000, 600, 3), 220, np.uint8)) is None
    finally:
        det.close()
