# FitMirror — working notes for Claude

Personal virtual try-on app. Spec build order: P1 auth/capture/validation/storage/delete-all → P2 2D try-on → P3 body fit + 3D viewer + size advice → P4 URL ingestion → P5 3D garment preview (flagged). Finish + test each phase before the next.

## Conventions
- Backend: FastAPI, **sync** endpoints + sync `pymongo` (FastAPI runs them in a threadpool). Settings via `app/config.py` (pydantic-settings, env vars). Singletons via `@lru_cache` getters (`get_db`, `get_storage`, `get_detector`) — tests `cache_clear()` them.
- Frontend: React 19 + Vite, plain CSS (`src/index.css` tokens, dark mode). Calls only same-origin `/api/*` via `src/api.js`.
- Swappable ML behind small Protocols (`PoseDetector` now; `InferenceProvider`, `BodyModel` to come).
- Run tests after every change: `cd backend && .venv/Scripts/python -m pytest -q`; `cd frontend && npx playwright test`.
- Diagnose root cause before fixing. No hardcoded secrets — add every new env var to `backend/.env.example`.
- New per-user collection? Add it to `USER_COLLECTIONS` in `app/db.py` (DELETE /me depends on it) and extend `tests/test_delete_me.py`.
- New per-user media? Store under `users/<user_id>/…` so `delete_prefix` purges it.

## Decisions (P1)
- **Auth**: email + password (argon2id), HS256 JWT in `fm_session` cookie: HttpOnly, SameSite=Lax, Secure (configurable for localhost). No cross-site cookies: Vercel rewrites `/api/*` → Render; Vite proxies `/api` in dev. CORS therefore not configured at all.
- **Storage**: R2 via boto3. Private bucket, signed GET URLs (300 s). R2 encrypts at rest by default; `ServerSideEncryption=AES256` header is sent only for real AWS S3 / moto (R2 rejects it). No app-level encryption (would break signed URLs to browser/providers).
- **Photos**: re-encoded to JPEG ≤2048px with EXIF (GPS) stripped and orientation applied before storage. One photo per view; retake replaces + deletes the old object. Rejected photos are never stored.
- **Validation** (`app/validation.py`): MediaPipe Pose Landmarker (full, CPU) → rules: decodable, ≥480×800, portrait, exactly one person, head/feet/hips visible, body ≥50% of frame height, upright torso (≤20° tilt), standing (hip<knee<ankle), arms not raised, view check via shoulder-width/torso ratio (front/back ≥0.45, side ≤0.35) and left/right shoulder order for front. Each rejection = `{code, reason}` with HTTP 422.
- **Height/weight**: `PATCH /me` (height 100–250 cm required by UI before capture; weight optional).
- **Logging**: never log bodies/images/signed URLs; botocore/boto3/s3transfer/urllib3 forced to WARNING (their DEBUG logs leak keys + presigned URLs). Render runs uvicorn with `--no-access-log`.
- **Test doubles**: `MONGO_URI=mongomock://` → mongomock; `POSE_DETECTOR=fake` → `FakeDetector` (blank image = nobody, w/h<0.55 = side pose, else front). `fake` is refused when `APP_ENV=production`. E2E backend = `backend/e2e_server.py` (in-process moto S3 on :5055).

## Endpoints (P1)
| Method | Path | Notes |
|---|---|---|
| GET | /health | |
| POST | /auth/register | `{email, password≥10}` → 201 + cookie; 409 duplicate |
| POST | /auth/login | 401 generic "Wrong email or password." |
| POST | /auth/logout | 204, clears cookie |
| GET | /me | `{id,email,height_cm,weight_kg}` |
| PATCH | /me | `{height_cm?, weight_kg?}` |
| DELETE | /me | purge bucket prefix → per-user collections → user; 204 + clears cookie |
| POST | /capture/photos | multipart `view`(front/side/back) + `file` → 201 photo w/ signed `url`, or 422 `{code,reason}` |
| GET | /capture/photos | list with fresh signed URLs |

