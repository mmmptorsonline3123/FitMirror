from datetime import datetime, timedelta, timezone
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from bson import ObjectId
from fastapi import Depends, HTTPException, Request, Response
import jwt
from .config import get_settings
from .db import get_db

COOKIE = "fm_session"
_ph = PasswordHasher()


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(hashed: str, pw: str) -> bool:
    try:
        return _ph.verify(hashed, pw)
    except VerifyMismatchError:
        return False


def set_session(resp: Response, user_id: str) -> None:
    s = get_settings()
    exp = datetime.now(timezone.utc) + timedelta(minutes=s.jwt_ttl_minutes)
    token = jwt.encode({"sub": user_id, "exp": exp}, s.jwt_secret, algorithm="HS256")
    # SameSite=Lax: frontend calls the API same-origin via the Vercel /api rewrite, so no cross-site cookie needed (also blocks CSRF POSTs).
    resp.set_cookie(COOKIE, token, httponly=True, secure=s.cookie_secure, samesite="lax", max_age=s.jwt_ttl_minutes * 60, path="/")


def clear_session(resp: Response) -> None:
    resp.delete_cookie(COOKIE, path="/")


def current_user(request: Request, db=Depends(get_db)) -> dict:
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Not authenticated")
    try:
        sub = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])["sub"]
        user = db.users.find_one({"_id": ObjectId(sub)})
    except Exception:
        raise HTTPException(401, "Invalid session")
    if not user:
        raise HTTPException(401, "Invalid session")
    return user
