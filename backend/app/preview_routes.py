"""Feature flags + flagged 3D garment preview (P5). With FEATURE_3D_GARMENT off, the preview endpoints 404."""
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from .auth import current_user
from .body_routes import latest_body
from .config import get_settings
from .db import get_db
from .garment3d import build_preview
from .storage import get_storage, user_prefix
from .tryon import advance_garment, own

router = APIRouter()


@router.get("/features")
def features():
    return {"garment_3d": get_settings().feature_3d_garment}


def require_flag():
    if not get_settings().feature_3d_garment:
        raise HTTPException(404, "Not found")


class PreviewRequest(BaseModel):
    garment_id: str


@router.post("/tryon3d", status_code=201, dependencies=[Depends(require_flag)])
def create_preview(body: PreviewRequest, user=Depends(current_user), db=Depends(get_db)):
    uid = str(user["_id"])
    g = advance_garment(own(db, "garments", body.garment_id, user), db)
    if g["status"] != "ready" or not g.get("category"):
        raise HTTPException(409, "This garment isn't ready yet.")
    b = latest_body(db, uid, done_only=True)
    if not b:
        raise HTTPException(409, "Build your 3D body first.")
    if not b.get("profile"):
        raise HTTPException(409, "Rebuild your 3D body to use the garment preview.")
    store = get_storage()
    try:
        glb = build_preview(b["profile"], g["category"], store.get(g.get("image_key") or g["original_key"]))
    except ValueError:
        raise HTTPException(422, "Couldn't fit this garment to your body model.")
    pid = uuid.uuid4().hex
    key = f"{user_prefix(uid)}previews3d/{pid}.glb"
    store.put(key, glb, "model/gltf-binary")
    doc = {"_id": pid, "user_id": uid, "garment_id": g["_id"], "body_id": b["_id"], "category": g["category"],
           "mesh_key": key, "created_at": datetime.now(timezone.utc)}
    db.previews3d.insert_one(doc)
    return {"id": pid, "garment_id": g["_id"], "body_id": b["_id"], "category": g["category"], "label": "preview",
            "mesh_url": f"/tryon3d/{pid}/mesh"}


@router.get("/tryon3d/{pid}/mesh", dependencies=[Depends(require_flag)])
def preview_mesh(pid: str, user=Depends(current_user), db=Depends(get_db)):
    doc = own(db, "previews3d", pid, user)
    return Response(get_storage().get(doc["mesh_key"]), media_type="model/gltf-binary", headers={"Cache-Control": "private, no-store"})
