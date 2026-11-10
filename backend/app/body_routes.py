"""Body fit (async via BackgroundTasks; CPU model is ~1 s, a GPU SMPL-X fitter would plug in behind BodyModel)."""
import io
import logging
import uuid
from datetime import datetime, timedelta, timezone
import numpy as np
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from PIL import Image
from .auth import current_user
from .body import BodyFitError, ViewInput, get_body_model
from .db import get_db
from .pose import get_detector
from .sizing import SizeChart, advise
from .storage import get_storage, user_prefix
from .tryon import own

log = logging.getLogger("fitmirror")
router = APIRouter()
STALE = timedelta(minutes=10)


def now():
    return datetime.now(timezone.utc)


def run_fit(body_id: str, db):
    b = db.bodies.find_one({"_id": body_id})
    if not b:
        return  # user deleted their data meanwhile
    store = get_storage()
    try:
        views = {}
        for view, pid in b["photo_ids"].items():
            photo = db.photos.find_one({"_id": pid})
            if not photo:
                raise BodyFitError(f"Your {view} photo was replaced while fitting. Please run it again.")
            rgb = np.asarray(Image.open(io.BytesIO(store.get(photo["key"]))).convert("RGB"))
            seg = get_detector().segment(rgb)
            if not seg:
                raise BodyFitError(f"We couldn't find you in the {view} photo. Retake it.")
            views[view] = ViewInput(*seg)
        fit = get_body_model().fit(views, b["height_cm"])
        key = f"{user_prefix(b['user_id'])}bodies/{body_id}/body.glb"
        store.put(key, fit.glb, "model/gltf-binary")
        update = {"status": "done", "measurements": fit.measurements, "notes": fit.notes, "mesh_key": key, "profile": fit.profile}
    except BodyFitError as e:
        update = {"status": "failed", "error": e.reason}
    except Exception as e:  # noqa: BLE001 - never leave a job stuck "running"
        log.exception("body fit crashed: %s", type(e).__name__)
        update = {"status": "failed", "error": "Something went wrong building your body model. Please try again."}
    res = db.bodies.update_one({"_id": body_id}, {"$set": {**update, "finished_at": now()}})
    if not res.matched_count and update.get("mesh_key"):
        store.delete(update["mesh_key"])  # user deleted their data mid-fit: don't leave an orphaned mesh


def body_out(b: dict) -> dict:
    if b["status"] == "running" and now() - b["created_at"].replace(tzinfo=timezone.utc) > STALE:
        b = {**b, "status": "failed", "error": "This fit was interrupted. Please run it again."}
    return {"id": b["_id"], "status": b["status"], "model": b["model"], "height_cm": b["height_cm"],
            "measurements": b.get("measurements"), "notes": b.get("notes", []), "error": b.get("error"),
            "views_used": sorted(b["photo_ids"]), "mesh_url": f"/body/{b['_id']}/mesh" if b.get("mesh_key") else None,
            "created_at": b["created_at"]}


@router.post("/body/fit", status_code=202)
def fit_body(tasks: BackgroundTasks, user=Depends(current_user), db=Depends(get_db)):
    uid = str(user["_id"])
    if not user.get("height_cm"):
        raise HTTPException(409, "Enter your height first.")
    photos = {p["view"]: p["_id"] for p in db.photos.find({"user_id": uid})}
    missing = [v for v in ("front", "side") if v not in photos]
    if missing:
        raise HTTPException(409, f"Capture your {' and '.join(missing)} photo first.")
    b = {"_id": uuid.uuid4().hex, "user_id": uid, "status": "running", "model": get_body_model().name,
         "height_cm": user["height_cm"], "photo_ids": photos, "created_at": now()}
    db.bodies.insert_one(b)
    tasks.add_task(run_fit, b["_id"], db)
    return body_out(b)


def latest_body(db, uid: str, done_only=False) -> dict | None:
    q = {"user_id": uid, **({"status": "done"} if done_only else {})}
    return next(iter(db.bodies.find(q).sort("created_at", -1).limit(1)), None)


@router.get("/body/latest")
def get_latest(user=Depends(current_user), db=Depends(get_db)):
    b = latest_body(db, str(user["_id"]))
    if not b:
        raise HTTPException(404, "No body model yet.")
    return body_out(b)


@router.get("/body/{bid}")
def get_body(bid: str, user=Depends(current_user), db=Depends(get_db)):
    return body_out(own(db, "bodies", bid, user))


@router.get("/body/{bid}/mesh")
def get_mesh(bid: str, user=Depends(current_user), db=Depends(get_db)):
    """Streamed through the API (same-origin, cookie-auth) so the 3D loader needs no bucket CORS or URL tokens."""
    b = own(db, "bodies", bid, user)
    if not b.get("mesh_key"):
        raise HTTPException(404, "Mesh not ready.")
    return Response(get_storage().get(b["mesh_key"]), media_type="model/gltf-binary", headers={"Cache-Control": "private, no-store"})


@router.get("/garments/{gid}/size-advice")
def size_advice(gid: str, user=Depends(current_user), db=Depends(get_db)):
    g = own(db, "garments", gid, user)
    if not g.get("category"):
        raise HTTPException(409, "Pick a category for this garment first.")
    b = latest_body(db, str(user["_id"]), done_only=True)
    if not b:
        raise HTTPException(409, "Build your body model first to get size advice.")
    chart = SizeChart.model_validate(g["size_chart"]) if g.get("size_chart") else None
    return {**advise(b["measurements"], chart, g["category"]), "body_id": b["_id"], "measurements": b["measurements"]}
