"""Public (no login) ITU OBS course-schedule access.

``https://obs.itu.edu.tr/public/DersProgram`` publishes every section offered in
the active term: CRN, meeting days and times, instructor, room, quota and
enrolment. No session is needed, so this module deliberately uses its own
anonymous HTTP session rather than the authenticated OBS client.
"""

from __future__ import annotations

import os
import re
from typing import Any

import requests
from bs4 import Tag

from .client import DEFAULT_HEADERS, DEFAULT_TIMEOUT
from .obs_client import DEFAULT_OBS_BASE_URL, ObsError
from .parsing import make_soup
from .prereq import COURSE_CODE_RE, normalize_code

PROGRAM_LEVELS = {
    "OL": "Önlisans",
    "LS": "Lisans",
    "LU": "Lisansüstü",
    "LUI": "Lisansüstü 2.Öğretim",
}

DAYS_TR_TO_EN = {
    "Pazartesi": "Monday",
    "Salı": "Tuesday",
    "Çarşamba": "Wednesday",
    "Perşembe": "Thursday",
    "Cuma": "Friday",
    "Cumartesi": "Saturday",
    "Pazar": "Sunday",
}
DAY_ORDER = list(DAYS_TR_TO_EN)

COLUMNS = [
    "crn",
    "code",
    "name",
    "teaching_method",
    "instructor",
    "building",
    "day",
    "time",
    "room",
    "capacity",
    "enrolled",
    "reservation",
    "eligible_programs",
    "prerequisites",
    "class_requirement",
]

_CODE_RE = re.compile(r"^\s*([A-Za-zÇĞİÖŞÜçğıöşü]{2,4})\s*([0-9][0-9A-Za-z]*)\s*$")


def _split_cell(cell: Tag) -> list[str]:
    """Multi-session rows pack their values into one cell split by <br/>."""
    if cell is None:
        return []
    parts = [
        part.strip()
        for part in cell.get_text("\n", strip=True).split("\n")
        if part.strip()
    ]
    return parts


def _to_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _decimal_tr(value: str | None) -> float | None:
    """``"95,00"`` -> ``95.0``; anything non-numeric (a class requirement) -> ``None``."""
    if not value:
        return None
    match = re.search(r"\d+(?:[.,]\d+)?", value)
    if match is None:
        return None
    try:
        return float(match.group().replace(",", "."))
    except ValueError:
        return None


def _minutes(clock: str | None) -> int | None:
    if not clock or ":" not in clock:
        return None
    hours, _, minutes = clock.partition(":")
    hour = _to_int(hours)
    minute = _to_int(minutes)
    if hour is None or minute is None:
        return None
    return hour * 60 + minute


def split_course_code(code: str) -> tuple[str, str] | None:
    """``"YZV 302E"`` -> ``("YZV", "302E")``."""
    match = _CODE_RE.match(code.replace(" ", " "))
    if not match:
        return None
    return match.group(1).upper(), match.group(2).upper()


class ObsPublicClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (
            base_url or os.getenv("OBS_BASE_URL") or DEFAULT_OBS_BASE_URL
        ).rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self._branch_cache: dict[str, list[dict[str, Any]]] = {}
        self._term_cache: dict[str, dict[str, Any]] = {}
        self._prereq_branch_cache: list[dict[str, Any]] | None = None
        self._program_cache: list[dict[str, Any]] | None = None

    def _get(self, path: str, params: dict[str, Any] | None = None) -> requests.Response:
        response = self.session.get(
            f"{self.base_url}{path}", params=params, timeout=DEFAULT_TIMEOUT
        )
        response.raise_for_status()
        if not response.encoding or response.encoding.lower() == "iso-8859-1":
            response.encoding = response.apparent_encoding or "utf-8"
        return response

    def _normalize_level(self, level: str | None) -> str:
        key = (level or "LS").strip().upper()
        if key in PROGRAM_LEVELS:
            return key
        for code, name in PROGRAM_LEVELS.items():
            if key.casefold() == name.casefold():
                return code
        raise ObsError(
            f"Unknown program level '{level}'. Use one of: {', '.join(PROGRAM_LEVELS)}."
        )

    def active_term(self, level: str = "LS") -> dict[str, Any]:
        """The term whose schedule the public pages currently publish.

        The page's ``GetAktifDonemByProgramSeviye`` endpoint reports the term the
        registration system considers current, which lags the published schedule
        and is written into an ``h1.guncelDonem`` element that no longer exists on
        the page. The schedule's own term is the hidden heading: ``#baslik1`` for
        associate/undergraduate/graduate, ``#baslik2`` for graduate evening.
        """
        key = self._normalize_level(level)
        if key not in self._term_cache:
            response = self._get("/public/DersProgram")
            soup = make_soup(response.text)
            heading = soup.find(id="baslik2" if key == "LUI" else "baslik1")
            term = None
            if heading is not None:
                term = re.sub(
                    r"\s*Ders\s+Programlar[ıi]\s*$", "", heading.get_text(" ", strip=True)
                ).strip()

            code = None
            link = soup.find("a", href=re.compile(r"/ders-programi/\d{6}/"))
            if link:
                match = re.search(r"/ders-programi/(\d{6})/", link["href"])
                code = match.group(1) if match else None

            self._term_cache[key] = {
                "level": key,
                "level_name": PROGRAM_LEVELS[key],
                "term": term,
                "term_code": code,
            }
        return self._term_cache[key]

    def registration_term(self, level: str = "LS") -> dict[str, Any]:
        """The term the registration system reports as current.

        Kept separate from :meth:`active_term` because the two routinely differ:
        the schedule for the next term is published while the previous term is
        still the "current" one.
        """
        key = self._normalize_level(level)
        response = self._get(
            "/public/DersProgram/GetAktifDonemByProgramSeviye",
            params={"programSeviyeTipiAnahtari": key},
        )
        payload = response.json() if "json" in response.headers.get("Content-Type", "") else {}
        return {
            "level": key,
            "level_name": PROGRAM_LEVELS[key],
            "term": payload.get("aktifDonem"),
        }

    def branch_codes(self, level: str = "LS", refresh: bool = False) -> list[dict[str, Any]]:
        key = self._normalize_level(level)
        if refresh or key not in self._branch_cache:
            response = self._get(
                "/public/DersProgram/SearchBransKoduByProgramSeviye",
                params={"programSeviyeTipiAnahtari": key},
            )
            payload = response.json()
            self._branch_cache[key] = [
                {"branch_id": item.get("bransKoduId"), "code": item.get("dersBransKodu")}
                for item in payload
                if item.get("dersBransKodu")
            ]
        return self._branch_cache[key]

    def resolve_branch(self, branch: str | int, level: str = "LS") -> dict[str, Any]:
        key = self._normalize_level(level)
        entries = self.branch_codes(key)
        needle = str(branch).strip().upper()
        for entry in entries:
            if str(entry["branch_id"]) == needle or entry["code"].upper() == needle:
                return entry
        raise ObsError(
            f"Unknown course branch '{branch}' for {PROGRAM_LEVELS[key]}. "
            f"Known codes include: {', '.join(e['code'] for e in entries[:15])}…"
        )

    def get_schedule(
        self,
        branch: str | int,
        *,
        level: str = "LS",
        course_code: str | None = None,
        day: str | None = None,
        instructor: str | None = None,
        only_available: bool = False,
    ) -> dict[str, Any]:
        """Return every published section for one course branch (e.g. ``YZV``)."""
        key = self._normalize_level(level)
        resolved = self.resolve_branch(branch, key)
        response = self._get(
            "/public/DersProgram/DersProgramSearch",
            params={
                "ProgramSeviyeTipiAnahtari": key,
                "DersBransKoduId": resolved["branch_id"],
            },
        )
        sections = self._parse_schedule_table(response.text)

        if course_code:
            wanted = course_code.replace(" ", "").upper()
            sections = [s for s in sections if (s["code"] or "").replace(" ", "").upper() == wanted]
        if day:
            wanted_day = day.strip().casefold()
            sections = [
                s
                for s in sections
                if any(
                    wanted_day in (m["day"] or "").casefold()
                    or wanted_day in (m["day_en"] or "").casefold()
                    for m in s["meetings"]
                )
            ]
        if instructor:
            wanted_instructor = instructor.strip().casefold()
            sections = [
                s for s in sections if wanted_instructor in (s["instructor"] or "").casefold()
            ]
        if only_available:
            sections = [s for s in sections if (s["available"] or 0) > 0]

        term = self.active_term(key)
        return {
            "level": key,
            "level_name": PROGRAM_LEVELS[key],
            "term": term["term"],
            "term_code": term["term_code"],
            "branch": resolved,
            "count": len(sections),
            "sections": sections,
        }

    def _parse_schedule_table(self, html: str) -> list[dict[str, Any]]:
        soup = make_soup(html)
        table = soup.find("table", id="dersProgramContainer") or soup.find("table")
        if table is None:
            return []

        sections: list[dict[str, Any]] = []
        for row in table.find_all("tr"):
            if "table-baslik" in (row.get("class") or []):
                continue
            cells = row.find_all("td")
            if len(cells) < len(COLUMNS):
                continue

            by_name = dict(zip(COLUMNS, cells))
            days = _split_cell(by_name["day"])
            times = _split_cell(by_name["time"])
            rooms = _split_cell(by_name["room"])
            buildings = _split_cell(by_name["building"])

            meetings = []
            for index, day_name in enumerate(days):
                slot = times[index] if index < len(times) else ""
                start, _, end = slot.partition("/")
                meetings.append(
                    {
                        "day": day_name,
                        "day_en": DAYS_TR_TO_EN.get(day_name, day_name),
                        "start": start.strip() or None,
                        "end": end.strip() or None,
                        "start_minutes": _minutes(start.strip()),
                        "end_minutes": _minutes(end.strip()),
                        "building": buildings[index] if index < len(buildings) else None,
                        "room": rooms[index] if index < len(rooms) else None,
                    }
                )

            capacity = _to_int(by_name["capacity"].get_text(strip=True))
            enrolled = _to_int(by_name["enrolled"].get_text(strip=True))
            prerequisite_link = by_name["prerequisites"].find("a")

            sections.append(
                {
                    "crn": by_name["crn"].get_text(strip=True),
                    "code": by_name["code"].get_text(" ", strip=True),
                    "name": by_name["name"].get_text(" ", strip=True),
                    "teaching_method": by_name["teaching_method"].get_text(" ", strip=True),
                    "instructor": by_name["instructor"].get_text(" ", strip=True),
                    "meetings": meetings,
                    "capacity": capacity,
                    "enrolled": enrolled,
                    "available": (
                        None if capacity is None or enrolled is None else capacity - enrolled
                    ),
                    "reservation": by_name["reservation"].get_text(" ", strip=True),
                    "eligible_programs": [
                        part.strip()
                        for part in by_name["eligible_programs"].get_text(" ", strip=True).split(",")
                        if part.strip() and part.strip() != "-"
                    ],
                    "has_prerequisites": bool(prerequisite_link),
                    "prerequisites_url": (
                        prerequisite_link.get("href") if prerequisite_link else None
                    ),
                    "class_requirement": by_name["class_requirement"].get_text(" ", strip=True),
                }
            )
        return sections

    # ------------------------------------------------- prerequisites (public)

    def prerequisite_branches(self, refresh: bool = False) -> list[dict[str, Any]]:
        """Branch codes that have published prerequisites. Narrower than the schedule list."""
        if refresh or self._prereq_branch_cache is None:
            response = self._get("/public/GenelTanimlamalar/DersOnsartList")
            soup = make_soup(response.text)
            select = soup.find("select", id="DersBransKoduId")
            self._prereq_branch_cache = [
                {"branch_id": _to_int(option.get("value")), "code": option.get_text(strip=True)}
                for option in (select.find_all("option") if select else [])
                if option.get("value")
            ]
        return self._prereq_branch_cache

    def get_prerequisites(self, branch: str | int) -> dict[str, Any]:
        """Published prerequisites for one course branch.

        These always reflect the programme's *current* definitions; OBS does not
        version them per course plan, so an older cohort is still assessed
        against the latest expressions.
        """
        needle = str(branch).strip().upper()
        resolved = next(
            (
                entry
                for entry in self.prerequisite_branches()
                if entry["code"].upper() == needle or str(entry["branch_id"]) == needle
            ),
            None,
        )
        if resolved is None:
            raise ObsError(
                f"No published prerequisites for branch '{branch}'. "
                f"Known codes include: {', '.join(e['code'] for e in self.prerequisite_branches()[:15])}…"
            )

        response = self._get(
            "/public/GenelTanimlamalar/OnsartAra",
            params={"DersBransKoduId": resolved["branch_id"]},
        )
        soup = make_soup(response.text)
        table = soup.find("table")
        courses = []
        for row in table.find_all("tr") if table else []:
            cells = row.find_all("td")
            if len(cells) < 4:
                continue
            # The code cell holds the Turkish and English variants of one course.
            codes = COURSE_CODE_RE.findall(cells[0].get_text(" ", strip=True))
            credit_text = cells[3].get_text(" ", strip=True)
            courses.append(
                {
                    "codes": [normalize_code(code) for code in codes],
                    "name": cells[1].get_text(" ", strip=True),
                    "expression": cells[2].get_text(" ", strip=True) or None,
                    "min_completed_credits": _decimal_tr(credit_text),
                    "credit_or_class_requirement": credit_text or None,
                }
            )
        return {"branch": resolved, "count": len(courses), "courses": courses}

    # -------------------------------------------------- equivalences (public)

    def equivalence_programs(self, refresh: bool = False) -> list[dict[str, Any]]:
        if refresh or self._program_cache is None:
            response = self._get("/public/GenelTanimlamalar/DersPlanDenklikleri")
            soup = make_soup(response.text)
            select = soup.find("select", id="programId")
            self._program_cache = [
                {"program_id": _to_int(option.get("value")), "name": option.get_text(strip=True)}
                for option in (select.find_all("option") if select else [])
                if option.get("value")
            ]
        return self._program_cache

    def resolve_equivalence_program(self, program: str | int) -> dict[str, Any]:
        needle = str(program).strip()
        entries = self.equivalence_programs()
        for entry in entries:
            if str(entry["program_id"]) == needle:
                return entry
        lowered = needle.casefold()
        matches = [entry for entry in entries if lowered in entry["name"].casefold()]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise ObsError(f"No academic programme matches '{program}'.")
        names = ", ".join(f"{m['program_id']} ({m['name']})" for m in matches[:8])
        raise ObsError(f"'{program}' is ambiguous. Candidates: {names}")

    def equivalence_plan_types(self, program_id: int) -> list[dict[str, Any]]:
        response = self.session.post(
            f"{self.base_url}/public/GenelTanimlamalar/GetPlanTipleriByProgramId",
            data={"programId": program_id},
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
        return [
            {"plan_type_id": item.get("objectId"), "name": item.get("planTipiAdiLanguage")}
            for item in response.json()
        ]

    def equivalence_plans(self, program_id: int, plan_type_id: int) -> list[dict[str, Any]]:
        response = self.session.post(
            f"{self.base_url}/public/GenelTanimlamalar/GetDersPlanlariByProgramIdAndPlanTipiId",
            data={"programId": program_id, "planTipiId": plan_type_id},
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
        return [
            {"plan_id": item.get("objectId"), "title": item.get("dersPlanBaslikLanguage")}
            for item in response.json()
        ]

    def get_equivalences(
        self,
        *,
        program_id: int,
        plan_type_id: int,
        plan_id: int,
        branch: str | int,
    ) -> dict[str, Any]:
        """Which courses count in place of a plan course, for one plan and branch."""
        branch_id = branch
        if not str(branch).isdigit():
            branch_id = self.resolve_branch(branch)["branch_id"]

        response = self._get(
            "/public/GenelTanimlamalar/DersDenklikAra",
            params={
                "ProgramId": program_id,
                "PlanTipiId": plan_type_id,
                "PlanId": plan_id,
                "DersBransKoduId": branch_id,
            },
        )
        soup = make_soup(response.text)
        table = soup.find("table")
        pairs = []
        for row in table.find_all("tr") if table else []:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            plan_codes = COURSE_CODE_RE.findall(cells[0].get_text(" ", strip=True))
            equivalent_codes = COURSE_CODE_RE.findall(cells[1].get_text(" ", strip=True))
            if not plan_codes or not equivalent_codes:
                continue
            pairs.append(
                {
                    "plan_course": normalize_code(plan_codes[0]),
                    "plan_course_label": cells[0].get_text(" ", strip=True),
                    "equivalents": [normalize_code(code) for code in equivalent_codes],
                    "equivalents_label": cells[1].get_text(" ", strip=True),
                }
            )
        return {
            "program_id": program_id,
            "plan_id": plan_id,
            "count": len(pairs),
            "equivalences": pairs,
        }

    def find_courses(
        self,
        codes: list[str],
        *,
        level: str = "LS",
        only_available: bool = False,
    ) -> dict[str, Any]:
        """Look up several courses at once, e.g. ``["YZV 302E", "BLG 223E"]``."""
        if not codes:
            raise ObsError("Provide at least one course code, for example 'YZV 302E'.")

        results: dict[str, Any] = {}
        not_found: list[str] = []
        for raw_code in codes:
            parts = split_course_code(raw_code)
            if parts is None:
                not_found.append(raw_code)
                continue
            branch, _number = parts
            try:
                schedule = self.get_schedule(
                    branch,
                    level=level,
                    course_code=raw_code,
                    only_available=only_available,
                )
            except ObsError:
                not_found.append(raw_code)
                continue
            if schedule["sections"]:
                results[raw_code.upper()] = schedule["sections"]
            else:
                not_found.append(raw_code)

        term = self.active_term(level)
        return {
            "level": term["level"],
            "term": term["term"],
            "term_code": term["term_code"],
            "found": results,
            "not_offered": not_found,
        }

    def check_conflicts(
        self,
        selections: list[dict[str, Any]],
        *,
        level: str = "LS",
    ) -> dict[str, Any]:
        """Check a candidate registration set for time clashes.

        Each selection is ``{"code": "YZV 302E", "crn": "12324"}``. The CRN may
        be omitted when the course has only one section.
        """
        chosen: list[dict[str, Any]] = []
        problems: list[str] = []

        for selection in selections:
            code = str(selection.get("code") or "").strip()
            crn = str(selection.get("crn") or "").strip()
            if not code and not crn:
                problems.append("A selection needs at least a course code or a CRN.")
                continue
            if not code:
                problems.append(f"CRN {crn} needs its course code to be looked up.")
                continue

            parts = split_course_code(code)
            try:
                schedule = self.get_schedule(
                    parts[0] if parts else code,
                    level=level,
                    course_code=code,
                )
            except ObsError as exc:
                problems.append(str(exc))
                continue
            sections = schedule["sections"]
            if crn:
                sections = [s for s in sections if s["crn"] == crn]
            if not sections:
                problems.append(f"No published section found for {code}{f' / CRN {crn}' if crn else ''}.")
                continue
            if len(sections) > 1:
                problems.append(
                    f"{code} has {len(sections)} sections; pass a CRN to pick one "
                    f"({', '.join(s['crn'] for s in sections)})."
                )
                continue
            chosen.append(sections[0])

        conflicts = []
        for first_index, first in enumerate(chosen):
            for second in chosen[first_index + 1 :]:
                for meeting_a in first["meetings"]:
                    for meeting_b in second["meetings"]:
                        if meeting_a["day"] != meeting_b["day"]:
                            continue
                        start_a, end_a = meeting_a["start_minutes"], meeting_a["end_minutes"]
                        start_b, end_b = meeting_b["start_minutes"], meeting_b["end_minutes"]
                        if None in (start_a, end_a, start_b, end_b):
                            continue
                        if start_a < end_b and start_b < end_a:
                            conflicts.append(
                                {
                                    "day": meeting_a["day"],
                                    "first": f"{first['code']} ({first['crn']}) {meeting_a['start']}-{meeting_a['end']}",
                                    "second": f"{second['code']} ({second['crn']}) {meeting_b['start']}-{meeting_b['end']}",
                                }
                            )

        weekly: dict[str, list[dict[str, Any]]] = {}
        for section in chosen:
            for meeting in section["meetings"]:
                weekly.setdefault(meeting["day"], []).append(
                    {
                        "code": section["code"],
                        "crn": section["crn"],
                        "start": meeting["start"],
                        "end": meeting["end"],
                        "room": meeting["room"],
                        "building": meeting["building"],
                    }
                )
        for entries in weekly.values():
            entries.sort(key=lambda item: item["start"] or "")

        term = self.active_term(level)
        return {
            "term": term["term"],
            "term_code": term["term_code"],
            "selected_count": len(chosen),
            "conflict_count": len(conflicts),
            "conflicts": conflicts,
            "problems": problems,
            "weekly": {
                day: weekly[day] for day in DAY_ORDER if day in weekly
            },
            "sections": chosen,
        }