## Env vars
See `backend/.env.example`: APP_ENV, JWT_SECRET, JWT_TTL_MINUTES, COOKIE_SECURE, MONGO_URI, MONGO_DB, S3_ENDPOINT_URL, S3_BUCKET, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY, S3_REGION, SIGNED_URL_TTL_SECONDS, POSE_MODEL_PATH, POSE_DETECTOR, MAX_UPLOAD_MB. Frontend: none secret (`VITE_API_PROXY` dev only).

## Test status (end of P1, 2026-11-09)
- pytest: 17 passed (auth, profile, capture + all rejection codes, EXIF strip, retake, isolation between users, no secrets in logs, delete-all, pose rules, real MediaPipe model smoke test).
- Playwright: 1 passed — register → height → rejected blank photo → front/side/back accepted → reload persists → delete all → signed URL 404s → login fails.

## Decisions (P2)
- **InferenceProvider** (`app/inference.py`): `classify_garment`, `segment_garment`, `tryon` each return a provider job id; `poll(kind, id) -> JobState(status, result, error)` where result is already decoded (category str / mask bytes / image bytes). `tryon_views` declares which capture views the model supports. Impls: `ReplicateProvider`, `FakeProvider` (tests/E2E; refused in production). Modal impl not written yet.
- **Async jobs without a worker**: POST submits predictions and stores their ids on the doc; every GET (`/garments*`, `/tryon*`) advances pending jobs by polling the provider and saving finished outputs to R2 under deterministic keys (safe to re-run). Survives Render restarts. Frontend polls every 2.5 s while anything is pending.
- **Replicate**: community models need a version id → resolved via `GET /v1/models/{owner}/{name}` → `latest_version.id`, cached per process. Input names are checked against `latest_version.openapi_schema...Input.properties` before submitting; missing required input → `ProviderError` naming the field; optional inputs (`category`, `crop`, `temperature`, `max_tokens`) are only sent if the schema has them. Output downloads use a separate client without the auth header.
- **Privacy**: images sent as data URLs ≤256 KB (`fit_jpeg`), which Replicate doesn't store; Replicate auto-deletes API predictions after 1 h. Output not saved within 1 h → view fails with "expired, run again".
- **Garment pipeline**: upload → validate (≥200×300, any orientation, EXIF stripped) → `original.jpg`. If no category given: classify (LLaVA, one-word answer parsed against the 4 categories) → unparseable/failed → `needs_category`, user picks. Then segment with a category-specific Grounded-SAM prompt (negative: face/hair/skin/hands/background). Output index 2 = mask → `cutout()` keeps masked pixels on white, crops, pads to 3:4 → `cutout.jpg`. Segmentation fails → `ready` with the full image + note. Changing category re-segments.
- **Try-on**: IDM-VTON, front view only (`tryon_views=("front",)`); side/back photos are listed in `unsupported_views` and shown honestly in the UI. Category map: dress→dresses, top/outerwear→upper_body, bottom→lower_body. Each view stores `photo_id`; if that photo is later retaken, `original_url` is null ("Photo retaken").
- Size advice is not part of P2 (needs P3 measurements); `size_chart` field exists on garments (null for uploads).

## Endpoints (P2)
| Method | Path | Notes |
|---|---|---|
| POST | /garments/from-image | multipart `file` + optional `category` → 202 garment (`status`: processing / ready / needs_category) or 422 `{code,reason}` |
| GET | /garments, /garments/{id} | advances jobs; signed `image_url` (cut-out or original) + `original_url` |
| PATCH | /garments/{id} | `{category}` → re-segments; 409 while processing |
| POST | /tryon | `{garment_id}` → 202 `{id,status:running,views,unsupported_views}`; 409 if garment not ready or no front photo; 404 if not yours |
| GET | /tryon, /tryon/{id} | advances jobs; per view `{status, result_url, original_url, error}` |

