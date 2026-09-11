"""Read-only client for notkutusu.com's instructor ratings ("hocametre").

notkutusu.com is a static React SPA; its data lives behind a separate JSON API.
Every *read* endpoint used here is public — no account, token, or cookie is
required, so nothing about the user is ever sent to notkutusu. Writing
(voting, commenting) needs a site-local login plus a browser-issued reCAPTCHA
token and is deliberately not implemented.

Known data-quality issue: the instructor table contains duplicate and
misspelled profiles, and votes fragment across them ("Ayşe Tosun Kühn" with 0
votes next to "Ayşe Tosun" with 135). ``lookup`` therefore returns every
matching profile plus a vote-weighted combination, instead of trusting the
first hit.
"""
from __future__ import annotations

import os
import re
import time
import unicodedata
from typing import Any

import requests

from .client import DEFAULT_TIMEOUT

DEFAULT_NOTKUTUSU_API_URL = "https://mordor-api-production.notkutusu.com"
SITE_URL = "https://notkutusu.com"

# Fixed rating criteria, each scored 1-5 with an independent vote count.
RATING_CRITERIA: dict[str, dict[str, str]] = {
    "GIVES_NOTES": {"tr": "Not paylaşımı", "en": "Shares lecture notes"},
    "IS_HELPFUL": {"tr": "Yardımseverlik", "en": "Helpfulness"},
    "ASSIGNS_HOMEWORK": {"tr": "Ödev", "en": "Assigns homework"},
    "TAKES_ATTENDANCE": {"tr": "Yoklama", "en": "Takes attendance"},
    "TEACHING_SKILLS": {"tr": "Anlatım", "en": "Teaching skills"},
}

_TITLE_RE = re.compile(
    r"^\s*(prof\.?|doç\.?|doc\.?|dr\.?|öğr\.?|ogr\.?|gör\.?|gor\.?|üyesi|uyesi|"
    r"arş\.?|ars\.?|yrd\.?|araş\.?|aras\.?)\s*",
    re.IGNORECASE,
)


class NotkutusuError(RuntimeError):
    """The notkutusu API could not be reached or returned an unexpected shape."""


def strip_academic_titles(name: str) -> str:
    """Drop leading academic titles: 'Prof. Dr. Ali Çakmak' -> 'Ali Çakmak'."""
    text = name.strip()
    while True:
        stripped = _TITLE_RE.sub("", text, count=1)
        if stripped == text:
            return text.strip()
        text = stripped


def normalize_name(name: str) -> str:
    """Case- and diacritic-insensitive form used to compare instructor names."""
    text = strip_academic_titles(name).replace("ı", "i").replace("İ", "I")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip().casefold()


def split_instructor_field(value: str | None) -> list[str]:
    """OBS lists co-taught sections as 'A B, C D' — split into single names."""
    if not value:
        return []
    parts = re.split(r"\s*(?:,|;|/|\bve\b|&)\s*", value)
    names = [strip_academic_titles(part) for part in parts]
    return [name for name in names if name and name != "-"]


