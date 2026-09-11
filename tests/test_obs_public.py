from __future__ import annotations

import unittest

from ninova_mcp.obs_client import ObsError
from ninova_mcp.obs_public import ObsPublicClient, split_course_code

SCHEDULE_HTML = """
<table id="dersProgramContainer">
  <tr class="table-baslik">
    <td>CRN</td><td>Ders Kodu</td><td>Ders Adı</td><td>Öğretim Yöntemi</td><td>Öğretim Üyesi</td>
    <td>Bina</td><td>Gün</td><td>Saat</td><td>Derslik</td><td>Kontenjan</td><td>Yazılan</td>
    <td>Reservasyon</td><td>Dersi Alabilen Programlar</td><td>Ders Önşartları</td><td>Başarılan Kredi</td>
  </tr>
  <tr>
    <td>12469</td>
    <td><a href="/public/DersBilgi?bransKodu=BLG&amp;dersNo=223E">BLG 223E</a></td>
    <td>Data Structures</td>
    <td>Fiziksel (Yüz yüze)</td>
    <td>Yusuf Hüseyin Şahin</td>
    <td>EEB<br/>EEB</td>
    <td>Salı<br/>Perşembe</td>
    <td>08:30/10:29<br/>14:30/16:29</td>
    <td>Z-18<br/>Z-17</td>
    <td>80</td>
    <td>30</td>
    <td>-</td>
    <td><a href="#">SECE_LS, BLGE_LS</a></td>
    <td><a href="/public/DersBilgi?bransKodu=BLG&amp;dersNo=223E">Detay</a></td>
    <td>-</td>
  </tr>
  <tr>
    <td>12322</td>
    <td>YZV 201E</td>
    <td>Data Structures</td>
    <td>Fiziksel (Yüz yüze)</td>
    <td>Gülşen Eryiğit</td>
    <td>BBB</td>
    <td>Salı</td>
    <td>08:30/12:29</td>
    <td>Z-19</td>
    <td>70</td>
    <td>70</td>
    <td>-</td>
    <td>YZVE_LS</td>
    <td>-</td>
    <td>-</td>
  </tr>
</table>
"""


class SplitCourseCodeTests(unittest.TestCase):
    def test_splits_branch_and_number(self):
        self.assertEqual(split_course_code("YZV 302E"), ("YZV", "302E"))
        self.assertEqual(split_course_code("blg223e".upper()), ("BLG", "223E"))
        self.assertEqual(split_course_code(" MAT 103E "), ("MAT", "103E"))

    def test_rejects_nonsense(self):
        self.assertIsNone(split_course_code("not a code at all"))
        self.assertIsNone(split_course_code(""))


class ParseScheduleTests(unittest.TestCase):
    def setUp(self):
        self.client = ObsPublicClient()
        self.sections = self.client._parse_schedule_table(SCHEDULE_HTML)

    def test_header_row_is_skipped(self):
        self.assertEqual(len(self.sections), 2)

    def test_multi_session_rows_split_into_meetings(self):
        blg = self.sections[0]
        self.assertEqual(blg["crn"], "12469")
        self.assertEqual(blg["code"], "BLG 223E")
        self.assertEqual(len(blg["meetings"]), 2)
        first, second = blg["meetings"]
        self.assertEqual((first["day"], first["day_en"]), ("Salı", "Tuesday"))
        self.assertEqual((first["start"], first["end"]), ("08:30", "10:29"))
        self.assertEqual(first["start_minutes"], 8 * 60 + 30)
        self.assertEqual(first["room"], "Z-18")
        self.assertEqual((second["day_en"], second["room"]), ("Thursday", "Z-17"))

    def test_quota_and_availability(self):
        blg, yzv = self.sections
        self.assertEqual((blg["capacity"], blg["enrolled"], blg["available"]), (80, 30, 50))
        self.assertEqual(yzv["available"], 0)

    def test_eligible_programs_and_prerequisites(self):
        blg, yzv = self.sections
        self.assertEqual(blg["eligible_programs"], ["SECE_LS", "BLGE_LS"])
        self.assertTrue(blg["has_prerequisites"])
        self.assertEqual(yzv["eligible_programs"], ["YZVE_LS"])
        self.assertFalse(yzv["has_prerequisites"])