## Test status (end of P2, 2026-11-09)
- pytest: 37 passed — P1 suite + garments (auto classify, user category, classifier failure → needs_category → PATCH, segmentation fallback, cut-out 3:4 white, bad files), try-on (success, provider failure, needs front photo, ownership, retaken photo), ReplicateProvider against a mocked HTTP API (data URLs ≤256 KB, schema drift fails loudly, optional inputs filtered, poll states incl. expired output, output-CDN download has no auth header), delete-all now covering garments + try-ons.
- Playwright: 2 passed — P1 flow + P2 flow (front+side photos → garment screenshot auto-categorised → try-on → Done → original/try-on images load → "side view not supported" → reload persists → category change re-processes).

## Decisions (P3)
- **Body model = `silhouette-v1`, not SMPL-X.** SMPL-X needs licensed model files (registration, non-commercial) that can't be fetched here, and a silhouette-based multi-view SMPL-X fit needs a differentiable renderer/GPU. Shipped instead: `SilhouetteModel` in `app/body.py`, behind `BodyModel` Protocol (`fit(views, height_cm) -> BodyFit(measurements, glb, notes)`); `BODY_MODEL=silhouette` (anything else raises). SMPL-X plugs in here later (likely GPU via Modal).
- **Inputs**: `PoseDetector.segment(rgb)` → first person's landmarks + bool mask (MediaPipe `output_segmentation_masks=True`; FakeDetector draws matching polygons). Front + side photos required, back optional.
- **Scale**: per photo, px/cm = (mask floor row − mask top row) / height_cm (top = hair top).
- **Levels** (from front landmarks): chest = shoulder − 0.28·torso; waist = narrowest front width in shoulder − [0.45…0.85]·torso; hip = hip joint − 0.10·torso. Width = median front run containing the body centre over ±1 cm rows (arms excluded as separate runs); depth = same on the side photo. Girth = Ramanujan ellipse of width × depth; back photo averages width and its disagreement widens ±.
- **Inseam** = first background pixel below the hips at the body centre column (gap between legs); legs touching → 0.45·height, ±6 cm + note. **Shoulder width** = landmark distance × 1.12 (joint centres → acromion).
- **± confidence** base: girths 3.5 cm, inseam 2.5, shoulders 2.5; +1 with no back photo, + half the front/back girth disagreement with one. Rounded to 0.5.
- **Mesh**: lofted ellipse rings every 1 cm (32 segments): torso+head from crotch to head top, one loft per leg below the crotch (innermost run each side). No arms. Hand-written GLB (positions + indices, metres, y-up, feet at y=0); client computes normals. Stored at `users/<id>/bodies/<body_id>/body.glb`, **served via `GET /body/{id}/mesh`** (cookie-auth, same origin, `no-store`) so the 3D loader needs no bucket CORS.
- **Async**: `POST /body/fit` → 202 `running`, fit runs in FastAPI `BackgroundTasks` (CPU, ~1 s). A doc still `running` after 10 min is reported `failed` ("interrupted"). If the user deletes their data mid-fit, the mesh is deleted instead of orphaned.
- **Size advice** (`app/sizing.py`): chart = `{unit: cm|in, sizes: [{label, ranges: {chest|waist|hip|inseam|shoulder_width: [lo,hi] | point}}]}`; a point v = [v−2, v+2]. Measures by category: top/outerwear chest·waist·shoulders; dress chest·waist·hip; bottom waist·hip·inseam. Per size: delta = outside-range distance (+ tight / − loose); score = 2·tight + loose; lowest wins. Notes per measure: Fits / Tight / Loose / Borderline (|delta| ≤ that measure's ±). Confidence high (all fit, ± ≤ 3.5) / medium (score ≤ 6) / low. No chart → `recommended: null, reason: "No size chart found for this garment."`. Assumes **body**-measurement charts, not flat garment measurements.
- Manual size chart entry (`PATCH /garments/{id}` `{size_chart}`, `null` clears) exists because uploads never have a chart until P4.
- Frontend: `Body.jsx` lazy-loaded (three + R3F + drei ≈ 1 MB chunk) so sign-in/capture stay light. `SizeAdvice.jsx` on each ready garment, with an inline chart editor ("86-91" or "88").

## Endpoints (P3)
| Method | Path | Notes |
|---|---|---|
| POST | /body/fit | 202 `{id, status: running, model, …}`; 409 if no height / front / side photo |
| GET | /body/latest | 404 if none |
| GET | /body/{id} | `{status, measurements{name:{value_cm,pm_cm}}, notes, error, views_used, mesh_url}` |
| GET | /body/{id}/mesh | `model/gltf-binary`, `Cache-Control: private, no-store` |
| PATCH | /garments/{id} | now `{category?, size_chart?}` |
| GET | /garments/{id}/size-advice | `{recommended, confidence, notes, reason, per_size, measurements, body_id}`; 409 if no category or no finished body |

## Test status (end of P3, 2026-11-10)
- pytest: 55 passed. P3 adds 18: geometry helpers, GLB round-trip, height calibration, fit end-to-end (plausible values, waist < chest/hip, mesh 1.7 m tall, feet at 0), no-back-photo note, preconditions, failure reporting, privacy (404 other user / 401 anon), delete-all removes body meshes, sizing (no chart, borderline, tight/loose, category measures, inches + point values, validation, endpoint).
- Playwright: 3 passed — P1, P2, P3 flow (fit refused without photos → 3 photos → build → all 5 measurement rows with ± → mesh 200 `model/gltf-binary` → canvas renders → dress garment → "No size chart found" → enter S/M/L chart → recommendation + 3 notes → no page errors).

## Decisions (P4)
- **Fetcher** (`app/fetch.py`): `make_client()` = httpx client (no redirects, `trust_env=False` so env proxies can't bypass the guard, UA `FitMirrorBot/1.0`) whose pool uses `GuardedBackend`. That is an `httpcore` network backend that resolves the host, refuses if **any** resolved IP is non-global (loopback, private, link-local incl. 169.254.169.254, CGNAT, multicast, IPv4-mapped IPv6), then connects to the IP it checked. This defeats DNS rebinding; TLS still verifies the hostname. httpx 0.28 has no public hook, so it sets `transport._pool`: **httpx is pinned to 0.28.1**, and `test_real_client_refuses_private_targets` breaks if that hook stops working. httpx re-wraps our `BlockedAddress` as `ConnectError`; we read it back from `__cause__`.
- `check_url`: http/https only, no userinfo, ports 80/443 only. Redirects are followed manually (max 5), and every hop goes through `check_url` + the guard.
- **robots.txt** per origin (cached per request) for the page *and* each image host: 200 → parse; 4xx → allow; 5xx/unreachable → disallow. Agent token `FitMirrorBot`.
- Limits: page 3 MB (`text/html`/xhtml), image `MAX_UPLOAD_MB` (`image/*`), 10 s per request, 25 s total. Runs synchronously inside the request (bounded by those limits) rather than as a job.
- **Parser** (`app/product_page.py`, stdlib `HTMLParser`, no JS): images from JSON-LD `Product.image` (str / list / ImageObject, also inside `@graph`) first, then `og:image*`, then `twitter:image`. Made absolute, deduped, max 8; tries up to 5 until one passes `load_garment_image`. Title from JSON-LD name → og:title → `<title>`.
- **Size chart from tables**: tries each `<table>` as-is and transposed. Header cell → measure via aliases (bust→chest, inside leg/leg length→inseam, shoulder). Values "86-91", "86 – 91" (range) or "88" (point → ±2 cm). Unit comes from the header, then the table text ("cm" / "in" / "inches" / `"`); with no unit, max chest/waist/hip < 60 → inches. When both cm and in columns exist, the cm column wins. Needs ≥ 2 sizes. Stored converted to cm.
- `POST /garments/from-url` reuses `create_garment()` (shared with from-image): `source: "url"`, `source_url` (final after redirects), `title`, `size_chart`, `product_images`. Every failure → 422 `{code, reason (+ "Please upload a screenshot of the product instead."), fallback: "screenshot"}`. Codes: bad_url, unsafe_url, blocked_by_robots, http_error, wrong_type, too_large, timeout, fetch_failed, no_image.
- `URL_FETCH_ALLOW=host:port,...` skips the IP/port checks for those exact endpoints. It exists **only** for the E2E fixture shop (`127.0.0.1:5056`, served by `e2e_server.py` from `frontend/e2e/fixtures/shop/`) and config refuses it when `APP_ENV=production`.
- Logs record the host only. **Found + fixed**: httpx's own INFO logger printed full request URLs (query strings can hold session tokens). `httpx`/`httpcore` loggers are now forced to WARNING alongside botocore.

## Endpoints (P4)
| Method | Path | Notes |
|---|---|---|
| POST | /garments/from-url | `{url, category?}` → 202 garment (now also `source`, `title`, `source_url`, `size_chart` if found) or 422 `{code, reason, fallback: "screenshot"}` |

## Test status (end of P4, 2026-11-10)
- pytest: 93 passed. P4 adds 38: parser (JSON-LD @graph + ImageObject + relative URLs, og/twitter fallback, size table, transposed inches table, cm-column preference, unitless-inches guess, non-chart tables ignored); `is_public` (11 address classes); `check_url` rejections (8); `GuardedBackend` with rebinding-style resolver (private / mixed answers); **real client, no mocks**, refusing 127.0.0.1, localhost, 169.254.169.254, [::1], 10/8, 0.0.0.0; robots disallow / 404-allow / 5xx-disallow; redirect re-check, size cap, wrong type, redirect loop; endpoint success (image fallback, title, chart, pipeline → ready, full URL not in logs), failures → screenshot fallback, SSRF end-to-end, auth, prod refuses allowlist.
- Playwright: 4 flows. 8/8 passed with `--repeat-each=2` on 6 workers (41 s). The P4 flow: robots-blocked page, 169.254.169.254 and port 8000 each refused with a specific message; then the fixture product page → title, "from 127.0.0.1", "Size chart found on the page" → ready → size advice recommends a size with no manual chart entry.

## Decisions (P5, behind `FEATURE_3D_GARMENT`, default false)
- **Flag**: `FEATURE_3D_GARMENT` env → `GET /features` → `{garment_3d}`. Off: `POST /tryon3d` and `GET /tryon3d/{id}/mesh` 404 (dependency `require_flag`) and the UI shows no entry point. E2E backend runs with it on; the flag-off UI test stubs `/api/features`.
- **Approach = template garments + texture projection** (no cloth sim, no third-party garment assets). `app/garment3d.py` lofts a per-category tube around the stored **body profile** (`bodies.profile`: torso + leg rings + shoulder/chest/waist/hip/crotch heights, saved at fit time since P5). Ease (×k + cm): top 1.06+2, outerwear 1.12+4, dress 1.05+1.5, bottom 1.04+1.5 (trouser legs 1.12+2).
  - top: hip − 0.15·torso → shoulder; outerwear: hip − 0.35·torso → shoulder + 0.05·torso.
  - dress: bodice over the torso + a flared skirt from the crotch to a 0.30·height hem (1.35× width / 1.25× depth at the hem, never narrower than both legs together).
  - bottom: waist → crotch, then one tube per leg down to 0.06·height.
  - No sleeves (the body has no arms). Open tube ends (`loft(caps=False)`).
- **Texture**: garment cut-out, white padding cropped, ≤ 1024 px JPEG embedded in the GLB. Planar front projection (u = x, v = y over the garment's bbox); the back shows the mirrored front. `write_glb` now supports optional `TEXCOORD_0` + embedded image with 4-byte-aligned bufferViews.
- `POST /tryon3d {garment_id}` → 201 `{id, label: "preview", category, body_id, mesh_url}`. Synchronous (CPU, sub-second). 409: garment not ready, no finished body, or a body fitted before P5 (no profile → "Rebuild your 3D body…"). GLB at `users/<id>/previews3d/<id>.glb`, doc in `previews3d` (in `USER_COLLECTIONS`).
- UI: "3D preview (beta)" button on ready garments (flag on only) → garment mesh added to the existing avatar viewer with a "Preview · approximate, not a fit simulation" bar and Hide. `polygonOffset` keeps it off the skin. Shown only when the preview's `body_id` matches the displayed body.
- **Found + fixed (P3 bug, surfaced by P5)**: on the crotch row the body centre column is background, so "run nearest the centre" snapped the torso's bottom ring onto the left leg and the right leg's top ring onto the torso. This was invisible on the body mesh, but the dress skirt inherited it (narrow, shifted 7 cm left). Torso rings now start 1.5 cm above the crotch and leg rings stop 1 cm below it. Regression tests: `test_profile_crotch_boundary_regression`, `test_dress_skirt_is_centred_and_flared`.

## Endpoints (P5)
| Method | Path | Notes |
|---|---|---|
| GET | /features | `{garment_3d: bool}` (no auth) |
| POST | /tryon3d | flag on: `{garment_id}` → 201 preview; flag off → 404 |
| GET | /tryon3d/{id}/mesh | textured `model/gltf-binary`, `private, no-store`; flag off → 404 |

## Test status (end of P5, 2026-11-10)
- pytest: **103 passed**. P5 adds 10: flag off hides all; per-category mesh (4: valid indices, UVs in [0,1], embedded JPEG + baseColorTexture, vertical extent matches the category's body landmarks); garment wider than the body at chest height; preconditions (no body / pre-P5 body); privacy + delete-all; crotch-boundary regression; dress skirt centred + flared.
- Playwright: **6 flows, 12/12 passed** with `--repeat-each=2` on 6 workers: P1, P2, P3, P4, P5 flag on (button → "Preview" bar → textured mesh 200 → canvas → Hide), P5 flag off (no button). The URL flow's wait for the fetched garment is now 25 s, matching the server's fetch budget (under load it took more than 5 s while still "Fetching…", with no error).

## Decisions (Wardrobe)
- Everything a user creates is in their wardrobe (no separate "save" step). `GET /wardrobe` → `{garments, tryons}`, newest first, advancing pending jobs, the same shape as `/garments` + `/tryon`. The UI now loads only `/wardrobe` (one request per poll instead of two).
- `DELETE /tryon/{id}` purges `users/<id>/tryons/<tid>/` then the doc. `DELETE /garments/{id}` cascades: its try-ons (media + docs), its 3D previews (GLB + docs), then `users/<id>/garments/<gid>/` and the garment doc. Media goes first, as in DELETE /me. Photos and the body model are never touched. 404 for other users' items.
- UI (`Wardrobe.jsx`): try-on cards (moved from `TryOn.jsx`, same `tryon-result`/`tryon-status` test IDs) with a "Compare" checkbox (finished try-ons only, max 4; disabled at 4) and Remove (confirm dialog). "Remove garment" sits on garment cards. With ≥ 2 ticked, a compare grid shows "You" (front original) + each front result, its title, and its recommended size when a body and size chart exist (size advice fetched once per garment). The grid scrolls sideways inside its box on narrow screens.

## Endpoints (Wardrobe)
| Method | Path | Notes |
|---|---|---|
| GET | /wardrobe | `{garments: [...], tryons: [...]}` |
| DELETE | /tryon/{id} | 204; 404 if not yours |
| DELETE | /garments/{id} | 204; cascades to its try-ons + 3D previews |

## Test status (wardrobe, 2026-11-10)
- pytest: **109 passed** (+6: listing order + empty + auth, advances running jobs, delete try-on (media + doc, 404 after), garment cascade (try-ons, previews, media gone; other garment, photos, body kept), other users can't delete).
- Playwright: **7 flows, 14/14 passed** with `--repeat-each=2` on 6 workers. New wardrobe flow: 2 garments → 2 try-ons → compare needs 2 ticks → 3 images load → Clear → remove try-on → remove garment (its try-on goes too) → reload keeps the state.
- All URL-flow waits for a fetch result now use the 25 s server budget (`FETCH`). Under 6 parallel workers the robots refusal also exceeded 5 s while still "Fetching…" (see the CPU note below).

## Decisions (Android app, branch `feat/android-app`)
- **Capacitor 8** shell in `frontend/android/` (generated by `cap add android`; app id `com.fitmirror.app`). `frontend/capacitor.config.json` sets **`server.url` = the hosted frontend** (placeholder `https://fitmirror.vercel.app`; change it to the real Vercel URL, then `npm run android:sync`). The WebView loads the live site instead of bundled files on purpose: bundled files would run at origin `https://localhost`, making every `/api` call cross-site and forcing `SameSite=None` cookies + CORS + CSRF tokens. Same-origin keeps the P1 auth model unchanged, and web deploys update the app without a store release. Cost: no offline mode (nothing works offline anyway; it's all server-side).
- Manifest: `CAMERA` permission (+ `uses-feature camera required=false`) so guided capture's `getUserMedia` works. Capacitor's WebChromeClient asks at runtime. File-upload fallback uses the Android picker. `allowBackup="false"` so Android cloud backup can't copy the WebView's session cookie.
- Links to other hosts (the "from <shop>" link) open in the system browser (Capacitor default for non-`server.url` hosts).
- Scripts (`frontend/`): `npm run android:sync` (build + `cap sync`), `npm run android:open` (Android Studio), `npm run android:apk` (debug APK via Gradle; needs JDK 21 + Android SDK 36).
- Local dev on an emulator: temporarily set `server.url` to `http://10.0.2.2:5173` with `"cleartext": true`. The camera won't work there (plain-http pages aren't a secure context); upload still does.

## Known issues / limits
- Android: needs the frontend deployed first (server.url). No release signing/keystore, Play Store listing or app icon yet (default Capacitor icon). Built on 2026-11-12 (debug APK, 4.3 MB; manifest checked with aapt2) but **not yet run on a device or emulator**.
- **Local Android toolchain on this PC (per-user, no admin)**: Microsoft OpenJDK 21 at `%LOCALAPPDATA%\Programs\jdk-21`, Android SDK at `%LOCALAPPDATA%\Android\Sdk` (cmdline-tools latest, platform-tools, platforms;android-36, build-tools;35.0.0; SDK license accepted by the user). JAVA_HOME/ANDROID_HOME are **not** set globally. Set them in the shell before building:
  `$env:JAVA_HOME="$env:LOCALAPPDATA\Programs\jdk-21"; $env:ANDROID_HOME="$env:LOCALAPPDATA\Android\Sdk"` then `cd frontend; npm run android:apk` → `android/app/build/outputs/apk/debug/app-debug.apk`. `frontend/android/local.properties` (git-ignored) holds `sdk.dir`. First Gradle build ~7 min (downloads Gradle 8.14.3 + deps); later builds are fast.
- Gotcha: `sdkmanager.bat` called from PowerShell splits `platforms;android-36` at the `;` ("Package platforms not found"). Call it via `cmd /c` with the package names in quotes. sdkmanager now prints a deprecation notice pointing to the new `android sdk` CLI but still works.
- **Single uvicorn process + CPU-bound work**: body fit (numpy + Python loops), 3D previews and image re-encoding share one GIL, so under concurrent load every request slows (seen in parallel E2E). Before real traffic: run `--workers 2+` on Render (each worker loads MediaPipe, about 200 MB) and/or move fits to a process pool or job queue.
- **Fixed 2026-11-10: pytest hung at process exit** after the real-MediaPipe smoke test (all tests passed, no summary, process never exited; leftover hung runs then stalled later runs). Root cause: a `PoseLandmarker` that was never closed blocked interpreter shutdown when other threads were alive (TestClient/anyio). Standalone it exited fine, which is why it was intermittent. Fix: `MediaPipeDetector.close()`, called in the test's `finally` and in the FastAPI `lifespan` shutdown (also stops slow Render restarts). 3 clean full runs after the fix.
- **Fixed 2026-11-10: E2E flakes under parallel workers** (try-on/size-advice clicks took more than 5 s to show, garment stuck "Processing…"; 3 of 6 runs failed, no API errors). Root cause: the R3F canvas rendered 60 fps forever and headless Chromium runs WebGL in software, which starved the CPU for the backend, Vite and the other browsers. Fix: `<Canvas frameloop="demand">` (also saves battery on phones). Afterwards 9/9 passed with `--repeat-each=3`. Set `E2E_SHOTS=<dir>` to make `body.spec.js` save viewer screenshots.
- Silhouette measurements are only validated on synthetic masks; they need checking against tape measurements on real people before the numbers (and ± values) are trusted. Loose clothing, hair volume and arms close to the torso will inflate girths.
- Mesh is a coarse "dress form" (no arms, elliptical sections, side-photo facing guessed from nose position).
- Pose acceptance with *real* photos is only unit-tested on synthetic landmarks; thresholds need tuning on real captures (esp. back view — MediaPipe often assumes the subject faces the camera, so back/front orientation is only enforced for front).
- "Fitted clothing" and "plain background" are guidance only, not validated server-side. No blur/lighting check.
- On this Windows dev machine Smart App Control blocked the MediaPipe DLL once (`WinError 4551`); the smoke test skips in that case. Render (Linux) unaffected. Use `POSE_DETECTOR=fake` locally if it recurs.
- No login rate limiting / lockout yet. No password reset or email verification.
- `vercel.json` rewrite destination is a placeholder (`fitmirror-api.onrender.com`).
- Atlas/R2/Replicate not provisioned yet — all testing ran on mongomock + moto + FakeProvider/mocked Replicate HTTP.
- Replicate schemas confirmed 2026-11-10 (use `replicate.com/<owner>/<model>/llms.txt` — the `/api` pages are client-rendered and useless to fetch):
  - IDM-VTON inputs: `garm_img`*, `human_img`*, `garment_des`, `mask_img`, `category`, `crop`, `force_dc`, `mask_only`, `steps`, `seed`; output = single URI string. `category` is a plain string (no enum published): `upper_body`/`lower_body` seen in examples, `dresses` only implied by the `force_dc` description → first real dress try-on should be checked.
  - LLaVA-13B inputs: `image`*, `prompt`*, `top_p`, `temperature`, `max_tokens`; output = array of string tokens (concatenate).
  - Grounded-SAM: from `predict.py` (output order annotated, neg_annotated, mask, inverted_mask).
  - Not yet run against the live API with a real token.
- IDM-VTON is non-commercial; Grounded-SAM on a model-worn screenshot may include skin/other garments — product-only images recommended in UI.
- Results only get saved when someone polls; if no client polls within Replicate's 1 h window, that view fails as expired.
- Jobs in flight at the provider when a user hits DELETE /me aren't cancelled (inputs were data URLs, not stored; outputs auto-delete in 1 h; our poll would 404 the doc and never save).
- No side/back try-on (model limitation). No wardrobe compare view yet (`GET /wardrobe` not built; `GET /tryon` + `GET /garments` cover listing).
- oxlint warns `set-state-in-effect` on the initial fetch in `TryOn.jsx` and `Body.jsx` (async fetch; harmless).

- URL ingestion only sees server-rendered HTML: shops that build the page with JavaScript, or show the size chart in a popup, image or iframe, give "no image" / no chart → screenshot fallback or manual chart. Many big retailers also block bots (robots.txt or 403 anti-bot pages), and that is intended behaviour.
- Size-chart parsing assumes body-measurement tables; a "garment measurements" table (flat widths, e.g. "Chest width 52") would be misread as body girth. There's no detection for that yet.
- Charset: pages are decoded as UTF-8 (with replacement); the `<meta charset>` / header charset is ignored.

- 3D preview is a visual approximation: fixed template shapes per category, front-projected texture (side seams stretch, back mirrored), no sleeves/collars/drape, no size-dependent fit. Bodies fitted before P5 need a rebuild.

## Next steps (all spec phases built; what's left before real users)
1. Provision MongoDB Atlas, a private R2 bucket (+ CORS GET for the Vercel origin), Replicate token; set the `vercel.json` rewrite target; deploy Render + Vercel.
2. Run one real end-to-end try-on on Replicate (check `category: "dresses"` for IDM-VTON) and real MediaPipe on real photos; tune pose thresholds.
3. Validate body measurements against tape measurements on several people; tune ± ranges.
4. Licensing before any commercial use: IDM-VTON (non-commercial), and SMPL-X if adopted.
5. Not built yet: login rate limiting, password reset, email verification. Multi-worker / process-pool setup for CPU-bound fits (see Known issues).
