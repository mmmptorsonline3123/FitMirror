import logging
from contextlib import asynccontextmanager
import uuid
from datetime import datetime, timezone
from fastapi import Depends, FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field
from pymongo.errors import DuplicateKeyError
from .auth import clear_session, current_user, hash_password, set_session, verify_password
from .config import get_settings
from .db import USER_COLLECTIONS, get_db
from . import pose
from .pose import get_detector
from .storage import get_storage, user_prefix
from .body_routes import router as body_router
from .preview_routes import router as preview_router
from .tryon import router as tryon_router
from .validation import PhotoRejected, validate_photo

log = logging.getLogger("fitmirror")  # never log request bodies, images or signed URLs
for noisy in ("botocore", "boto3", "s3transfer", "urllib3", "httpx", "httpcore"):  # their logs contain object keys, presigned URLs, full user-supplied URLs
    logging.getLogger(noisy).setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(_app):
    yield
    if pose.get_detector.cache_info().currsize and hasattr(det := pose.get_detector(), "close"):
        det.close()  # unclosed MediaPipe landmarker can hang process exit -> slow Render restarts


app = FastAPI(title="FitMirror API", lifespan=lifespan)
app.include_router(tryon_router)
app.include_router(body_router)
app.include_router(preview_router)


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)


class Profile(BaseModel):
    height_cm: float | None = Field(default=None, ge=100, le=250)
    weight_kg: float | None = Field(default=None, ge=25, le=350)


def public_user(u: dict) -> dict:
    return {"id": str(u["_id"]), "email": u["email"], "height_cm": u.get("height_cm"), "weight_kg": u.get("weight_kg")}


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/auth/register", status_code=201)
def register(body: Credentials, response: Response, db=Depends(get_db)):
    try:
        res = db.users.insert_one({"email": body.email.lower(), "password_hash": hash_password(body.password),
                                   "created_at": datetime.now(timezone.utc)})
    except DuplicateKeyError:
        raise HTTPException(409, "An account with that email already exists.")
    set_session(response, str(res.inserted_id))
    return public_user(db.users.find_one({"_id": res.inserted_id}))


@app.post("/auth/login")
def login(body: Credentials, response: Response, db=Depends(get_db)):
    user = db.users.find_one({"email": body.email.lower()})
    if not user or not verify_password(user["password_hash"], body.password):
        raise HTTPException(401, "Wrong email or password.")
    set_session(response, str(user["_id"]))
    return public_user(user)


@app.post("/auth/logout", status_code=204)
def logout(response: Response):
    clear_session(response)


@app.get("/me")
def me(user=Depends(current_user)):
    return public_user(user)


@app.patch("/me")
def update_me(body: Profile, user=Depends(current_user), db=Depends(get_db)):
    changes = body.model_dump(exclude_unset=True)
    if changes:
        db.users.update_one({"_id": user["_id"]}, {"$set": changes})
    return public_user(db.users.find_one({"_id": user["_id"]}))


@app.delete("/me", status_code=204)
def delete_me(response: Response, user=Depends(current_user), db=Depends(get_db)):
    uid = str(user["_id"])
    # Media first: if the bucket purge fails we abort with DB intact so the user can retry (no orphaned, unreachable photos).
    get_storage().delete_prefix(user_prefix(uid))
    for name in USER_COLLECTIONS:
        db[name].delete_many({"user_id": uid})
    db.users.delete_one({"_id": user["_id"]})
    log.info("user data purged")
    clear_session(response)


def photo_out(p: dict) -> dict:
    return {"id": p["_id"], "view": p["view"], "width": p["width"], "height": p["height"],
            "url": get_storage().signed_url(p["key"]), "created_at": p["created_at"]}


@app.post("/capture/photos", status_code=201)
async def upload_photo(view: str = Form(...), file: UploadFile = File(...), user=Depends(current_user), db=Depends(get_db)):
    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    data = await file.read(max_bytes + 1)
    try:
        img = validate_photo(data, view, get_detector(), max_bytes)
    except PhotoRejected as e:
        log.info("photo rejected: view=%s code=%s", view, e.code)
        return JSONResponse(status_code=422, content={"code": e.code, "reason": e.reason})
    uid = str(user["_id"])
    pid = uuid.uuid4().hex
    key = f"{user_prefix(uid)}photos/{view}-{pid}.jpg"
    storage = get_storage()
    storage.put(key, img.jpeg, "image/jpeg")
    old = db.photos.find_one_and_delete({"user_id": uid, "view": view})
    doc = {"_id": pid, "user_id": uid, "view": view, "key": key, "width": img.width, "height": img.height,
           "created_at": datetime.now(timezone.utc)}
    db.photos.insert_one(doc)
    if old:
        storage.delete(old["key"])  # one photo per view: retake replaces
    return photo_out(doc)


@app.get("/capture/photos")
def list_photos(user=Depends(current_user), db=Depends(get_db)):
    return [photo_out(p) for p in db.photos.find({"user_id": str(user["_id"])})]
