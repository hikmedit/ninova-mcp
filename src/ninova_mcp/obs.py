"""Shaping layer over the ITU OBS student JSON API.

The raw API answers with Turkish field names and wraps every payload in a
``statusCode``/``resultMessage`` envelope. This module unwraps those envelopes
and reshapes the parts a student actually asks about: what has been taken, what
is still missing for graduation, grades, schedule and registration state.
"""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .env import load_ninova_env
from .obs_client import ObsClient, ObsError
from .prereq import (
    PrerequisiteParseError,
    base_code,
    evaluate,
    iter_atoms,
    normalize_code,
    parse,
)

if TYPE_CHECKING:
    from .obs_public import ObsPublicClient

# ITU's letter scale, verified against the GPA OBS itself reports.
GRADE_POINTS: dict[str, float] = {
    "AA": 4.00,
    "BA+": 3.75,
    "BA": 3.50,
    "BB+": 3.25,
    "BB": 3.00,
    "CB+": 2.75,
    "CB": 2.50,
    "CC+": 2.25,
    "CC": 2.00,
    "DC+": 1.75,
    "DC": 1.50,
    "DD+": 1.25,
    "DD": 1.00,
    "FF": 0.00,
    "VF": 0.00,
}
# Grades that carry no grade point but are not failures.
NON_GRADED_PASS = {"BL", "MU", "T", "S", "E"}
FAILING_GRADES = {"FF", "FD", "VF", "BZ", "F"}


def _envelope(payload: Any, key: str) -> tuple[Any, dict[str, Any]]:
    """Split an OBS response into its data part and its status metadata."""
    if not isinstance(payload, dict):
        return payload, {}
    meta = {
        "status_code": payload.get("statusCode"),
        "result_code": payload.get("resultCode"),
        "result_message": payload.get("resultMessage"),
    }
    return payload.get(key), meta


def _ok(meta: dict[str, Any]) -> bool:
    return meta.get("status_code") in (0, None)


