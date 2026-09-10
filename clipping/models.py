"""Canonical types. Provider adapters normalize their JSON into these."""

from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    """ISO 8601 in UTC with a trailing Z, the one format stored in SQLite."""
    if dt.tzinfo is None:
        raise ValueError("refusing to store a naive datetime; attach a timezone")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Creator(BaseModel):
    handle: str
    follower_count: int = Field(ge=0)
    ig_user_id: str | None = None
    full_name: str | None = None
    is_verified: bool = False
    is_private: bool = False
    profile_pic_url: str | None = None

    @field_validator("handle")
    @classmethod
    def _strip_at(cls, v: str) -> str:
        return v.lstrip("@").strip().casefold()


class Post(BaseModel):
    shortcode: str
    creator_handle: str
    taken_at: datetime
    url: str = ""
    ig_media_id: str | None = None
    media_type: str | None = None
    caption: str | None = None
    thumbnail_url: str | None = None
    hashtags: list[str] = Field(default_factory=list)
    discovery_source: str = "tagged"

    @field_validator("creator_handle")
    @classmethod
    def _strip_at(cls, v: str) -> str:
        return v.lstrip("@").strip().casefold()

    def model_post_init(self, _ctx) -> None:
        if not self.url:
            object.__setattr__(self, "url", f"https://www.instagram.com/p/{self.shortcode}/")


class Metrics(BaseModel):
    like_count: int = Field(ge=0)
    comment_count: int = Field(ge=0)
    view_count: int | None = None
    play_count: int | None = None