class NotkutusuClient:
    def __init__(
        self,
        base_url: str | None = None,
        *,
        session: requests.Session | None = None,
        request_delay: float = 0.25,
    ) -> None:
        self.base_url = (
            base_url or os.getenv("NOTKUTUSU_API_URL") or DEFAULT_NOTKUTUSU_API_URL
        ).rstrip("/")
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Accept-Language": "tr",
                "Origin": SITE_URL,
                "Referer": SITE_URL + "/",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
                ),
            }
        )
        self.request_delay = request_delay
        self._last_request = 0.0

    # ------------------------------------------------------------------ http

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        wait = self.request_delay - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        try:
            response = self.session.get(
                f"{self.base_url}{path}", params=params, timeout=DEFAULT_TIMEOUT
            )
        except requests.RequestException as exc:
            raise NotkutusuError(f"notkutusu request failed: {exc}") from exc
        finally:
            self._last_request = time.monotonic()
        if response.status_code >= 400:
            raise NotkutusuError(
                f"notkutusu returned HTTP {response.status_code} for {path}: "
                f"{response.text[:200]}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise NotkutusuError(f"notkutusu returned non-JSON for {path}.") from exc

    # ------------------------------------------------------------- endpoints

    def search_instructors(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        """Substring search over instructor names (``filter=name:like:<query>``)."""
        query = strip_academic_titles(query)
        if not query:
            raise NotkutusuError("Provide an instructor name to search for.")
        payload = self._get(
            "/instructors",
            {"page": 1, "size": max(1, min(int(limit), 50)), "filter": f"name:like:{query}"},
        )
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise NotkutusuError("Unexpected response shape from /instructors.")
        return [self._instructor_summary(item) for item in items]

    def get_ratings(self, slug: str) -> dict[str, Any]:
        """Per-criterion averages and vote counts for one instructor profile."""
        payload = self._get(
            f"/instructors/ratings/by-slug/{slug}",
            # Exactly the relation set the site itself requests. Asking for a
            # subset makes the API answer HTTP 500 for any profile with votes.
            {"relations": "faculty|instructorRatings|instructorRatings.user"},
        )
        if not isinstance(payload, dict) or "ratings" not in payload:
            raise NotkutusuError(f"No ratings payload for instructor slug '{slug}'.")
        instructor = self._instructor_summary(payload.get("instructor") or {})
        ratings = self._normalize_ratings(payload.get("ratings") or [])
        return {
            "instructor": instructor,
            "ratings": ratings,
            "total_votes": sum(r["count"] for r in ratings),
            "profile_url": f"{SITE_URL}/hocametre/{slug}" if slug else None,
        }

    def get_comments(
        self, instructor_id: int | str, *, limit: int = 20, page: int = 1
    ) -> dict[str, Any]:
        """Free-text student comments, newest first."""
        payload = self._get(
            f"/instructor-comments/instructor/{instructor_id}",
            # The sort direction must be lowercase; "DESC" is rejected.
            {"page": max(1, int(page)), "size": max(1, min(int(limit), 100)), "sort": "createdAt:desc"},
        )
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise NotkutusuError("Unexpected response shape from /instructor-comments.")
        return {
            "instructor_id": instructor_id,
            "total": payload.get("totalItems"),
            "page": page,
            "comments": [
                {
                    "id": item.get("id"),
                    "comment": item.get("comment"),
                    "created_at": item.get("createdAt"),
                }
                for item in items
            ],
        }

    def get_instructor(
        self, slug: str, *, include_comments: bool = True, comment_limit: int = 10
    ) -> dict[str, Any]:
        result = self.get_ratings(slug)
        if include_comments and result["instructor"].get("id") is not None:
            result["comments"] = self.get_comments(
                result["instructor"]["id"], limit=comment_limit
            )
        return result

    # ------------------------------------------------------------- composite

    def lookup(
        self,
        names: list[str],
        *,
        include_comments: bool = False,
        comment_limit: int = 5,
        max_profiles: int = 5,
    ) -> dict[str, Any]:
        """Resolve several instructor names to rated profiles.

        For each name: every matching profile with its ratings (most-voted
        first) and a vote-weighted ``combined`` score across them, because the
        same person is often split over duplicate profiles.
        """
        results: list[dict[str, Any]] = []
        for raw_name in names:
            name = strip_academic_titles(raw_name)
            if not name:
                continue
            profiles, query_used = self._find_profiles(name, limit=max_profiles)
            rated: list[dict[str, Any]] = []
            for profile in profiles[:max_profiles]:
                try:
                    detail = self.get_ratings(profile["slug"])
                except NotkutusuError as exc:
                    rated.append({**profile, "error": str(exc)})
                    continue
                entry = {
                    **profile,
                    # The ratings payload carries the faculty; the search hit does not.
                    **{k: v for k, v in detail["instructor"].items() if v is not None},
                    "ratings": detail["ratings"],
                    "total_votes": detail["total_votes"],
                    "profile_url": detail["profile_url"],
                }
                if include_comments and profile.get("id") is not None:
                    try:
                        entry["comments"] = self.get_comments(
                            profile["id"], limit=comment_limit
                        )["comments"]
                    except NotkutusuError as exc:
                        entry["comments_error"] = str(exc)
                rated.append(entry)
            rated.sort(key=lambda item: item.get("total_votes") or 0, reverse=True)
            results.append(
                {
                    "query": raw_name,
                    "search_term": query_used,
                    "profile_count": len(rated),
                    "profiles": rated,
                    "combined": combine_ratings(rated),
                }
            )
        return {
            "source": SITE_URL + "/hocametre",
            "criteria": {
                key: {"scale": "1-5", **labels} for key, labels in RATING_CRITERIA.items()
            },
            "note": (
                "Ratings are anonymous student votes on notkutusu.com, not an official "
                "İTÜ source. The same instructor may be split across duplicate profiles; "
                "'combined' is the vote-weighted average over every matched profile."
            ),
            "instructors": results,
        }

    # --------------------------------------------------------------- helpers

    def _find_profiles(self, name: str, *, limit: int) -> tuple[list[dict[str, Any]], str]:
        """Collect every profile that plausibly belongs to ``name``.

        The full name alone is not enough: the votes for "Ayşe Tosun Kühn" sit
        on a second profile called "Ayşe Tosun". So the surname is searched as
        well, and a hit is kept when its tokens and the requested name's tokens
        contain one another in either direction ("Ayşe Tosun" ⊂ "Ayşe Tosun
        Kühn"), which admits shortened duplicates but not other people who
        merely share the surname.
        """
        tokens = name.split()
        wanted = set(normalize_name(name).split())
        # Full name first, then every surname-like token on its own: the
        # duplicate carrying the votes may lack any one of them.
        attempts = [name] + [tok for tok in tokens[1:] if len(tok) >= 3]
        found: dict[str, dict[str, Any]] = {}
        terms_used: list[str] = []
        for attempt in attempts:
            hits = self.search_instructors(attempt, limit=max(limit, 10))
            if hits:
                terms_used.append(attempt)
            for item in hits:
                have = set(normalize_name(item.get("name") or "").split())
                if not have or not (wanted <= have or have <= wanted):
                    continue
                key = item.get("slug") or str(item.get("id"))
                found.setdefault(key, item)
        target = normalize_name(name)
        ordered = sorted(
            found.values(), key=lambda item: normalize_name(item.get("name") or "") != target
        )
        return ordered, " | ".join(terms_used) if terms_used else name

    @staticmethod
    def _instructor_summary(item: dict[str, Any]) -> dict[str, Any]:
        faculty = item.get("faculty")
        if isinstance(faculty, dict):
            faculty = faculty.get("name")
        return {
            "id": item.get("id"),
            "name": item.get("name"),
            "slug": item.get("slug"),
            "faculty": faculty,
        }

    @staticmethod
    def _normalize_ratings(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_key = {item.get("name"): item for item in raw if isinstance(item, dict)}
        rows: list[dict[str, Any]] = []
        for key, labels in RATING_CRITERIA.items():
            item = by_key.get(key, {})
            count = int(item.get("count") or 0)
            average = item.get("averageRating")
            rows.append(
                {
                    "criterion": key,
                    "label_tr": labels["tr"],
                    "label_en": labels["en"],
                    # The API reports 0 for unrated criteria; that is "no data", not a score.
                    "average": round(float(average), 2) if count and average is not None else None,
                    "count": count,
                }
            )
        return rows


def combine_ratings(profiles: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Vote-weighted average per criterion across several profiles of one person."""
    totals: dict[str, tuple[float, int]] = {key: (0.0, 0) for key in RATING_CRITERIA}
    for profile in profiles:
        for row in profile.get("ratings") or []:
            key = row.get("criterion")
            if key not in totals or row.get("average") is None or not row.get("count"):
                continue
            weighted, count = totals[key]
            totals[key] = (weighted + row["average"] * row["count"], count + row["count"])
    total_votes = sum(count for _, count in totals.values())
    if total_votes == 0:
        return None
    return {
        "total_votes": total_votes,
        "profiles_merged": sum(1 for p in profiles if p.get("total_votes")),
        "ratings": [
            {
                "criterion": key,
                "label_tr": RATING_CRITERIA[key]["tr"],
                "label_en": RATING_CRITERIA[key]["en"],
                "average": round(weighted / count, 2) if count else None,
                "count": count,
            }
            for key, (weighted, count) in totals.items()
        ],
    }
