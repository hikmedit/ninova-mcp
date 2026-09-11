from __future__ import annotations

import unittest

from ninova_mcp.obs import (
    GRADE_POINTS,
    ObsService,
    _course_code,
    _decimal,
    _envelope,
    _minutes_to_clock,
)
from ninova_mcp.obs_client import ObsError


class FakeClient:
    """Stands in for ObsClient so the service can be tested without network."""

    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def api_get(self, path: str, *, retry: bool = True) -> object:
        self.calls.append(path)
        if path not in self.responses:
            raise AssertionError(f"unexpected request: {path}")
        return self.responses[path]


SEMESTERS = {
    "ogrenciDonemListesi": [
        {
            "donemKodu": "202510",
            "akademikDonemAdi": "2024-2025 - Güz Dönemi",
            "akademikDonemAdiEN": "2024-2025 - Fall Semester",
            "sonAkademikDurumDonemId": False,
            "akademikDonemId": 773,
        },
        {
            "donemKodu": "202620",
            "akademikDonemAdi": "2025-2026 - Bahar Dönemi",
            "akademikDonemAdiEN": "2025-2026 - Spring Semester",
            "sonAkademikDurumDonemId": True,
            "akademikDonemId": 795,
        },
    ],
    "statusCode": 0,
    "resultMessage": "İşlem başarılı.",
}


class HelperTests(unittest.TestCase):
    def test_decimal_accepts_turkish_locale_strings(self):
        self.assertEqual(_decimal("3,5"), 3.5)
        self.assertEqual(_decimal(4.0), 4.0)
        self.assertIsNone(_decimal(""))
        self.assertIsNone(_decimal("not a number"))

    def test_course_code_joins_branch_and_number(self):
        self.assertEqual(_course_code("YZV", "302E"), "YZV 302E")
        self.assertEqual(_course_code("YZV 302E", None), "YZV 302E")
        self.assertEqual(_course_code("YZV 302E", "302E"), "YZV 302E")

    def test_minutes_to_clock(self):
        self.assertEqual(_minutes_to_clock(570), "09:30")
        self.assertEqual(_minutes_to_clock(749), "12:29")
        self.assertIsNone(_minutes_to_clock(None))

    def test_envelope_splits_data_and_status(self):
        data, meta = _envelope({"items": [1], "statusCode": 0, "resultMessage": "ok"}, "items")
        self.assertEqual(data, [1])
        self.assertEqual(meta["status_code"], 0)
        self.assertEqual(meta["result_message"], "ok")

    def test_grade_scale_matches_itu_gpa(self):
        # 2025-2026 Spring, cross-checked against the 1.51 GPA OBS reports.
        rows = [("CC", 3.0), ("DC+", 2.0), ("DD", 3.0), ("DD+", 3.0), ("AA", 2.0), ("CC", 3.0), ("FF", 4.0)]
        points = sum(GRADE_POINTS[grade] * credits for grade, credits in rows)
        credits = sum(credits for _grade, credits in rows)
        self.assertAlmostEqual(round(points / credits, 2), 1.51)


class SemesterResolutionTests(unittest.TestCase):
    def _service(self) -> ObsService:
        return ObsService(client=FakeClient({"/api/ogrenci/DonemListesi/": SEMESTERS}))

    def test_defaults_to_the_current_semester(self):
        self.assertEqual(self._service().resolve_semester(None)["code"], "202620")

    def test_resolves_by_term_code_and_id(self):
        service = self._service()
        self.assertEqual(service.resolve_semester("202510")["semester_id"], 773)
        self.assertEqual(service.resolve_semester(795)["code"], "202620")

    def test_resolves_by_name_fragment(self):
        self.assertEqual(self._service().resolve_semester("2024-2025 - Güz")["code"], "202510")

    def test_ambiguous_name_is_rejected(self):
        with self.assertRaises(ObsError):
            self._service().resolve_semester("Dönemi")

    def test_unknown_semester_lists_known_codes(self):
        with self.assertRaises(ObsError) as ctx:
            self._service().resolve_semester("209999")
        self.assertIn("202620", str(ctx.exception))


class GradesTests(unittest.TestCase):
    def test_grades_are_annotated_with_points_and_pass_state(self):
        client = FakeClient(
            {
                "/api/ogrenci/DonemListesi/": SEMESTERS,
                "/api/ogrenci/Sinif/SinifHarfNotuListesi/795": {
                    "sinifHarfNotuResultList": [
                        {
                            "crn": "1",
                            "bransKodu": "YZV",
                            "dersNo": "322E",
                            "dersAdiEN": "Applied Data Engineering",
                            "dersAdiTR": "Uygulamalı Veri Mühendisliği",
                            "harfNotu": "AA",
                        },
                        {
                            "crn": "2",
                            "bransKodu": "BBF",
                            "dersNo": "201E",
                            "dersAdiEN": "Probability and Statistics",
                            "dersAdiTR": "Olasılık ve İstatistik",
                            "harfNotu": "FF",
                        },
                        {
                            "crn": "3",
                            "bransKodu": "TUR",
                            "dersNo": "121",
                            "dersAdiEN": "Turkish I",
                            "dersAdiTR": "Türk Dili I",
                            "harfNotu": "BL",
                        },
                    ],
                    "statusCode": 0,
                },
            }
        )
        result = ObsService(client=client).get_grades()
        self.assertEqual(result["semester"]["code"], "202620")
        codes = [course["code"] for course in result["courses"]]
        self.assertEqual(codes, ["YZV 322E", "BBF 201E", "TUR 121"])
        self.assertEqual(result["courses"][0]["grade_points"], 4.0)
        self.assertTrue(result["courses"][0]["passed"])
        self.assertFalse(result["courses"][1]["passed"])
        # BL carries no grade point but still counts as passed.
        self.assertIsNone(result["courses"][2]["grade_points"])
        self.assertTrue(result["courses"][2]["passed"])