def _decimal(value: Any) -> float | None:
    """OBS mixes ``3.5`` floats with ``"3,5"`` locale strings."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return None


def _course_code(brans_kodu: Any, ders_kodu: Any = None) -> str:
    """Build a display code from the branch/number split OBS uses."""
    brans = (brans_kodu or "").strip()
    ders = (ders_kodu or "").strip()
    if brans and ders and not brans.endswith(ders):
        return f"{brans} {ders}"
    return brans or ders


def _section_open_to(section: dict[str, Any], program_code: str | None) -> bool:
    """Sections carry a programme allow-list; an empty list means open to all."""
    if not program_code:
        return True
    allowed = section.get("eligible_programs") or []
    if not allowed:
        return True
    return program_code.upper() in {entry.upper() for entry in allowed}


def _english_name(name: str | None) -> str | None:
    """OBS prints prerequisite course names as ``"Türkçe Ad / English Name"``."""
    if not name:
        return None
    parts = [part.strip() for part in name.split("/") if part.strip()]
    return parts[-1] if parts else None


def _minutes_to_clock(value: Any) -> str | None:
    if not isinstance(value, int):
        return None
    return f"{value // 60:02d}:{value % 60:02d}"


class ObsService:
    def __init__(self, client: ObsClient | None = None) -> None:
        self._client = client
        self._semesters: list[dict[str, Any]] | None = None
        self._programs: list[dict[str, Any]] | None = None

    @property
    def client(self) -> ObsClient:
        if self._client is None:
            self._client = ObsClient()
        return self._client

    # ------------------------------------------------------------------ auth

    def auth_status(self) -> dict[str, Any]:
        load_ninova_env()
        credentials_present = bool(
            (os.getenv("ITU_USERNAME") or os.getenv("NINOVA_USERNAME"))
            and (os.getenv("ITU_PASSWORD") or os.getenv("NINOVA_PASSWORD"))
        )
        status: dict[str, Any] = {"credentials_present": credentials_present}
        if not credentials_present:
            status["authenticated"] = False
            status["message"] = (
                "Set ITU_USERNAME/ITU_PASSWORD (or reuse NINOVA_USERNAME/NINOVA_PASSWORD) "
                "to enable OBS login."
            )
            return status
        try:
            self.client.ensure_logged_in()
            status["authenticated"] = self.client.is_authenticated()
            status["session"] = self.client.session_info()
        except ObsError as exc:
            status["authenticated"] = False
            status["message"] = str(exc)
        return status

    def refresh_session(self) -> dict[str, Any]:
        session = self.client.login(force=True)
        self._semesters = None
        self._programs = None
        return {"authenticated": True, "session": session}

    # ------------------------------------------------------- shared lookups

    def semesters(self, refresh: bool = False) -> list[dict[str, Any]]:
        if self._semesters is None or refresh:
            data, _meta = _envelope(
                self.client.api_get("/api/ogrenci/DonemListesi/"), "ogrenciDonemListesi"
            )
            self._semesters = [
                {
                    "semester_id": item.get("akademikDonemId"),
                    "code": item.get("donemKodu"),
                    "name": item.get("akademikDonemAdi"),
                    "name_en": item.get("akademikDonemAdiEN"),
                    "is_current": bool(item.get("sonAkademikDurumDonemId")),
                }
                for item in (data or [])
            ]
        return self._semesters

    def list_semesters(self, refresh: bool = True) -> dict[str, Any]:
        entries = self.semesters(refresh=refresh)
        return {
            "count": len(entries),
            "current": self.current_semester(),
            "semesters": entries,
        }

    def current_semester(self) -> dict[str, Any] | None:
        entries = self.semesters()
        for entry in entries:
            if entry["is_current"]:
                return entry
        return entries[-1] if entries else None

    def resolve_semester(self, semester: str | int | None) -> dict[str, Any]:
        """Accept a semester id, a term code like ``202620``, or a name fragment."""
        entries = self.semesters()
        if not entries:
            raise ObsError("OBS returned no semesters for this account.")
        if semester is None or semester == "":
            current = self.current_semester()
            if current is None:
                raise ObsError("Could not determine the current semester.")
            return current

        needle = str(semester).strip()
        for entry in entries:
            if str(entry["semester_id"]) == needle or str(entry["code"]) == needle:
                return entry

        lowered = needle.casefold()
        matches = [
            entry
            for entry in entries
            if lowered in (entry["name"] or "").casefold()
            or lowered in (entry["name_en"] or "").casefold()
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            names = ", ".join(f"{m['code']} ({m['name']})" for m in matches)
            raise ObsError(f"Semester '{semester}' is ambiguous. Candidates: {names}")
        codes = ", ".join(str(entry["code"]) for entry in entries)
        raise ObsError(f"Unknown semester '{semester}'. Known term codes: {codes}")

    def programs(self, refresh: bool = False) -> list[dict[str, Any]]:
        if self._programs is None or refresh:
            data, _meta = _envelope(
                self.client.api_get("/api/ogrenci/OgrenciProgamListesi/"),
                "ogrenciProgramBilgiListesi",
            )
            self._programs = [
                {
                    "program_id": item.get("ogrenciProgramId"),
                    "name": item.get("programAdi"),
                    "student_number": item.get("ogrenciNo"),
                }
                for item in (data or [])
            ]
        return self._programs

    def resolve_program(self, program: str | int | None) -> dict[str, Any]:
        entries = self.programs()
        if not entries:
            raise ObsError("OBS returned no academic programs for this account.")
        if program is None or program == "":
            return entries[0]
        needle = str(program).strip()
        for entry in entries:
            if str(entry["program_id"]) == needle:
                return entry
        lowered = needle.casefold()
        matches = [entry for entry in entries if lowered in (entry["name"] or "").casefold()]
        if len(matches) == 1:
            return matches[0]
        names = ", ".join(f"{e['program_id']} ({e['name']})" for e in entries)
        raise ObsError(f"Unknown program '{program}'. Known programs: {names}")

    # ------------------------------------------------------------- profile

    def get_profile(self) -> dict[str, Any]:
        personal, personal_meta = _envelope(
            self.client.api_get("/api/ogrenci/KisiselBilgiler/"), "kisiselBilgiler"
        )
        identities, _ = _envelope(
            self.client.api_get("/api/ogrenci/OgrenciYetkiListesi"), "kisiYetkiListesi"
        )
        advisors, _ = _envelope(
            self.client.api_get("/api/ogrenci/DanismanBilgi"), "ogrenciDanismanBilgiList"
        )

        personal = personal or {}
        programs = self.programs()
        standing = self.get_academic_standing()
        latest = next(
            (record["latest"] for record in standing["records"] if record.get("latest")), None
        )
        profile = {
            "name": personal.get("adSoyad"),
            "student_number": None,
            "faculty": personal.get("fakulteTR"),
            "faculty_en": personal.get("fakulteEN"),
            "department": personal.get("bolumAdiTR"),
            "department_en": personal.get("bolumAdiEN"),
            "program": programs[0]["name"] if programs else None,
            "class_level": latest["class_level"] if latest else None,
            "cumulative_gpa": latest["cumulative_gpa"] if latest else None,
            "email": personal.get("ituePosta") or personal.get("ePosta") or None,
            "phone": personal.get("telefon") or None,
            "has_secondary_program": bool(personal.get("ikincilProgramiVar")),
            "secondary_department": personal.get("ikincilBolumAdiTR") or None,
        }
        identity_list = [
            {
                "student_number": item.get("ogrenciNo"),
                "level": item.get("programSeviyesiTR"),
                "level_en": item.get("programSeviyesiEN"),
                "level_key": item.get("programSeviyeAnahtari"),
                "label": item.get("yetkiAdiTR"),
            }
            for item in (identities or [])
        ]
        if identity_list:
            profile["student_number"] = identity_list[0]["student_number"]

        return {
            "ok": _ok(personal_meta),
            "profile": profile,
            "registration_status": self.get_registration_status()["records"],
            "identities": identity_list,
            "advisors": [
                {
                    "name": " ".join(filter(None, [item.get("unvanAdiTR"), item.get("adi"), item.get("soyadi")])),
                    "role": item.get("danismanTipiAnahtari"),
                    "unit": item.get("akademikBirimAdiTR") or item.get("birimi"),
                }
                for item in (advisors or [])
            ],
            "programs": self.programs(),
        }

    # -------------------------------------------------------- graduation

    def get_graduation_progress(
        self,
        program: str | int | None = None,
        *,
        include_courses: bool = True,
        include_raw: bool = False,
    ) -> dict[str, Any]:
        """"Mezuniyetime Ne Kaldı" — plan requirements vs. what has been earned."""
        selected = self.resolve_program(program)
        payload = self.client.api_get(
            f"/api/ogrenci/MezuniyetimeNeKaldi/{selected['program_id']}"
        )
        data, meta = _envelope(payload, "mezuniyetimeNeKaldiBilgi")
        if not data:
            return {
                "ok": False,
                "program": selected,
                "message": meta.get("result_message") or "OBS returned no graduation data.",
            }

        plan_raw = data.get("dersPlaniVM") or {}
        required_credits = _decimal(plan_raw.get("gerekliMezuniyetKredisi"))
        earned_credits = _decimal(data.get("metKrediTotal")) or 0.0
        min_gpa = _decimal(plan_raw.get("gerekliMinGPA"))
        gpa = _decimal(data.get("gpa"))
        required_internship = plan_raw.get("gerekliStajGunu")
        done_internship = data.get("ogrenciTamamlananStaj")

        plan_entries = data.get("checkMetMezuniyetList") or []
        completed = [self._plan_entry(item) for item in plan_entries if item.get("isMet")]
        remaining = [self._plan_entry(item) for item in plan_entries if not item.get("isMet")]
        remaining_credits_from_plan = round(
            sum(entry["credits"] or 0.0 for entry in remaining), 2
        )

        total_courses = data.get("toplamDersSayisi") or len(plan_entries)
        done_courses = data.get("tamamlananDersSayisi") or len(completed)

        summary = {
            "gpa": gpa,
            "required_min_gpa": min_gpa,
            "gpa_requirement_met": None if gpa is None or min_gpa is None else gpa >= min_gpa,
            "akts": _decimal(data.get("akts")),
            "earned_credits": earned_credits,
            "required_credits": required_credits,
            "remaining_credits": (
                round(max(required_credits - earned_credits, 0.0), 2)
                if required_credits is not None
                else None
            ),
            "remaining_plan_credits": remaining_credits_from_plan,
            "completed_courses": done_courses,
            "total_courses": total_courses,
            "remaining_courses": len(remaining),
            "credit_completion_percent": (
                round(earned_credits / required_credits * 100, 1)
                if required_credits
                else None
            ),
            "internship_days_completed": done_internship,
            "internship_days_required": required_internship,
            "internship_requirement_met": (
                None
                if done_internship is None or required_internship is None
                else done_internship >= required_internship
            ),
        }

        result: dict[str, Any] = {
            "ok": _ok(meta),
            "program": selected,
            "plan": {
                "plan_id": plan_raw.get("planId"),
                "title": plan_raw.get("dersPlaniBaslik"),
                "department": plan_raw.get("akademikBolumAdiTR"),
                "program": plan_raw.get("akademikProgramAdiTR"),
                "semester_count": plan_raw.get("akademikDonemSayisi"),
                "required_credits": required_credits,
                "required_english_credits": _decimal(plan_raw.get("gerekliIngilizceKredi")),
                "required_min_gpa": min_gpa,
                "required_internship_days": required_internship,
            },
            "summary": summary,
        }

        if include_courses:
            result["completed"] = completed
            result["remaining"] = remaining
            result["remaining_by_plan_semester"] = self._group_by_semester(remaining)
            result["unused_courses"] = [
                {
                    "code": (item.get("bransKodu") or "").strip(),
                    "name": item.get("dersAdi"),
                    "credits": _decimal(item.get("kredisi")),
                    "letter_grade": item.get("harfNotu"),
                    "term": item.get("donem"),
                    "crn": item.get("crn"),
                    "can_still_count": bool(item.get("isValidAndUnused")),
                }
                for item in (data.get("unusedSinifOgrenciList") or [])
            ]
        if include_raw:
            result["raw"] = data
        return result

    def _plan_entry(self, item: dict[str, Any]) -> dict[str, Any]:
        code = (item.get("bransKodu") or "").strip()
        group = (item.get("grupName") or "").strip()
        return {
            "plan_semester": item.get("donemNo"),
            "order": item.get("siraNo"),
            "code": code or None,
            "name": item.get("dersAdi"),
            "credits": _decimal(item.get("kredisiDec")) or _decimal(item.get("kredisi")),
            "elective_group": group or None,
            "elective_group_id": item.get("grupId"),
            "is_elective_slot": bool(group) and not code,
            "letter_grade": (item.get("harfNotu") or "").strip() or None,
            "counted_credits": _decimal(item.get("sayilanKredi")),
            "taken_term": item.get("donem"),
            "crn": item.get("crn"),
        }

    def _group_by_semester(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        buckets: dict[Any, list[dict[str, Any]]] = {}
        for entry in entries:
            buckets.setdefault(entry["plan_semester"], []).append(entry)
        grouped = []
        for semester in sorted(buckets, key=lambda value: (value is None, value)):
            courses = buckets[semester]
            grouped.append(
                {
                    "plan_semester": semester,
                    "course_count": len(courses),
                    "credits": round(sum(c["credits"] or 0.0 for c in courses), 2),
                    "courses": courses,
                }
            )
        return grouped

    # ------------------------------------------------------------- academics

    def get_academic_standing(self) -> dict[str, Any]:
        """Semester-by-semester GPA/credit history plus the latest snapshot."""
        data, meta = _envelope(
            self.client.api_get("/api/ogrenci/KayitDurumu"), "kayitDurumuList"
        )
        blocks = []
        for item in data or []:
            semesters = [
                {
                    "code": row.get("akademikDonemKodu"),
                    "name": row.get("akademikDonemAdi"),
                    "class_level": row.get("sinifSeviye"),
                    "credits": _decimal(row.get("verilenKredi")),
                    "semester_gpa": _decimal(row.get("donemlikNotOrtalamasi")),
                    "cumulative_gpa": _decimal(row.get("genelNotOrtalamasi")),
                }
                for row in (item.get("kayitDurumuDonemList") or [])
            ]
            semesters.sort(key=lambda row: str(row["code"] or ""))
            blocks.append(
                {
                    "scope": item.get("akademikBolumAdi") or item.get("akademikProgramAdi"),
                    "scope_en": item.get("akademikBolumAdiEN") or item.get("akademikProgramAdiEN"),
                    "status": item.get("durum") or item.get("durumKodu"),
                    "semesters": semesters,
                    "latest": semesters[-1] if semesters else None,
                }
            )
        return {"ok": _ok(meta), "records": blocks}

    def get_semester_status(self, semester: str | int | None = None) -> dict[str, Any]:
        """The ``AkademikDurum`` snapshot for one semester."""
        target = self.resolve_semester(semester)
        data, meta = _envelope(
            self.client.api_get(f"/api/ogrenci/AkademikDurum/{target['semester_id']}"),
            "akademikDurum",
        )
        if not data:
            return {
                "ok": False,
                "semester": target,
                "message": meta.get("result_message") or "No academic status for this semester.",
            }
        return {
            "ok": _ok(meta),
            "semester": target,
            "status": {
                "class_level": data.get("sinifSeviye"),
                "attempted_credits": _decimal(data.get("verilenKredi")),
                "earned_credits": _decimal(data.get("alinanKredi")),
                "total_attempted_credits": _decimal(data.get("toplamVerilenKredi")),
                "cumulative_gpa": _decimal(data.get("genelNotOrtalamasi")),
                "semester_gpa": _decimal(data.get("donemlikNotOrtalamasi")),
                "graduated": bool(data.get("mezuniyet")),
            },
        }

    def get_grades(self, semester: str | int | None = None) -> dict[str, Any]:
        """Final letter grades for one semester."""
        target = self.resolve_semester(semester)
        data, meta = _envelope(
            self.client.api_get(f"/api/ogrenci/Sinif/SinifHarfNotuListesi/{target['semester_id']}"),
            "sinifHarfNotuResultList",
        )
        courses = [
            {
                "code": _course_code(item.get("bransKodu"), item.get("dersNo"))
                or item.get("dersKodu"),
                "name": item.get("dersAdiEN") or item.get("dersAdiTR"),
                "name_tr": (item.get("dersAdiTR") or "").strip(),
                "letter_grade": (item.get("harfNotu") or "").strip() or None,
                "crn": item.get("crn"),
                "language": item.get("dersDilKodu"),
            }
            for item in (data or [])
        ]
        for course in courses:
            grade = course["letter_grade"]
            course["grade_points"] = GRADE_POINTS.get(grade or "")
            course["passed"] = (
                None
                if not grade
                else grade in NON_GRADED_PASS or grade not in FAILING_GRADES
            )
        return {
            "ok": _ok(meta),
            "semester": target,
            "count": len(courses),
            "courses": courses,
        }

    def get_interim_grades(
        self,
        semester: str | int | None = None,
        *,
        course: str | None = None,
    ) -> dict[str, Any]:
        """In-term grades (midterm, quizzes, final) for a semester's courses.

        The endpoint is keyed by *class* id, not semester id, so the registered
        courses are resolved first and each class is fetched in turn. Every
        component comes with the class mean, standard deviation and the
        student's rank, which is what makes a letter-grade estimate possible
        before grades are posted.
        """
        target = self.resolve_semester(semester)
        registered = self.get_registered_courses(target["semester_id"])["courses"]
        if course:
            wanted = normalize_code(course).replace(" ", "").upper()
            registered = [
                item
                for item in registered
                if (item["code"] or "").replace(" ", "").upper() == wanted
                or wanted in (item["name"] or "").upper().replace(" ", "")
            ]
            if not registered:
                raise ObsError(f"No registered course matching '{course}' in {target['code']}.")

        courses = []
        for item in registered:
            payload = self.client.api_get(
                f"/api/ogrenci/Sinif/SinifDonemIciNotListesi/{item['class_id']}"
            )
            data, meta = _envelope(payload, "sinifDonemIciNotListesi")
            components = [
                {
                    "name": row.get("degerlendirmeOlcutuAdi"),
                    "note": row.get("aciklama"),
                    "score": _decimal(row.get("not")),
                    "class_mean": _decimal(row.get("ortalama")),
                    "std_dev": _decimal(row.get("standartSapma")),
                    "rank": row.get("sinifSirasi"),
                    "student_count": row.get("ogrenciSayisi"),
                    "weight_percent": _decimal(row.get("degerlendirmeKatkisi")),
                }
                for row in (data or [])
            ]
            for component in components:
                std = component["std_dev"]
                component["z_score"] = (
                    round((component["score"] - component["class_mean"]) / std, 3)
                    if std and component["score"] is not None and component["class_mean"] is not None
                    else None
                )
            courses.append(
                {
                    "code": item["code"],
                    "name": item["name"],
                    "class_id": item["class_id"],
                    "crn": item["crn"],
                    "ok": _ok(meta),
                    "weighted_total": _decimal(
                        payload.get("ortalama") if isinstance(payload, dict) else None
                    ),
                    "components": components,
                    **self._weighted_position(components),
                }
            )
        return {"semester": target, "count": len(courses), "courses": courses}

    @staticmethod
    def _weighted_position(components: list[dict[str, Any]]) -> dict[str, Any]:
        """Where the student sits against the class, across all components.

        The spread of the weighted total depends on how correlated the
        components are, which OBS does not publish. Both extremes are reported
        rather than a single made-up number: independent components give the
        narrowest spread, perfectly correlated ones the widest, and the truth
        sits between.
        """
        usable = [
            component
            for component in components
            if component["score"] is not None
            and component["class_mean"] is not None
            and component["std_dev"]
            and component["weight_percent"]
        ]
        if not usable:
            return {"class_weighted_mean": None, "z_score_range": None}

        weight_total = sum(component["weight_percent"] for component in usable) / 100
        student = sum(c["score"] * c["weight_percent"] / 100 for c in usable)
        class_mean = sum(c["class_mean"] * c["weight_percent"] / 100 for c in usable)
        linear = sum(c["std_dev"] * c["weight_percent"] / 100 for c in usable)
        quadratic = sum((c["std_dev"] * c["weight_percent"] / 100) ** 2 for c in usable)

        gap = student - class_mean
        independent = quadratic**0.5
        correlated = linear
        return {
            "weights_cover_percent": round(weight_total * 100, 2),
            "class_weighted_mean": round(class_mean, 2),
            "gap_to_class_mean": round(gap, 2),
            "z_score_range": {
                "if_independent": round(gap / independent, 3) if independent else None,
                "if_correlated": round(gap / correlated, 3) if correlated else None,
            },
        }

    def get_course_history(self, *, include_empty: bool = False) -> dict[str, Any]:
        """Every graded course across every semester the student has attended."""
        history = []
        all_courses: list[dict[str, Any]] = []
        for entry in self.semesters():
            grades = self.get_grades(entry["semester_id"])
            courses = grades["courses"]
            if not courses and not include_empty:
                continue
            for course in courses:
                enriched = dict(course)
                enriched["term"] = entry["code"]
                enriched["semester_name"] = entry["name"]
                all_courses.append(enriched)
            history.append(
                {
                    "semester": entry,
                    "count": len(courses),
                    "courses": courses,
                }
            )

        passed = [c for c in all_courses if c["passed"] is True]
        failed = [c for c in all_courses if c["passed"] is False]
        return {
            "semester_count": len(history),
            "course_count": len(all_courses),
            "passed_count": len(passed),
            "failed_count": len(failed),
            "failed_courses": [
                {"code": c["code"], "name": c["name"], "term": c["term"], "letter_grade": c["letter_grade"]}
                for c in failed
            ],
            "semesters": history,
        }

    def get_registered_courses(self, semester: str | int | None = None) -> dict[str, Any]:
        target = self.resolve_semester(semester)
        data, meta = _envelope(
            self.client.api_get(f"/api/ogrenci/sinif/KayitliSinifListesi/{target['semester_id']}"),
            "kayitSinifResultList",
        )
        courses = [
            {
                "class_id": item.get("sinifId"),
                "crn": item.get("crn"),
                "code": _course_code(item.get("bransKodu"), item.get("dersKodu")),
                "name": item.get("dersAdiEN") or item.get("dersAdiTR"),
                "name_tr": item.get("dersAdiTR"),
                "language": item.get("dersDilKodu"),
                "term": item.get("donem"),
                "place_time": item.get("yerZamanBilgiEN") or item.get("yerZamanBilgiTR"),
            }
            for item in (data or [])
        ]
        return {
            "ok": _ok(meta),
            "semester": target,
            "count": len(courses),
            "courses": courses,
        }

    def get_schedule(self, semester: str | int | None = None) -> dict[str, Any]:
        target = self.resolve_semester(semester)
        data, meta = _envelope(
            self.client.api_get(f"/api/ogrenci/Takvim/DersTakvimi/{target['semester_id']}"),
            "kayitSinifResultList",
        )
        meetings = []
        for item in data or []:
            code = _course_code(item.get("bransKodu"), item.get("dersKodu"))
            for slot in item.get("sinifYerZaman") or []:
                meetings.append(
                    {
                        "code": code,
                        "name": item.get("dersAdiEN") or item.get("dersAdiTR"),
                        "crn": item.get("crn"),
                        "day": slot.get("gunAdiEN") or slot.get("gunAdiTR"),
                        "day_tr": slot.get("gunAdiTR"),
                        "start": _minutes_to_clock(slot.get("baslangicSaati")),
                        "end": _minutes_to_clock(slot.get("bitisSaati")),
                        "campus": slot.get("kampusAdi"),
                        "building": slot.get("binaAdi"),
                        "room": slot.get("mekanAdi"),
                    }
                )
        day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        meetings.sort(
            key=lambda m: (
                day_order.index(m["day"]) if m["day"] in day_order else len(day_order),
                m["start"] or "",
            )
        )
        return {
            "ok": _ok(meta),
            "semester": target,
            "count": len(meetings),
            "meetings": meetings,
        }

    def get_exam_schedule(self, semester: str | int | None = None) -> dict[str, Any]:
        target = self.resolve_semester(semester)
        data, meta = _envelope(
            self.client.api_get(f"/api/ogrenci/Takvim/FinalTakvimi?donemId={target['semester_id']}"),
            "finalBilgiList",
        )
        exams = [
            {
                "code": item.get("dersKodu"),
                "name": item.get("dersAdiEN") or item.get("dersAdiTR"),
                "crn": item.get("crn"),
                "starts_at": item.get("baslangicTarihi"),
                "ends_at": item.get("bitisTarihi"),
                "rooms": [
                    " ".join(filter(None, [room.get("binaAdi"), room.get("derslikKodu")]))
                    for room in (item.get("finalMekanList") or [])
                ],
            }
            for item in (data or [])
        ]
        exams.sort(key=lambda item: item["starts_at"] or "")
        return {"ok": _ok(meta), "semester": target, "count": len(exams), "exams": exams}

    def get_attendance(self, class_id: int) -> dict[str, Any]:
        data, meta = _envelope(
            self.client.api_get(f"/api/ogrenci/Sinif/SinifOgrenciYoklama/{class_id}"),
            "sinifOgrenciYoklama",
        )
        return {
            "ok": _ok(meta) and data is not None,
            "class_id": class_id,
            "message": meta.get("result_message"),
            "attendance": data,
        }

    def get_registration_status(self) -> dict[str, Any]:
        data, meta = _envelope(
            self.client.api_get("/api/ogrenci/DersKayitDurumu"), "dersKayitDurumuList"
        )
        records = [
            {
                "program": item.get("akademikProgramAdi"),
                "department": item.get("akademikBolumAdi"),
                "faculty": item.get("fakulteAdi"),
                "status": item.get("durum"),
                "semesters": [
                    {
                        "code": row.get("akademikDonemKodu"),
                        "name": row.get("akademikDonemAdi"),
                        "registration_state": row.get("dersKayitDurumu"),
                    }
                    for row in (item.get("dersKayitDurumuDonemList") or [])
                ],
            }
            for item in (data or [])
        ]
        return {"ok": _ok(meta), "records": records}

    def get_internships(self) -> dict[str, Any]:
        data, meta = _envelope(
            self.client.api_get("/api/ogrenci/StajBilgi"), "ogrenciStajBilgiList"
        )
        return {"ok": _ok(meta), "count": len(data or []), "internships": data or []}

    def get_announcements(self, limit: int = 20, page: int = 1) -> dict[str, Any]:
        data, meta = _envelope(
            self.client.api_get(f"/api/ogrenci/Duyuru/Liste/{page}/{limit}"), "duyuruOzetList"
        )
        return {"ok": _ok(meta), "count": len(data or []), "announcements": data or []}

    def get_transcript(
        self,
        *,
        language: str = "tr",
        output_dir: str | None = None,
        filename: str | None = None,
    ) -> dict[str, Any]:
        """Download the official transcript preview PDF."""
        normalized = (language or "tr").strip().lower()
        if normalized in {"en", "eng", "english", "ingilizce"}:
            path = "/api/ogrenci/Belgeler/TranskriptIngilizceOnizleme"
            suffix = "en"
        else:
            path = "/api/ogrenci/Belgeler/TranskriptOnizleme"
            suffix = "tr"

        payload = self.client.api_get(path)
        encoded = (payload or {}).get("belgeAsByteArray") if isinstance(payload, dict) else None
        _data, meta = _envelope(payload, "belgeAsByteArray")
        if not encoded:
            return {
                "ok": False,
                "message": meta.get("result_message") or "OBS returned no transcript document.",
            }

        content = base64.b64decode(encoded)
        target_dir = Path(output_dir or (Path.cwd() / "downloads"))
        target_dir.mkdir(parents=True, exist_ok=True)
        name = filename or f"transkript-{suffix}.pdf"
        if not re.search(r"\.pdf$", name, re.IGNORECASE):
            name = f"{name}.pdf"
        target = target_dir / name
        target.write_bytes(content)
        return {
            "ok": True,
            "language": suffix,
            "path": str(target),
            "bytes": len(content),
        }

    # --------------------------------------------------------- prerequisites

    def passed_courses(self) -> dict[str, Any]:
        """Every course passed, keyed by collapsed code with the best grade kept.

        Repeats matter here: a course failed in one term and passed in another
        must count as passed, and the higher grade is the one a prerequisite is
        checked against.
        """
        best: dict[str, dict[str, Any]] = {}
        history = self.get_course_history()
        for semester in history["semesters"]:
            for course in semester["courses"]:
                if course["passed"] is not True or not course["code"]:
                    continue
                key = base_code(course["code"])
                points = course["grade_points"]
                current = best.get(key)
                # A non-graded pass (BL/MU) has no points; never let it displace
                # a graded pass, but keep it when nothing better exists.
                if current is None or (points is not None and (current["points"] is None or points > current["points"])):
                    best[key] = {
                        "code": course["code"],
                        "base_code": key,
                        "points": points,
                        "letter_grade": course["letter_grade"],
                        "term": semester["semester"]["code"],
                    }
        return best

    def _course_name_map(self, graduation: dict[str, Any]) -> dict[str, str]:
        """``{collapsed code: course name}`` from the plan and the student's record."""
        names: dict[str, str] = {}
        for bucket in ("completed", "remaining"):
            for entry in graduation.get(bucket, []):
                if entry["code"] and entry["name"]:
                    names.setdefault(base_code(entry["code"]), entry["name"])
        for entry in graduation.get("unused_courses", []):
            if entry["code"] and entry["name"]:
                names.setdefault(base_code(entry["code"]), entry["name"])
        return names

    def _equivalence_map(
        self,
        public: "ObsPublicClient",
        program_id: int,
        plan_type_id: int,
        plan_id: int,
        branches: list[str],
    ) -> dict[str, set[str]]:
        """``{plan course -> {codes that satisfy it}}``, in collapsed form.

        Recorded in both directions: a prerequisite may name either the plan
        course or the course that replaced it.
        """
        mapping: dict[str, set[str]] = {}
        for branch in branches:
            try:
                payload = public.get_equivalences(
                    program_id=program_id,
                    plan_type_id=plan_type_id,
                    plan_id=plan_id,
                    branch=branch,
                )
            except ObsError:
                continue
            for pair in payload["equivalences"]:
                plan_key = base_code(pair["plan_course"])
                equivalents = {base_code(code) for code in pair["equivalents"]}
                mapping.setdefault(plan_key, set()).update(equivalents)
                for equivalent in equivalents:
                    mapping.setdefault(equivalent, set()).add(plan_key)
        return mapping

    @staticmethod
    def _parse_assumed(assume_passed: list[str] | None) -> dict[str, str]:
        """``["YZV 201E", "MAT 104E:BB"]`` -> ``{"YZV 201": "DD", "MAT 104": "BB"}``.

        A grade earned in a term OBS has not posted yet is invisible to every
        endpoint, so planning needs a way to say "treat this as passed". The
        default is DD: the lowest passing grade, and therefore the assumption
        that claims the least.
        """
        assumed: dict[str, str] = {}
        for item in assume_passed or []:
            code, _, grade = str(item).partition(":")
            grade = (grade or "DD").strip().upper()
            if grade not in GRADE_POINTS:
                raise ObsError(
                    f"'{grade}' is not an ITU letter grade. Use one of: {', '.join(GRADE_POINTS)}."
                )
            assumed[base_code(code)] = grade
        return assumed

    def check_prerequisites(
        self,
        codes: list[str] | None = None,
        *,
        program: str | int | None = None,
        assume_passed: list[str] | None = None,
        public_client: "ObsPublicClient | None" = None,
    ) -> dict[str, Any]:
        """Decide which courses the student may actually register for.

        Combines three sources that OBS keeps apart: the student's own passed
        courses, the course-plan equivalences for their plan, and the
        prerequisite expressions — which OBS publishes only for the programme's
        *current* definitions, so an older cohort is assessed against the latest
        rules even though their plan is older.
        """
        from .obs_public import ObsPublicClient

        public = public_client or ObsPublicClient()
        graduation = self.get_graduation_progress()
        plan_id = graduation["plan"]["plan_id"]
        earned_credits = graduation["summary"]["earned_credits"]

        assumed_keys = set(self._parse_assumed(assume_passed))
        targets = list(codes or [])
        if not targets:
            targets = [
                entry["code"]
                for entry in graduation["remaining"]
                if entry["code"] and base_code(entry["code"]) not in assumed_keys
            ]
        targets = [normalize_code(code) for code in targets]
        if not targets:
            raise ObsError("No courses to check.")

        program_entry = public.resolve_equivalence_program(
            program or graduation["plan"]["program"] or "Yapay Zeka"
        )
        plan_types = public.equivalence_plan_types(program_entry["program_id"])
        plan_type_id = next(
            (item["plan_type_id"] for item in plan_types if item["name"] == "Lisans"),
            plan_types[0]["plan_type_id"] if plan_types else 2,
        )

        passed = self.passed_courses()
        assumed = self._parse_assumed(assume_passed)
        for key, grade in assumed.items():
            passed.setdefault(
                key,
                {
                    "code": key,
                    "base_code": key,
                    "points": GRADE_POINTS[grade],
                    "letter_grade": grade,
                    "term": "assumed",
                },
            )

        prerequisites: dict[str, dict[str, Any]] = {}
        parsed: dict[str, Any] = {}
        for branch in sorted({code.split(" ")[0] for code in targets}):
            try:
                payload = public.get_prerequisites(branch)
            except ObsError:
                continue
            for course in payload["courses"]:
                for code in course["codes"]:
                    prerequisites[base_code(code)] = course

        atom_branches: set[str] = set()
        for record in prerequisites.values():
            try:
                tree = parse(record["expression"])
            except PrerequisiteParseError:
                tree = None
            parsed[id(record)] = tree
            for atom in iter_atoms(tree):
                atom_branches.add(atom.code.split(" ")[0])

        # A blocker reported as a bare code is not actionable, so pull in the
        # branches named inside the expressions purely to learn course names.
        names = self._course_name_map(graduation)
        for branch in sorted(atom_branches - {code.split(" ")[0] for code in targets}):
            try:
                payload = public.get_prerequisites(branch)
            except ObsError:
                continue
            for course in payload["courses"]:
                for code in course["codes"]:
                    names.setdefault(base_code(code), _english_name(course["name"]))

        # Equivalences must cover every branch that can appear on either side of
        # a comparison: the targets, whatever the student has passed, and every
        # course named inside a prerequisite expression. Missing any of these
        # silently reports a satisfied prerequisite as unmet.
        branches = sorted(
            {code.split(" ")[0] for code in targets}
            | {code.split(" ")[0] for code in passed}
            | atom_branches
        )
        equivalences = self._equivalence_map(
            public, program_entry["program_id"], plan_type_id, plan_id, branches
        )

        # Expand the passed set through equivalences so an old code still counts.
        expanded: dict[str, float | None] = {key: entry["points"] for key, entry in passed.items()}
        substitutions: dict[str, str] = {}
        for key, entry in list(passed.items()):
            for alias in equivalences.get(key, set()):
                if alias not in expanded:
                    expanded[alias] = entry["points"]
                    substitutions[alias] = entry["code"]

        # Missing grade points (BL/MU) still satisfy a minimum-grade atom.
        lookup = {key: (0.0 if value is None else value) for key, value in expanded.items()}

        results = []
        for code in targets:
            record = prerequisites.get(base_code(code))
            if record is None:
                results.append(
                    {
                        "code": code,
                        "name": names.get(base_code(code)),
                        "eligible": True,
                        "reason": "No prerequisites published for this course.",
                        "expression": None,
                        "missing": [],
                    }
                )
                continue

            tree = parsed.get(id(record))
            if tree is None and record["expression"]:
                results.append(
                    {
                        "code": code,
                        "eligible": None,
                        "reason": "Could not parse the published prerequisite expression.",
                        "expression": record["expression"],
                        "missing": [],
                    }
                )
                continue

            verdict = evaluate(tree, lookup, GRADE_POINTS)
            required_credits = record["min_completed_credits"]
            credit_ok = (
                True
                if required_credits is None or earned_credits is None
                else earned_credits >= required_credits
            )

            def describe(entry: dict[str, Any]) -> dict[str, Any]:
                course_name = names.get(base_code(entry["code"]))
                return {
                    "code": entry["code"],
                    "name": course_name,
                    "min_grade": entry["min_grade"],
                    "label": (
                        f"{entry['code']} — {course_name} (min {entry['min_grade']})"
                        if course_name
                        else entry["label"]
                    ),
                }

            blockers = [describe(entry) for entry in verdict["missing"]]
            if not credit_ok:
                blockers.append(
                    {
                        "code": None,
                        "name": None,
                        "min_grade": None,
                        "label": (
                            f"{required_credits:g} completed credits required, "
                            f"{earned_credits:g} earned"
                        ),
                    }
                )

            satisfied_codes = {base_code(entry["code"]) for entry in verdict["satisfied_by"]}
            used_substitutions = sorted(
                {
                    f"{alias} ({names.get(alias, '?')}) ← {substitutions[alias]}"
                    for alias in substitutions
                    if alias in satisfied_codes
                }
            )

            results.append(
                {
                    "code": code,
                    "name": _english_name(record["name"]),
                    "eligible": bool(verdict["satisfied"] and credit_ok),
                    "expression": record["expression"],
                    "min_completed_credits": required_credits,
                    "satisfied_by": [describe(entry) for entry in verdict["satisfied_by"]],
                    "missing": blockers,
                    "counted_via_equivalence": used_substitutions,
                }
            )

        results.sort(key=lambda item: (item["eligible"] is not True, item["code"]))
        return {
            "plan_id": plan_id,
            "plan_title": graduation["plan"]["title"],
            "earned_credits": earned_credits,
            "note": (
                "Prerequisites are published only for the programme's current definitions, "
                "so they are applied as-is even though the course plan is an older one."
            ),
            "passed_course_count": len(passed),
            "assumed_passed": [
                {"code": key, "name": names.get(key), "assumed_grade": grade}
                for key, grade in sorted(assumed.items())
            ],
            "equivalences_applied": sorted(
                {f"{alias} ← {code}" for alias, code in substitutions.items()}
            ),
            "eligible": [
                {"code": item["code"], "name": item.get("name")}
                for item in results
                if item["eligible"] is True
            ],
            "blocked": [
                {"code": item["code"], "name": item.get("name")}
                for item in results
                if item["eligible"] is False
            ],
            "courses": results,
        }

    def get_registration_options(
        self,
        *,
        level: str = "LS",
        only_eligible: bool = False,
        assume_passed: list[str] | None = None,
        program_code: str | None = None,
        public_client: "ObsPublicClient | None" = None,
    ) -> dict[str, Any]:
        """What the student can actually register for, per remaining plan course.

        A plan course may be offered under a different code than the plan names
        — codes get renamed between cohorts — so each remaining course is
        checked under its own code *and* every code declared equivalent to it.
        """
        from .obs_public import ObsPublicClient

        public = public_client or ObsPublicClient()
        graduation = self.get_graduation_progress()
        eligibility = self.check_prerequisites(
            assume_passed=assume_passed, public_client=public
        )
        verdicts = {base_code(item["code"]): item for item in eligibility["courses"]}
        assumed_keys = set(self._parse_assumed(assume_passed))

        program_entry = public.resolve_equivalence_program(
            graduation["plan"]["program"] or "Yapay Zeka"
        )
        plan_types = public.equivalence_plan_types(program_entry["program_id"])
        plan_type_id = next(
            (item["plan_type_id"] for item in plan_types if item["name"] == "Lisans"),
            plan_types[0]["plan_type_id"] if plan_types else 2,
        )

        remaining = [
            entry
            for entry in graduation["remaining"]
            if entry["code"] and base_code(entry["code"]) not in assumed_keys
        ]
        branches = sorted({entry["code"].split(" ")[0] for entry in remaining})
        equivalences = self._equivalence_map(
            public, program_entry["program_id"], plan_type_id, graduation["plan"]["plan_id"], branches
        )

        term = public.active_term(level)
        offerings: dict[str, list[dict[str, Any]]] = {}
        equivalent_branches = {
            code.split(" ")[0] for codes in equivalences.values() for code in codes
        }
        for branch in sorted(set(branches) | equivalent_branches):
            try:
                schedule = public.get_schedule(branch, level=level)
            except ObsError:
                continue
            for section in schedule["sections"]:
                offerings.setdefault(base_code(section["code"]), []).append(section)

        options = []
        for entry in remaining:
            plan_key = base_code(entry["code"])
            candidate_codes = [plan_key, *sorted(equivalences.get(plan_key, set()))]
            offered = []
            for candidate in candidate_codes:
                for section in offerings.get(candidate, []):
                    if not _section_open_to(section, program_code):
                        continue
                    offered.append(
                        {
                            "code": section["code"],
                            "name": section["name"],
                            "crn": section["crn"],
                            "instructor": section["instructor"],
                            "meetings": section["meetings"],
                            "capacity": section["capacity"],
                            "available": section["available"],
                            "eligible_programs": section["eligible_programs"],
                            # Codes are matched on their collapsed form, so a
                            # Turkish section can surface for an English plan
                            # course (and vice versa). Say which one this is.
                            "exact_code_match": normalize_code(section["code"])
                            == normalize_code(entry["code"]),
                            "satisfies_plan_course_via": (
                                None if candidate == plan_key else candidate
                            ),
                        }
                    )
            offered.sort(key=lambda item: not item["exact_code_match"])
            verdict = verdicts.get(plan_key, {})
            option = {
                "plan_course": entry["code"],
                "plan_semester": entry["plan_semester"],
                "name": entry["name"],
                "credits": entry["credits"],
                "equivalent_codes": sorted(equivalences.get(plan_key, set())),
                "prerequisites_met": verdict.get("eligible"),
                "prerequisite_blockers": verdict.get("missing", []),
                "offered_this_term": bool(offered),
                "sections": offered,
            }
            if only_eligible and not (option["offered_this_term"] and option["prerequisites_met"]):
                continue
            options.append(option)

        options.sort(
            key=lambda item: (
                not (item["offered_this_term"] and item["prerequisites_met"]),
                item["plan_semester"] or 0,
            )
        )
        takeable = [
            {"code": item["plan_course"], "name": item["name"], "credits": item["credits"]}
            for item in options
            if item["offered_this_term"] and item["prerequisites_met"]
        ]
        return {
            "term": term["term"],
            "term_code": term["term_code"],
            "plan_id": graduation["plan"]["plan_id"],
            "note": eligibility["note"],
            "assumed_passed": eligibility["assumed_passed"],
            "takeable_now": takeable,
            "elective_slots": [
                {
                    "plan_semester": entry["plan_semester"],
                    "group": entry["elective_group"],
                    "group_id": entry["elective_group_id"],
                    "credits": entry["credits"],
                }
                for entry in graduation["remaining"]
                if not entry["code"]
            ],
            "options": options,
        }

    def get_elective_pool(self, group_id: int) -> dict[str, Any]:
        """The courses that can fill one elective slot of the student's plan."""
        data, meta = _envelope(
            self.client.api_get(f"/api/ogrenci/GrupDersBilgi/{group_id}"), "grupDersBilgiList"
        )
        courses = [
            {
                "code": normalize_code(item.get("dersKodu")),
                "name": item.get("dersAdi"),
                "credits": _decimal(item.get("kredisi")),
                "ects": _decimal(item.get("aktsKredisi")),
            }
            for item in (data or [])
        ]
        return {"group_id": group_id, "ok": _ok(meta), "count": len(courses), "courses": courses}

    def get_elective_options(
        self,
        *,
        level: str = "LS",
        assume_passed: list[str] | None = None,
        only_offered: bool = True,
        check_prerequisites: bool = True,
        program_code: str | None = None,
        public_client: "ObsPublicClient | None" = None,
    ) -> dict[str, Any]:
        """For each unfilled elective slot: the pool, and what of it runs this term."""
        from .obs_public import ObsPublicClient

        public = public_client or ObsPublicClient()
        graduation = self.get_graduation_progress()
        assumed_keys = set(self._parse_assumed(assume_passed))
        already_passed = set(self.passed_courses()) | assumed_keys

        slots = [
            entry
            for entry in graduation["remaining"]
            if not entry["code"] and entry["elective_group_id"]
        ]

        pools: dict[int, list[dict[str, Any]]] = {}
        for slot in slots:
            group_id = slot["elective_group_id"]
            if group_id not in pools:
                pools[group_id] = self.get_elective_pool(group_id)["courses"]

        # One schedule fetch per branch, shared across every slot that needs it.
        branches = sorted(
            {course["code"].split(" ")[0] for pool in pools.values() for course in pool}
        )
        offerings: dict[str, list[dict[str, Any]]] = {}
        for branch in branches:
            try:
                schedule = public.get_schedule(branch, level=level)
            except ObsError:
                continue
            for section in schedule["sections"]:
                offerings.setdefault(base_code(section["code"]), []).append(section)

        offered_codes = sorted(
            {
                course["code"]
                for pool in pools.values()
                for course in pool
                if offerings.get(base_code(course["code"]))
            }
        )
        verdicts: dict[str, dict[str, Any]] = {}
        if check_prerequisites and offered_codes:
            report = self.check_prerequisites(
                codes=offered_codes, assume_passed=assume_passed, public_client=public
            )
            verdicts = {base_code(item["code"]): item for item in report["courses"]}

        result_slots = []
        for slot in slots:
            options = []
            for course in pools[slot["elective_group_id"]]:
                key = base_code(course["code"])
                sections = [
                    section
                    for section in offerings.get(key, [])
                    if _section_open_to(section, program_code)
                ]
                # A Turkish and an English section share a collapsed code, so
                # keep the one whose code the pool actually names.
                sections.sort(
                    key=lambda section: normalize_code(section["code"])
                    != normalize_code(course["code"])
                )
                if only_offered and not sections:
                    continue
                verdict = verdicts.get(key, {})
                options.append(
                    {
                        "code": course["code"],
                        "name": course["name"],
                        "credits": course["credits"],
                        "ects": course["ects"],
                        "already_passed": key in already_passed,
                        "offered_this_term": bool(sections),
                        "prerequisites_met": verdict.get("eligible"),
                        "prerequisite_blockers": verdict.get("missing", []),
                        "sections": [
                            {
                                "crn": section["crn"],
                                "code": section["code"],
                                "name": section["name"],
                                "instructor": section["instructor"],
                                "meetings": section["meetings"],
                                "capacity": section["capacity"],
                                "available": section["available"],
                                "eligible_programs": section["eligible_programs"],
                                "exact_code_match": normalize_code(section["code"])
                                == normalize_code(course["code"]),
                            }
                            for section in sections
                        ],
                    }
                )
            options.sort(
                key=lambda item: (
                    item["already_passed"],
                    item["prerequisites_met"] is False,
                    item["code"],
                )
            )
            result_slots.append(
                {
                    "plan_semester": slot["plan_semester"],
                    "group": slot["elective_group"],
                    "group_id": slot["elective_group_id"],
                    "credits_required": slot["credits"],
                    "pool_size": len(pools[slot["elective_group_id"]]),
                    "option_count": len(options),
                    "takeable_now": [
                        option["code"]
                        for option in options
                        if option["offered_this_term"]
                        and option["prerequisites_met"] is not False
                        and not option["already_passed"]
                    ],
                    "options": options,
                }
            )

        term = public.active_term(level)
        return {
            "term": term["term"],
            "term_code": term["term_code"],
            "slot_count": len(result_slots),
            "slots": result_slots,
        }

    def gpa_basis(
        self, *, public_client: "ObsPublicClient | None" = None
    ) -> dict[str, Any]:
        """Reconstruct which attempts currently count toward the GPA.

        ITU counts only the *last* attempt of a course, and courses declared
        equivalent to each other are the same course for this purpose — so a
        ``MAT 281`` failure replaced by a later ``BBF 102`` pass drops out of the
        average entirely. The reconstructed GPA is returned next to the one OBS
        reports so a caller can see whether the model still agrees.
        """
        from .obs_public import ObsPublicClient

        public = public_client or ObsPublicClient()
        graduation = self.get_graduation_progress(include_raw=True)
        raw = graduation["raw"]

        attempts: list[tuple[str, str, str, float]] = []
        for item in raw.get("checkMetMezuniyetList") or []:
            if item.get("harfNotu") and item.get("donem"):
                attempts.append(
                    (item["donem"], base_code(item["bransKodu"]), item["harfNotu"],
                     _decimal(item.get("kredisiDec")) or 0.0)
                )
        for item in raw.get("unusedSinifOgrenciList") or []:
            if item.get("harfNotu") and item.get("donem"):
                attempts.append(
                    (item["donem"], base_code(item["bransKodu"]), item["harfNotu"],
                     _decimal(item.get("kredisi")) or 0.0)
                )
        if not attempts:
            raise ObsError("OBS returned no graded attempts to build a GPA from.")

        branches = sorted({code.split(" ")[0] for _term, code, _g, _k in attempts})
        program = public.resolve_equivalence_program(
            graduation["plan"]["program"] or "Yapay Zeka"
        )
        plan_types = public.equivalence_plan_types(program["program_id"])
        plan_type_id = next(
            (item["plan_type_id"] for item in plan_types if item["name"] == "Lisans"),
            plan_types[0]["plan_type_id"] if plan_types else 2,
        )
        equivalences = self._equivalence_map(
            public, program["program_id"], plan_type_id, graduation["plan"]["plan_id"], branches
        )

        parent: dict[str, str] = {}

        def find(key: str) -> str:
            parent.setdefault(key, key)
            while parent[key] != key:
                parent[key] = parent[parent[key]]
                key = parent[key]
            return key

        def union(a: str, b: str) -> None:
            root_a, root_b = find(a), find(b)
            if root_a != root_b:
                parent[root_a] = root_b

        for key, aliases in equivalences.items():
            for alias in aliases:
                union(key, alias)

        grouped: dict[str, list[tuple[str, str, str, float]]] = {}
        for attempt in sorted(attempts):
            grouped.setdefault(find(attempt[1]), []).append(attempt)

        counted = {root: sorted(items)[-1] for root, items in grouped.items()}

        # OBS posts letter grades before it recomputes the term aggregates, so
        # its own summary (gpa, earned credits, completed course count) can lag
        # the detail lists by a whole semester. Detect that rather than letting
        # it read as a broken reconstruction.
        latest_graded_term = max(term for term, _c, _g, _k in attempts)
        settled_terms = [
            entry["code"]
            for record in self.get_academic_standing()["records"]
            for entry in record["semesters"]
            if entry["cumulative_gpa"] is not None
        ]
        latest_settled_term = max(settled_terms) if settled_terms else None
        stale = bool(latest_settled_term and latest_graded_term > latest_settled_term)

        return {
            "counted": counted,
            "latest_graded_term": latest_graded_term,
            "latest_settled_term": latest_settled_term,
            "obs_summary_stale": stale,
            "pending_terms": sorted(
                {
                    term
                    for term, _c, _g, _k in attempts
                    if latest_settled_term and term > latest_settled_term
                }
            ),
            "repeated": {
                root: [
                    {"term": t, "code": c, "letter_grade": g, "credits": k}
                    for t, c, g, k in items
                ]
                for root, items in grouped.items()
                if len(items) > 1
            },
            "reported_gpa": graduation["summary"]["gpa"],
            "find": find,
        }

    @staticmethod
    def _gpa_of(counted: dict[str, tuple[str, str, str, float]]) -> tuple[float, float, float | None]:
        points = credits = 0.0
        for _term, _code, grade, credit in counted.values():
            # Non-graded passes (BL/MU) and 0-credit courses carry no weight.
            if grade not in GRADE_POINTS or not credit:
                continue
            points += GRADE_POINTS[grade] * credit
            credits += credit
        return points, credits, (points / credits if credits else None)

    def _credits_for(
        self,
        code: str,
        key: str,
        find: Any,
        *,
        public_client: "ObsPublicClient | None" = None,
    ) -> float:
        """Credits for a course the student has never attempted.

        The plan may name the course under a different code than the one being
        asked about — ``BLG 317E`` in the plan is offered as ``BBF 302E`` — so
        the match is made on the equivalence group, not the literal code. An
        elective is not in the plan's course list at all and has to come from
        its group pool. Failing to resolve raises rather than defaulting to
        zero, which would silently shrink the GPA denominator.
        """
        graduation = self.get_graduation_progress()
        for entry in graduation["remaining"] + graduation["completed"]:
            if entry["code"] and find(base_code(entry["code"])) == find(key):
                if entry["credits"]:
                    return float(entry["credits"])

        for slot in graduation["remaining"]:
            if not slot["elective_group_id"]:
                continue
            for pool_course in self.get_elective_pool(slot["elective_group_id"])["courses"]:
                if find(base_code(pool_course["code"])) == find(key) and pool_course["credits"]:
                    return float(pool_course["credits"])

        raise ObsError(
            f"Could not determine the credit value of {code}. It is not in the course plan "
            "under any equivalent code, nor in an elective pool. Pass it as 'CODE:GRADE' only "
            "for courses the plan knows about."
        )

    def project_gpa(
        self,
        assume_grades: list[str] | None = None,
        *,
        public_client: "ObsPublicClient | None" = None,
    ) -> dict[str, Any]:
        """Project the GPA under hypothetical grades, e.g. ``["YZV 201E:CC"]``."""
        basis = self.gpa_basis(public_client=public_client)
        counted = dict(basis["counted"])
        find = basis["find"]

        points, credits, current = self._gpa_of(counted)
        reported = basis["reported_gpa"]
        stale = basis["obs_summary_stale"]
        matches = (
            current is not None and reported is not None and abs(round(current, 2) - reported) < 0.011
        )

        applied = []
        for entry in assume_grades or []:
            code, _, grade = str(entry).partition(":")
            grade = grade.strip().upper()
            code = normalize_code(code)
            if grade not in GRADE_POINTS:
                raise ObsError(
                    f"'{grade}' is not a graded ITU letter. Use one of: {', '.join(GRADE_POINTS)}."
                )
            key = base_code(code)
            existing = counted.get(find(key))
            credit = existing[3] if existing else None
            if credit is None:
                credit = self._credits_for(code, key, find, public_client=public_client)
            counted[find(key)] = ("9999", code, grade, credit)
            applied.append(
                {
                    "code": code,
                    "assumed_grade": grade,
                    "credits": credit,
                    "replaces": (
                        {"letter_grade": existing[2], "term": existing[0]} if existing else None
                    ),
                }
            )

        new_points, new_credits, projected = self._gpa_of(counted)
        return {
            "model": (
                "Last attempt of each course counts; equivalent course codes are treated as "
                "the same course; 0-credit and non-graded (BL/MU) results are excluded."
            ),
            "model_agrees_with_obs": matches or stale,
            "obs_summary_stale": stale,
            "pending_terms": basis["pending_terms"],
            "reconciliation": (
                "OBS has posted letter grades for "
                f"{', '.join(basis['pending_terms'])} but has not recomputed its term aggregates "
                f"(its last settled term is {basis['latest_settled_term']}), so the figure it "
                "reports is the previous one. The reconstruction below already includes the new grades."
                if stale
                else (
                    "Reconstruction matches the GPA OBS reports."
                    if matches
                    else "Reconstruction disagrees with OBS and no pending term explains it — the model may need revisiting."
                )
            ),
            "current": {
                "gpa": round(current, 4) if current is not None else None,
                "gpa_rounded": round(current, 2) if current is not None else None,
                "reported_by_obs": reported,
                "reported_by_obs_is_stale": stale,
                "grade_points": round(points, 2),
                "credits_in_average": round(credits, 1),
            },
            "assumed": applied,
            "projected": {
                "gpa": round(projected, 4) if projected is not None else None,
                "gpa_rounded": round(projected, 2) if projected is not None else None,
                "grade_points": round(new_points, 2),
                "credits_in_average": round(new_credits, 1),
                "change": (
                    round(projected - current, 4)
                    if projected is not None and current is not None
                    else None
                ),
            },
        }

    def api_get(self, path: str) -> Any:
        """Escape hatch for any read-only OBS student API endpoint."""
        cleaned = path.strip()
        if not cleaned.startswith("/"):
            cleaned = "/" + cleaned
        if not cleaned.lower().startswith("/api/ogrenci/"):
            raise ObsError("Only /api/ogrenci/... endpoints can be read with this tool.")
        return {"path": cleaned, "payload": self.client.api_get(cleaned)}
