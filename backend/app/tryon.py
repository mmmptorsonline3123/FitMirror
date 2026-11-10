"""Garments (from screenshot) + 2D try-on. Long jobs are async: POST returns an id; each GET advances the job by polling
the provider and saving finished outputs. No worker process, so jobs survive Render restarts."""
import io
import logging
import uuid
from datetime import datetime, timezone
from typing import Literal
import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from PIL import Image, ImageOps
from urllib.parse import urlsplit
from pydantic import BaseModel, Field
from .auth import current_user
from .config import get_settings
from .db import get_db
from .inference import CATEGORIES, InferenceProvider, ProviderError, get_provider
from .sizing import SizeChart
from .storage import get_storage, user_prefix
from .fetch import Fetcher, FetchError, make_client
from .product_page import parse_product_page
from .validation import CleanImage, PhotoRejected, load_image

log = logging.getLogger("fitmirror")
router = APIRouter()
Category = Literal["dress", "top", "bottom", "outerwear"]
PROVIDER_DOWN = "The try-on service is unavailable right now. Please try again in a few minutes."


def now():
    return datetime.now(timezone.utc)


def _submit(fn, *args) -> dict:
    try:
        return {"id": fn(*args), "status": "pending"}
    except (ProviderError, httpx.HTTPError) as e:
        log.warning("provider submit failed: %s %s", type(e).__name__, str(e)[:200])
        return {"id": None, "status": "failed", "error": PROVIDER_DOWN}


def _poll(p: InferenceProvider, kind, job: dict):
    try:
        return p.poll(kind, job["id"])
    except (ProviderError, httpx.HTTPError) as e:
        log.warning("provider poll failed: %s", type(e).__name__)
        return None  # transient: try again on the next GET


# ---------- garments ----------

