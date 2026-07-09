from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .client import NinovaAuthError, NinovaClient, NinovaError
from .env import load_ninova_env
from .parsing import (
    SnapshotReference,
    compare_snapshot_payloads,
    extract_announcement_detail,
    extract_announcements_list,
    extract_attendance,
    extract_assignment_detail,
    extract_assignment_upload_status,
    extract_assignments_list,
    extract_course_sections,
    extract_gradebook,
    extract_course_info,
    extract_file_directory,
    extract_message_board,
    extract_message_thread_detail,
    extract_remote_learning,
    is_internal_ninova_url,
    make_snapshot_payload,
    ninova_datetime_iso,
    normalize_lookup_text,
    normalize_url,
    parse_html_page,
    pretty_json,
    sanitize_filename,
    slugify,
    summarize_dashboard,
)
from .tracking import diff_course_snapshots, load_tracking_state, merge_updates, save_tracking_state, utc_now_iso

SERVER_NAME = "ninova-mcp"
SERVER_VERSION = "0.1.5"

SERVER_INSTRUCTIONS = (
    "This connector reads the user's own İTÜ Ninova account: courses, "
    "announcements, assignments, grades, class/lesson files, message boards, "
    "attendance, and deadlines.\n\n"
    "Whenever the user asks about their courses or school — assignments/homework "
    "(ödev), due dates or deadlines (teslim, son tarih), grades (not, ortalama), "
    "announcements (duyuru), lecture or class files (ders/sınıf dosyası), "
    "attendance (yoklama), message boards (mesaj panosu), or a specific course "
    "(ders) — call these tools to fetch the real answer live from Ninova instead "
    "of guessing. You DO have access; don't say otherwise. Questions are often in "
    "Turkish.\n\n"
    "Typical flow: call list_courses or get_dashboard to discover the courses, "
    "resolve the one the user means, then call the specific tool (e.g. "
    "get_course_assignments, get_course_grades, get_course_announcements). For "
    "'what's due / upcoming' use get_upcoming_deadlines; for a broad status use "
    "get_dashboard.\n\n"
    "Requires NINOVA_USERNAME and NINOVA_PASSWORD; if login fails, ask the user to "
    "check their İTÜ credentials."
)