class GraduationTests(unittest.TestCase):
    PAYLOAD = {
        "mezuniyetimeNeKaldiBilgi": {
            "dersPlaniVM": {
                "gerekliMezuniyetKredisi": 128.0,
                "dersPlaniBaslik": "Test Plan",
                "akademikDonemSayisi": 8,
                "planId": 1563,
                "gerekliStajGunu": 40,
                "gerekliIngilizceKredi": 128.0,
                "gerekliMinGPA": 2.0,
            },
            "checkMetMezuniyetList": [
                {
                    "isMet": True,
                    "donemNo": 1,
                    "siraNo": 5,
                    "harfNotu": "BB",
                    "bransKodu": "MAT 103E",
                    "dersAdi": "Mathematics I",
                    "kredisi": "4",
                    "kredisiDec": 4.0,
                    "grupName": "",
                    "sayilanKredi": 4.0,
                    "donem": "202410",
                    "crn": "10141",
                },
                {
                    "isMet": False,
                    "donemNo": 3,
                    "siraNo": 6,
                    "harfNotu": "",
                    "bransKodu": "",
                    "dersAdi": "3rd Semester Elective Course (ITB)",
                    "kredisi": "3",
                    "kredisiDec": 3.0,
                    "grupName": "3rd Semester Elective Course (ITB)",
                    "sayilanKredi": 0.0,
                    "donem": None,
                    "crn": None,
                },
                {
                    "isMet": False,
                    "donemNo": 3,
                    "siraNo": 1,
                    "harfNotu": "",
                    "bransKodu": "YZV 201E",
                    "dersAdi": "Data Structures",
                    "kredisi": "3,5",
                    "kredisiDec": 3.5,
                    "grupName": "",
                    "sayilanKredi": 0.0,
                    "donem": None,
                    "crn": None,
                },
            ],
            "unusedSinifOgrenciList": [
                {
                    "harfNotu": "DD",
                    "bransKodu": "MAT 103E",
                    "dersAdi": "Mathematics I",
                    "kredisi": 4.0,
                    "isValidAndUnused": False,
                    "donem": "202320",
                    "crn": "25072",
                }
            ],
            "metKrediTotal": 62.5,
            "gpa": 2.1,
            "akts": 1.99,
            "ogrenciTamamlananStaj": 0,
            "toplamDersSayisi": 49,
            "tamamlananDersSayisi": 25,
        },
        "statusCode": 0,
    }

    def _service(self) -> ObsService:
        return ObsService(
            client=FakeClient(
                {
                    "/api/ogrenci/OgrenciProgamListesi/": {
                        "ogrenciProgramBilgiListesi": [
                            {
                                "ogrenciProgramId": 167501,
                                "programAdi": "Test Program",
                                "ogrenciNo": "150220321",
                            }
                        ],
                        "statusCode": 0,
                    },
                    "/api/ogrenci/MezuniyetimeNeKaldi/167501": self.PAYLOAD,
                }
            )
        )

    def test_summary_totals(self):
        result = self._service().get_graduation_progress()
        summary = result["summary"]
        self.assertEqual(summary["earned_credits"], 62.5)
        self.assertEqual(summary["required_credits"], 128.0)
        self.assertEqual(summary["remaining_credits"], 65.5)
        self.assertEqual(summary["credit_completion_percent"], 48.8)
        self.assertTrue(summary["gpa_requirement_met"])
        self.assertFalse(summary["internship_requirement_met"])

    def test_completed_and_remaining_split(self):
        result = self._service().get_graduation_progress()
        self.assertEqual([c["code"] for c in result["completed"]], ["MAT 103E"])
        self.assertEqual(
            sorted(c["code"] or c["elective_group"] for c in result["remaining"]),
            ["3rd Semester Elective Course (ITB)", "YZV 201E"],
        )

    def test_elective_slots_are_flagged_and_credits_parsed(self):
        remaining = self._service().get_graduation_progress()["remaining"]
        elective = next(c for c in remaining if c["is_elective_slot"])
        data_structures = next(c for c in remaining if c["code"] == "YZV 201E")
        self.assertEqual(elective["elective_group"], "3rd Semester Elective Course (ITB)")
        self.assertIsNone(elective["code"])
        self.assertEqual(data_structures["credits"], 3.5)

    def test_remaining_is_grouped_by_plan_semester(self):
        grouped = self._service().get_graduation_progress()["remaining_by_plan_semester"]
        self.assertEqual([bucket["plan_semester"] for bucket in grouped], [3])
        self.assertEqual(grouped[0]["course_count"], 2)
        self.assertEqual(grouped[0]["credits"], 6.5)

    def test_summary_only_mode_omits_course_lists(self):
        result = self._service().get_graduation_progress(include_courses=False)
        self.assertNotIn("completed", result)
        self.assertNotIn("remaining", result)
        self.assertIn("summary", result)


class ApiGuardTests(unittest.TestCase):
    def test_api_get_rejects_paths_outside_the_student_api(self):
        service = ObsService(client=FakeClient({}))
        with self.assertRaises(ObsError):
            service.api_get("/api/personel/Bilgi")

    def test_api_get_normalizes_a_missing_leading_slash(self):
        client = FakeClient({"/api/ogrenci/KayitDurumu": {"kayitDurumuList": [], "statusCode": 0}})
        result = ObsService(client=client).api_get("api/ogrenci/KayitDurumu")
        self.assertEqual(result["path"], "/api/ogrenci/KayitDurumu")


if __name__ == "__main__":
    unittest.main()
