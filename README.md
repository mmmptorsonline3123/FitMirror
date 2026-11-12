# FitMirror

Personal virtual try-on: capture your body (front/side/back), build a 3D body model, try garments on.

**Status: all 5 phases done (P5 behind a feature flag), plus the wardrobe:** saved garments and try-ons, compare 2–4 try-ons side by side, and remove a try-on or a garment (removing a garment deletes its try-ons and previews too). P5: 3D garment preview on your avatar, set `FEATURE_3D_GARMENT=true` to enable; labelled "Preview" and approximate (template garments + front texture projection, not a cloth simulation).

Earlier phases: P1: auth, guided capture, server-side photo validation, private storage, delete-all-data. P2: garment from screenshot (auto category + segmentation), 2D try-on, result gallery. P3: 3D body model + measurements (± confidence) + 3D viewer, size advice against a garment size chart. P4: garment from a product URL (image, title and size chart extracted server-side; respects robots.txt; SSRF-protected; falls back to "upload a screenshot").

> **Body model note:** the shipped body model is `silhouette-v1` — a license-free, CPU-only estimate from your photo silhouettes, not SMPL-X. SMPL-X fitting is planned behind the same `BodyModel` interface but needs licensed model files (see License flags).

## Run locally

```bash
cd backend && python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt   # bin/pip on macOS/Linux
mkdir -p models && curl -sfL -o models/pose_landmarker_full.task https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/latest/pose_landmarker_full.task
cp .env.example .env   # fill in values
.venv/Scripts/uvicorn app.main:app --reload
cd ../frontend && npm install && npm run dev      # http://localhost:5173
```

No Atlas/R2 yet? `python e2e_server.py` (in `backend/`) runs the API against in-memory Mongo + local fake S3 + fake pose detector.

## Android app

`frontend/android/` is a Capacitor shell that opens the hosted FitMirror site (same login, camera and features as the web app).

1. Deploy the frontend (see Deploy), then put its URL in `frontend/capacitor.config.json` → `server.url`.
2. Install **Android Studio** (it bundles the JDK and Android SDK).
3. `cd frontend && npm run android:sync && npm run android:open`, then press ▶ Run in Android Studio, or `npm run android:apk` for `android/app/build/outputs/apk/debug/app-debug.apk`.
4. Play Store: create a signing key in Android Studio (Build → Generate Signed App Bundle) and upload the `.aab`.

## Tests

```bash
cd backend && .venv/Scripts/python -m pytest -q
cd frontend && npx playwright test      # starts e2e_server.py + Vite itself
```

## Deploy

- **Backend → Render** via `render.yaml` (CPU only; downloads the MediaPipe pose model at build). Run with `--no-access-log`.
- **Frontend → Vercel**, root `frontend/`. `vercel.json` rewrites `/api/*` to the Render URL — edit the destination to your service URL. Same-origin API means the session cookie is first-party (`SameSite=Lax`, `HttpOnly`, `Secure`).
- **R2**: private bucket, no public/r2.dev access. Add a CORS rule allowing `GET` from your Vercel origin (thumbnails load via signed URLs).

## Privacy

| Data | Where it lives | Who else sees it |
|---|---|---|
| Email, password (argon2id hash), height, weight | MongoDB Atlas (encrypted at rest by Atlas) | nobody |
| Body photos (re-encoded, EXIF/GPS stripped) | R2 private bucket (AES-256 at rest), read only via 5-min signed URLs | nobody in P1. P2+: try-on provider — see below |
| Pose landmarks | computed in-process on Render, never stored | nobody |
| Garment screenshot + cut-out | R2 private bucket | Replicate (see below) |
| Try-on results | R2 private bucket | generated at Replicate |
| Body mesh (GLB) + measurements | R2 private bucket + MongoDB | nobody: computed on Render (CPU); mesh is streamed only through the authenticated API |
| Garment size charts you enter | MongoDB | nobody |
| 3D garment previews (GLB, flag on) | R2 private bucket | nobody: built on Render (CPU), served only via the authenticated API |
| Product links you paste | MongoDB (final URL after redirects) | the shop's own server (we fetch the page + image as `FitMirrorBot/1.0`, no cookies, no user data sent) |

**URL fetching** obeys robots.txt (RFC 9309: missing robots.txt → allowed; server error → treated as disallowed) and only connects to public IP addresses on ports 80/443. The address check runs at socket-connect time after DNS resolution, so redirects and DNS rebinding to internal addresses are refused. Pages are capped at 3 MB, images at 12 MB, with 10 s per request and a 25 s total budget. Pasted URLs are never logged (host only); httpx/httpcore request logging is silenced for that reason.

**Replicate** (the only inference provider so far) receives:
- *Garment classification* (LLaVA-13B): the garment screenshot. Skipped if you pick the category yourself.
- *Garment segmentation* (Grounded-SAM): the garment screenshot.
- *Try-on* (IDM-VTON): your **front** photo + the garment cut-out. Side/back photos are never sent (the model doesn't support them).

Every image goes as a data URL of ≤256 KB (downscaled to ≤1024 px). Replicate documents that it does not store data-URL inputs, and auto-deletes API prediction inputs, outputs and logs after 1 hour. We copy each output into R2 on the first poll after it finishes. Replicate is never given signed URLs. There's no stricter zero-retention switch for community models; for a contractual guarantee, run the models on your own Modal deployment behind the same `InferenceProvider` interface. Photos are never logged and never placed in URLs/query params; boto/urllib3 debug logs are silenced because they contain object keys and signed URLs.

**Delete all my data** (`DELETE /me`) purges every object under `users/<id>/` in the bucket, every per-user collection listed in `USER_COLLECTIONS`, then the account. Covered by `tests/test_delete_me.py` and the Playwright flow.

## ⚠️ License flags

- **SMPL-X is licensed for non-commercial research only.** Commercial use needs a separate license from Meshcapade / Max Planck. It is **not** bundled or used yet: the body model sits behind the `BodyModel` interface (`app/body.py`), and the default `silhouette-v1` model uses no third-party body model at all. To add SMPL-X you must register at smpl-x.is.tue.mpg.de, accept its terms and supply the model files yourself.
- MediaPipe Pose Landmarker: Apache 2.0.
- **IDM-VTON (`cuuupid/idm-vton`, the default try-on model) is non-commercial only** (its Replicate page says so). Swap `REPLICATE_TRYON_MODEL` / the provider before any commercial launch.
- Grounded-SAM and LLaVA: check each model's license on its Replicate page before commercial use.