TERM_PAGE_HTML = """
<div class="content-area">
  <h1 id="baslik1" style="display:none;">2026-2027 Güz Dönemi Ders Programları</h1>
  <h1 id="baslik2" style="display:none;">2025-2026 III. Dönem Tezsiz II. Öğretim Ders Programları</h1>
  <a href="https://www.sis.itu.edu.tr/TR/ogrenci/ders-programi/202710/lisans-genel-duyurular.php">Duyurular</a>
</div>
"""


class ActiveTermTests(unittest.TestCase):
    """The published term comes from the hidden heading, not the stale AJAX endpoint."""

    def setUp(self):
        self.client = ObsPublicClient()

        class FakeResponse:
            text = TERM_PAGE_HTML

        self.client._get = lambda path, params=None: FakeResponse()  # type: ignore[method-assign]

    def test_undergraduate_reads_baslik1(self):
        term = self.client.active_term("LS")
        self.assertEqual(term["term"], "2026-2027 Güz Dönemi")
        self.assertEqual(term["term_code"], "202710")

    def test_graduate_evening_reads_baslik2(self):
        self.assertEqual(
            self.client.active_term("LUI")["term"],
            "2025-2026 III. Dönem Tezsiz II. Öğretim",
        )

    def test_term_is_cached_per_level(self):
        self.client.active_term("LS")
        calls = []
        self.client._get = lambda path, params=None: calls.append(path)  # type: ignore[method-assign]
        self.client.active_term("LS")
        self.assertEqual(calls, [])


class LevelValidationTests(unittest.TestCase):
    def test_accepts_codes_and_names(self):
        client = ObsPublicClient()
        self.assertEqual(client._normalize_level("ls"), "LS")
        self.assertEqual(client._normalize_level("Lisansüstü"), "LU")
        self.assertEqual(client._normalize_level(None), "LS")

    def test_rejects_unknown_level(self):
        with self.assertRaises(ObsError):
            ObsPublicClient()._normalize_level("PHD")


class ConflictDetectionTests(unittest.TestCase):
    """check_conflicts() drives get_schedule(), which is stubbed out here."""

    def setUp(self):
        self.client = ObsPublicClient()
        sections = {s["code"]: s for s in self.client._parse_schedule_table(SCHEDULE_HTML)}

        def fake_get_schedule(branch, *, level="LS", course_code=None, **_kwargs):
            match = sections.get((course_code or "").upper())
            return {"sections": [match] if match else []}

        self.client.get_schedule = fake_get_schedule  # type: ignore[method-assign]
        self.client.active_term = lambda level="LS": {  # type: ignore[method-assign]
            "term": "2026-2027 Güz Dönemi",
            "term_code": "202710",
        }

    def test_detects_overlapping_meetings(self):
        result = self.client.check_conflicts(
            [{"code": "BLG 223E"}, {"code": "YZV 201E"}]
        )
        self.assertEqual(result["selected_count"], 2)
        self.assertEqual(result["conflict_count"], 1)
        self.assertEqual(result["conflicts"][0]["day"], "Salı")

    def test_single_course_has_no_conflicts(self):
        result = self.client.check_conflicts([{"code": "BLG 223E"}])
        self.assertEqual(result["conflict_count"], 0)
        self.assertEqual(list(result["weekly"]), ["Salı", "Perşembe"])

    def test_missing_section_is_reported_as_a_problem(self):
        result = self.client.check_conflicts([{"code": "MAT 103E"}])
        self.assertEqual(result["selected_count"], 0)
        self.assertTrue(result["problems"])


if __name__ == "__main__":
    unittest.main()
