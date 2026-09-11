from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import requests

from .client import DEFAULT_HEADERS, DEFAULT_TIMEOUT
from .env import load_ninova_env
from .parsing import clean_text, make_soup, normalize_lookup_text

DEFAULT_OBS_BASE_URL = "https://obs.itu.edu.tr"
STUDENT_ENTRY_PATH = "/ogrenci/"
JWT_PATH = "/ogrenci/auth/jwt"
API_PREFIX = "/api/ogrenci/"
LOGIN_HOST_RE = re.compile(r"girisv3\.itu\.edu\.tr", re.IGNORECASE)


class ObsError(RuntimeError):
    """Base OBS error."""


class ObsAuthError(ObsError):
    """Authentication failed."""


@dataclass(slots=True)
class ObsCredentials:
    username: str
    password: str

    @classmethod
    def from_env(cls) -> "ObsCredentials":
        load_ninova_env()
        username = os.getenv("ITU_USERNAME") or os.getenv("NINOVA_USERNAME")
        password = os.getenv("ITU_PASSWORD") or os.getenv("NINOVA_PASSWORD")
        if not username or not password:
            raise ObsAuthError(
                "ITU_USERNAME/ITU_PASSWORD or NINOVA_USERNAME/NINOVA_PASSWORD must both be set."
            )
        return cls(username=username, password=password)


