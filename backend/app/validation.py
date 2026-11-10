"""Server-side photo validation. Every rejection carries a specific, user-actionable reason."""
import io
import math
from dataclasses import dataclass
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError
from .pose import Landmark, PoseDetector

VIEWS = ("front", "side", "back")
MIN_SHORT, MIN_LONG, MAX_LONG = 480, 800, 2048
VIS = 0.5  # landmark visibility threshold
NOSE, LSH, RSH, LWR, RWR, LHIP, RHIP, LKN, RKN, LANK, RANK = 0, 11, 12, 15, 16, 23, 24, 25, 26, 27, 28


class PhotoRejected(Exception):
    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code, self.reason = code, reason


@dataclass
class CleanImage:
    jpeg: bytes  # re-encoded: EXIF (incl. GPS) stripped, orientation applied
    rgb: np.ndarray
    width: int
    height: int


def load_image(data: bytes, max_bytes: int, min_short: int = MIN_SHORT, min_long: int = MIN_LONG, portrait: bool = True) -> CleanImage:
    if len(data) > max_bytes:
        raise PhotoRejected("too_large", f"Photo is larger than {max_bytes // (1024 * 1024)} MB.")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise PhotoRejected("bad_image", "That file isn't a readable image. Upload a JPEG, PNG or WebP photo.")
    img = ImageOps.exif_transpose(img).convert("RGB")
    w, h = img.size
    if min(w, h) < min_short or max(w, h) < min_long:
        raise PhotoRejected("low_resolution", f"Image is too small ({w}x{h}). Use at least {min_short}x{min_long} pixels.")
    if portrait and h < w:
        raise PhotoRejected("landscape", "Hold the camera upright (portrait) so your whole body fits vertically.")
    img.thumbnail((MAX_LONG, MAX_LONG))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)  # no exif= argument -> metadata dropped
    return CleanImage(buf.getvalue(), np.asarray(img), *img.size)


def check_pose(people: list[list[Landmark]], view: str, w: int, h: int) -> None:
    if not people:
        raise PhotoRejected("no_person", "No person detected. Stand in good light with your whole body in the frame.")
    if len(people) > 1:
        raise PhotoRejected("multiple_people", "More than one person detected. Make sure only you are in the photo.")
    lm = people[0]
    P = lambda i: (lm[i].x * w, lm[i].y * h)  # noqa: E731 - pixel coords so distances aren't skewed by aspect ratio
    seen = lambda i: lm[i].visibility >= VIS and 0.0 <= lm[i].x <= 1.0 and 0.0 <= lm[i].y <= 1.0  # noqa: E731

    if not (seen(NOSE) or (view != "front" and seen(LSH) and seen(RSH))) or lm[NOSE].y < 0.01:
        raise PhotoRejected("head_cut_off", "Your head is cut off. Step back or tilt the camera so there's space above your head.")
    if not (seen(LANK) or seen(RANK)) or max(lm[LANK].y, lm[RANK].y) > 0.99:
        raise PhotoRejected("feet_cut_off", "Your feet are cut off. Step back until your feet are fully in the frame.")
    if not all(seen(i) for i in (LHIP, RHIP)) and not (view == "side" and (seen(LHIP) or seen(RHIP))):
        raise PhotoRejected("body_hidden", "Your hips aren't clearly visible. Wear fitted clothing and keep arms away from your body.")

    top = min(lm[NOSE].y, lm[LSH].y, lm[RSH].y)
    bottom = max(lm[LANK].y, lm[RANK].y)
    if bottom - top < 0.5:
        raise PhotoRejected("too_far", "You're too far from the camera. Move closer so your body fills most of the frame height.")

    mid = lambda a, b: ((P(a)[0] + P(b)[0]) / 2, (P(a)[1] + P(b)[1]) / 2)  # noqa: E731
    sh, hip = mid(LSH, RSH), mid(LHIP, RHIP)
    torso = math.dist(sh, hip) or 1.0
    tilt = math.degrees(math.atan2(abs(sh[0] - hip[0]), abs(hip[1] - sh[1]) or 1e-6))
    if sh[1] >= hip[1] or tilt > 20:
        raise PhotoRejected("not_upright", "Stand straight and upright, facing the camera position for this view.")
    knees = (P(LKN)[1] + P(RKN)[1]) / 2
    ankles = (P(LANK)[1] + P(RANK)[1]) / 2
    if not (hip[1] < knees < ankles):
        raise PhotoRejected("not_standing", "Stand with straight legs — no sitting, kneeling or crouching.")
    if min(P(LWR)[1], P(RWR)[1]) < P(NOSE)[1] and seen(LWR) and seen(RWR):
        raise PhotoRejected("arms_raised", "Lower your arms: hold them slightly away from your sides (A-pose).")

    width_ratio = abs(P(LSH)[0] - P(RSH)[0]) / torso
    if view in ("front", "back") and width_ratio < 0.45:
        raise PhotoRejected("wrong_view", f"For the {view} photo, face {'the camera' if view == 'front' else 'away from the camera'} squarely, not sideways.")
    if view == "side" and width_ratio > 0.35:
        raise PhotoRejected("wrong_view", "For the side photo, turn 90° so your shoulder points at the camera.")
    # MediaPipe "left" = subject's left, which appears on image-right when facing the camera.
    if view == "front" and lm[LSH].x < lm[RSH].x:
        raise PhotoRejected("wrong_view", "This looks like your back. For the front photo, face the camera.")


def validate_photo(data: bytes, view: str, detector: PoseDetector, max_bytes: int) -> CleanImage:
    if view not in VIEWS:
        raise PhotoRejected("bad_view", f"view must be one of {', '.join(VIEWS)}.")
    img = load_image(data, max_bytes)
    check_pose(detector.detect(img.rgb), view, img.width, img.height)
    return img
