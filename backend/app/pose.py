"""Pose detection behind a tiny interface so the detector is swappable (and fakeable for E2E)."""
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol
import numpy as np
from .config import get_settings


@dataclass
class Landmark:
    x: float  # normalized 0..1 (image width)
    y: float  # normalized 0..1 (image height)
    visibility: float


class PoseDetector(Protocol):
    # `view` is the capture view the user claims (front/side/back). Real detectors ignore it; FakeDetector uses it.
    def detect(self, rgb: np.ndarray, view: str | None = None) -> list[list[Landmark]]:
        """Return one 33-landmark list (MediaPipe/BlazePose topology) per detected person."""

    def segment(self, rgb: np.ndarray, view: str | None = None) -> tuple[list[Landmark], np.ndarray] | None:
        """First person's landmarks + bool person mask (h, w), or None if nobody."""


class MediaPipeDetector:
    def __init__(self, model_path: str):
        from mediapipe.tasks.python import BaseOptions, vision
        self._mp_image = __import__("mediapipe").Image
        self._fmt = __import__("mediapipe").ImageFormat.SRGB
        self._lm = vision.PoseLandmarker.create_from_options(
            vision.PoseLandmarkerOptions(base_options=BaseOptions(model_asset_path=model_path), num_poses=2,
                                         output_segmentation_masks=True)
        )

    def close(self):
        self._lm.close()  # an unclosed landmarker can hang interpreter exit (seen under pytest on Windows)

    def _run(self, rgb):
        return self._lm.detect(self._mp_image(image_format=self._fmt, data=np.ascontiguousarray(rgb)))

    def detect(self, rgb, view=None):
        return [[Landmark(p.x, p.y, p.visibility or 0.0) for p in pose] for pose in self._run(rgb).pose_landmarks]

    def segment(self, rgb, view=None):
        res = self._run(rgb)
        if not res.pose_landmarks or not res.segmentation_masks:
            return None
        mask = np.squeeze(res.segmentation_masks[0].numpy_view()) > 0.5
        return [Landmark(p.x, p.y, p.visibility or 0.0) for p in res.pose_landmarks[0]], mask


# Ideal standing front-view A-pose, used only by FakeDetector.
_FAKE = {0: (0.5, 0.12), 11: (0.64, 0.25), 12: (0.36, 0.25), 13: (0.68, 0.38), 14: (0.32, 0.38), 15: (0.7, 0.5), 16: (0.3, 0.5),
         23: (0.55, 0.52), 24: (0.45, 0.52), 25: (0.55, 0.7), 26: (0.45, 0.7), 27: (0.55, 0.88), 28: (0.45, 0.88),
         29: (0.55, 0.9), 30: (0.45, 0.9), 31: (0.56, 0.91), 32: (0.44, 0.91)}

_FAKE_SIDE = {0: (0.54, 0.12), 11: (0.51, 0.25), 12: (0.49, 0.25), 23: (0.505, 0.52), 24: (0.495, 0.52)}

# Matching silhouettes (normalized polygons; head drawn as ellipse). Head top y=0.06, floor y=0.92, crotch y=0.58.
_FRONT_POLYS = [
    [(.47, .16), (.53, .16), (.53, .22), (.47, .22)],  # neck
    [(.37, .22), (.63, .22), (.635, .32), (.60, .42), (.60, .50), (.62, .58), (.38, .58), (.40, .50), (.40, .42), (.365, .32)],  # torso
    [(.38, .58), (.497, .58), (.495, .92), (.44, .92)], [(.503, .58), (.62, .58), (.56, .92), (.505, .92)],  # legs
    [(.645, .22), (.68, .22), (.73, .50), (.70, .50)], [(.355, .22), (.32, .22), (.27, .50), (.30, .50)],  # arms (apart from torso)
]
_SIDE_POLYS = [
    [(.47, .16), (.53, .16), (.53, .22), (.47, .22)],
    [(.40, .22), (.60, .22), (.62, .32), (.60, .42), (.615, .55), (.60, .58), (.40, .58), (.385, .55), (.40, .42), (.38, .32)],
    [(.42, .58), (.58, .58), (.555, .92), (.45, .92)],
]


class FakeDetector:
    """Test/demo only (blocked in production by config). Blank (uniform) image -> nobody. Otherwise an ideal pose:
    side if the caller says view="side" (or, with no view given, if the image is narrow, w/h < 0.55), else front."""
    @staticmethod
    def _is_side(rgb, view):
        h, w = rgb.shape[:2]
        return view == "side" if view else w / h < 0.55

    def detect(self, rgb, view=None):
        if float(rgb.std()) < 2.0:
            return []
        pts = {**_FAKE, **_FAKE_SIDE} if self._is_side(rgb, view) else _FAKE
        return [[Landmark(*pts.get(i, (0.5, 0.3)), 0.99) for i in range(33)]]

    def segment(self, rgb, view=None):
        from PIL import Image, ImageDraw
        people = self.detect(rgb, view)
        if not people:
            return None
        h, w = rgb.shape[:2]
        img = Image.new("1", (w, h), 0)
        d = ImageDraw.Draw(img)
        d.ellipse([(.445 * w, .06 * h), (.555 * w, .17 * h)], fill=1)
        for poly in _SIDE_POLYS if self._is_side(rgb, view) else _FRONT_POLYS:
            d.polygon([(x * w, y * h) for x, y in poly], fill=1)
        return people[0], np.array(img, dtype=bool)


@lru_cache
def get_detector() -> PoseDetector:
    s = get_settings()
    return FakeDetector() if s.pose_detector == "fake" else MediaPipeDetector(s.pose_model_path)
