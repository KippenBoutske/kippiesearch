"""it's learning sync (je school e.d.) via requests — geen Chromium.

Aanpak:
1. HTML-login met gebruikersnaam/wachtwoord (geen SSO nodig).
2. Daarna REST-endpoints onder /restapi/personal/ met dezelfde sessiecookie.
3. Fallback: klassieke ASPX-crawl van AllCourses + folders + files.

Er is een officiële REST API, maar die vereist een OAuth-app (client_id) van
school/itslearning. Voor een studentaccount zonder SSO is sessie-login +
REST-met-cookie of HTML-scrape de werkbare onofficiële route (zelfde patroon
als bestaande dumpers). Playwright/Chromium blijft bewust UIT: te zwaar voor
een Pi 5 naast Home Assistant.

Credentials komen alleen uit add-on-opties; ze worden niet gelogd.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from kippiesearch.config import Settings
from kippiesearch.logging_setup import get_logger
from kippiesearch.status import AppStatus
from kippiesearch.store import VectorStore, utcnow

log = get_logger("itslearning")

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._\\-]+")


def _safe(name: str) -> str:
    cleaned = _SAFE_NAME.sub("_", (name or "").strip())[:120]
    return cleaned or "item"


class ItslearningSync:
    def __init__(self, settings: Settings, status: AppStatus, store: VectorStore | None = None):
        self.settings = settings
        self.status = status
        self.store = store
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "KippieSearch/0.1 (personal local archive; +https://github.com/KippenBoutske/kippiesearch)"
            }
        )
        self.base = settings.itslearning_url.rstrip("/")

    def enabled(self) -> bool:
        return bool(
            self.settings.itslearning_enabled
            and self.settings.itslearning_username
            and self.settings.itslearning_password
        )

    def _sleep(self) -> None:
        time.sleep(max(0.2, self.settings.itslearning_rate_limit_s))

    def _get(self, url: str, **kwargs) -> requests.Response:
        self._sleep()
        response = self.session.get(url, timeout=45, **kwargs)
        response.raise_for_status()
        return response

    def _post(self, url: str, **kwargs) -> requests.Response:
        self._sleep()
        response = self.session.post(url, timeout=45, **kwargs)
        response.raise_for_status()
        return response

    def login(self) -> None:
        landing = self._get(self.base + "/")
        soup = BeautifulSoup(landing.text, "lxml")
        form = soup.find("form")
        payload: dict[str, str] = {}
        action = self.base + "/"
        if form:
            action = urljoin(self.base + "/", form.get("action") or "/")
            for inp in form.find_all("input"):
                name = inp.get("name")
                if not name:
                    continue
                payload[name] = inp.get("value") or ""
        # Gangbare veldnamen
        user_keys = [
            k
            for k in payload
            if "user" in k.lower() or k.lower() in {"login", "username", "ctl00$username"}
        ]
        pass_keys = [k for k in payload if "pass" in k.lower()]
        if not user_keys:
            payload["ctl00$ContentPlaceHolder$Username$ctl00"] = self.settings.itslearning_username
            payload["Username"] = self.settings.itslearning_username
        else:
            for key in user_keys:
                payload[key] = self.settings.itslearning_username
        if not pass_keys:
            payload["Password"] = self.settings.itslearning_password
        else:
            for key in pass_keys:
                payload[key] = self.settings.itslearning_password
        # Extra aliases
        payload.setdefault("LoginName", self.settings.itslearning_username)
        response = self.session.post(action, data=payload, timeout=45, allow_redirects=True)
        if response.status_code >= 400:
            raise RuntimeError(f"it's learning login HTTP {response.status_code}")
        text = response.text.lower()
        if "password" in text and ("login" in text or "inloggen" in text) and "dashboard" not in text:
            if "/Dashboard" not in response.url and "Index.aspx" in response.url:
                raise RuntimeError("it's learning login mislukt (controleer gebruikersnaam/wachtwoord)")
        log.info("itslearning_login_ok", host=urlparse(self.base).hostname)

    def _rest_json(self, path: str) -> Any | None:
        url = urljoin(self.base + "/", path.lstrip("/"))
        try:
            self._sleep()
            response = self.session.get(
                url,
                timeout=45,
                headers={"Accept": "application/json"},
            )
            if response.status_code >= 400:
                return None
            if "json" not in response.headers.get("content-type", "") and not response.text[:1] in "[{":
                return None
            return response.json()
        except Exception as exc:  # noqa: BLE001
            log.info("itslearning_rest_skip", path=path, error=str(exc))
            return None

    def _courses_via_rest(self) -> list[dict[str, Any]]:
        for path in (
            "/restapi/personal/courses/v1?PageIndex=0&PageSize=100",
            "/restapi/personal/yourfiles/courses/v1?PageIndex=0&PageSize=100",
        ):
            data = self._rest_json(path)
            if not data:
                continue
            items = data.get("EntityArray") or data.get("entities") or data.get("Courses") or data
            if isinstance(items, dict):
                items = items.get("EntityArray") or []
            if isinstance(items, list) and items:
                return items
        return []

    def _resources_via_rest(self, course_id: str, folder_id: str | None = None) -> list[dict[str, Any]]:
        if folder_id:
            path = (
                f"/restapi/personal/courses/{course_id}/folders/{folder_id}/resources/v1"
                "?PageIndex=0&PageSize=100"
            )
        else:
            path = (
                f"/restapi/personal/courses/{course_id}/resources/v1?PageIndex=0&PageSize=100"
            )
        data = self._rest_json(path)
        if not data:
            return []
        items = data.get("EntityArray") or data.get("Resources") or data
        return items if isinstance(items, list) else []

    def _download_url(self, url: str, dest: Path) -> bool:
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._sleep()
        with self.session.get(url, timeout=90, stream=True) as response:
            if response.status_code >= 400:
                return False
            size = 0
            max_bytes = self.settings.max_file_bytes
            with dest.open("wb") as handle:
                for chunk in response.iter_content(64 * 1024):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > max_bytes:
                        handle.close()
                        dest.unlink(missing_ok=True)
                        return False
                    handle.write(chunk)
        return dest.is_file() and dest.stat().st_size > 0

    def _courses_via_html(self) -> list[tuple[str, str]]:
        url = self.base + "/Course/AllCourses.aspx"
        try:
            html = self._get(url).text
        except Exception:
            return []
        soup = BeautifulSoup(html, "lxml")
        found: list[tuple[str, str]] = []
        for link in soup.find_all("a", href=True):
            href = link["href"]
            if "CourseID=" not in href and "CourseId=" not in href and "/Course/" not in href:
                continue
            match = re.search(r"CourseI[dD]=(\d+)", href)
            if not match:
                continue
            title = link.get_text(" ", strip=True) or match.group(1)
            found.append((match.group(1), title))
        # uniek
        uniq = {}
        for cid, title in found:
            uniq[cid] = title
        return list(uniq.items())

    def _crawl_folder_html(self, course_id: str, folder_id: str | None, dest_root: Path) -> int:
        saved = 0
        if folder_id:
            url = self.base + f"/Folder/processfolder.aspx?FolderID={folder_id}"
        else:
            url = self.base + f"/ContentArea/ContentArea.aspx?LocationID={course_id}&LocationType=1"
        try:
            html = self._get(url).text
        except Exception as exc:  # noqa: BLE001
            self.status.error(url, str(exc))
            return 0
        soup = BeautifulSoup(html, "lxml")
        for link in soup.find_all("a", href=True):
            href = urljoin(self.base + "/", link["href"])
            text = link.get_text(" ", strip=True)
            if "FolderID=" in href:
                nested = re.search(r"FolderID=(\d+)", href)
                if nested and nested.group(1) != folder_id:
                    saved += self._crawl_folder_html(course_id, nested.group(1), dest_root)
                continue
            if "File/" in href or "fs_folderfile" in href or "Download" in href:
                filename = _safe(text) or "bestand"
                dest = dest_root / filename
                try:
                    if self._download_url(href, dest):
                        saved += 1
                        self.status.progress(str(dest), processed=saved)
                except Exception as exc:  # noqa: BLE001
                    self.status.error(href, str(exc))
        return saved

    def sync(self) -> dict[str, Any]:
        if not self.enabled():
            return {"ok": False, "reason": "itslearning uit of credentials ontbreken"}
        self.status.begin("sync-itslearning")
        downloaded = 0
        courses_n = 0
        try:
            self.login()
            dest_root = self.settings.itslearning_dir
            dest_root.mkdir(parents=True, exist_ok=True)
            rest_courses = self._courses_via_rest()
            if rest_courses:
                courses_n = len(rest_courses)
                for course in rest_courses:
                    cid = str(
                        course.get("CourseId")
                        or course.get("Id")
                        or course.get("courseId")
                        or ""
                    )
                    title = _safe(
                        course.get("Title") or course.get("Name") or course.get("title") or cid
                    )
                    if not cid:
                        continue
                    course_dir = dest_root / title
                    resources = self._resources_via_rest(cid)
                    for item in resources:
                        url = (
                            item.get("Url")
                            or item.get("DownloadUrl")
                            or item.get("ContentUrl")
                            or ""
                        )
                        name = _safe(item.get("Title") or item.get("Name") or "bestand")
                        element_type = str(item.get("ElementType") or item.get("Type") or "")
                        if "folder" in element_type.lower():
                            folder_id = str(item.get("FolderId") or item.get("Id") or "")
                            nested = self._resources_via_rest(cid, folder_id)
                            for child in nested:
                                child_url = (
                                    child.get("Url")
                                    or child.get("DownloadUrl")
                                    or child.get("ContentUrl")
                                    or ""
                                )
                                child_name = _safe(child.get("Title") or child.get("Name") or "bestand")
                                if child_url:
                                    if self._download_url(urljoin(self.base + "/", child_url), course_dir / child_name):
                                        downloaded += 1
                            continue
                        if url:
                            if self._download_url(urljoin(self.base + "/", url), course_dir / name):
                                downloaded += 1
                    # HTML-fallback per cursus als REST geen files gaf
                    if downloaded == 0:
                        downloaded += self._crawl_folder_html(cid, None, course_dir)
            else:
                html_courses = self._courses_via_html()
                courses_n = len(html_courses)
                for cid, title in html_courses:
                    downloaded += self._crawl_folder_html(cid, None, dest_root / _safe(title))
            if self.store:
                self.store.set_meta("last_itslearning_sync", utcnow())
                self.store.set_meta("last_error", "")
            result = {"ok": True, "courses": courses_n, "downloaded": downloaded}
            self.status.finish(f"itslearning courses={courses_n} files={downloaded}")
            log.info("itslearning_sync_done", courses=courses_n, downloaded=downloaded)
            return result
        except Exception as exc:  # noqa: BLE001
            if self.store:
                self.store.set_meta("last_error", f"itslearning: {exc}")
            self.status.finish(f"itslearning failed: {exc}")
            log.warning("itslearning_sync_failed", error=str(exc))
            return {"ok": False, "error": str(exc)}