class NinovaMcpApp:
    def __init__(self) -> None:
        load_ninova_env()
        self._client: NinovaClient | None = None
        state_root = os.getenv("NINOVA_STATE_DIR") or str(Path.home() / ".ninova_state")
        self.state_dir = Path(state_root)
        self.snapshot_dir = self.state_dir / "snapshots"
        self.tracking_state_path = self.state_dir / "tracking-state.json"
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)

    @property
    def client(self) -> NinovaClient:
        if self._client is None:
            self._client = NinovaClient()
        return self._client

    def auth_status(self) -> dict[str, Any]:
        credentials_present = bool(os.getenv("NINOVA_USERNAME") and os.getenv("NINOVA_PASSWORD"))
        status: dict[str, Any] = {
            "credentials_present": credentials_present,
            "state_dir": str(self.state_dir),
        }
        if not credentials_present:
            status["authenticated"] = False
            status["message"] = "Set NINOVA_USERNAME and NINOVA_PASSWORD to enable login."
            return status

        try:
            session = self.client.ensure_logged_in(verify=True)
            status["authenticated"] = True
            status["session"] = session
        except NinovaAuthError as exc:
            status["authenticated"] = False
            status["message"] = str(exc)
        return status

    def refresh_session(self) -> dict[str, Any]:
        session = self.client.login(force=True)
        return {
            "authenticated": True,
            "session": session,
        }

    def get_dashboard(self) -> dict[str, Any]:
        html, response = self.client.get_html("/Kampus1")
        page_data = parse_html_page(response.url, html, base_url=self.client.base_url)
        return summarize_dashboard(page_data, html=html, base_url=self.client.base_url)

    def list_courses(self) -> dict[str, Any]:
        dashboard = self.get_dashboard()
        return {
            "count": len(dashboard["courses"]),
            "courses": dashboard["courses"],
        }

    def get_courses(self) -> dict[str, Any]:
        return self.list_courses()

    def get_course_announcements(
        self,
        course: str,
        include_full_text: bool = False,
        limit: int = 50,
    ) -> dict[str, Any]:
        resolved = self._resolve_course(course)
        html, response = self.client.get_html(resolved["url"] + "/Duyurular")
        announcements = extract_announcements_list(html, response.url, base_url=self.client.base_url)
        announcements = announcements[: max(1, min(limit, 200))]
        if include_full_text:
            announcements = [self._merge_announcement_detail(item) for item in announcements]
        return {
            "course": resolved,
            "count": len(announcements),
            "announcements": announcements,
        }

    def get_dashboard_announcements(
        self,
        include_full_text: bool = False,
        limit: int = 20,
    ) -> dict[str, Any]:
        html, response = self.client.get_html("/Kampus?1/Duyurular")
        announcements = extract_announcements_list(html, response.url, base_url=self.client.base_url)
        announcements = announcements[: max(1, min(limit, 200))]
        if include_full_text:
            announcements = [self._merge_announcement_detail(item) for item in announcements]
        return {
            "count": len(announcements),
            "announcements": announcements,
        }

    def get_course_assignments(
        self,
        course: str,
        limit: int = 100,
    ) -> dict[str, Any]:
        resolved = self._resolve_course(course)
        html, response = self.client.get_html(resolved["url"] + "/Odevler")
        assignments = extract_assignments_list(html, response.url, base_url=self.client.base_url)
        assignments = assignments[: max(1, min(limit, 200))]
        assignments = [self._merge_assignment_detail(item) for item in assignments]
        return {
            "course": resolved,
            "count": len(assignments),
            "assignments": assignments,
        }

    def get_dashboard_assignments(
        self,
        limit: int = 20,
    ) -> dict[str, Any]:
        html, response = self.client.get_html("/Kampus?1/Odevler")
        assignments = extract_assignments_list(html, response.url, base_url=self.client.base_url)
        assignments = assignments[: max(1, min(limit, 200))]
        assignments = [self._merge_assignment_detail(item) for item in assignments]
        return {
            "count": len(assignments),
            "assignments": assignments,
        }

    def get_course_info(self, course: str) -> dict[str, Any]:
        resolved = self._resolve_course(course)
        html, response = self.client.get_html(resolved["url"] + "/SinifBilgileri")
        payload = extract_course_info(html, response.url, base_url=self.client.base_url)
        payload["course"] = resolved
        return payload

    def get_course_sections(self, course: str) -> dict[str, Any]:
        resolved = self._resolve_course(course)
        html, response = self.client.get_html(resolved["url"])
        sections = extract_course_sections(html, response.url, base_url=self.client.base_url)
        return {
            "course": resolved,
            "count": len(sections),
            "sections": sections,
        }

    def get_course_grades(self, course: str) -> dict[str, Any]:
        resolved = self._resolve_course(course)
        html, response = self.client.get_html(resolved["url"] + "/Notlar")
        payload = extract_gradebook(html, response.url, base_url=self.client.base_url)
        payload["course"] = resolved
        return payload

    def get_course_message_board(
        self,
        course: str,
        include_thread_details: bool = False,
        limit: int = 50,
    ) -> dict[str, Any]:
        resolved = self._resolve_course(course)
        html, response = self.client.get_html(resolved["url"] + "/MesajPanosu")
        payload = extract_message_board(html, response.url, base_url=self.client.base_url)
        topics = payload["topics"][: max(1, min(limit, 200))]
        if include_thread_details:
            topics = [self._merge_message_thread_detail(item) for item in topics]
        return {
            "course": resolved,
            "count": len(topics),
            "topics": topics,
        }

    def get_course_attendance(self, course: str) -> dict[str, Any]:
        resolved = self._resolve_course(course)
        html, response = self.client.get_html(resolved["url"] + "/Yoklama")
        payload = extract_attendance(html, response.url, base_url=self.client.base_url)
        payload["course"] = resolved
        return payload

    def get_course_remote_learning(self, course: str) -> dict[str, Any]:
        resolved = self._resolve_course(course)
        html, response = self.client.get_html(resolved["url"] + "/UzaktanEgitim")
        payload = extract_remote_learning(html, response.url, base_url=self.client.base_url)
        payload["course"] = resolved
        return payload

    def get_course_overview(
        self,
        course: str,
        refresh: bool = False,
        file_max_depth: int = 3,
    ) -> dict[str, Any]:
        resolved = self._resolve_course(course)
        if not refresh:
            state = self._load_tracking_state_document()
            existing = state["courses"].get(resolved["url"])
            if existing:
                snapshot = existing["snapshot"]
                snapshot["source"] = "tracked_state"
                return snapshot

        snapshot = self._collect_course_snapshot(
            resolved,
            include_files=True,
            file_max_depth=file_max_depth,
        )
        snapshot["source"] = "live"
        return snapshot

    def sync_all_courses(
        self,
        include_files: bool = True,
        file_max_depth: int = 3,
        course_limit: int | None = None,
    ) -> dict[str, Any]:
        courses = self.list_courses()["courses"]
        if course_limit is not None:
            courses = courses[: max(1, min(course_limit, len(courses)))]

        synced_at = utc_now_iso()
        state = self._load_tracking_state_document()
        baseline = not bool(state["courses"])
        updates: list[dict[str, Any]] = []
        course_results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        current_course_urls = {course["url"] for course in courses}

        for course in courses:
            try:
                snapshot = self._collect_course_snapshot(
                    course,
                    include_files=include_files,
                    file_max_depth=file_max_depth,
                )
            except Exception as exc:
                errors.append({"course": course, "error": str(exc)})
                continue

            previous_entry = state["courses"].get(course["url"])
            previous_snapshot = previous_entry.get("snapshot") if previous_entry else None
            course_updates = diff_course_snapshots(
                course=course,
                previous_snapshot=None if baseline else previous_snapshot,
                current_snapshot=snapshot,
                detected_at=synced_at,
            )
            if previous_entry is None and not baseline:
                course_updates.insert(
                    0,
                    {
                        "id": slugify(f"{course['url']}-course-added-{synced_at}")[:32],
                        "detected_at": synced_at,
                        "course": course,
                        "entity_type": "course",
                        "action": "added",
                        "entity_id": course["url"],
                        "summary": f"course:added:{course.get('code') or course.get('title') or course['url']}",
                        "before": None,
                        "after": course,
                    },
                )

            updates.extend(course_updates)
            state["courses"][course["url"]] = {
                "course": course,
                "synced_at": synced_at,
                "snapshot": snapshot,
            }
            course_results.append(
                {
                    "course": course,
                    "update_count": len(course_updates),
                }
            )

        removed_course_urls = set(state["courses"]) - current_course_urls
        for removed_url in sorted(removed_course_urls):
            removed_entry = state["courses"].pop(removed_url)
            if baseline:
                continue
            updates.append(
                {
                    "id": slugify(f"{removed_url}-course-removed-{synced_at}")[:32],
                    "detected_at": synced_at,
                    "course": removed_entry["course"],
                    "entity_type": "course",
                    "action": "removed",
                    "entity_id": removed_url,
                    "summary": f"course:removed:{removed_entry['course'].get('code') or removed_entry['course'].get('title') or removed_url}",
                    "before": removed_entry["course"],
                    "after": None,
                }
            )

        state["last_sync_at"] = synced_at
        state["updates"] = merge_updates(state["updates"], updates)
        self._save_tracking_state_document(state)

        return {
            "synced_at": synced_at,
            "baseline_created": baseline,
            "course_count": len(course_results),
            "update_count": len(updates),
            "courses": course_results,
            "updates": updates[:100],
            "errors": errors,
            "tracking_state_path": str(self.tracking_state_path),
        }

    def get_updates(
        self,
        limit: int = 100,
        course: str | None = None,
        entity_type: str | None = None,
    ) -> dict[str, Any]:
        state = self._load_tracking_state_document()
        updates = list(state["updates"])

        if course is not None:
            resolved = self._resolve_course(course)
            updates = [item for item in updates if item["course"]["url"] == resolved["url"]]

        if entity_type is not None:
            target = normalize_lookup_text(entity_type)
            updates = [
                item
                for item in updates
                if normalize_lookup_text(item.get("entity_type")) == target
            ]

        updates = updates[: max(1, min(limit, 500))]
        return {
            "last_sync_at": state["last_sync_at"],
            "count": len(updates),
            "updates": updates,
        }

    def get_upcoming_deadlines(
        self,
        days: int = 14,
        refresh: bool = False,
    ) -> dict[str, Any]:
        if refresh or not self.tracking_state_path.exists():
            self.sync_all_courses()

        state = self._load_tracking_state_document()
        deadline_items: list[dict[str, Any]] = []
        now = datetime.now(tz=UTC)
        upper_bound = now.timestamp() + max(1, min(days, 180)) * 86400

        for course_entry in state["courses"].values():
            course = course_entry["course"]
            assignments = course_entry["snapshot"]["overview"]["assignments"]
            for assignment in assignments:
                submission_end_iso = assignment.get("submission_end_iso") or ninova_datetime_iso(
                    assignment.get("submission_end")
                )
                if submission_end_iso is None:
                    continue
                due_at = datetime.fromisoformat(submission_end_iso).astimezone(UTC)
                if due_at.timestamp() < now.timestamp() or due_at.timestamp() > upper_bound:
                    continue
                requested_file_count = assignment.get("requested_file_count") or 0
                uploaded_file_count = assignment.get("uploaded_file_count") or 0
                deadline_items.append(
                    {
                        "course": course,
                        "title": assignment.get("title"),
                        "url": assignment.get("url"),
                        "submission_end": assignment.get("submission_end"),
                        "submission_end_iso": submission_end_iso,
                        "requested_file_count": requested_file_count,
                        "uploaded_file_count": uploaded_file_count,
                        "is_fully_uploaded": bool(requested_file_count) and uploaded_file_count >= requested_file_count,
                    }
                )

        deadline_items.sort(key=lambda item: item["submission_end_iso"])
        return {
            "last_sync_at": state["last_sync_at"],
            "days": max(1, min(days, 180)),
            "count": len(deadline_items),
            "deadlines": deadline_items,
        }

    def get_course_class_files(
        self,
        course: str,
        recursive: bool = True,
        max_depth: int = 3,
    ) -> dict[str, Any]:
        resolved = self._resolve_course(course)
        payload = self._walk_file_directory(
            resolved["url"] + "/SinifDosyalari",
            recursive=recursive,
            max_depth=max_depth,
        )
        payload["course"] = resolved
        payload["scope"] = "class_files"
        return payload

    def get_course_lesson_files(
        self,
        course: str,
        recursive: bool = True,
        max_depth: int = 3,
    ) -> dict[str, Any]:
        resolved = self._resolve_course(course)
        payload = self._walk_file_directory(
            resolved["url"] + "/DersDosyalari",
            recursive=recursive,
            max_depth=max_depth,
        )
        payload["course"] = resolved
        payload["scope"] = "lesson_files"
        return payload

    def read_page(
        self,
        url: str,
        include_text: bool = True,
        link_limit: int = 200,
    ) -> dict[str, Any]:
        html, response = self.client.get_html(url)
        content_type = response.headers.get("Content-Type", "")
        if "html" not in content_type.lower():
            return {
                "url": response.url,
                "content_type": content_type,
                "content_length": response.headers.get("Content-Length"),
                "message": "The requested resource is not HTML. Use download_resource to save it.",
            }

        page_data = parse_html_page(response.url, html, base_url=self.client.base_url)
        result = {
            "url": page_data["url"],
            "title": page_data["title"],
            "headings": page_data["headings"],
            "links": page_data["links"][: max(1, min(link_limit, 500))],
            "attachments": page_data["attachments"],
            "tables": page_data["tables"],
            "text_hash": page_data["text_hash"],
        }
        if include_text:
            result["text_excerpt"] = page_data["text_excerpt"]
        return result

    def crawl_course(
        self,
        course_url: str,
        max_depth: int = 2,
        max_pages: int = 25,
        include_downloads: bool = True,
    ) -> dict[str, Any]:
        start_url = normalize_url(course_url, self.client.base_url)
        course_path = self._extract_course_root_path(start_url)
        visited: set[str] = set()
        queue: list[tuple[str, int]] = [(start_url, 0)]
        pages: list[dict[str, Any]] = []
        downloads: list[dict[str, Any]] = []
        seen_downloads: set[tuple[str, str]] = set()

        while queue and len(pages) < max_pages:
            current_url, depth = queue.pop(0)
            normalized = self._strip_fragment(current_url)
            if normalized in visited:
                continue
            visited.add(normalized)

            html, response = self.client.get_html(normalized)
            page = parse_html_page(response.url, html, base_url=self.client.base_url)
            page_summary = {
                "url": page["url"],
                "title": page["title"],
                "headings": page["headings"][:10],
                "link_count": len(page["links"]),
                "attachment_count": len(page["attachments"]),
            }
            pages.append(page_summary)

            if include_downloads:
                for attachment in page["attachments"]:
                    key = (attachment["url"], attachment["text"])
                    if key in seen_downloads:
                        continue
                    seen_downloads.add(key)
                    downloads.append(attachment)

            if depth >= max_depth:
                continue

            for link in page["links"]:
                if link["kind"] != "page":
                    continue
                if not is_internal_ninova_url(link["url"], self.client.base_url):
                    continue
                if not self._is_inside_course(link["url"], course_path):
                    continue
                candidate = self._strip_fragment(link["url"])
                if candidate not in visited:
                    queue.append((candidate, depth + 1))

        return {
            "course_url": start_url,
            "course_path": course_path,
            "pages_crawled": len(pages),
            "pages": pages,
            "downloads": downloads[:500],
        }

    def download_resource(
        self,
        url: str,
        output_dir: str | None = None,
        filename: str | None = None,
    ) -> dict[str, Any]:
        response = self.client.get(url, stream=True)
        target_dir = Path(output_dir or (self.state_dir / "downloads")).expanduser().resolve()
        target_dir.mkdir(parents=True, exist_ok=True)

        resolved_name = sanitize_filename(filename) if filename else self._filename_from_response(response)
        target_path = self._unique_download_path(target_dir / resolved_name)
        with target_path.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    handle.write(chunk)

        return {
            "url": response.url,
            "content_type": response.headers.get("Content-Type"),
            "content_length": response.headers.get("Content-Length"),
            "path": str(target_path),
        }

    def snapshot_page(self, url: str, label: str | None = None) -> dict[str, Any]:
        html, response = self.client.get_html(url)
        page_data = parse_html_page(response.url, html, base_url=self.client.base_url)
        payload = make_snapshot_payload(page_data, label=label)
        snapshot_path = self._snapshot_path(page_data["url"], label=label)
        snapshot_path.write_text(
            json.dumps(
                {
                    "captured_at": datetime.now(tz=UTC).isoformat(),
                    "snapshot": payload,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return {
            "snapshot_path": str(snapshot_path),
            "url": page_data["url"],
            "title": page_data["title"],
            "text_hash": page_data["text_hash"],
        }

    def diff_snapshot(
        self,
        url: str,
        snapshot_path: str | None = None,
        label: str | None = None,
    ) -> dict[str, Any]:
        html, response = self.client.get_html(url)
        page_data = parse_html_page(response.url, html, base_url=self.client.base_url)
        current = make_snapshot_payload(page_data, label=label)
        previous = self._load_snapshot(url=response.url, snapshot_path=snapshot_path, label=label)
        comparison = compare_snapshot_payloads(previous.payload, current)
        comparison["snapshot_path"] = str(previous.path)
        return comparison

    def _load_snapshot(
        self,
        *,
        url: str,
        snapshot_path: str | None,
        label: str | None,
    ) -> SnapshotReference:
        if snapshot_path:
            path = Path(snapshot_path).expanduser().resolve()
            payload = json.loads(path.read_text(encoding="utf-8"))["snapshot"]
            return SnapshotReference(path=path, payload=payload)

        candidates = sorted(self.snapshot_dir.glob("*.json"), reverse=True)
        normalized_url = normalize_url(url, self.client.base_url)
        for candidate in candidates:
            document = json.loads(candidate.read_text(encoding="utf-8"))
            snapshot = document["snapshot"]
            if snapshot.get("url") != normalized_url:
                continue
            if label is not None and snapshot.get("label") != label:
                continue
            return SnapshotReference(path=candidate, payload=snapshot)
        raise NinovaError("No matching snapshot was found.")

    def _snapshot_path(self, url: str, label: str | None) -> Path:
        parsed = urlparse(url)
        slug = slugify((label or parsed.path.strip("/") or "dashboard").replace("/", "-"))
        timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        return self.snapshot_dir / f"{slug}-{timestamp}.json"

    def _resolve_course(self, course: str) -> dict[str, Any]:
        courses = self.list_courses()["courses"]
        if not course.strip():
            raise NinovaError("course must be a non-empty string.")

        if "/Sinif/" in course or course.startswith("/") or urlparse(course).scheme:
            normalized = normalize_url(course, self.client.base_url)
            root_path = self._extract_course_root_path(normalized)
            root_url = f"{self.client.base_url}{root_path}"
            for item in courses:
                if item["url"] == root_url:
                    return item
            return {"code": None, "title": None, "url": root_url, "context": course}

        target = normalize_lookup_text(course)
        exact_matches = [
            item
            for item in courses
            if target in {
                normalize_lookup_text(item.get("code")),
                normalize_lookup_text(item.get("title")),
            }
        ]
        if len(exact_matches) == 1:
            return exact_matches[0]
        if len(exact_matches) > 1:
            raise NinovaError(f"Ambiguous course reference: {course}")

        fuzzy_matches = [
            item
            for item in courses
            if target in normalize_lookup_text(item.get("code"))
            or target in normalize_lookup_text(item.get("title"))
            or target in normalize_lookup_text(item.get("context"))
        ]
        if not fuzzy_matches:
            raise NinovaError(f"Course not found: {course}")
        if len(fuzzy_matches) > 1:
            raise NinovaError(
                "Course reference is ambiguous. Use an exact course code or course URL."
            )
        return fuzzy_matches[0]

    def _merge_announcement_detail(self, announcement: dict[str, Any]) -> dict[str, Any]:
        html, response = self.client.get_html(announcement["url"])
        detail = extract_announcement_detail(html, response.url, base_url=self.client.base_url)
        merged = announcement.copy()
        merged.update(
            {
                "body_text": detail.get("body_text"),
                "published_at": announcement.get("published_at") or detail.get("published_at"),
            }
        )
        return merged

    def _merge_assignment_detail(self, assignment: dict[str, Any]) -> dict[str, Any]:
        html, response = self.client.get_html(assignment["url"])
        detail = extract_assignment_detail(html, response.url, base_url=self.client.base_url)
        upload_status: dict[str, Any] = {}
        upload_url = assignment.get("upload_url") or detail.get("upload_url")
        if upload_url:
            try:
                upload_html, upload_response = self.client.get_html(upload_url)
            except NinovaError:
                upload_status = {}
            else:
                upload_status = extract_assignment_upload_status(
                    upload_html,
                    upload_response.url,
                    base_url=self.client.base_url,
                )
        merged = assignment.copy()
        merged.update(
            {
                "description": detail.get("description"),
                "source_files": detail.get("source_files"),
                "required_files": detail.get("required_files"),
                "upload_url": upload_url,
                "submission_start": assignment.get("submission_start") or detail.get("submission_start"),
                "submission_end": assignment.get("submission_end") or detail.get("submission_end"),
                "requested_file_count": upload_status.get("requested_file_count", assignment.get("requested_file_count")),
                "uploaded_file_count": upload_status.get("uploaded_file_count", assignment.get("uploaded_file_count")),
                "upload_items": upload_status.get("upload_items"),
            }
        )
        return merged

    def _merge_message_thread_detail(self, topic: dict[str, Any]) -> dict[str, Any]:
        if not topic.get("url"):
            return topic
        html, response = self.client.get_html(topic["url"])
        detail = extract_message_thread_detail(html, response.url, base_url=self.client.base_url)
        merged = topic.copy()
        merged["thread"] = detail
        return merged

    def _load_tracking_state_document(self) -> dict[str, Any]:
        return load_tracking_state(self.tracking_state_path)

    def _save_tracking_state_document(self, state: dict[str, Any]) -> None:
        save_tracking_state(self.tracking_state_path, state)

    def _collect_course_snapshot(
        self,
        course: dict[str, Any],
        *,
        include_files: bool,
        file_max_depth: int,
    ) -> dict[str, Any]:
        errors: list[dict[str, str]] = []

        course_html, course_response = self.client.get_html(course["url"])
        sections = extract_course_sections(course_html, course_response.url, base_url=self.client.base_url)

        info = self._safe_extract_course_payload(
            course["url"] + "/SinifBilgileri",
            extractor=extract_course_info,
            default={
                "url": course["url"] + "/SinifBilgileri",
                "title": None,
                "headings": [],
                "identity": {},
                "class_meta": {},
                "weekly_schedule": [],
                "course_details": {},
                "weekly_plan": [],
            },
            errors=errors,
            error_scope="info",
        )

        announcements = self._safe_extract_course_payload(
            course["url"] + "/Duyurular",
            extractor=lambda html, url, base_url: {
                "announcements": extract_announcements_list(html, url, base_url=base_url)
            },
            default={"announcements": []},
            errors=errors,
            error_scope="announcements",
        )["announcements"][:200]
        for item in announcements:
            item["published_at_iso"] = ninova_datetime_iso(item.get("published_at"))

        assignments = self._safe_extract_course_payload(
            course["url"] + "/Odevler",
            extractor=lambda html, url, base_url: {
                "assignments": extract_assignments_list(html, url, base_url=base_url)
            },
            default={"assignments": []},
            errors=errors,
            error_scope="assignments",
        )["assignments"][:200]
        assignments = [self._merge_assignment_detail(item) for item in assignments]
        for item in assignments:
            item["submission_start_iso"] = ninova_datetime_iso(item.get("submission_start"))
            item["submission_end_iso"] = ninova_datetime_iso(item.get("submission_end"))

        if include_files:
            try:
                class_files = self._walk_file_directory(
                    course["url"] + "/SinifDosyalari",
                    recursive=True,
                    max_depth=file_max_depth,
                )["entries"]
            except Exception as exc:
                class_files = []
                errors.append({"scope": "class_files", "path": course["url"] + "/SinifDosyalari", "error": str(exc)})
            try:
                lesson_files = self._walk_file_directory(
                    course["url"] + "/DersDosyalari",
                    recursive=True,
                    max_depth=file_max_depth,
                )["entries"]
            except Exception as exc:
                lesson_files = []
                errors.append({"scope": "lesson_files", "path": course["url"] + "/DersDosyalari", "error": str(exc)})
        else:
            class_files = []
            lesson_files = []

        grades = self._safe_extract_course_payload(
            course["url"] + "/Notlar",
            extractor=extract_gradebook,
            default={"url": course["url"] + "/Notlar", "student_name": None, "weighted_average": None, "count": 0, "grades": []},
            errors=errors,
            error_scope="grades",
        )

        message_board = self._safe_extract_course_payload(
            course["url"] + "/MesajPanosu",
            extractor=extract_message_board,
            default={"url": course["url"] + "/MesajPanosu", "count": 0, "topics": []},
            errors=errors,
            error_scope="message_board",
        )

        attendance = self._safe_extract_course_payload(
            course["url"] + "/Yoklama",
            extractor=extract_attendance,
            default={
                "url": course["url"] + "/Yoklama",
                "student_name": None,
                "headers": [],
                "count": 0,
                "weeks": [],
                "total_present_marks": 0,
                "total_absent_marks": 0,
            },
            errors=errors,
            error_scope="attendance",
        )

        remote_learning = self._safe_extract_course_payload(
            course["url"] + "/UzaktanEgitim",
            extractor=extract_remote_learning,
            default={
                "url": course["url"] + "/UzaktanEgitim",
                "active_count": 0,
                "past_count": 0,
                "active_sessions": [],
                "past_sessions": [],
            },
            errors=errors,
            error_scope="remote_learning",
        )

        return {
            "course": course,
            "captured_at": utc_now_iso(),
            "overview": {
                "sections": sections,
                "info": info,
                "announcements": announcements,
                "assignments": assignments,
                "class_files": class_files,
                "lesson_files": lesson_files,
                "grades": grades,
                "message_board": message_board,
                "attendance": attendance,
                "remote_learning": remote_learning,
            },
            "errors": errors,
        }

    def _safe_extract_course_payload(
        self,
        path: str,
        *,
        extractor: Callable[[str, str, str], dict[str, Any]],
        default: dict[str, Any],
        errors: list[dict[str, str]],
        error_scope: str,
    ) -> dict[str, Any]:
        try:
            html, response = self.client.get_html(path)
            return extractor(html, response.url, self.client.base_url)
        except Exception as exc:
            errors.append({"scope": error_scope, "path": path, "error": str(exc)})
            return default

    def _walk_file_directory(
        self,
        root_url: str,
        *,
        recursive: bool,
        max_depth: int,
    ) -> dict[str, Any]:
        start_url = normalize_url(root_url, self.client.base_url)
        queue: list[tuple[str, int, str]] = [(start_url, 0, "/")]
        visited: set[str] = set()
        entries: list[dict[str, Any]] = []
        pages: list[dict[str, Any]] = []

        while queue:
            current_url, depth, current_path = queue.pop(0)
            normalized = self._strip_fragment(current_url)
            if normalized in visited:
                continue
            visited.add(normalized)

            html, response = self.client.get_html(current_url)
            listing = extract_file_directory(
                html,
                response.url,
                base_url=self.client.base_url,
                current_path=current_path,
            )
            pages.append(
                {
                    "url": response.url,
                    "current_path": current_path,
                    "entry_count": len(listing["entries"]),
                }
            )

            for entry in listing["entries"]:
                entries.append(entry)
                if recursive and entry["entry_type"] == "folder" and depth < max_depth:
                    queue.append((entry["url"], depth + 1, entry["path"]))

        return {
            "root_url": start_url,
            "recursive": recursive,
            "max_depth": max_depth,
            "pages_visited": len(pages),
            "pages": pages,
            "entry_count": len(entries),
            "entries": entries,
        }

    def _extract_course_root_path(self, url: str) -> str:
        parsed = urlparse(url)
        match = re.search(r"(/Sinif/\d+\.\d+)", parsed.path)
        if not match:
            raise NinovaError("course_url must point to a Ninova course path like /Sinif/<id>.<id>.")
        return match.group(1)

    def _is_inside_course(self, url: str, course_path: str) -> bool:
        path = urlparse(url).path
        return path == course_path or path.startswith(course_path + "/")

    def _strip_fragment(self, url: str) -> str:
        parsed = urlparse(url)
        suffix = f"?{parsed.query}" if parsed.query else ""
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}{suffix}"

    def _filename_from_response(self, response: Any) -> str:
        disposition = response.headers.get("Content-Disposition", "")
        filename_match = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.IGNORECASE)
        if filename_match:
            return sanitize_filename(filename_match.group(1))
        filename_match = re.search(r'filename="?([^";]+)"?', disposition, re.IGNORECASE)
        if filename_match:
            return sanitize_filename(filename_match.group(1))
        parsed = urlparse(response.url)
        basename = Path(parsed.path).name
        return sanitize_filename(basename or "download")

    def _unique_download_path(self, path: Path) -> Path:
        if not path.exists():
            return path
        stem = path.stem or "download"
        suffix = path.suffix
        counter = 2
        while True:
            candidate = path.with_name(f"{stem}-{counter}{suffix}")
            if not candidate.exists():
                return candidate
            counter += 1


TOOLS: list[dict[str, Any]] = [
    {
        "name": "auth_status",
        "title": "Authentication Status",
        "description": "Check whether Ninova credentials are configured and whether a fresh session can be created.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "refresh_session",
        "title": "Refresh Ninova Session",
        "description": "Force a new login with NINOVA_USERNAME and NINOVA_PASSWORD.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_dashboard",
        "title": "Get Dashboard",
        "description": "Read the Ninova dashboard and summarize courses, recent announcements, assignments, and messages.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "list_courses",
        "title": "List Courses",
        "description": "List all discovered Ninova courses from the dashboard.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_courses",
        "title": "Get Courses",
        "description": "Return all courses visible in the Ninova dashboard.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_course_announcements",
        "title": "Get Course Announcements",
        "description": "Return announcements for a specific Ninova course.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "course": {
                    "type": "string",
                    "description": "Course code, title, course path, or full course URL.",
                },
                "include_full_text": {
                    "type": "boolean",
                    "default": False,
                    "description": "Fetch each announcement detail page and include full body text.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "default": 50,
                },
            },
            "required": ["course"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_course_class_files",
        "title": "Get Course Class Files",
        "description": "List files and folders under the Ninova 'Sınıf Dosyaları' section for a course.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "course": {
                    "type": "string",
                    "description": "Course code, title, course path, or full course URL.",
                },
                "recursive": {"type": "boolean", "default": True},
                "max_depth": {"type": "integer", "minimum": 0, "maximum": 8, "default": 3},
            },
            "required": ["course"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_course_lesson_files",
        "title": "Get Course Lesson Files",
        "description": "List files and folders under the Ninova 'Ders Dosyaları' section for a course.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "course": {
                    "type": "string",
                    "description": "Course code, title, course path, or full course URL.",
                },
                "recursive": {"type": "boolean", "default": True},
                "max_depth": {"type": "integer", "minimum": 0, "maximum": 8, "default": 3},
            },
            "required": ["course"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_course_assignments",
        "title": "Get Course Assignments",
        "description": "Return a course's assignment list together with each assignment's full detail page.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "course": {
                    "type": "string",
                    "description": "Course code, title, course path, or full course URL.",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 100},
            },
            "required": ["course"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_course_info",
        "title": "Get Course Info",
        "description": "Return structured information from a course's 'Sınıf Bilgileri' page.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "course": {
                    "type": "string",
                    "description": "Course code, title, course path, or full course URL.",
                }
            },
            "required": ["course"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_course_sections",
        "title": "Get Course Sections",
        "description": "List the direct course routes exposed on the Ninova course home page.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "course": {
                    "type": "string",
                    "description": "Course code, title, course path, or full course URL.",
                }
            },
            "required": ["course"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_course_grades",
        "title": "Get Course Grades",
        "description": "Read the Ninova 'Notlar' page for a course.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "course": {
                    "type": "string",
                    "description": "Course code, title, course path, or full course URL.",
                }
            },
            "required": ["course"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_course_message_board",
        "title": "Get Course Message Board",
        "description": "Read the Ninova 'Mesaj Panosu' page for a course.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "course": {
                    "type": "string",
                    "description": "Course code, title, course path, or full course URL.",
                },
                "include_thread_details": {
                    "type": "boolean",
                    "default": False,
                    "description": "Fetch each topic page and include parsed posts.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "default": 50,
                },
            },
            "required": ["course"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_course_attendance",
        "title": "Get Course Attendance",
        "description": "Read the Ninova 'Yoklama' page for a course.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "course": {
                    "type": "string",
                    "description": "Course code, title, course path, or full course URL.",
                }
            },
            "required": ["course"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_course_remote_learning",
        "title": "Get Course Remote Learning",
        "description": "Read the Ninova 'Uzaktan Eğitim' page for a course.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "course": {
                    "type": "string",
                    "description": "Course code, title, course path, or full course URL.",
                }
            },
            "required": ["course"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_course_overview",
        "title": "Get Course Overview",
        "description": "Return a combined view of a course's sections, announcements, assignments, files, grades, message board, attendance, and remote learning routes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "course": {
                    "type": "string",
                    "description": "Course code, title, course path, or full course URL.",
                },
                "refresh": {
                    "type": "boolean",
                    "default": False,
                    "description": "Fetch live data instead of using the stored tracking state when available.",
                },
                "file_max_depth": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 8,
                    "default": 3,
                },
            },
            "required": ["course"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_dashboard_announcements",
        "title": "Get Dashboard Announcements",
        "description": "Return the announcements listed under the Ninova dashboard's aggregated announcements page.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_full_text": {
                    "type": "boolean",
                    "default": False,
                    "description": "Fetch full text for each announcement detail page.",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_dashboard_assignments",
        "title": "Get Dashboard Assignments",
        "description": "Return the assignments listed under the Ninova dashboard's aggregated assignments page, including full details.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "sync_all_courses",
        "title": "Sync All Courses",
        "description": "Fetch all visible courses, store a tracking snapshot, and return newly detected changes since the previous sync.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_files": {
                    "type": "boolean",
                    "default": True,
                    "description": "Include class and lesson file inventories in the sync.",
                },
                "file_max_depth": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 8,
                    "default": 3,
                },
                "course_limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "description": "Optional limit for how many courses to sync.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_updates",
        "title": "Get Tracked Updates",
        "description": "Read the stored Ninova tracking history and return recent detected changes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "default": 100,
                },
                "course": {
                    "type": "string",
                    "description": "Optional course code, title, path, or full course URL filter.",
                },
                "entity_type": {
                    "type": "string",
                    "description": "Optional entity type filter such as assignments, announcements, grades, or message_topics.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_upcoming_deadlines",
        "title": "Get Upcoming Deadlines",
        "description": "Return assignments whose submission deadline is approaching based on the stored tracking snapshot.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 180,
                    "default": 14,
                },
                "refresh": {
                    "type": "boolean",
                    "default": False,
                    "description": "Run a sync before computing deadlines.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "read_page",
        "title": "Read Ninova Page",
        "description": "Fetch any Ninova page and return a structured summary of text, headings, links, tables, and attachments.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Absolute Ninova URL or a relative path like /Kampus1 or /Sinif/123.456.",
                },
                "include_text": {
                    "type": "boolean",
                    "default": True,
                    "description": "Include a text excerpt from the page body.",
                },
                "link_limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "default": 200,
                    "description": "Maximum number of links to return.",
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
    },
    {
        "name": "crawl_course",
        "title": "Crawl Course",
        "description": "Inventory pages and downloadable resources inside a Ninova course tree.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "course_url": {
                    "type": "string",
                    "description": "Course root URL or path, for example /Sinif/36851.118733.",
                },
                "max_depth": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 5,
                    "default": 2,
                },
                "max_pages": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 25,
                },
                "include_downloads": {
                    "type": "boolean",
                    "default": True,
                },
            },
            "required": ["course_url"],
            "additionalProperties": False,
        },
    },
    {
        "name": "download_resource",
        "title": "Download Resource",
        "description": "Download a Ninova file or other authenticated resource to disk.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Absolute Ninova URL or relative path.",
                },
                "output_dir": {
                    "type": "string",
                    "description": "Directory to save the file into. Defaults to ./downloads.",
                },
                "filename": {
                    "type": "string",
                    "description": "Optional filename override.",
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
    },
    {
        "name": "snapshot_page",
        "title": "Snapshot Page",
        "description": "Save a structured snapshot of a Ninova page for later comparison.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Absolute Ninova URL or relative path.",
                },
                "label": {
                    "type": "string",
                    "description": "Optional label to help identify the snapshot.",
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
    },
    {
        "name": "diff_snapshot",
        "title": "Diff Snapshot",
        "description": "Compare the current state of a Ninova page against a previously stored snapshot.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Absolute Ninova URL or relative path.",
                },
                "snapshot_path": {
                    "type": "string",
                    "description": "Optional explicit snapshot file path. If omitted, the latest matching snapshot is used.",
                },
                "label": {
                    "type": "string",
                    "description": "Optional label filter when selecting the latest snapshot.",
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
    },
]