class ObsClient:
    """Credential-based client for the ITU OBS student portal.

    OBS and Ninova share the same girisv3 single sign-on, so the same ITU
    account works for both. The student portal is a SPA backed by a JSON API
    that expects a bearer token issued at ``/ogrenci/auth/jwt`` after the SSO
    session cookies are in place.
    """

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or os.getenv("OBS_BASE_URL") or DEFAULT_OBS_BASE_URL).rstrip("/")
        self.credentials = ObsCredentials.from_env()
        self.session = self._build_session()
        self.token: str | None = None
        self.last_login_at: str | None = None
        self.login_method: str | None = None

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update(DEFAULT_HEADERS)
        session.headers["Accept"] = "application/json, text/plain, */*"
        return session

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.base_url}/{path.lstrip('/')}"

    def _decode_response(self, response: requests.Response) -> str:
        if not response.encoding or response.encoding.lower() == "iso-8859-1":
            response.encoding = response.apparent_encoding or "utf-8"
        return response.text

    def _looks_like_login_page(self, response: requests.Response, html: str | None = None) -> bool:
        if LOGIN_HOST_RE.search(response.url):
            return True
        text = html if html is not None else self._decode_response(response)
        return (
            "ContentPlaceHolder1_tbUserName" in text
            and "ContentPlaceHolder1_tbPassword" in text
        )

    def _extract_login_error(self, html: str) -> str | None:
        soup = make_soup(html)
        for selector in (".validation-summary-errors", ".error", "#ContentPlaceHolder1_lblMessage"):
            node = soup.select_one(selector)
            if node:
                text = clean_text(node.get_text(" ", strip=True))
                if text:
                    return text
        text = clean_text(soup.get_text(" ", strip=True))
        normalized = normalize_lookup_text(text)
        for marker in ("hatali", "yanlis", "invalid", "failed", "kilitli"):
            if marker in normalized:
                return text[:300]
        return None

    def _build_login_payload(self, login_html: str) -> dict[str, str]:
        soup = make_soup(login_html)
        payload: dict[str, str] = {}
        for input_tag in soup.select("input[name]"):
            input_name = input_tag.get("name")
            if not input_name:
                continue
            payload[input_name] = input_tag.get("value", "")

        payload["ctl00$ContentPlaceHolder1$tbUserName"] = self.credentials.username
        payload["ctl00$ContentPlaceHolder1$tbPassword"] = self.credentials.password
        payload["ctl00$ContentPlaceHolder1$btnLogin"] = payload.get(
            "ctl00$ContentPlaceHolder1$btnLogin", "Login"
        )
        return payload

    def _mark_logged_in(self, method: str) -> None:
        self.last_login_at = datetime.now(tz=UTC).isoformat()
        self.login_method = method

    def login(self, force: bool = False) -> dict[str, Any]:
        if force:
            self.session = self._build_session()
            self.token = None

        entry = self.session.get(
            self._url(STUDENT_ENTRY_PATH),
            timeout=DEFAULT_TIMEOUT,
            allow_redirects=True,
        )
        html = self._decode_response(entry)

        if self._looks_like_login_page(entry, html=html):
            payload = self._build_login_payload(html)
            response = self.session.post(
                entry.url,
                data=payload,
                headers={"Referer": entry.url},
                timeout=DEFAULT_TIMEOUT,
                allow_redirects=True,
            )
            login_html = self._decode_response(response)
            if self._looks_like_login_page(response, html=login_html):
                error = self._extract_login_error(login_html)
                if self._playwright_allowed():
                    try:
                        self._login_with_playwright()
                    except Exception as exc:  # pragma: no cover - best-effort fallback
                        raise ObsAuthError(f"OBS login failed: {error or exc}") from exc
                else:
                    raise ObsAuthError(f"OBS login failed: {error or 'unknown error'}")
            else:
                self._mark_logged_in("requests")
        else:
            self._mark_logged_in("existing-session")

        self.refresh_token()
        return self.session_info()

    def _playwright_allowed(self) -> bool:
        return os.getenv("NINOVA_DISABLE_PLAYWRIGHT_FALLBACK") not in {"1", "true", "TRUE"}

    def _login_with_playwright(self) -> None:  # pragma: no cover - login fallback
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            raise ObsAuthError("Playwright fallback is unavailable.") from exc

        with sync_playwright() as playwright:
            browser = None
            try:
                try:
                    browser = playwright.chromium.launch(channel="chrome", headless=True)
                except Exception:
                    browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent=DEFAULT_HEADERS["User-Agent"],
                    locale="tr-TR",
                )
                page = context.new_page()
                page.goto(self._url(STUDENT_ENTRY_PATH), wait_until="domcontentloaded", timeout=60000)
                page.locator("#ContentPlaceHolder1_tbUserName").fill(self.credentials.username)
                page.locator("#ContentPlaceHolder1_tbPassword").fill(self.credentials.password)
                page.locator("#ContentPlaceHolder1_btnLogin").click()
                try:
                    page.wait_for_url(lambda url: "girisv3.itu.edu.tr" not in url, timeout=60000)
                except PlaywrightTimeoutError as exc:
                    error_text = clean_text(page.locator("body").inner_text())[:300]
                    raise ObsAuthError(f"OBS login failed: {error_text}") from exc

                self.session = self._build_session()
                for cookie in context.cookies():
                    self.session.cookies.set(
                        cookie["name"],
                        cookie["value"],
                        domain=cookie.get("domain"),
                        path=cookie.get("path", "/"),
                    )
                self._mark_logged_in("playwright")
            finally:
                if browser is not None:
                    browser.close()

    def refresh_token(self) -> str:
        response = self.session.get(self._url(JWT_PATH), timeout=DEFAULT_TIMEOUT)
        text = self._decode_response(response).strip().strip('"')
        if response.status_code != 200 or not text or self._looks_like_login_page(response, html=text):
            raise ObsAuthError("Could not obtain an OBS API token; the SSO session is not active.")
        self.token = text
        self.session.headers["Authorization"] = f"Bearer {self.token}"
        return self.token

    def ensure_logged_in(self) -> dict[str, Any]:
        if self.token is None:
            return self.login()
        return self.session_info()

    def is_authenticated(self) -> bool:
        try:
            payload = self.api_get("/api/ogrenci/OgrenciYetkiListesi", retry=False)
        except ObsError:
            return False
        return isinstance(payload, dict) and "kisiYetkiListesi" in payload

    def session_info(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "username": self.credentials.username,
            "last_login_at": self.last_login_at,
            "login_method": self.login_method,
            "has_token": bool(self.token),
            "cookie_names": sorted(cookie.name for cookie in self.session.cookies),
        }

    def api_get(self, path: str, *, retry: bool = True) -> Any:
        """GET a JSON endpoint of the OBS student API."""
        self.ensure_logged_in()
        response = self.session.get(self._url(path), timeout=DEFAULT_TIMEOUT, allow_redirects=True)

        if retry and self._needs_reauth(response):
            try:
                self.refresh_token()
            except ObsAuthError:
                self.login(force=True)
            response = self.session.get(
                self._url(path), timeout=DEFAULT_TIMEOUT, allow_redirects=True
            )
            if self._needs_reauth(response):
                self.login(force=True)
                response = self.session.get(
                    self._url(path), timeout=DEFAULT_TIMEOUT, allow_redirects=True
                )

        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")
        if "json" not in content_type.lower():
            raise ObsError(
                f"Expected JSON from {path} but received {content_type or 'an unknown content type'}."
            )
        return response.json()

    def _needs_reauth(self, response: requests.Response) -> bool:
        if response.status_code in {401, 403}:
            return True
        content_type = response.headers.get("Content-Type", "").lower()
        if "json" in content_type:
            return False
        return self._looks_like_login_page(response)
