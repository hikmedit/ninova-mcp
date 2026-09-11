"""Offline tests for the notkutusu 'hocametre' client and the tool registry."""
from __future__ import annotations

import unittest
from typing import Any

from ninova_mcp import server
from ninova_mcp.notkutusu import (
    NotkutusuClient,
    NotkutusuError,
    combine_ratings,
    normalize_name,
    split_instructor_field,
    strip_academic_titles,
)


class _Response:
    def __init__(self, status: int, payload: Any) -> None:
        self.status_code = status
        self._payload = payload
        self.text = str(payload)

    def json(self) -> Any:
        return self._payload


class _Session:
    """Minimal stand-in for requests.Session with canned answers per path."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.headers: dict[str, str] = {}
        self.routes = routes
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def get(self, url: str, params: dict[str, Any] | None = None, timeout: int = 0) -> _Response:
        path = url.split("notkutusu.com", 1)[1]
        self.calls.append((path, params))
        if path == "/instructors":
            term = (params or {}).get("filter", "").split(":", 2)[-1]
            items = [
                item for item in self.routes["instructors"]
                if normalize_name(term) in normalize_name(item["name"])
            ]
            return _Response(200, {"totalItems": len(items), "items": items})
        if path.startswith("/instructors/ratings/by-slug/"):
            slug = path.rsplit("/", 1)[1]
            if (params or {}).get("relations") != "faculty|instructorRatings|instructorRatings.user":
                return _Response(500, {"message": "Something went wrong"})
            return _Response(200, self.routes["ratings"][slug])
        if path.startswith("/instructor-comments/instructor/"):
            return _Response(200, {"totalItems": 1, "items": [
                {"id": "c1", "comment": "Notları paylaşıyor.", "createdAt": "2026-05-01T10:00:00Z", "userId": "u"}
            ]})
        return _Response(404, {})


def _rating(name: str, avg: float, count: int) -> dict[str, Any]:
    return {"name": name, "userRating": None, "averageRating": avg, "count": count}


ROUTES = {
    "instructors": [
        {"id": "1", "name": "Ayşe Tosun Kühn", "slug": "ayse_tosun_kuhn", "email": "x@itu.edu.tr"},
        {"id": "2", "name": "Ayşe Tosun", "slug": "ayse_tosun"},
        {"id": "3", "name": "Mehmet Tosun", "slug": "mehmet_tosun"},
    ],
    "ratings": {
        "ayse_tosun_kuhn": {
            "instructor": {"id": "1", "name": "Ayşe Tosun Kühn", "slug": "ayse_tosun_kuhn", "faculty": None},
            "ratings": [_rating("GIVES_NOTES", 0, 0), _rating("TAKES_ATTENDANCE", 0, 0)],
        },
        "ayse_tosun": {
            "instructor": {"id": "2", "name": "Ayşe Tosun", "slug": "ayse_tosun", "faculty": {"name": "BBF"}},
            "ratings": [_rating("GIVES_NOTES", 2.0, 30), _rating("TAKES_ATTENDANCE", 1.6, 25)],
        },
        "mehmet_tosun": {
            "instructor": {"id": "3", "name": "Mehmet Tosun", "slug": "mehmet_tosun"},
            "ratings": [_rating("GIVES_NOTES", 5.0, 10)],
        },
    },
}


class NameHelpersTests(unittest.TestCase):
    def test_strip_titles(self) -> None:
        self.assertEqual(strip_academic_titles("Prof. Dr. Ali Çakmak"), "Ali Çakmak")
        self.assertEqual(strip_academic_titles("Öğr. Gör. Dr. Berna Kiraz"), "Berna Kiraz")
        self.assertEqual(strip_academic_titles("Ali Çakmak"), "Ali Çakmak")

    def test_normalize_name_is_diacritic_and_case_insensitive(self) -> None:
        self.assertEqual(normalize_name("Ahmet Cüneyd TANTUĞ"), normalize_name("ahmet cuneyd tantug"))
        self.assertEqual(normalize_name("Işık"), normalize_name("isik"))

    def test_split_instructor_field(self) -> None:
        self.assertEqual(
            split_instructor_field("Prof. Dr. Ali Çakmak, Doç. Dr. Berna Kiraz"),
            ["Ali Çakmak", "Berna Kiraz"],
        )
        self.assertEqual(split_instructor_field("-"), [])
        self.assertEqual(split_instructor_field(None), [])


class ClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = _Session(ROUTES)
        self.client = NotkutusuClient(session=self.session, request_delay=0)

    def test_search_drops_email(self) -> None:
        found = self.client.search_instructors("Tosun")
        self.assertEqual({item["name"] for item in found}, {"Ayşe Tosun Kühn", "Ayşe Tosun", "Mehmet Tosun"})
        self.assertNotIn("email", found[0])

    def test_ratings_use_full_relation_set_and_null_unrated(self) -> None:
        detail = self.client.get_ratings("ayse_tosun_kuhn")
        self.assertEqual(detail["total_votes"], 0)
        self.assertTrue(all(row["average"] is None for row in detail["ratings"]))
        self.assertEqual(len(detail["ratings"]), 5)

    def test_lookup_merges_duplicate_profiles_but_not_other_people(self) -> None:
        result = self.client.lookup(["Prof. Dr. Ayşe Tosun Kühn"])
        entry = result["instructors"][0]
        names = [p["name"] for p in entry["profiles"]]
        self.assertEqual(names, ["Ayşe Tosun", "Ayşe Tosun Kühn"])  # most-voted first
        self.assertNotIn("Mehmet Tosun", names)
        combined = entry["combined"]
        self.assertEqual(combined["total_votes"], 55)
        by_key = {row["criterion"]: row for row in combined["ratings"]}
        self.assertEqual(by_key["GIVES_NOTES"]["average"], 2.0)
        self.assertEqual(by_key["TEACHING_SKILLS"]["average"], None)
        self.assertEqual(entry["profiles"][0]["faculty"], "BBF")

    def test_lookup_with_comments(self) -> None:
        result = self.client.lookup(["Mehmet Tosun"], include_comments=True, comment_limit=1)
        profile = result["instructors"][0]["profiles"][0]
        self.assertEqual(profile["comments"][0]["comment"], "Notları paylaşıyor.")
        self.assertNotIn("userId", profile["comments"][0])

    def test_http_error_is_wrapped(self) -> None:
        with self.assertRaises(NotkutusuError):
            self.client._get("/nope")

    def test_combine_ratings_empty(self) -> None:
        self.assertIsNone(combine_ratings([]))


class ToolRegistryTests(unittest.TestCase):
    def test_every_tool_is_an_app_method(self) -> None:
        for tool in server.TOOLS:
            self.assertTrue(callable(getattr(server.NinovaMcpApp, tool["name"], None)), tool["name"])

    def test_tool_names_are_unique(self) -> None:
        names = [tool["name"] for tool in server.TOOLS]
        self.assertEqual(len(names), len(set(names)))

    def test_remote_surface_hides_disk_and_raw_api_tools(self) -> None:
        for hidden in ("download_resource", "obs_get_transcript", "obs_api_get"):
            self.assertNotIn(hidden, server.REMOTE_TOOL_NAMES)
        for exposed in ("obs_get_registration_options", "hocametre_rate_course_sections"):
            self.assertIn(exposed, server.REMOTE_TOOL_NAMES)


if __name__ == "__main__":
    unittest.main()