def cutout(original: bytes, mask: bytes) -> bytes:
    """Keep only masked pixels on white, crop to the garment, pad to 3:4 (what VTON models expect)."""
    img = Image.open(io.BytesIO(original)).convert("RGB")
    m = Image.open(io.BytesIO(mask)).convert("L").resize(img.size).point(lambda v: 255 if v > 127 else 0)
    bbox = m.getbbox()
    if not bbox:
        raise ValueError("empty mask")
    out = Image.composite(img, Image.new("RGB", img.size, "white"), m).crop(bbox)
    w, h = out.size
    W, H = (w, w * 4 // 3) if h < w * 4 // 3 else (h * 3 // 4, h)
    canvas = Image.new("RGB", (int(W * 1.1), int(H * 1.1)), "white")
    canvas.paste(out, ((canvas.width - w) // 2, (canvas.height - h) // 2))
    buf = io.BytesIO()
    canvas.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def advance_garment(g: dict, db) -> dict:
    if g["status"] != "processing":
        return g
    p, store, jobs = get_provider(), get_storage(), g["jobs"]
    cj = jobs.get("classify")
    if cj and cj["status"] == "pending":
        st = _poll(p, "classify", cj)
        if not st or st.status == "pending":
            return g
        cj["status"] = st.status
        if st.status == "succeeded" and st.result in CATEGORIES:
            g["category"], g["category_source"] = st.result, "auto"
        else:
            g["status"], g["note"] = "needs_category", "We couldn't tell what kind of garment this is. Please pick a category."
    if g["status"] == "processing" and g.get("category") and not jobs.get("segment"):
        jobs["segment"] = _submit(p.segment_garment, store.get(g["original_key"]), g["category"])
    sj = jobs.get("segment")
    if sj and sj["status"] == "pending":
        st = _poll(p, "segment", sj)
        if not st or st.status == "pending":
            db.garments.replace_one({"_id": g["_id"]}, g)
            return g
        sj["status"] = st.status
        if st.status == "succeeded":
            try:
                key = f"{user_prefix(g['user_id'])}garments/{g['_id']}/cutout.jpg"
                store.put(key, cutout(store.get(g["original_key"]), st.result), "image/jpeg")
                g["image_key"] = key
            except (ValueError, OSError):
                sj["status"] = "failed"
    if sj and sj["status"] == "failed":
        g["note"] = "We couldn't isolate the garment, so the full image will be used. A product-only image works best."
    if sj and sj["status"] != "pending":
        g["status"] = "ready"
    db.garments.replace_one({"_id": g["_id"]}, g)
    return g


def garment_out(g: dict) -> dict:
    s = get_storage()
    return {"id": g["_id"], "status": g["status"], "category": g.get("category"), "category_source": g.get("category_source"),
            "note": g.get("note"), "source": g.get("source"), "source_url": g.get("source_url"), "title": g.get("title"),
            "size_chart": g.get("size_chart"),
            "image_url": s.signed_url(g.get("image_key") or g["original_key"]), "original_url": s.signed_url(g["original_key"]),
            "created_at": g["created_at"]}


def own(db, coll: str, oid: str, user) -> dict:
    doc = db[coll].find_one({"_id": oid, "user_id": str(user["_id"])})
    if not doc:
        raise HTTPException(404, "Not found")
    return doc


def load_garment_image(data: bytes) -> CleanImage:
    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    return load_image(data, max_bytes, min_short=200, min_long=300, portrait=False)


def create_garment(db, uid: str, jpeg: bytes, category: str | None, source: str, **extra) -> dict:
    gid = uuid.uuid4().hex
    key = f"{user_prefix(uid)}garments/{gid}/original.jpg"
    get_storage().put(key, jpeg, "image/jpeg")
    jobs = {} if category else {"classify": _submit(get_provider().classify_garment, jpeg)}
    g = {"_id": gid, "user_id": uid, "status": "processing", "category": category, "category_source": "user" if category else None,
         "source": source, "source_url": None, "title": None, "size_chart": None, "original_key": key, "image_key": None,
         "jobs": jobs, "note": None, "created_at": now(), **extra}
    if jobs.get("classify", {}).get("status") == "failed":
        g["status"], g["note"] = "needs_category", "Please pick a category."
    db.garments.insert_one(g)
    return advance_garment(g, db)


@router.post("/garments/from-image", status_code=202)
async def garment_from_image(file: UploadFile = File(...), category: Category | None = Form(None),
                             user=Depends(current_user), db=Depends(get_db)):
    try:
        img = load_garment_image(await file.read(get_settings().max_upload_mb * 1024 * 1024 + 1))
    except PhotoRejected as e:
        return JSONResponse(status_code=422, content={"code": e.code, "reason": e.reason})
    return garment_out(create_garment(db, str(user["_id"]), img.jpeg, category, "upload"))


class UrlRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    category: Category | None = None


SCREENSHOT_HINT = " Please upload a screenshot of the product instead."


@router.post("/garments/from-url", status_code=202)
def garment_from_url(body: UrlRequest, user=Depends(current_user), db=Depends(get_db)):
    """Fetch a product page server-side (SSRF-guarded, robots.txt respected), take its product image, title and size chart."""
    allow = {h.strip() for h in get_settings().url_fetch_allow.split(",") if h.strip()}
    host = urlsplit(body.url).hostname or "?"  # log the host only: full URLs can carry tracking/session tokens
    try:
        with make_client(allow) as client:
            f = Fetcher(client, allow)
            final_url, html, _ = f.get(body.url, 3_000_000, ("text/html", "application/xhtml"))
            page = parse_product_page(html.decode("utf-8", "replace"), final_url)
            if not page.images:
                raise FetchError("no_image", "We couldn't find a product image on that page.")
            img = None
            for u in page.images[:5]:
                try:
                    img = load_garment_image(f.get(u, get_settings().max_upload_mb * 1024 * 1024, ("image/",))[1])
                    break
                except (FetchError, PhotoRejected):
                    continue
            if img is None:
                raise FetchError("no_image", "We couldn't download a usable product image from that page.")
    except FetchError as e:
        log.info("url ingest failed: host=%s code=%s", host, e.code)
        return JSONResponse(status_code=422, content={"code": e.code, "reason": e.reason + SCREENSHOT_HINT, "fallback": "screenshot"})
    g = create_garment(db, str(user["_id"]), img.jpeg, body.category, "url", source_url=final_url, title=page.title,
                       size_chart=page.size_chart.model_dump() if page.size_chart else None, product_images=page.images)
    return garment_out(g)


@router.get("/garments")
def list_garments(user=Depends(current_user), db=Depends(get_db)):
    return [garment_out(advance_garment(g, db)) for g in db.garments.find({"user_id": str(user["_id"])}).sort("created_at", -1)]


@router.get("/garments/{gid}")
def get_garment(gid: str, user=Depends(current_user), db=Depends(get_db)):
    return garment_out(advance_garment(own(db, "garments", gid, user), db))


class GarmentPatch(BaseModel):
    category: Category | None = None
    size_chart: SizeChart | None = None  # send null to clear


@router.patch("/garments/{gid}")
def update_garment(gid: str, body: GarmentPatch, user=Depends(current_user), db=Depends(get_db)):
    g = own(db, "garments", gid, user)
    if "size_chart" in body.model_fields_set:
        g["size_chart"] = body.size_chart.model_dump() if body.size_chart else None
        db.garments.update_one({"_id": g["_id"]}, {"$set": {"size_chart": g["size_chart"]}})
    if body.category:
        if g["status"] == "processing":
            raise HTTPException(409, "Still processing this garment.")
        if body.category != g.get("category") or g["status"] == "needs_category":
            g.update(category=body.category, category_source="user", status="processing", image_key=None, note=None)
            g["jobs"].pop("segment", None)  # re-segment for the new category
    return garment_out(advance_garment(g, db))


# ---------- try-on ----------

def advance_tryon(t: dict, db) -> dict:
    if t["status"] != "running":
        return t
    p, store = get_provider(), get_storage()
    for view, v in t["views"].items():
        if v["status"] != "pending":
            continue
        st = _poll(p, "tryon", v)
        if not st or st.status == "pending":
            continue
        if st.status == "succeeded":
            key = f"{user_prefix(t['user_id'])}tryons/{t['_id']}/{view}.jpg"
            store.put(key, st.result, "image/jpeg")
            v.update(status="succeeded", key=key)
        else:
            v.update(status="failed", error="The try-on model couldn't process this photo. " + (st.error or ""))
    states = [v["status"] for v in t["views"].values()]
    if "pending" not in states:
        t["status"] = "done" if "succeeded" in states else "failed"
    db.tryons.replace_one({"_id": t["_id"]}, t)
    return t


def tryon_out(t: dict, db) -> dict:
    s = get_storage()
    photos = {p["view"]: p for p in db.photos.find({"user_id": t["user_id"]})}
    views = {}
    for view, v in t["views"].items():
        photo = photos.get(view)
        views[view] = {"status": v["status"], "error": v.get("error"),
                       "result_url": s.signed_url(v["key"]) if v.get("key") else None,
                       "original_url": s.signed_url(photo["key"]) if photo and photo["_id"] == v["photo_id"] else None}
    return {"id": t["_id"], "garment_id": t["garment_id"], "status": t["status"], "provider": t["provider"],
            "views": views, "unsupported_views": t["unsupported_views"], "created_at": t["created_at"]}


class TryOnRequest(BaseModel):
    garment_id: str


@router.post("/tryon", status_code=202)
def start_tryon(body: TryOnRequest, user=Depends(current_user), db=Depends(get_db)):
    uid = str(user["_id"])
    g = advance_garment(own(db, "garments", body.garment_id, user), db)
    if g["status"] != "ready":
        raise HTTPException(409, "This garment isn't ready yet." if g["status"] == "processing" else "Pick a category for this garment first.")
    photos = {p["view"]: p for p in db.photos.find({"user_id": uid})}
    p, store = get_provider(), get_storage()
    if not any(v in photos for v in p.tryon_views):
        raise HTTPException(409, f"Capture your {' / '.join(p.tryon_views)} photo first.")
    garment_jpeg = store.get(g.get("image_key") or g["original_key"])
    views = {}
    for view in p.tryon_views:
        if view in photos:
            job = _submit(p.tryon, store.get(photos[view]["key"]), garment_jpeg, g["category"])
            views[view] = {**job, "photo_id": photos[view]["_id"], "key": None}
    t = {"_id": uuid.uuid4().hex, "user_id": uid, "garment_id": g["_id"], "provider": p.name, "status": "running",
         "views": views, "unsupported_views": [v for v in photos if v not in p.tryon_views], "created_at": now()}
    db.tryons.insert_one(t)
    return tryon_out(advance_tryon(t, db), db)


@router.get("/tryon")
def list_tryons(user=Depends(current_user), db=Depends(get_db)):
    return [tryon_out(advance_tryon(t, db), db) for t in db.tryons.find({"user_id": str(user["_id"])}).sort("created_at", -1)]


@router.get("/tryon/{tid}")
def get_tryon(tid: str, user=Depends(current_user), db=Depends(get_db)):
    return tryon_out(advance_tryon(own(db, "tryons", tid, user), db), db)


# ---------- wardrobe ----------

@router.get("/wardrobe")
def wardrobe(user=Depends(current_user), db=Depends(get_db)):
    """Everything the user has saved: garments (newest first) and their try-ons (newest first), jobs advanced."""
    uid = str(user["_id"])
    return {"garments": [garment_out(advance_garment(g, db)) for g in db.garments.find({"user_id": uid}).sort("created_at", -1)],
            "tryons": [tryon_out(advance_tryon(t, db), db) for t in db.tryons.find({"user_id": uid}).sort("created_at", -1)]}


def _purge_tryon(t: dict, db) -> None:
    get_storage().delete_prefix(f"{user_prefix(t['user_id'])}tryons/{t['_id']}/")
    db.tryons.delete_one({"_id": t["_id"]})


@router.delete("/tryon/{tid}", status_code=204)
def delete_tryon(tid: str, user=Depends(current_user), db=Depends(get_db)):
    _purge_tryon(own(db, "tryons", tid, user), db)


@router.delete("/garments/{gid}", status_code=204)
def delete_garment(gid: str, user=Depends(current_user), db=Depends(get_db)):
    """Removes the garment, its images, every try-on and 3D preview made from it. Media first, like DELETE /me."""
    g = own(db, "garments", gid, user)
    store = get_storage()
    for t in db.tryons.find({"user_id": g["user_id"], "garment_id": gid}):
        _purge_tryon(t, db)
    for p in db.previews3d.find({"user_id": g["user_id"], "garment_id": gid}):
        store.delete(p["mesh_key"])
        db.previews3d.delete_one({"_id": p["_id"]})
    store.delete_prefix(f"{user_prefix(g['user_id'])}garments/{gid}/")
    db.garments.delete_one({"_id": gid})