LOCAL_TOOL_NAMES: list[str] = [tool["name"] for tool in TOOLS]
REMOTE_EXCLUDED_TOOLS = {"download_resource", "snapshot_page", "diff_snapshot"}
REMOTE_TOOL_NAMES: list[str] = [
    name for name in LOCAL_TOOL_NAMES if name not in REMOTE_EXCLUDED_TOOLS
]


def register_tools(mcp: Any, app: NinovaMcpApp, tool_names: list[str]) -> None:
    """Register Ninova tools on a FastMCP instance from the shared metadata.

    Both the local stdio server and the remote HTTP server go through this so
    the two transports always expose the same tool contract.
    """
    metadata = {tool["name"]: tool for tool in TOOLS}
    for name in tool_names:
        fn = getattr(app, name)
        meta = metadata.get(name, {})
        mcp.add_tool(
            fn,
            name=name,
            title=meta.get("title"),
            description=meta.get("description"),
            structured_output=True,
        )


def apply_server_version(mcp: Any, version: str = SERVER_VERSION) -> None:
    """Report our package version in the MCP ``serverInfo`` handshake.

    FastMCP does not forward a version, so the low-level server otherwise
    falls back to the SDK's own version. This tolerates SDK internals
    changing and simply leaves the default in place if it cannot.
    """
    server = getattr(mcp, "_mcp_server", None)
    if server is not None:
        try:
            server.version = version
        except Exception:  # pragma: no cover - defensive against SDK changes
            pass


def build_stdio_server(app: NinovaMcpApp | None = None) -> Any:
    """Build the FastMCP server for the local stdio transport.

    Uses the official MCP SDK so message framing is spec-compliant
    (newline-delimited JSON), which is what Claude Desktop, Claude Code,
    Cursor, and Codex actually speak.
    """
    from mcp.server.fastmcp import FastMCP

    app = app or NinovaMcpApp()
    mcp = FastMCP(SERVER_NAME, instructions=SERVER_INSTRUCTIONS)
    apply_server_version(mcp)
    register_tools(mcp, app, LOCAL_TOOL_NAMES)
    return mcp


def main() -> None:
    build_stdio_server().run()


if __name__ == "__main__":
    main()
