from functools import lru_cache
from pymongo import MongoClient
from .config import get_settings


@lru_cache
def get_db():
    s = get_settings()
    if s.mongo_uri.startswith("mongomock://"):
        import mongomock  # test / local-E2E only
        client = mongomock.MongoClient()
    else:
        client = MongoClient(s.mongo_uri, tz_aware=True)
    db = client[s.mongo_db]
    db.users.create_index("email", unique=True)
    db.photos.create_index([("user_id", 1), ("view", 1)], unique=True)
    db.garments.create_index([("user_id", 1), ("created_at", -1)])
    db.tryons.create_index([("user_id", 1), ("created_at", -1)])
    db.bodies.create_index([("user_id", 1), ("created_at", -1)])
    return db


# Every collection holding per-user data. DELETE /me purges all of these — add new ones here.
USER_COLLECTIONS = ["photos", "garments", "tryons", "bodies", "previews3d"]
