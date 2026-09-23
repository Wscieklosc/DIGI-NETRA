import json
import re
import time
import requests

from bs4 import BeautifulSoup
from rich import print
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote, unquote, urljoin, urlparse

from .instaemailfind import instafind as infind
from .scrap import chess
from .scrap import instauser
from .scrap import gitinfo


# ============================================================
# STATUSY WYNIKOW
# ============================================================

FOUND = "FOUND"
NOT_FOUND = "NOT_FOUND"
POSSIBLE = "POSSIBLE"
UNKNOWN = "UNKNOWN"
BLOCKED = "BLOCKED"
RATE_LIMIT = "RATE_LIMIT"
ERROR = "ERROR"


# ============================================================
# KANDYDACI USERNAME Z DISPLAY NAME
# ============================================================

def generate_username_candidates(name):
    """Return a small, deterministic set of username candidates."""
    normalized = " ".join(name.split())

    if not normalized:
        return []

    parts = normalized.split(" ")
    if len(parts) == 1:
        return [normalized]

    normalized_parts = [part.casefold() for part in parts]
    reversed_parts = list(reversed(normalized_parts))

    candidates = [" ".join(normalized_parts)]
    candidates.extend(
        separator.join(normalized_parts)
        for separator in ("", ".", "_", "-")
    )
    candidates.extend(
        separator.join(reversed_parts)
        for separator in (".", "_", "-", "")
    )

    return list(dict.fromkeys(candidates))


# ============================================================
# WCZYTYWANIE KONFIGURACJI SERWISOW
# ============================================================

def load_sites_config(path="main/murl.json"):
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


# ============================================================
# SPECJALNA DETEKCJA PROFILI X
# ============================================================

def _x_profile_url_matches(value, username):
    if not value:
        return False

    parsed = urlparse(value)

    return (
        parsed.scheme == "https"
        and parsed.netloc.lower() in ("x.com", "www.x.com")
        and unquote(parsed.path).strip("/").casefold()
        == username.casefold()
    )


def classify_x_profile_response(username, response, profile_url):
    if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", username):
        return (
            NOT_FOUND,
            None,
            "invalid X username format"
        )

    soup = BeautifulSoup(response.text, "html.parser")

    title = (
        soup.title.get_text(" ", strip=True)
        if soup.title
        else ""
    )

    canonical_node = soup.find(
        "link",
        rel=lambda value: value and "canonical" in value,
    )
    canonical_url = (
        canonical_node.get("href", "")
        if canonical_node
        else ""
    )

    og_url_node = soup.find(
        "meta",
        attrs={"property": "og:url"},
    )
    og_url = (
        og_url_node.get("content", "")
        if og_url_node
        else ""
    )

    og_title_node = soup.find(
        "meta",
        attrs={"property": "og:title"},
    )
    og_title = (
        og_title_node.get("content", "")
        if og_title_node
        else ""
    )

    final_url_matches = _x_profile_url_matches(
        response.url,
        username,
    )
    canonical_matches = _x_profile_url_matches(
        canonical_url,
        username,
    )
    og_url_matches = _x_profile_url_matches(
        og_url,
        username,
    )

    handle_pattern = re.compile(
        rf"\(@{re.escape(username)}\)\s*(?:/|on)\s*X(?:\s|$)",
        re.IGNORECASE,
    )
    title_matches = bool(handle_pattern.search(title))
    og_title_matches = bool(handle_pattern.search(og_title))

    not_found_title = "user profile not found" in title.casefold()
    not_found_og_title = (
        "user profile not found" in og_title.casefold()
    )

    if (
        not_found_title
        and not_found_og_title
        and not canonical_matches
        and not og_url_matches
    ):
        return (
            NOT_FOUND,
            None,
            "X not-found metadata"
        )

    login_titles = (
        "log in / x",
        "log in to x / x",
        "sign up for x",
    )

    if (
        title.casefold() in login_titles
        and not canonical_matches
        and not og_url_matches
    ):
        return (
            BLOCKED,
            None,
            "X login page"
        )

    if (
        response.status_code == 200
        and final_url_matches
        and canonical_matches
        and og_url_matches
        and (title_matches or og_title_matches)
    ):
        return (
            FOUND,
            profile_url,
            "X profile metadata confirmed"
        )

    if 200 <= response.status_code < 300 and final_url_matches:
        return (
            POSSIBLE,
            profile_url,
            "incomplete X profile metadata"
        )

    return (
        UNKNOWN,
        None,
        "unrecognized X response"
    )


# ============================================================
# SPECJALNA DETEKCJA PROFILI TIKTOK
# ============================================================

def _tiktok_profile_url_matches(value, username):
    if not value:
        return False

    parsed = urlparse(value)

    return (
        parsed.scheme == "https"
        and parsed.netloc.lower()
        in ("tiktok.com", "www.tiktok.com")
        and unquote(parsed.path).strip("/").casefold()
        == f"@{username}".casefold()
    )


def _tiktok_username_format_is_valid(username):
    return (
        bool(username)
        and not username.endswith(".")
        and all(
            character.isalnum() or character in "_."
            for character in username
        )
    )


def classify_tiktok_profile_response(username, response, profile_url):
    if not _tiktok_username_format_is_valid(username):
        return (
            NOT_FOUND,
            None,
            "invalid TikTok username format"
        )

    final_url_matches = _tiktok_profile_url_matches(
        response.url,
        username,
    )

    soup = BeautifulSoup(response.text, "html.parser")
    data_node = soup.find(
        "script",
        id="__UNIVERSAL_DATA_FOR_REHYDRATION__",
    )

    if data_node is None:
        if 200 <= response.status_code < 300 and final_url_matches:
            return (
                POSSIBLE,
                profile_url,
                "TikTok profile data missing"
            )

        return (
            UNKNOWN,
            None,
            "unrecognized TikTok response"
        )

    try:
        page_data = json.loads(
            data_node.string or data_node.get_text()
        )
    except (TypeError, json.JSONDecodeError):
        return (
            UNKNOWN,
            None,
            "invalid TikTok profile data"
        )

    default_scope = page_data.get("__DEFAULT_SCOPE__", {})
    user_detail = default_scope.get("webapp.user-detail")

    if not isinstance(user_detail, dict):
        return (
            POSSIBLE,
            profile_url if final_url_matches else None,
            "TikTok user detail missing"
        )

    user_info = user_detail.get("userInfo")
    user = (
        user_info.get("user")
        if isinstance(user_info, dict)
        else None
    )
    stats = (
        user_info.get("stats")
        if isinstance(user_info, dict)
        else None
    )
    share_meta = user_detail.get("shareMeta")

    unique_id = (
        user.get("uniqueId", "")
        if isinstance(user, dict)
        else ""
    )
    user_id = (
        str(user.get("id", ""))
        if isinstance(user, dict)
        else ""
    )
    sec_uid = (
        user.get("secUid", "")
        if isinstance(user, dict)
        else ""
    )
    share_description = (
        share_meta.get("desc", "")
        if isinstance(share_meta, dict)
        else ""
    )

    identity_matches = (
        unique_id.casefold() == username.casefold()
        and user_id.isdigit()
        and bool(sec_uid)
        and isinstance(stats, dict)
        and share_description.casefold().startswith(
            f"@{username} ".casefold()
        )
    )

    if (
        response.status_code == 200
        and final_url_matches
        and identity_matches
    ):
        return (
            FOUND,
            profile_url,
            "TikTok embedded profile data confirmed"
        )

    status_code = user_detail.get("statusCode")
    status_message = str(
        user_detail.get("statusMsg", "")
    ).strip()
    status_message_lower = status_message.casefold()

    blocked_markers = (
        "banned",
        "suspend",
        "captcha",
        "challenge",
        "login",
    )

    if any(
        marker in status_message_lower
        for marker in blocked_markers
    ):
        return (
            BLOCKED,
            None,
            f"TikTok status: {status_message}"
        )

    if (
        status_code == 10221
        and not status_message
        and not isinstance(user_info, dict)
        and not isinstance(share_meta, dict)
        and final_url_matches
    ):
        return (
            NOT_FOUND,
            None,
            "TikTok user-detail status 10221"
        )

    if 200 <= response.status_code < 300 and final_url_matches:
        return (
            POSSIBLE,
            profile_url,
            "incomplete TikTok profile data"
        )

    return (
        UNKNOWN,
        None,
        "unrecognized TikTok response"
    )


# ============================================================
# SPECJALNA DETEKCJA PUBLICZNYCH PROFILI XVIDEOS
# ============================================================

def _xvideos_profile_url_matches(value, username):
    if not value:
        return False

    parsed = urlparse(value)

    return (
        parsed.scheme == "https"
        and parsed.netloc.lower()
        in ("xvideos.com", "www.xvideos.com")
        and unquote(parsed.path).rstrip("/").casefold()
        == f"/profiles/{username}".casefold()
        and not parsed.query
        and not parsed.fragment
    )


def classify_xvideos_profile_response(username, response, profile_url):
    status_code = response.status_code
    final_url_matches = _xvideos_profile_url_matches(
        response.url,
        username,
    )

    if status_code == 429:
        return (
            RATE_LIMIT,
            None,
            "HTTP 429"
        )

    if status_code in (401, 403):
        return (
            BLOCKED,
            None,
            f"HTTP {status_code}"
        )

    final_url_lower = response.url.casefold()
    block_url_markers = (
        "/login",
        "/signin",
        "/challenge",
        "/captcha",
    )

    if any(
        marker in final_url_lower
        for marker in block_url_markers
    ):
        return (
            BLOCKED,
            None,
            "XVideos login or challenge redirect"
        )

    if status_code >= 500:
        return (
            ERROR,
            None,
            f"HTTP {status_code}"
        )

    soup = BeautifulSoup(response.text, "html.parser")
    title = (
        soup.title.get_text(" ", strip=True)
        if soup.title
        else ""
    )
    normalized_title = " ".join(title.split())

    blocked_title_markers = (
        "access denied",
        "captcha",
        "challenge",
        "just a moment",
        "log in",
        "sign in",
    )

    if any(
        marker in normalized_title.casefold()
        for marker in blocked_title_markers
    ):
        return (
            BLOCKED,
            None,
            "XVideos access challenge"
        )

    not_found_heading = soup.find(
        "h1",
        string=lambda value: (
            value
            and "this profile doesn't exist"
            in " ".join(value.split()).casefold()
        ),
    )
    not_found_title = (
        "unknown profile" in normalized_title.casefold()
    )

    if status_code == 404:
        if (
            final_url_matches
            and not_found_title
            and not_found_heading is not None
        ):
            return (
                NOT_FOUND,
                None,
                "no public XVideos profile at this username"
            )

        return (
            UNKNOWN,
            None,
            "unconfirmed XVideos 404 response"
        )

    if status_code >= 400:
        return (
            UNKNOWN,
            None,
            f"HTTP {status_code}"
        )

    profile_title = soup.find(id="profile-title")
    profile_heading = (
        profile_title.find(["h1", "h2"])
        if profile_title
        else None
    )
    profile_heading_text = (
        " ".join(
            profile_heading.get_text(" ", strip=True).split()
        )
        if profile_heading
        else ""
    )

    title_pattern = re.compile(
        rf"^{re.escape(username)}\s+-\s+profile page\s+-\s+"
        r"xvideos\.com$",
        re.IGNORECASE,
    )
    heading_pattern = re.compile(
        rf"^{re.escape(username)}(?:\s|$)",
        re.IGNORECASE,
    )

    title_matches = bool(
        title_pattern.fullmatch(normalized_title)
    )
    heading_matches = bool(
        heading_pattern.search(profile_heading_text)
    )
    exact_profile_link = any(
        _xvideos_profile_url_matches(
            urljoin(response.url, node.get("href", "")),
            username,
        )
        for node in soup.find_all("a", href=True)
    )

    profile_title_match = re.fullmatch(
        r"(?P<username>.+?)\s+-\s+profile page\s+-\s+"
        r"xvideos\.com",
        normalized_title,
        re.IGNORECASE,
    )
    marker_username = (
        profile_title_match.group("username").strip()
        if profile_title_match
        else ""
    )
    conflicting_profile_markers = (
        marker_username
        and marker_username.casefold() != username.casefold()
        and profile_title is not None
        and bool(
            re.match(
                rf"^{re.escape(marker_username)}(?:\s|$)",
                profile_heading_text,
                re.IGNORECASE,
            )
        )
        and any(
            _xvideos_profile_url_matches(
                urljoin(response.url, node.get("href", "")),
                marker_username,
            )
            for node in soup.find_all("a", href=True)
        )
    )

    if (
        status_code == 200
        and final_url_matches
        and conflicting_profile_markers
    ):
        return (
            UNKNOWN,
            None,
            "XVideos profile markers identify another username"
        )

    if (
        status_code == 200
        and final_url_matches
        and title_matches
        and profile_title is not None
        and heading_matches
        and exact_profile_link
    ):
        return (
            FOUND,
            profile_url,
            "public XVideos profile found; identity not verified"
        )

    if 200 <= status_code < 300 and final_url_matches:
        return (
            POSSIBLE,
            profile_url,
            "incomplete XVideos profile evidence"
        )

    return (
        UNKNOWN,
        None,
        "unrecognized XVideos response"
    )


# ============================================================
# SPECJALNA DETEKCJA PUBLICZNYCH PROFILI XNXX
# ============================================================

def _xnxx_profile_url_matches(value, username):
    if not value:
        return False

    parsed = urlparse(value)

    return (
        parsed.scheme == "https"
        and parsed.netloc.casefold() in ("xnxx.com", "www.xnxx.com")
        and unquote(parsed.path).rstrip("/")
        == f"/pornstar/{username}"
        and not parsed.query
        and not parsed.fragment
    )


def _extract_xnxx_conf(page_text):
    marker = "window.xv.conf"
    marker_position = page_text.find(marker)

    if marker_position < 0:
        return None, False

    object_position = page_text.find("{", marker_position + len(marker))

    if object_position < 0:
        return None, True

    try:
        config, _ = json.JSONDecoder().raw_decode(
            page_text[object_position:]
        )
    except (TypeError, ValueError):
        return None, True

    return config if isinstance(config, dict) else None, True


def classify_xnxx_profile_response(username, response, profile_url):
    status_code = response.status_code

    if status_code == 429:
        return (
            RATE_LIMIT,
            None,
            "HTTP 429"
        )

    if status_code in (401, 403):
        return (
            BLOCKED,
            None,
            f"HTTP {status_code}"
        )

    if status_code >= 500:
        return (
            ERROR,
            None,
            f"HTTP {status_code}"
        )

    final_url_lower = response.url.casefold()
    block_url_markers = (
        "/login",
        "/signin",
        "/challenge",
        "/captcha",
    )

    if any(
        marker in final_url_lower
        for marker in block_url_markers
    ):
        return (
            BLOCKED,
            None,
            "XNXX login or challenge redirect"
        )

    soup = BeautifulSoup(response.text, "html.parser")
    title = (
        " ".join(soup.title.get_text(" ", strip=True).split())
        if soup.title
        else ""
    )
    title_lower = title.casefold()
    blocked_title_markers = (
        "access denied",
        "captcha",
        "challenge",
        "just a moment",
        "log in",
        "sign in",
    )

    if any(
        marker in title_lower
        for marker in blocked_title_markers
    ):
        return (
            BLOCKED,
            None,
            "XNXX access challenge"
        )

    final_url_matches = _xnxx_profile_url_matches(
        response.url,
        username,
    )

    alternate = soup.find(
        "link",
        attrs={"hreflang": "x-default"},
    )
    alternate_url = alternate.get("href", "") if alternate else ""

    if alternate_url and not _xnxx_profile_url_matches(
        urljoin(response.url, alternate_url),
        username,
    ):
        return (
            UNKNOWN,
            None,
            "XNXX x-default URL conflicts with the username"
        )

    config, config_marker_seen = _extract_xnxx_conf(response.text)
    data = config.get("data") if isinstance(config, dict) else None
    action = data.get("action") if isinstance(data, dict) else None
    user = data.get("user") if isinstance(data, dict) else None

    not_found_heading = soup.find(
        ["h1", "h2"],
        string=lambda value: (
            value
            and " ".join(value.split()).casefold()
            == "this profile doesn't exist !"
        ),
    )

    if status_code == 404:
        if (
            final_url_matches
            and "unknown profile" in title_lower
            and not_found_heading is not None
            and user is None
        ):
            return (
                NOT_FOUND,
                None,
                "no public XNXX model profile at this path"
            )

        return (
            UNKNOWN,
            None,
            "unconfirmed XNXX 404 response"
        )

    if status_code >= 400:
        return (
            UNKNOWN,
            None,
            f"HTTP {status_code}"
        )

    if status_code != 200 or not final_url_matches:
        return (
            UNKNOWN,
            None,
            "unexpected XNXX final URL"
        )

    if config_marker_seen and config is None:
        return (
            UNKNOWN,
            None,
            "invalid XNXX embedded profile data"
        )

    if action is not None and action != "profile":
        return (
            UNKNOWN,
            None,
            "XNXX embedded action conflicts with a profile page"
        )

    if user is not None:
        if not isinstance(user, dict):
            return (
                UNKNOWN,
                None,
                "invalid XNXX embedded user data"
            )

        if user.get("username") != username:
            return (
                UNKNOWN,
                None,
                "XNXX embedded username conflicts with the request"
            )

        if user.get("url") != f"/pornstar/{username}":
            return (
                UNKNOWN,
                None,
                "XNXX embedded profile URL conflicts with the request"
            )

        user_id = user.get("id_user")

        if not user_id or not str(user_id).strip():
            return (
                UNKNOWN,
                None,
                "XNXX embedded profile id is missing"
            )

        if user.get("model") is not True:
            return (
                UNKNOWN,
                None,
                "XNXX embedded account is not a model profile"
            )

    profile_title_matches = bool(
        re.fullmatch(
            r".+\s+-\s+model page\s+-\s+xnxx\.com",
            title,
            re.IGNORECASE,
        )
    )
    body_classes = soup.body.get("class", []) if soup.body else []
    profile_body = "profile-page" in body_classes
    profile_heading_node = soup.select_one("#profile-info-title")
    profile_heading = soup.select_one(
        "h1#profile-info-title, h2#profile-info-title"
    )
    profile_username_marker = (
        profile_heading.select_one(".profile-username")
        if profile_heading
        else None
    )
    profile_username_text = (
        " ".join(
            profile_username_marker.get_text(" ", strip=True).split()
        )
        if profile_username_marker
        else ""
    )

    if title and not profile_title_matches:
        return (
            UNKNOWN,
            None,
            "XNXX title conflicts with a model profile"
        )

    if soup.body and body_classes and not profile_body:
        return (
            UNKNOWN,
            None,
            "XNXX body marker conflicts with a profile page"
        )

    if profile_heading_node is not None and profile_heading is None:
        return (
            UNKNOWN,
            None,
            "XNXX profile heading has an unexpected structure"
        )

    if (
        profile_title_matches
        and profile_body
        and profile_heading is not None
        and bool(profile_username_text)
        and action == "profile"
        and isinstance(user, dict)
    ):
        return (
            FOUND,
            profile_url,
            "public XNXX model profile found; identity not verified"
        )

    return (
        POSSIBLE,
        profile_url,
        "incomplete XNXX model profile evidence"
    )


# ============================================================
# SPECJALNA DETEKCJA PUBLICZNYCH PROFILI BOOKSUSI
# ============================================================

def _booksusi_profile_url_matches(value, username):
    if not isinstance(value, str) or not value:
        return False

    parsed = urlparse(value)

    return (
        parsed.scheme == "https"
        and parsed.netloc.casefold() == "booksusi.com"
        and unquote(parsed.path).casefold()
        == f"/user/{username}/".casefold()
        and not parsed.query
        and not parsed.fragment
    )


def _booksusi_json_ld_data(soup):
    profile_pages = []
    people = []
    invalid_json = False

    for node in soup.find_all(
        "script",
        attrs={"type": "application/ld+json"},
    ):
        try:
            payload = json.loads(node.string or node.get_text())
        except (TypeError, ValueError):
            invalid_json = True
            continue

        values = payload if isinstance(payload, list) else [payload]

        for value in values:
            if not isinstance(value, dict):
                continue

            candidates = [value]
            graph = value.get("@graph")

            if isinstance(graph, list):
                candidates.extend(graph)

            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue

                if candidate.get("@type") == "ProfilePage":
                    profile_pages.append(candidate)

                if candidate.get("@type") == "Person":
                    people.append(candidate)

                main_entity = candidate.get("mainEntity")

                if (
                    isinstance(main_entity, dict)
                    and main_entity.get("@type") == "Person"
                ):
                    people.append(main_entity)

    return profile_pages, people, invalid_json


def classify_booksusi_profile_response(username, response, profile_url):
    status_code = response.status_code

    if status_code == 429:
        return (
            RATE_LIMIT,
            None,
            "HTTP 429"
        )

    if status_code in (401, 403):
        return (
            BLOCKED,
            None,
            f"HTTP {status_code}"
        )

    if status_code >= 500:
        return (
            ERROR,
            None,
            f"HTTP {status_code}"
        )

    final_url_lower = response.url.casefold()
    block_url_markers = (
        "/login",
        "/signin",
        "/challenge",
        "/captcha",
    )

    if any(
        marker in final_url_lower
        for marker in block_url_markers
    ):
        return (
            BLOCKED,
            None,
            "BookSusi login or challenge redirect"
        )

    soup = BeautifulSoup(response.text, "html.parser")
    title = (
        " ".join(soup.title.get_text(" ", strip=True).split())
        if soup.title
        else ""
    )
    title_lower = title.casefold()
    blocked_title_markers = (
        "access denied",
        "captcha",
        "challenge",
        "just a moment",
    )

    if any(
        marker in title_lower
        for marker in blocked_title_markers
    ):
        return (
            BLOCKED,
            None,
            "BookSusi access challenge"
        )

    final_url_matches = _booksusi_profile_url_matches(
        response.url,
        username,
    )
    canonical = soup.find(
        "link",
        rel=lambda value: value and "canonical" in value,
    )
    canonical_url = canonical.get("href", "") if canonical else ""
    canonical_matches = _booksusi_profile_url_matches(
        canonical_url,
        username,
    )
    profile_pages, people, invalid_json = _booksusi_json_ld_data(
        soup
    )
    profile_container = soup.select_one("#profile-new.profile")

    not_found_heading = any(
        " ".join(node.get_text(" ", strip=True).split()) == "404!"
        for node in soup.find_all("h1")
    )
    not_found_message = (
        "die seite die sie versucht haben zu öffnen gibt es leider "
        "nicht auf unserem server!"
    )
    normalized_page_text = " ".join(
        soup.get_text(" ", strip=True).split()
    ).casefold()

    if status_code == 404:
        if (
            final_url_matches
            and not_found_heading
            and not_found_message in normalized_page_text
            and canonical is None
            and not profile_pages
            and not people
            and not invalid_json
            and profile_container is None
        ):
            return (
                NOT_FOUND,
                None,
                "no public BookSusi profile at this path"
            )

        return (
            UNKNOWN,
            None,
            "unconfirmed BookSusi 404 response"
        )

    if status_code >= 400:
        return (
            UNKNOWN,
            None,
            f"HTTP {status_code}"
        )

    if status_code != 200 or not final_url_matches:
        return (
            UNKNOWN,
            None,
            "unexpected BookSusi final URL"
        )

    if canonical_url and not canonical_matches:
        return (
            UNKNOWN,
            None,
            "BookSusi canonical conflicts with the username"
        )

    if invalid_json:
        return (
            UNKNOWN,
            None,
            "invalid BookSusi JSON-LD data"
        )

    if len(profile_pages) > 1:
        return (
            UNKNOWN,
            None,
            "multiple BookSusi ProfilePage records"
        )

    profile_page = profile_pages[0] if profile_pages else None
    main_entity = (
        profile_page.get("mainEntity")
        if isinstance(profile_page, dict)
        else None
    )

    if main_entity is not None and not isinstance(main_entity, dict):
        return (
            UNKNOWN,
            None,
            "invalid BookSusi mainEntity data"
        )

    if isinstance(main_entity, dict):
        entity_type = main_entity.get("@type")
        entity_name = main_entity.get("name")
        entity_url = main_entity.get("url")

        if entity_type is not None and entity_type != "Person":
            return (
                UNKNOWN,
                None,
                "BookSusi mainEntity is not a Person"
            )

        if entity_name is not None and not isinstance(
            entity_name,
            str,
        ):
            return (
                UNKNOWN,
                None,
                "invalid BookSusi profile name"
            )

        if entity_url and not _booksusi_profile_url_matches(
            entity_url,
            username,
        ):
            return (
                UNKNOWN,
                None,
                "BookSusi mainEntity URL conflicts with the username"
            )

    title_matches = bool(
        re.search(r"(?:^|\s)booksusi$", title, re.IGNORECASE)
    )
    complete_main_entity = (
        isinstance(main_entity, dict)
        and main_entity.get("@type") == "Person"
        and isinstance(main_entity.get("name"), str)
        and bool(main_entity.get("name", "").strip())
        and _booksusi_profile_url_matches(
            main_entity.get("url"),
            username,
        )
    )

    if (
        canonical_matches
        and profile_container is not None
        and len(profile_pages) == 1
        and complete_main_entity
        and title_matches
    ):
        return (
            FOUND,
            profile_url,
            "public BookSusi profile found; identity not verified"
        )

    return (
        POSSIBLE,
        profile_url,
        "incomplete BookSusi profile evidence"
    )


# ============================================================
# SPECJALNA DETEKCJA PUBLICZNYCH PROFILI FANSLY
# ============================================================

def classify_fansly_profile_response(username, response):
    status_code = response.status_code

    if status_code == 429:
        return (
            RATE_LIMIT,
            None,
            "HTTP 429"
        )

    if status_code in (401, 403):
        return (
            BLOCKED,
            None,
            f"HTTP {status_code}"
        )

    if status_code >= 500:
        return (
            ERROR,
            None,
            f"HTTP {status_code}"
        )

    final_url = response.url.casefold()
    block_url_markers = (
        "/login",
        "/signin",
        "/challenge",
        "/captcha",
    )

    if any(
        marker in final_url
        for marker in block_url_markers
    ):
        return (
            BLOCKED,
            None,
            "Fansly login or challenge redirect"
        )

    try:
        payload = response.json()
    except (TypeError, ValueError):
        content = response.text.casefold()
        block_content_markers = (
            "captcha",
            "challenge",
            "access denied",
            "log in",
            "sign in",
        )

        if any(
            marker in content
            for marker in block_content_markers
        ):
            return (
                BLOCKED,
                None,
                "Fansly access challenge"
            )

        return (
            UNKNOWN,
            None,
            "Fansly response is not valid JSON"
        )

    if status_code >= 400:
        return (
            UNKNOWN,
            None,
            f"HTTP {status_code}"
        )

    if status_code != 200 or not isinstance(payload, dict):
        return (
            UNKNOWN,
            None,
            "unrecognized Fansly response"
        )

    if payload.get("success") is not True:
        return (
            UNKNOWN,
            None,
            "Fansly lookup was not successful"
        )

    accounts = payload.get("response")

    if not isinstance(accounts, list):
        return (
            UNKNOWN,
            None,
            "Fansly account list is missing"
        )

    if not accounts:
        return (
            NOT_FOUND,
            None,
            "no public Fansly profile in this lookup"
        )

    if len(accounts) != 1 or not isinstance(accounts[0], dict):
        return (
            UNKNOWN,
            None,
            "unexpected Fansly account data"
        )

    account = accounts[0]
    account_username = account.get("username")
    account_id = account.get("id")
    valid_account_id = (
        isinstance(account_id, (str, int))
        and not isinstance(account_id, bool)
        and bool(str(account_id).strip())
    )

    if (
        isinstance(account_username, str)
        and account_username.casefold() == username.casefold()
        and valid_account_id
    ):
        public_profile_url = (
            "https://fansly.com/"
            f"{quote(username, safe='')}"
        )

        return (
            FOUND,
            public_profile_url,
            "public Fansly profile found; identity not verified"
        )

    return (
        UNKNOWN,
        None,
        "incomplete or mismatched Fansly account data"
    )


# ============================================================
# SPECJALNA DETEKCJA PUBLICZNYCH PROFILI PORNHUB
# ============================================================

def _pornhub_url_matches(value, expected_path):
    if not value:
        return False

    parsed = urlparse(value)

    return (
        parsed.scheme == "https"
        and parsed.netloc.casefold()
        in ("pornhub.com", "www.pornhub.com")
        and unquote(parsed.path).rstrip("/").casefold()
        == expected_path.rstrip("/").casefold()
        and not parsed.query
        and not parsed.fragment
    )


def _pornhub_name_matches_slug(value, username):
    if not value:
        return False

    normalized_name = re.sub(
        r"[^a-z0-9]+",
        "-",
        value.casefold(),
    ).strip("-")

    return normalized_name == username.casefold()


def classify_pornhub_profile_response(username, response, profile_url):
    status_code = response.status_code
    profile_path = f"/pornstar/{username}"

    if status_code == 429:
        return (
            RATE_LIMIT,
            None,
            "HTTP 429"
        )

    if status_code in (401, 403):
        return (
            BLOCKED,
            None,
            f"HTTP {status_code}"
        )

    if status_code >= 500:
        return (
            ERROR,
            None,
            f"HTTP {status_code}"
        )

    final_url_lower = response.url.casefold()
    block_url_markers = (
        "/login",
        "/signin",
        "/challenge",
        "/captcha",
    )

    if any(
        marker in final_url_lower
        for marker in block_url_markers
    ):
        return (
            BLOCKED,
            None,
            "Pornhub login or challenge redirect"
        )

    soup = BeautifulSoup(response.text, "html.parser")
    title = (
        " ".join(soup.title.get_text(" ", strip=True).split())
        if soup.title
        else ""
    )
    title_lower = title.casefold()
    blocked_title_markers = (
        "access denied",
        "captcha",
        "challenge",
        "just a moment",
        "log in",
        "sign in",
    )

    if any(
        marker in title_lower
        for marker in blocked_title_markers
    ):
        return (
            BLOCKED,
            None,
            "Pornhub access challenge"
        )

    canonical = soup.find("link", rel="canonical")
    canonical_url = (
        canonical.get("href", "")
        if canonical
        else ""
    )
    final_url_matches = _pornhub_url_matches(
        response.url,
        profile_path,
    )
    canonical_matches = _pornhub_url_matches(
        canonical_url,
        profile_path,
    )

    profile_header = soup.select_one("section.topProfileHeader")
    profile_heading = (
        profile_header.find("h1", attrs={"itemprop": "name"})
        if profile_header
        else None
    )
    profile_name = (
        " ".join(
            profile_heading.get_text(" ", strip=True).split()
        )
        if profile_heading
        else ""
    )
    profile_name_matches = _pornhub_name_matches_slug(
        profile_name,
        username,
    )

    title_is_profile = (
        bool(profile_name)
        and title_lower.startswith(
            f"{profile_name.casefold()} porn"
        )
        and "video" in title_lower
        and title_lower.endswith("| pornhub")
    )
    verified_marker = (
        profile_header.find(
            attrs={
                "data-title": lambda value: (
                    value
                    and value.casefold()
                    in ("verified model", "verified pornstar")
                )
            }
        )
        if profile_header
        else None
    )
    exact_profile_link = any(
        _pornhub_url_matches(
            urljoin(response.url, node.get("href", "")),
            profile_path,
        )
        for node in soup.select("#mainMenuProfile a[href]")
    )

    response_history = getattr(response, "history", [])
    if not isinstance(response_history, (list, tuple)):
        response_history = []

    redirected_from_profile = any(
        history_item.status_code in (301, 302, 303, 307, 308)
        and _pornhub_url_matches(
            history_item.url,
            profile_path,
        )
        for history_item in response_history
    )
    final_url_is_catalog = _pornhub_url_matches(
        response.url,
        "/pornstars",
    )
    canonical_is_catalog = _pornhub_url_matches(
        canonical_url,
        "/pornstars",
    )
    requested_profile_markers = (
        canonical_matches
        or profile_name_matches
        or exact_profile_link
    )

    if (
        status_code == 200
        and redirected_from_profile
        and final_url_is_catalog
        and canonical_is_catalog
        and not requested_profile_markers
    ):
        return (
            NOT_FOUND,
            None,
            "no public Pornhub profile at this path"
        )

    if status_code >= 400:
        return (
            UNKNOWN,
            None,
            f"HTTP {status_code}"
        )

    if status_code != 200 or not final_url_matches:
        return (
            UNKNOWN,
            None,
            "unexpected Pornhub final URL"
        )

    if canonical_url and not canonical_matches:
        return (
            UNKNOWN,
            None,
            "Pornhub canonical does not match the profile"
        )

    if profile_name and not profile_name_matches:
        return (
            UNKNOWN,
            None,
            "Pornhub profile name conflicts with the username"
        )

    if (
        canonical_matches
        and title_is_profile
        and profile_name_matches
        and verified_marker is not None
        and exact_profile_link
    ):
        return (
            FOUND,
            profile_url,
            "public Pornhub profile found; identity not verified"
        )

    return (
        POSSIBLE,
        profile_url,
        "incomplete Pornhub profile evidence"
    )


# ============================================================
# SPECJALNA DETEKCJA PUBLICZNYCH PROFILI TINDER
# ============================================================

def _tinder_url_matches(value, expected_path):
    if not value:
        return False

    parsed = urlparse(value)

    return (
        parsed.scheme == "https"
        and parsed.netloc.casefold()
        in ("tinder.com", "www.tinder.com")
        and unquote(parsed.path).rstrip("/").casefold()
        == expected_path.rstrip("/").casefold()
        and not parsed.query
        and not parsed.fragment
    )


def _tinder_json_ld_people(soup):
    people = []

    for node in soup.find_all(
        "script",
        attrs={"type": "application/ld+json"},
    ):
        try:
            payload = json.loads(node.string or node.get_text())
        except (TypeError, ValueError):
            continue

        values = payload if isinstance(payload, list) else [payload]

        for value in values:
            if not isinstance(value, dict):
                continue

            candidates = [value]
            graph = value.get("@graph")
            if isinstance(graph, list):
                candidates.extend(graph)

            for candidate in candidates:
                if (
                    isinstance(candidate, dict)
                    and candidate.get("@type") == "Person"
                ):
                    people.append(candidate)

    return people


def classify_tinder_profile_response(username, response, profile_url):
    status_code = response.status_code
    profile_path = f"/@{username}"

    if status_code == 429:
        return (
            RATE_LIMIT,
            None,
            "HTTP 429"
        )

    if status_code in (401, 403):
        return (
            BLOCKED,
            None,
            f"HTTP {status_code}"
        )

    if status_code >= 500:
        return (
            ERROR,
            None,
            f"HTTP {status_code}"
        )

    final_url_lower = response.url.casefold()
    block_url_markers = (
        "/login",
        "/signin",
        "/auth",
        "/challenge",
        "/captcha",
    )

    if any(
        marker in final_url_lower
        for marker in block_url_markers
    ):
        return (
            BLOCKED,
            None,
            "Tinder login or challenge redirect"
        )

    soup = BeautifulSoup(response.text, "html.parser")
    title = (
        " ".join(soup.title.get_text(" ", strip=True).split())
        if soup.title
        else ""
    )
    title_lower = title.casefold()
    blocked_title_markers = (
        "access denied",
        "captcha",
        "challenge",
        "just a moment",
        "log in",
        "sign in",
    )

    if any(
        marker in title_lower
        for marker in blocked_title_markers
    ):
        return (
            BLOCKED,
            None,
            "Tinder access challenge"
        )

    canonical = soup.find("link", rel="canonical")
    canonical_url = (
        canonical.get("href", "")
        if canonical
        else ""
    )
    og_url_node = soup.find(
        "meta",
        attrs={"property": "og:url"},
    )
    og_url = (
        og_url_node.get("content", "")
        if og_url_node
        else ""
    )

    final_url_matches = _tinder_url_matches(
        response.url,
        profile_path,
    )
    canonical_matches = _tinder_url_matches(
        canonical_url,
        profile_path,
    )
    og_url_matches = _tinder_url_matches(
        og_url,
        profile_path,
    )
    final_url_is_home = _tinder_url_matches(
        response.url,
        "/",
    )
    canonical_is_home = _tinder_url_matches(
        canonical_url,
        "/",
    )
    og_url_is_home = _tinder_url_matches(
        og_url,
        "/",
    )

    exact_title_username = bool(
        re.search(
            rf"(?<![\w])@{re.escape(username)}(?![\w])",
            title,
            re.IGNORECASE,
        )
    )
    title_usernames = re.findall(
        r"@([A-Za-z0-9._-]+)",
        title,
    )
    title_username_conflict = (
        bool(title_usernames)
        and not exact_title_username
    )

    people = _tinder_json_ld_people(soup)
    matching_people = [
        person
        for person in people
        if (
            isinstance(person.get("alternateName"), str)
            and person["alternateName"].casefold()
            == username.casefold()
        )
    ]
    person_username_conflict = (
        bool(people)
        and not matching_people
    )

    general_page_without_profile = (
        (final_url_is_home or canonical_is_home or og_url_is_home)
        and canonical_is_home
        and og_url_is_home
        and not people
        and not exact_title_username
    )

    if general_page_without_profile:
        return (
            UNKNOWN,
            None,
            "no public Tinder profile exposed; account existence unknown"
        )

    if status_code >= 400:
        return (
            UNKNOWN,
            None,
            f"HTTP {status_code}"
        )

    if status_code != 200 or not final_url_matches:
        return (
            UNKNOWN,
            None,
            "unexpected Tinder final URL"
        )

    if canonical_url and not canonical_matches:
        return (
            UNKNOWN,
            None,
            "Tinder canonical does not match the profile"
        )

    if og_url and not og_url_matches:
        return (
            UNKNOWN,
            None,
            "Tinder og:url does not match the profile"
        )

    if title_username_conflict or person_username_conflict:
        return (
            UNKNOWN,
            None,
            "Tinder profile markers identify another username"
        )

    if (
        canonical_matches
        and og_url_matches
        and exact_title_username
        and len(matching_people) == 1
    ):
        return (
            FOUND,
            profile_url,
            "public Tinder profile found under this username; "
            "identity not verified"
        )

    return (
        POSSIBLE,
        profile_url,
        "incomplete Tinder profile evidence"
    )


# ============================================================
# SPECJALNA DETEKCJA KANALOW YOUTUBE
# ============================================================

def _youtube_handle_from_value(value):
    if not isinstance(value, str) or not value:
        return None

    decoded_value = unquote(value).strip()

    if decoded_value.startswith("@") and "/" not in decoded_value:
        return decoded_value[1:].casefold()

    parsed = urlparse(decoded_value)

    if parsed.netloc.lower() not in (
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
    ):
        return None

    path = unquote(parsed.path).strip("/")

    if path.startswith("@") and "/" not in path:
        return path[1:].casefold()

    return None


def _youtube_handle_url_matches(value, username):
    return _youtube_handle_from_value(value) == username.casefold()


def _youtube_channel_id_from_value(value):
    if not isinstance(value, str) or not value:
        return None

    decoded_value = unquote(value).strip()
    channel_id_pattern = re.compile(r"UC[A-Za-z0-9_-]{10,}")

    if channel_id_pattern.fullmatch(decoded_value):
        return decoded_value

    parsed = urlparse(decoded_value)
    path_parts = [
        part
        for part in unquote(parsed.path).split("/")
        if part
    ]

    for index, part in enumerate(path_parts[:-1]):
        if part.casefold() != "channel":
            continue

        candidate = path_parts[index + 1]

        if channel_id_pattern.fullmatch(candidate):
            return candidate

    return None


def _youtube_initial_data(html):
    markers = (
        "var ytInitialData =",
        "window[\"ytInitialData\"] =",
        "ytInitialData =",
    )

    for marker in markers:
        marker_position = html.find(marker)

        if marker_position < 0:
            continue

        json_text = html[marker_position + len(marker):].lstrip()

        try:
            return json.JSONDecoder().raw_decode(json_text)[0]
        except (json.JSONDecodeError, TypeError):
            continue

    return None


def _youtube_profile_renderers(initial_data):
    channel_renderers = []
    microformat_renderers = []
    canonical_base_urls = []

    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if (
                    key == "channelMetadataRenderer"
                    and isinstance(child, dict)
                ):
                    channel_renderers.append(child)
                elif (
                    key == "microformatDataRenderer"
                    and isinstance(child, dict)
                ):
                    microformat_renderers.append(child)
                elif (
                    key == "canonicalBaseUrl"
                    and isinstance(child, str)
                ):
                    canonical_base_urls.append(child)

                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    if initial_data is not None:
        walk(initial_data)

    return (
        channel_renderers,
        microformat_renderers,
        canonical_base_urls,
    )


def classify_youtube_channel_response(username, response, profile_url):
    status_code = response.status_code
    final_url = response.url or ""
    parsed_final_url = urlparse(final_url)
    final_host = parsed_final_url.netloc.casefold()
    final_path = unquote(parsed_final_url.path).casefold()

    blocked_hosts = (
        "consent.youtube.com",
        "accounts.google.com",
    )
    blocked_paths = (
        "/login",
        "/signin",
        "/challenge",
    )

    if (
        final_host in blocked_hosts
        or any(marker in final_path for marker in blocked_paths)
    ):
        return (
            BLOCKED,
            None,
            "YouTube consent, login or challenge redirect"
        )

    if status_code == 429:
        return (
            RATE_LIMIT,
            None,
            "HTTP 429"
        )

    if status_code in (401, 403):
        return (
            BLOCKED,
            None,
            f"HTTP {status_code}"
        )

    if status_code >= 500:
        return (
            ERROR,
            None,
            f"HTTP {status_code}"
        )

    soup = BeautifulSoup(response.text, "html.parser")
    title = (
        soup.title.get_text(" ", strip=True)
        if soup.title
        else ""
    )

    canonical_node = soup.find(
        "link",
        rel=lambda value: value and "canonical" in value,
    )
    canonical_url = (
        canonical_node.get("href", "")
        if canonical_node
        else ""
    )

    og_url_node = soup.find(
        "meta",
        attrs={"property": "og:url"},
    )
    og_url = (
        og_url_node.get("content", "")
        if og_url_node
        else ""
    )

    og_type_node = soup.find(
        "meta",
        attrs={"property": "og:type"},
    )
    og_type = (
        og_type_node.get("content", "")
        if og_type_node
        else ""
    )

    identifier_node = soup.find(
        "meta",
        attrs={"itemprop": "identifier"},
    )
    identifier = (
        identifier_node.get("content", "")
        if identifier_node
        else ""
    )

    initial_data = _youtube_initial_data(response.text)
    (
        channel_renderers,
        microformat_renderers,
        canonical_base_urls,
    ) = _youtube_profile_renderers(initial_data)

    metadata_handle_values = list(canonical_base_urls)
    microformat_handle_values = []
    json_channel_values = []

    for renderer in channel_renderers:
        owner_urls = renderer.get("ownerUrls", [])

        if isinstance(owner_urls, list):
            metadata_handle_values.extend(owner_urls)

        metadata_handle_values.append(
            renderer.get("vanityChannelUrl", "")
        )
        json_channel_values.extend((
            renderer.get("externalId", ""),
            renderer.get("channelUrl", ""),
        ))

    for renderer in microformat_renderers:
        json_channel_values.append(renderer.get("urlCanonical", ""))
        profile_page = (
            renderer
            .get("channelProfileMicroformatDetails", {})
            .get("profilePage", {})
        )

        if not isinstance(profile_page, dict):
            continue

        json_channel_values.append(profile_page.get("url", ""))
        main_entity = profile_page.get("mainEntity", {})

        if isinstance(main_entity, dict):
            microformat_handle_values.append(
                main_entity.get("alternateName", "")
            )
            json_channel_values.append(main_entity.get("url", ""))

    metadata_handles = {
        handle
        for value in metadata_handle_values
        if (handle := _youtube_handle_from_value(value))
    }
    microformat_handles = {
        handle
        for value in microformat_handle_values
        if (handle := _youtube_handle_from_value(value))
    }
    expected_handle = username.casefold()
    all_handles = metadata_handles | microformat_handles
    handle_conflict = bool(
        all_handles
        and all_handles != {expected_handle}
    )

    html_channel_ids = {
        channel_id
        for value in (canonical_url, og_url, identifier)
        if (channel_id := _youtube_channel_id_from_value(value))
    }
    json_channel_ids = {
        channel_id
        for value in json_channel_values
        if (channel_id := _youtube_channel_id_from_value(value))
    }
    all_channel_ids = html_channel_ids | json_channel_ids
    channel_id_conflict = len(all_channel_ids) > 1

    final_handle = _youtube_handle_from_value(final_url)
    final_url_matches = _youtube_handle_url_matches(
        final_url,
        username,
    )
    final_url_conflict = (
        final_handle is not None
        and not final_url_matches
    )

    if final_url_conflict or handle_conflict or channel_id_conflict:
        return (
            UNKNOWN,
            None,
            "YouTube handle or channel ID conflict"
        )

    title_is_channel = (
        title.casefold().endswith(" - youtube")
        and title.casefold() != "404 not found"
    )
    og_type_is_profile = og_type.casefold() == "profile"
    has_channel_renderer = bool(channel_renderers)
    metadata_handle_matches = metadata_handles == {expected_handle}
    microformat_handle_matches = (
        microformat_handles == {expected_handle}
    )
    channel_id_matches = (
        len(html_channel_ids) == 1
        and html_channel_ids == json_channel_ids
    )

    if (
        status_code == 404
        and final_url_matches
        and title.casefold() == "404 not found"
        and not html_channel_ids
        and initial_data is None
        and "ytInitialData" not in response.text
        and not channel_renderers
        and not microformat_renderers
        and not og_type_is_profile
    ):
        return (
            NOT_FOUND,
            None,
            "public YouTube channel not found at this handle"
        )

    if status_code >= 400:
        return (
            UNKNOWN,
            None,
            f"unconfirmed YouTube HTTP {status_code} response"
        )

    if status_code != 200 or not final_url_matches:
        return (
            UNKNOWN,
            None,
            "unexpected YouTube final URL"
        )

    if (
        metadata_handle_matches
        and microformat_handle_matches
        and channel_id_matches
        and title_is_channel
        and og_type_is_profile
        and has_channel_renderer
    ):
        return (
            FOUND,
            profile_url,
            "public YouTube channel found; identity not verified"
        )

    return (
        POSSIBLE,
        profile_url,
        "incomplete YouTube channel evidence"
    )


# ============================================================
# SPECJALNA DETEKCJA PROFILI FACEBOOK
# ============================================================

def _facebook_profile_url_matches(value, username):
    if not isinstance(value, str) or not value:
        return False

    parsed = urlparse(value)

    if parsed.scheme not in ("http", "https"):
        return False

    if parsed.netloc.casefold() not in (
        "facebook.com",
        "www.facebook.com",
        "m.facebook.com",
    ):
        return False

    return (
        unquote(parsed.path).strip("/").casefold()
        == username.casefold()
    )


def _facebook_deep_link_profile_id(value):
    if not isinstance(value, str):
        return None

    match = re.fullmatch(r"fb://profile/(\d+)", value.strip())

    if not match:
        return None

    return match.group(1)


def _facebook_normalized_title(value):
    return " ".join(value.split()).casefold()


def classify_facebook_profile_response(username, response, profile_url):
    status_code = response.status_code
    final_url = response.url or ""
    parsed_final_url = urlparse(final_url)
    final_path = unquote(parsed_final_url.path).casefold()

    blocked_paths = (
        "/login",
        "/checkpoint",
        "/challenge",
        "/captcha",
    )

    if any(marker in final_path for marker in blocked_paths):
        return (
            BLOCKED,
            None,
            "Facebook login, checkpoint or challenge redirect"
        )

    if status_code == 429:
        return (
            RATE_LIMIT,
            None,
            "HTTP 429"
        )

    if status_code in (401, 403):
        return (
            BLOCKED,
            None,
            f"HTTP {status_code}"
        )

    if status_code >= 500:
        return (
            ERROR,
            None,
            f"HTTP {status_code}"
        )

    soup = BeautifulSoup(response.text, "html.parser")
    title = (
        soup.title.get_text(" ", strip=True)
        if soup.title
        else ""
    )

    canonical_node = soup.find(
        "link",
        rel=lambda value: value and "canonical" in value,
    )
    canonical_url = (
        canonical_node.get("href", "")
        if canonical_node
        else ""
    )

    def meta_content(property_name):
        node = soup.find(
            "meta",
            attrs={"property": property_name},
        )

        return node.get("content", "") if node else ""

    og_url = meta_content("og:url")
    og_title = meta_content("og:title")
    android_url = meta_content("al:android:url")
    ios_url = meta_content("al:ios:url")

    clear_login_titles = {
        "log into facebook",
        "log in to facebook",
        "facebook – log in or sign up",
        "facebook - log in or sign up",
    }
    normalized_title = _facebook_normalized_title(title)
    login_form = soup.find(
        "form",
        action=lambda value: (
            isinstance(value, str)
            and "/login" in value.casefold()
        ),
    )

    if normalized_title in clear_login_titles and login_form:
        return (
            BLOCKED,
            None,
            "Facebook login wall"
        )

    route_names = set(re.findall(
        r'"canonicalRouteName":"([^"]+)"',
        response.text,
    ))
    error_route = (
        any("CometErrorRoute" in route for route in route_names)
        or (
            '"privacy":true' in response.text
            and '"tracePolicy":"comet.error"' in response.text
        )
    )

    if error_route:
        return (
            UNKNOWN,
            None,
            "Facebook error/privacy route; account existence unknown"
        )

    expected_username = username.casefold()
    user_vanities = {
        unquote(value).casefold()
        for value in re.findall(
            r'"userVanity":"([^"]+)"',
            response.text,
        )
    }
    route_vanities = {
        unquote(value).casefold()
        for value in re.findall(
            r'"vanity":"([^"]+)"',
            response.text,
        )
    }
    route_profile_paths = {
        unquote(value.replace("\\/", "/")).strip("/").casefold()
        for value in re.findall(
            r'"url":"((?:\\/|/)[^"?#]+)"',
            response.text,
        )
    }
    route_user_ids = set(re.findall(
        r'"userID":"(\d+)"',
        response.text,
    ))

    final_url_matches = _facebook_profile_url_matches(
        final_url,
        username,
    )
    canonical_matches = _facebook_profile_url_matches(
        canonical_url,
        username,
    )
    og_url_matches = _facebook_profile_url_matches(
        og_url,
        username,
    )

    final_path_parts = [
        part
        for part in unquote(parsed_final_url.path).split("/")
        if part
    ]
    final_url_conflict = (
        len(final_path_parts) == 1
        and final_path_parts[0].casefold() != expected_username
    )
    canonical_conflict = bool(
        canonical_url
        and not canonical_matches
    )
    og_url_conflict = bool(
        og_url
        and not og_url_matches
    )
    vanity_conflict = bool(
        (user_vanities and user_vanities != {expected_username})
        or (route_vanities and route_vanities != {expected_username})
    )
    route_path_conflict = bool(
        route_profile_paths
        and expected_username not in route_profile_paths
    )

    android_profile_id = _facebook_deep_link_profile_id(android_url)
    ios_profile_id = _facebook_deep_link_profile_id(ios_url)
    deep_link_ids = {
        profile_id
        for profile_id in (android_profile_id, ios_profile_id)
        if profile_id
    }
    profile_id_conflict = (
        bool(android_url and ios_url)
        and (
            not android_profile_id
            or not ios_profile_id
            or android_profile_id != ios_profile_id
        )
    ) or (
        bool(route_user_ids and deep_link_ids)
        and route_user_ids != deep_link_ids
    ) or len(route_user_ids) > 1

    title_conflict = bool(
        title
        and og_title
        and _facebook_normalized_title(title)
        != _facebook_normalized_title(og_title)
    )

    if (
        final_url_conflict
        or canonical_conflict
        or og_url_conflict
        or vanity_conflict
        or route_path_conflict
        or profile_id_conflict
        or title_conflict
    ):
        return (
            UNKNOWN,
            None,
            "Facebook profile evidence conflict"
        )

    if status_code >= 400:
        return (
            UNKNOWN,
            None,
            f"unconfirmed Facebook HTTP {status_code}; "
            "account existence unknown"
        )

    if status_code != 200 or not final_url_matches:
        return (
            UNKNOWN,
            None,
            "unexpected Facebook final URL; account existence unknown"
        )

    titles_match = bool(
        title
        and og_title
        and normalized_title != "facebook"
        and normalized_title
        == _facebook_normalized_title(og_title)
    )
    route_username_matches = (
        user_vanities == {expected_username}
        and route_vanities == {expected_username}
        and expected_username in route_profile_paths
    )
    profile_id_matches = (
        bool(android_profile_id)
        and bool(ios_profile_id)
        and len(deep_link_ids) == 1
        and deep_link_ids == route_user_ids
    )
    profile_root_matches = (
        "comet.fbweb.CometProfilePlusLoggedOutRoute" in route_names
        and "ProfilePlusCometLoggedOutRoot.react" in response.text
    )

    if (
        canonical_matches
        and og_url_matches
        and titles_match
        and route_username_matches
        and profile_id_matches
        and profile_root_matches
    ):
        return (
            FOUND,
            profile_url,
            "public Facebook profile found; identity not verified"
        )

    return (
        POSSIBLE,
        profile_url,
        "incomplete Facebook public profile evidence"
    )


# ============================================================
# SPECJALNA DETEKCJA PROFILI GITHUB
# ============================================================

def _github_profile_url_matches(value, username):
    if not isinstance(value, str) or not value:
        return False

    parsed = urlparse(value)

    return (
        parsed.scheme in ("http", "https")
        and parsed.netloc.casefold() in ("github.com", "www.github.com")
        and unquote(parsed.path).strip("/").casefold()
        == username.casefold()
    )


def _github_avatar_user_id(value):
    if not isinstance(value, str):
        return None

    parsed = urlparse(value)

    if parsed.netloc.casefold() not in (
        "avatars.githubusercontent.com",
        "avatars.github.com",
    ):
        return None

    match = re.fullmatch(r"/u/(\d+)", parsed.path.rstrip("/"))

    return match.group(1) if match else None


def classify_github_profile_response(username, response, profile_url):
    status_code = response.status_code
    final_url = response.url or ""
    parsed_final_url = urlparse(final_url)
    final_path = unquote(parsed_final_url.path).casefold()

    if any(
        marker in final_path
        for marker in ("/login", "/sessions", "/challenge", "/captcha")
    ):
        return (
            BLOCKED,
            None,
            "GitHub login or challenge redirect"
        )

    if status_code == 429:
        return (
            RATE_LIMIT,
            None,
            "HTTP 429"
        )

    if status_code in (401, 403):
        return (
            BLOCKED,
            None,
            f"HTTP {status_code}"
        )

    if status_code >= 500:
        return (
            ERROR,
            None,
            f"HTTP {status_code}"
        )

    soup = BeautifulSoup(response.text, "html.parser")
    title = (
        soup.title.get_text(" ", strip=True)
        if soup.title
        else ""
    )

    def meta_content(attribute, value):
        node = soup.find("meta", attrs={attribute: value})
        return node.get("content", "") if node else ""

    canonical_node = soup.find(
        "link",
        rel=lambda value: value and "canonical" in value,
    )
    canonical_url = (
        canonical_node.get("href", "")
        if canonical_node
        else ""
    )
    og_url = meta_content("property", "og:url")
    og_type = meta_content("property", "og:type")
    og_title = meta_content("property", "og:title")
    og_image = meta_content("property", "og:image")
    profile_username = meta_content("property", "profile:username")
    metadata_login = meta_content(
        "name",
        "octolytics-dimension-user_login",
    )
    metadata_user_id = meta_content(
        "name",
        "octolytics-dimension-user_id",
    )

    expected_username = username.casefold()
    final_url_matches = _github_profile_url_matches(final_url, username)
    canonical_matches = _github_profile_url_matches(
        canonical_url,
        username,
    )
    og_url_matches = _github_profile_url_matches(og_url, username)

    final_url_conflict = bool(
        parsed_final_url.netloc.casefold()
        in ("github.com", "www.github.com")
        and unquote(parsed_final_url.path).strip("/")
        and not final_url_matches
    )
    canonical_conflict = bool(canonical_url and not canonical_matches)
    og_url_conflict = bool(og_url and not og_url_matches)

    public_usernames = {
        value.casefold()
        for value in (profile_username, metadata_login)
        if value
    }
    username_conflict = bool(
        public_usernames
        and public_usernames != {expected_username}
    )

    avatar_user_id = _github_avatar_user_id(og_image)
    stable_user_ids = {
        value
        for value in (metadata_user_id, avatar_user_id)
        if value
    }
    stable_id_conflict = (
        len(stable_user_ids) > 1
        or bool(metadata_user_id and not metadata_user_id.isdigit())
    )

    profile_schema = soup.find(
        attrs={
            "itemtype": re.compile(
                r"^https?://schema\.org/(?:Person|Organization)$"
            )
        }
    )
    profile_marker_present = bool(profile_schema)
    profile_evidence_present = any((
        canonical_url,
        og_url,
        profile_username,
        metadata_login,
        metadata_user_id,
        avatar_user_id,
        profile_marker_present,
    ))

    if (
        final_url_conflict
        or canonical_conflict
        or og_url_conflict
        or username_conflict
        or stable_id_conflict
    ):
        return (
            UNKNOWN,
            None,
            "GitHub profile evidence conflict"
        )

    not_found_marker = (
        response.text.strip().casefold() == "not found"
        or "page not found" in title.casefold()
        or title.casefold() == "404 · github"
    )

    if (
        status_code == 404
        and final_url_matches
        and not_found_marker
        and not profile_evidence_present
        and og_type.casefold() != "profile"
    ):
        return (
            NOT_FOUND,
            None,
            "public GitHub profile not found at this username"
        )

    if status_code >= 400:
        return (
            UNKNOWN,
            None,
            f"unconfirmed GitHub HTTP {status_code} response"
        )

    if status_code != 200 or not final_url_matches:
        return (
            UNKNOWN,
            None,
            "unexpected GitHub final URL"
        )

    title_matches = (
        title.casefold().startswith(expected_username)
        and title.casefold().endswith("· github")
    )
    og_title_matches = bool(
        og_title
        and og_title.casefold().startswith(expected_username)
    )
    username_matches = (
        profile_username.casefold() == expected_username
        and (
            not metadata_login
            or metadata_login.casefold() == expected_username
        )
    )
    stable_id_present = bool(
        avatar_user_id
        and len(stable_user_ids) == 1
    )

    if (
        canonical_matches
        and og_url_matches
        and og_type.casefold() == "profile"
        and title_matches
        and og_title_matches
        and username_matches
        and profile_marker_present
        and stable_id_present
    ):
        return (
            FOUND,
            profile_url,
            "public GitHub profile found; identity not verified"
        )

    return (
        POSSIBLE,
        profile_url,
        "incomplete GitHub public profile evidence"
    )


# ============================================================
# SPECJALNA DETEKCJA PROFILI INSTAGRAM
# ============================================================

def _instagram_profile_url_matches(value, username):
    if not isinstance(value, str) or not value:
        return False

    parsed = urlparse(value)

    return (
        parsed.scheme in ("http", "https")
        and parsed.netloc.casefold()
        in ("instagram.com", "www.instagram.com")
        and unquote(parsed.path).strip("/").casefold()
        == username.casefold()
    )


def _instagram_ios_username(value):
    if not isinstance(value, str):
        return None

    match = re.fullmatch(
        r"instagram://user\?username=([^&]+)",
        value.strip(),
        re.IGNORECASE,
    )

    return unquote(match.group(1)).casefold() if match else None


def _instagram_android_username(value):
    if not isinstance(value, str) or not value:
        return None

    parsed = urlparse(value)

    if parsed.netloc.casefold() not in (
        "instagram.com",
        "www.instagram.com",
    ):
        return None

    path_parts = [
        part
        for part in unquote(parsed.path).split("/")
        if part
    ]

    if len(path_parts) == 2 and path_parts[0].casefold() == "_u":
        return path_parts[1].casefold()

    return None


def classify_instagram_profile_response(username, response, profile_url):
    status_code = response.status_code
    final_url = response.url or ""
    parsed_final_url = urlparse(final_url)
    final_path = unquote(parsed_final_url.path).casefold()

    blocked_paths = (
        "/accounts/login",
        "/challenge",
        "/checkpoint",
        "/captcha",
    )

    if any(marker in final_path for marker in blocked_paths):
        return (
            BLOCKED,
            None,
            "Instagram login or challenge redirect"
        )

    if status_code == 429:
        return (
            RATE_LIMIT,
            None,
            "HTTP 429"
        )

    if status_code in (401, 403):
        return (
            BLOCKED,
            None,
            f"HTTP {status_code}"
        )

    if status_code >= 500:
        return (
            ERROR,
            None,
            f"HTTP {status_code}"
        )

    soup = BeautifulSoup(response.text, "html.parser")
    title = (
        soup.title.get_text(" ", strip=True)
        if soup.title
        else ""
    )

    def meta_content(property_name):
        node = soup.find("meta", attrs={"property": property_name})
        return node.get("content", "") if node else ""

    canonical_node = soup.find(
        "link",
        rel=lambda value: value and "canonical" in value,
    )
    canonical_url = (
        canonical_node.get("href", "")
        if canonical_node
        else ""
    )
    og_url = meta_content("og:url")
    og_type = meta_content("og:type")
    og_title = meta_content("og:title")
    ios_url = meta_content("al:ios:url")
    android_url = meta_content("al:android:url")

    expected_username = username.casefold()
    final_url_matches = _instagram_profile_url_matches(
        final_url,
        username,
    )
    canonical_matches = _instagram_profile_url_matches(
        canonical_url,
        username,
    )
    og_url_matches = _instagram_profile_url_matches(og_url, username)

    final_username = (
        unquote(parsed_final_url.path).strip("/").casefold()
        if parsed_final_url.netloc.casefold()
        in ("instagram.com", "www.instagram.com")
        else None
    )
    final_url_conflict = bool(
        final_username
        and final_username != expected_username
    )
    canonical_conflict = bool(canonical_url and not canonical_matches)
    og_url_conflict = bool(og_url and not og_url_matches)

    ios_username = _instagram_ios_username(ios_url)
    android_username = _instagram_android_username(android_url)
    deep_link_usernames = {
        value
        for value in (ios_username, android_username)
        if value
    }

    route_usernames = {
        unquote(value).casefold()
        for value in re.findall(
            r'"params"\s*:\s*\{\s*"username"\s*:\s*"([^"]+)"',
            response.text,
        )
    }
    username_conflict = bool(
        (deep_link_usernames and deep_link_usernames != {expected_username})
        or (route_usernames and route_usernames != {expected_username})
    )

    props_profile_ids = set(re.findall(
        r'PolarisProfilePostsTabRoot\.react"\s*\}\s*,'
        r'\s*"props"\s*:\s*\{\s*"id"\s*:\s*"(\d+)"',
        response.text,
    ))
    page_profile_ids = set(re.findall(
        r'profilePage_(\d+)',
        response.text,
    ))
    logging_profile_ids = set(re.findall(
        r'"profile_id"\s*:\s*"(\d+)"',
        response.text,
    ))
    stable_profile_ids = (
        props_profile_ids
        | page_profile_ids
        | logging_profile_ids
    )
    stable_id_conflict = len(stable_profile_ids) > 1

    profile_root_present = (
        "PolarisLoggedOutDesktopWWWProfileRoot.react" in response.text
        and "PolarisProfilePostsTabRoot.react" in response.text
        and (
            "comet.igweb.PolarisLoggedOutDesktopWWWProfileRoute"
            in response.text
        )
    )
    profile_json_complete = (
        len(props_profile_ids) == 1
        and props_profile_ids == page_profile_ids
        and props_profile_ids == logging_profile_ids
    )

    handle_pattern = re.compile(
        rf"\(@{re.escape(username)}\)\s*[•·]",
        re.IGNORECASE,
    )
    title_matches = bool(handle_pattern.search(title))
    og_title_matches = bool(handle_pattern.search(og_title))

    clear_login_wall = (
        title.casefold() in ("login • instagram", "log in • instagram")
        and bool(soup.find("form", action=re.compile(r"/accounts/login")))
        and not canonical_url
        and not og_url
    )

    if clear_login_wall:
        return (
            BLOCKED,
            None,
            "Instagram login wall"
        )

    if (
        final_url_conflict
        or canonical_conflict
        or og_url_conflict
        or username_conflict
        or stable_id_conflict
    ):
        return (
            UNKNOWN,
            None,
            "Instagram profile evidence conflict"
        )

    if status_code >= 400:
        return (
            UNKNOWN,
            None,
            f"unconfirmed Instagram HTTP {status_code}; "
            "account existence unknown"
        )

    if status_code != 200 or not final_url_matches:
        return (
            UNKNOWN,
            None,
            "unexpected Instagram final URL; account existence unknown"
        )

    if (
        canonical_matches
        and og_url_matches
        and og_type.casefold() == "profile"
        and title_matches
        and og_title_matches
        and route_usernames == {expected_username}
        and profile_root_present
        and profile_json_complete
        and len(stable_profile_ids) == 1
        and (
            not deep_link_usernames
            or deep_link_usernames == {expected_username}
        )
    ):
        return (
            FOUND,
            profile_url,
            "public Instagram profile found; identity not verified"
        )

    soft_not_found = (
        title.casefold() == "instagram"
        and not canonical_url
        and not og_url
        and not route_usernames
        and not stable_profile_ids
        and not profile_root_present
    )

    if soft_not_found:
        return (
            UNKNOWN,
            None,
            "Instagram public profile not confirmed; "
            "account existence unknown"
        )

    return (
        POSSIBLE,
        profile_url,
        "incomplete Instagram public profile evidence"
    )


# ============================================================
# WSPOLNE NARZEDZIA DLA PUBLICZNYCH PROFILI
# ============================================================

def _public_profile_url_matches(value, hosts, expected_path):
    if not isinstance(value, str) or not value:
        return False

    parsed = urlparse(value)

    return (
        parsed.scheme in ("http", "https")
        and parsed.netloc.casefold() in hosts
        and unquote(parsed.path).rstrip("/").casefold()
        == expected_path.rstrip("/").casefold()
    )


def _public_profile_meta(soup, attribute, name):
    node = soup.find("meta", attrs={attribute: name})
    return node.get("content", "") if node else ""


def _public_profile_canonical(soup):
    node = soup.find(
        "link",
        rel=lambda value: value and "canonical" in value,
    )
    return node.get("href", "") if node else ""


def _walk_public_json(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_public_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_public_json(child)


def _public_json_ld_documents(soup):
    documents = []

    for node in soup.select('script[type="application/ld+json"]'):
        try:
            documents.append(json.loads(node.string or node.get_text()))
        except (TypeError, json.JSONDecodeError):
            continue

    return documents


def _public_profile_transport_result(response, service_name):
    status_code = response.status_code
    final_path = unquote(urlparse(response.url or "").path).casefold()

    if status_code == 429:
        return RATE_LIMIT, None, "HTTP 429"

    if status_code in (401, 403):
        return BLOCKED, None, f"HTTP {status_code}"

    if status_code >= 500:
        return ERROR, None, f"HTTP {status_code}"

    blocked_paths = (
        "/login",
        "/signin",
        "/checkpoint",
        "/challenge",
        "/captcha",
        "/accounts/login",
    )

    if any(
        final_path == marker or final_path.startswith(f"{marker}/")
        for marker in blocked_paths
    ):
        return (
            BLOCKED,
            None,
            f"{service_name} login or challenge redirect",
        )

    return None


# ============================================================
# SPECJALNA DETEKCJA PROFILI PINTEREST
# ============================================================

def _pinterest_profile_url_matches(value, username):
    return _public_profile_url_matches(
        value,
        ("pinterest.com", "www.pinterest.com"),
        f"/{username}",
    )


def classify_pinterest_profile_response(username, response, profile_url):
    transport_result = _public_profile_transport_result(
        response,
        "Pinterest",
    )
    if transport_result:
        return transport_result

    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    canonical_url = _public_profile_canonical(soup)
    og_url = _public_profile_meta(soup, "property", "og:url")
    og_type = _public_profile_meta(soup, "property", "og:type")
    expected_username = username.casefold()

    final_matches = _pinterest_profile_url_matches(response.url, username)
    canonical_matches = _pinterest_profile_url_matches(
        canonical_url,
        username,
    )
    og_url_matches = _pinterest_profile_url_matches(og_url, username)

    profile_pages = [
        item
        for document in _public_json_ld_documents(soup)
        for item in _walk_public_json(document)
        if item.get("@type") == "ProfilePage"
    ]
    profile_people = [
        item.get("mainEntity")
        for item in profile_pages
        if isinstance(item.get("mainEntity"), dict)
        and item["mainEntity"].get("@type") == "Person"
    ]

    schema_usernames = {
        item.get("alternateName", "").casefold()
        for item in profile_people
        if isinstance(item.get("alternateName"), str)
        and item.get("alternateName")
    }
    schema_urls = {
        value
        for item in profile_people
        for value in (item.get("url"), item.get("identifier"))
        if isinstance(value, str) and value
    }

    initial_props = {}
    initial_props_node = soup.select_one("script#__PWS_INITIAL_PROPS__")
    if initial_props_node:
        try:
            initial_props = json.loads(
                initial_props_node.string or initial_props_node.get_text()
            )
        except (TypeError, json.JSONDecodeError):
            initial_props = {}

    matching_users = [
        item
        for item in _walk_public_json(initial_props)
        if isinstance(item.get("username"), str)
        and item.get("username", "").casefold() == expected_username
        and item.get("type") == "user"
    ]
    stable_ids = {
        str(item.get("id"))
        for item in matching_users
        if item.get("id") not in (None, "")
    }
    invalid_stable_id = any(not value.isdigit() for value in stable_ids)

    final_conflict = bool(response.url and not final_matches)
    canonical_conflict = bool(canonical_url and not canonical_matches)
    og_url_conflict = bool(og_url and not og_url_matches)
    schema_username_conflict = bool(
        schema_usernames and schema_usernames != {expected_username}
    )
    schema_url_conflict = any(
        not _pinterest_profile_url_matches(value, username)
        for value in schema_urls
    )
    stable_id_conflict = len(stable_ids) > 1 or invalid_stable_id

    if any((
        final_conflict,
        canonical_conflict,
        og_url_conflict,
        schema_username_conflict,
        schema_url_conflict,
        stable_id_conflict,
    )):
        return UNKNOWN, None, "Pinterest profile evidence conflict"

    if response.status_code >= 400:
        return (
            UNKNOWN,
            None,
            f"unconfirmed Pinterest HTTP {response.status_code}; "
            "account existence unknown",
        )

    if response.status_code != 200 or not final_matches:
        return UNKNOWN, None, "unexpected Pinterest final URL"

    title_matches = f"({username})".casefold() in title.casefold()
    schema_matches = (
        len(profile_pages) == 1
        and len(profile_people) == 1
        and schema_usernames == {expected_username}
        and bool(schema_urls)
        and all(
            _pinterest_profile_url_matches(value, username)
            for value in schema_urls
        )
    )

    if (
        canonical_matches
        and og_url_matches
        and og_type.casefold() == "profile"
        and title_matches
        and schema_matches
        and len(stable_ids) == 1
    ):
        return (
            FOUND,
            profile_url,
            "public Pinterest profile found; identity not verified",
        )

    profile_evidence = any((
        canonical_url,
        og_url,
        profile_pages,
        matching_users,
    ))
    if profile_evidence:
        return (
            POSSIBLE,
            profile_url,
            "incomplete Pinterest public profile evidence",
        )

    return (
        UNKNOWN,
        None,
        "Pinterest public profile not confirmed; account existence unknown",
    )


# ============================================================
# SPECJALNA DETEKCJA KANALOW TWITCH
# ============================================================

def _twitch_profile_url_matches(value, username):
    return _public_profile_url_matches(
        value,
        ("twitch.tv", "www.twitch.tv"),
        f"/{username}",
    )


def _twitch_profile_image_id(value):
    if not isinstance(value, str) or not value:
        return None

    parsed = urlparse(value)
    if parsed.netloc.casefold() != "static-cdn.jtvnw.net":
        return None

    match = re.search(
        r"/jtv_user_pictures/"
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
        r"[0-9a-f]{4}-[0-9a-f]{12})-profile_image-",
        parsed.path,
        re.IGNORECASE,
    )
    return match.group(1).casefold() if match else None


def classify_twitch_profile_response(username, response, profile_url):
    transport_result = _public_profile_transport_result(
        response,
        "Twitch",
    )
    if transport_result:
        return transport_result

    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    canonical_url = _public_profile_canonical(soup)
    og_url = _public_profile_meta(soup, "property", "og:url")
    og_type = _public_profile_meta(soup, "property", "og:type")
    og_image = _public_profile_meta(soup, "property", "og:image")
    expected_username = username.casefold()

    final_matches = _twitch_profile_url_matches(response.url, username)
    canonical_matches = _twitch_profile_url_matches(
        canonical_url,
        username,
    )
    og_url_matches = _twitch_profile_url_matches(og_url, username)

    profile_people = []
    for document in _public_json_ld_documents(soup):
        for item in _walk_public_json(document):
            if item.get("@type") != "ProfilePage":
                continue
            person = item.get("mainEntity")
            if isinstance(person, dict) and person.get("@type") == "Person":
                profile_people.append(person)

    public_usernames = {
        item.get("alternateName", "").casefold()
        for item in profile_people
        if isinstance(item.get("alternateName"), str)
        and item.get("alternateName")
    }
    public_urls = {
        item.get("url")
        for item in profile_people
        if isinstance(item.get("url"), str) and item.get("url")
    }
    profile_image_ids = {
        value
        for value in (
            *(
                _twitch_profile_image_id(item.get("image"))
                for item in profile_people
            ),
            _twitch_profile_image_id(og_image),
        )
        if value
    }

    conflicts = (
        bool(response.url and not final_matches),
        bool(canonical_url and not canonical_matches),
        bool(og_url and not og_url_matches),
        bool(public_usernames and public_usernames != {expected_username}),
        any(
            not _twitch_profile_url_matches(value, username)
            for value in public_urls
        ),
        len(profile_image_ids) > 1,
    )
    if any(conflicts):
        return UNKNOWN, None, "Twitch channel evidence conflict"

    if response.status_code >= 400:
        return (
            UNKNOWN,
            None,
            f"unconfirmed Twitch HTTP {response.status_code}; "
            "channel existence unknown",
        )

    if response.status_code != 200 or not final_matches:
        return UNKNOWN, None, "unexpected Twitch final URL"

    if (
        canonical_matches
        and og_url_matches
        and og_type.casefold() == "profile"
        and title.casefold().endswith(" - twitch")
        and title.casefold() != "twitch"
        and len(profile_people) == 1
        and public_usernames == {expected_username}
        and len(public_urls) == 1
        and len(profile_image_ids) == 1
    ):
        return (
            FOUND,
            profile_url,
            "public Twitch channel found; identity not verified",
        )

    profile_evidence = any((
        canonical_url,
        og_url,
        profile_people,
        og_type.casefold() == "profile",
    ))
    if profile_evidence:
        return (
            POSSIBLE,
            profile_url,
            "incomplete Twitch public channel evidence",
        )

    return (
        UNKNOWN,
        None,
        "Twitch public channel not confirmed; account existence unknown",
    )


# ============================================================
# SPECJALNA DETEKCJA PROFILI SOUNDCLOUD
# ============================================================

def _soundcloud_profile_url_matches(value, username):
    return _public_profile_url_matches(
        value,
        ("soundcloud.com", "www.soundcloud.com"),
        f"/{username}",
    )


def _soundcloud_profile_reference_matches(value, username):
    if isinstance(value, str) and value.startswith("/"):
        return (
            unquote(value).rstrip("/").casefold()
            == f"/{username}".casefold()
        )

    return _soundcloud_profile_url_matches(value, username)


def _soundcloud_hydrated_users(soup):
    for node in soup.find_all("script"):
        script = node.string or node.get_text()
        marker_position = script.find("window.__sc_hydration")
        if marker_position < 0:
            continue

        assignment_position = script.find("=", marker_position)
        if assignment_position < 0:
            continue

        try:
            hydration, _ = json.JSONDecoder().raw_decode(
                script[assignment_position + 1:].lstrip()
            )
        except json.JSONDecodeError:
            continue

        if not isinstance(hydration, list):
            continue

        return [
            item.get("data")
            for item in hydration
            if isinstance(item, dict)
            and item.get("hydratable") == "user"
            and isinstance(item.get("data"), dict)
        ]

    return []


def _soundcloud_deep_link_id(value):
    if not isinstance(value, str):
        return None

    match = re.fullmatch(r"soundcloud://users:(\d+)", value.strip())
    return match.group(1) if match else None


def classify_soundcloud_profile_response(username, response, profile_url):
    transport_result = _public_profile_transport_result(
        response,
        "SoundCloud",
    )
    if transport_result:
        return transport_result

    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    canonical_url = _public_profile_canonical(soup)
    og_url = _public_profile_meta(soup, "property", "og:url")
    og_type = _public_profile_meta(soup, "property", "og:type")
    expected_username = username.casefold()

    final_matches = _soundcloud_profile_url_matches(response.url, username)
    canonical_matches = _soundcloud_profile_url_matches(
        canonical_url,
        username,
    )
    og_url_matches = _soundcloud_profile_url_matches(og_url, username)
    hydrated_users = _soundcloud_hydrated_users(soup)
    profile_user = hydrated_users[0] if len(hydrated_users) == 1 else None

    public_slugs = {
        str(item.get("permalink")).casefold()
        for item in hydrated_users
        if item.get("permalink")
    }
    public_urls = {
        value
        for item in hydrated_users
        for value in (item.get("permalink_url"), item.get("url"))
        if isinstance(value, str) and value
    }
    stable_ids = {
        str(item.get("id"))
        for item in hydrated_users
        if item.get("id") not in (None, "")
    }
    referenced_ids = set()
    for item in hydrated_users:
        urn_match = re.fullmatch(
            r"soundcloud:users:(\d+)",
            str(item.get("urn", "")),
        )
        uri_match = re.fullmatch(
            r"https://api\.soundcloud\.com/users/"
            r"soundcloud%3Ausers%3A(\d+)",
            str(item.get("uri", "")),
            re.IGNORECASE,
        )
        if urn_match:
            referenced_ids.add(urn_match.group(1))
        if uri_match:
            referenced_ids.add(uri_match.group(1))

    deep_link_ids = {
        value
        for node in soup.find_all(
            "meta",
            attrs={"property": re.compile(r"^al:(?:ios|android):url$")},
        )
        for value in (_soundcloud_deep_link_id(node.get("content", "")),)
        if value
    }

    stable_id_conflict = (
        len(stable_ids) > 1
        or len(referenced_ids) > 1
        or len(deep_link_ids) > 1
        or bool(stable_ids and referenced_ids and stable_ids != referenced_ids)
        or bool(stable_ids and deep_link_ids and stable_ids != deep_link_ids)
        or any(not value.isdigit() for value in stable_ids)
    )
    conflicts = (
        bool(response.url and not final_matches),
        bool(canonical_url and not canonical_matches),
        bool(og_url and not og_url_matches),
        bool(public_slugs and public_slugs != {expected_username}),
        any(
            not _soundcloud_profile_reference_matches(value, username)
            for value in public_urls
        ),
        stable_id_conflict,
    )
    if any(conflicts):
        return UNKNOWN, None, "SoundCloud profile evidence conflict"

    profile_evidence = any((
        canonical_url,
        og_url,
        hydrated_users,
        og_type,
    ))
    not_found_title = title.casefold().startswith("soundcloud - hear the")
    if (
        response.status_code == 404
        and final_matches
        and not_found_title
        and not profile_evidence
    ):
        return NOT_FOUND, None, "public SoundCloud profile not found"

    if response.status_code >= 400:
        return UNKNOWN, None, f"unconfirmed SoundCloud HTTP {response.status_code}"

    if response.status_code != 200 or not final_matches:
        return UNKNOWN, None, "unexpected SoundCloud final URL"

    profile_complete = bool(
        profile_user
        and profile_user.get("kind") == "user"
        and public_slugs == {expected_username}
        and stable_ids
        and stable_ids == referenced_ids
        and stable_ids == deep_link_ids
    )
    if (
        canonical_matches
        and og_url_matches
        and og_type.casefold() == "music.musician"
        and profile_complete
    ):
        return (
            FOUND,
            profile_url,
            "public SoundCloud profile found; identity not verified",
        )

    return (
        POSSIBLE,
        profile_url,
        "incomplete SoundCloud public profile evidence",
    )


# ============================================================
# SPECJALNA DETEKCJA PROFILI XING
# ============================================================

def _xing_profile_url_matches(value, username):
    return _public_profile_url_matches(
        value,
        ("xing.com", "www.xing.com"),
        f"/profile/{username}",
    )


def _xing_runtime_state(soup):
    node = soup.select_one("script#runtime-config")
    if not node:
        return {}

    script = node.string or node.get_text()
    marker = "window.crate="
    marker_position = script.find(marker)
    if marker_position < 0:
        return {}

    serialized = script[marker_position + len(marker):]
    serialized = re.sub(r"\bundefined\b", "null", serialized)

    try:
        value, _ = json.JSONDecoder().raw_decode(serialized)
    except json.JSONDecodeError:
        return {}

    return value if isinstance(value, dict) else {}


def _xing_profile_record(runtime_state, username):
    state = (
        runtime_state.get("serverData", {}).get("APOLLO_STATE", {})
        if isinstance(runtime_state, dict)
        else {}
    )
    if not isinstance(state, dict):
        return None

    root_query = state.get("ROOT_QUERY", {})
    references = []
    if isinstance(root_query, dict):
        for key, value in root_query.items():
            if not key.startswith("xingIdWithError("):
                continue
            if f'"id":"{username}"'.casefold() not in key.casefold():
                continue
            if isinstance(value, dict) and isinstance(value.get("__ref"), str):
                references.append(value["__ref"])

    records = [
        state.get(reference)
        for reference in references
        if isinstance(state.get(reference), dict)
    ]
    return records[0] if len(records) == 1 else None


def classify_xing_profile_response(username, response, profile_url):
    transport_result = _public_profile_transport_result(response, "XING")
    if transport_result:
        return transport_result

    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    canonical_url = _public_profile_canonical(soup)
    og_url = _public_profile_meta(soup, "property", "og:url")
    og_type = _public_profile_meta(soup, "property", "og:type")
    expected_username = username.casefold()

    final_matches = _xing_profile_url_matches(response.url, username)
    canonical_matches = _xing_profile_url_matches(canonical_url, username)
    og_url_matches = _xing_profile_url_matches(og_url, username)

    runtime_state = _xing_runtime_state(soup)
    profile_record = _xing_profile_record(runtime_state, username)
    record_username = (
        str(profile_record.get("pageName", "")).casefold()
        if profile_record
        else ""
    )
    stable_id = str(profile_record.get("id", "")) if profile_record else ""
    stable_id_valid = bool(
        re.fullmatch(r"\d+\.[0-9a-f]+", stable_id, re.IGNORECASE)
    )

    profile_people = [
        item
        for document in _public_json_ld_documents(soup)
        for item in _walk_public_json(document)
        if item.get("@type") == "Person"
    ]
    schema_urls = {
        item.get("sameAs")
        for item in profile_people
        if isinstance(item.get("sameAs"), str) and item.get("sameAs")
    }

    conflicts = (
        bool(response.url and not final_matches),
        bool(canonical_url and not canonical_matches),
        bool(og_url and not og_url_matches),
        bool(record_username and record_username != expected_username),
        bool(stable_id and not stable_id_valid),
        any(
            not _xing_profile_url_matches(value, username)
            for value in schema_urls
        ),
    )
    if any(conflicts):
        return UNKNOWN, None, "XING profile evidence conflict"

    profile_evidence = any((
        canonical_url,
        og_url,
        profile_record,
        profile_people,
        og_type,
    ))
    if (
        response.status_code == 404
        and final_matches
        and title.casefold() == "404 - not found | xing"
        and not profile_evidence
    ):
        return NOT_FOUND, None, "public XING profile not found"

    if response.status_code >= 400:
        return UNKNOWN, None, f"unconfirmed XING HTTP {response.status_code}"

    if response.status_code != 200 or not final_matches:
        return UNKNOWN, None, "unexpected XING final URL"

    if (
        canonical_matches
        and og_url_matches
        and og_type.casefold() == "profile"
        and title.casefold().endswith("| xing")
        and record_username == expected_username
        and stable_id_valid
    ):
        return (
            FOUND,
            profile_url,
            "public XING profile found; identity not verified",
        )

    return (
        POSSIBLE,
        profile_url,
        "incomplete XING public profile evidence",
    )


# ============================================================
# SPECJALNA DETEKCJA PUBLICZNYCH PROFILI SNAPCHAT
# ============================================================

def _snapchat_profile_url_matches(value, username):
    return _public_profile_url_matches(
        value,
        ("snapchat.com", "www.snapchat.com"),
        f"/@{username}",
    )


def _snapchat_public_profile(soup):
    node = soup.select_one("script#__NEXT_DATA__")
    if not node:
        return {}, ""

    try:
        data = json.loads(node.string or node.get_text())
    except (TypeError, json.JSONDecodeError):
        return {}, ""

    user_profile = data.get("props", {}).get("pageProps", {}).get(
        "userProfile",
        {},
    )
    if not isinstance(user_profile, dict):
        return {}, ""

    public_profile = user_profile.get("publicProfileInfo", {})
    if not isinstance(public_profile, dict):
        public_profile = {}

    return public_profile, str(user_profile.get("$case", ""))


def classify_snapchat_profile_response(username, response, profile_url):
    transport_result = _public_profile_transport_result(
        response,
        "Snapchat",
    )
    if transport_result:
        return transport_result

    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    canonical_url = _public_profile_canonical(soup)
    og_url = _public_profile_meta(soup, "property", "og:url")
    expected_username = username.casefold()

    final_matches = _snapchat_profile_url_matches(response.url, username)
    canonical_matches = _snapchat_profile_url_matches(
        canonical_url,
        username,
    )
    og_url_matches = _snapchat_profile_url_matches(og_url, username)

    public_profile, profile_case = _snapchat_public_profile(soup)
    public_username = str(public_profile.get("username", "")).casefold()
    stable_ids = [
        str(public_profile.get(key, ""))
        for key in ("businessProfileId", "hostUserId")
        if public_profile.get(key)
    ]
    uuid_pattern = re.compile(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
        r"[0-9a-f]{4}-[0-9a-f]{12}",
        re.IGNORECASE,
    )
    invalid_stable_id = any(
        not uuid_pattern.fullmatch(value)
        for value in stable_ids
    )

    profile_pages = [
        item
        for document in _public_json_ld_documents(soup)
        for item in _walk_public_json(document)
        if item.get("@type") == "ProfilePage"
    ]
    profile_people = [
        item.get("mainEntity")
        for item in profile_pages
        if isinstance(item.get("mainEntity"), dict)
        and item["mainEntity"].get("@type") in ("Person", "Organization")
    ]
    schema_usernames = {
        item.get("alternateName", "").casefold()
        for item in profile_people
        if isinstance(item.get("alternateName"), str)
        and item.get("alternateName")
    }
    schema_urls = {
        item.get("url")
        for item in profile_people
        if isinstance(item.get("url"), str) and item.get("url")
    }

    conflicts = (
        bool(response.url and not final_matches),
        bool(canonical_url and not canonical_matches),
        bool(og_url and not og_url_matches),
        bool(public_username and public_username != expected_username),
        bool(schema_usernames and schema_usernames != {expected_username}),
        any(
            not _snapchat_profile_url_matches(value, username)
            for value in schema_urls
        ),
        invalid_stable_id,
    )
    if any(conflicts):
        return UNKNOWN, None, "Snapchat public profile evidence conflict"

    if response.status_code >= 400:
        return (
            UNKNOWN,
            None,
            f"Snapchat public profile not confirmed (HTTP "
            f"{response.status_code}); account existence unknown",
        )

    if response.status_code != 200 or not final_matches:
        return UNKNOWN, None, "unexpected Snapchat final URL"

    title_matches = f"(@{username})".casefold() in title.casefold()
    schema_complete = (
        len(profile_pages) == 1
        and len(profile_people) == 1
        and schema_usernames == {expected_username}
        and len(schema_urls) == 1
        and all(
            _snapchat_profile_url_matches(value, username)
            for value in schema_urls
        )
    )

    if (
        canonical_matches
        and og_url_matches
        and title_matches
        and profile_case == "publicProfileInfo"
        and public_username == expected_username
        and bool(public_profile.get("title"))
        and schema_complete
    ):
        return (
            FOUND,
            profile_url,
            "public Snapchat profile found; identity not verified",
        )

    profile_evidence = any((
        canonical_url,
        og_url,
        public_profile,
        profile_pages,
    ))
    if profile_evidence:
        return (
            POSSIBLE,
            profile_url,
            "incomplete Snapchat public profile evidence",
        )

    return (
        UNKNOWN,
        None,
        "Snapchat public profile not confirmed; account existence unknown",
    )


# ============================================================
# SPECJALNA DETEKCJA PROFILI GITLAB
# ============================================================

def _gitlab_profile_url_matches(value, username):
    return _public_profile_url_matches(
        value,
        ("gitlab.com", "www.gitlab.com"),
        f"/{username}",
    )


def classify_gitlab_profile_response(username, response, profile_url):
    status_code = response.status_code
    final_path = unquote(urlparse(response.url or "").path).casefold()

    if (
        final_path.startswith("/users/sign_in")
        or final_path.startswith("/users/sign_in/")
    ):
        return BLOCKED, None, "GitLab sign-in or challenge redirect"

    transport_result = _public_profile_transport_result(response, "GitLab")
    if transport_result:
        return transport_result

    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    canonical_url = _public_profile_canonical(soup)
    og_url = _public_profile_meta(soup, "property", "og:url")
    og_type = _public_profile_meta(soup, "property", "og:type")
    expected_username = username.casefold()

    final_matches = _gitlab_profile_url_matches(response.url, username)
    canonical_matches = _gitlab_profile_url_matches(
        canonical_url,
        username,
    )
    og_url_matches = _gitlab_profile_url_matches(og_url, username)

    profile_body = soup.find("body", attrs={"data-page": "users:show"})
    profile_header = soup.find(
        attrs={"data-testid": "user-profile-header"}
    )
    action_nodes = soup.select(
        ".js-user-profile-actions[data-user-id][data-rss-subscription-path]"
    )
    achievement_nodes = soup.select("#js-user-achievements[data-user-id]")

    stable_ids = {
        node.get("data-user-id", "")
        for node in (*action_nodes, *achievement_nodes)
        if node.get("data-user-id")
    }
    rss_usernames = set()
    for node in action_nodes:
        rss_path = unquote(node.get("data-rss-subscription-path", ""))
        match = re.fullmatch(r"/([^/]+)\.atom", rss_path)
        if match:
            rss_usernames.add(match.group(1).casefold())

    breadcrumb_urls = set()
    for document in _public_json_ld_documents(soup):
        if not isinstance(document, dict):
            continue
        if document.get("@type") != "BreadcrumbList":
            continue
        for item in document.get("itemListElement", []):
            if isinstance(item, dict) and isinstance(item.get("item"), str):
                breadcrumb_urls.add(item["item"])

    conflicts = (
        bool(response.url and not final_matches),
        bool(canonical_url and not canonical_matches),
        bool(og_url and not og_url_matches),
        bool(rss_usernames and rss_usernames != {expected_username}),
        len(stable_ids) > 1,
        any(not value.isdigit() for value in stable_ids),
        any(
            not _gitlab_profile_url_matches(value, username)
            for value in breadcrumb_urls
        ),
    )
    if any(conflicts):
        return UNKNOWN, None, "GitLab profile evidence conflict"

    if status_code >= 400:
        return (
            UNKNOWN,
            None,
            f"unconfirmed GitLab HTTP {status_code}; "
            "profile existence unknown",
        )

    if status_code != 200 or not final_matches:
        return UNKNOWN, None, "unexpected GitLab final URL"

    if (
        og_url_matches
        and og_type.casefold() == "object"
        and title.casefold().endswith(" · gitlab")
        and profile_body is not None
        and profile_header is not None
        and rss_usernames == {expected_username}
        and len(stable_ids) == 1
        and len(action_nodes) == 1
        and len(achievement_nodes) == 1
        and len(breadcrumb_urls) == 1
    ):
        return (
            FOUND,
            profile_url,
            "public GitLab profile found; identity not verified",
        )

    return (
        POSSIBLE,
        profile_url,
        "incomplete GitLab public profile evidence",
    )


# ============================================================
# SPECJALNA DETEKCJA NAMESPACE DOCKER HUB
# ============================================================

def _dockerhub_profile_url_matches(value, username):
    return _public_profile_url_matches(
        value,
        ("hub.docker.com",),
        f"/u/{username}",
    )


def _deserialize_router_data(flattened):
    if not isinstance(flattened, list) or not flattened:
        return None

    memo = {}
    resolving = set()

    def resolve(index):
        if isinstance(index, bool):
            return index
        if not isinstance(index, int):
            return index
        if index < 0 or index >= len(flattened):
            return None
        if index in memo:
            return memo[index]
        if index in resolving:
            return None

        resolving.add(index)
        value = flattened[index]

        if isinstance(value, dict):
            result = {}
            memo[index] = result
            for encoded_key, encoded_value in value.items():
                if not re.fullmatch(r"_\d+", encoded_key):
                    continue
                key = resolve(int(encoded_key[1:]))
                if isinstance(key, str):
                    result[key] = resolve(encoded_value)
        elif isinstance(value, list):
            result = [resolve(item) for item in value]
            memo[index] = result
        else:
            result = value
            memo[index] = result

        resolving.discard(index)
        return result

    return resolve(0)


def _dockerhub_router_data(soup):
    pattern = re.compile(
        r"streamController\.enqueue\("
        r"(\"(?:\\.|[^\"\\])*\")\)",
        re.DOTALL,
    )

    for node in soup.find_all("script"):
        script = node.string or node.get_text()
        if "streamController.enqueue" not in script:
            continue

        for match in pattern.finditer(script):
            try:
                serialized = json.loads(match.group(1))
                flattened = json.loads(serialized)
            except (TypeError, json.JSONDecodeError):
                continue

            value = _deserialize_router_data(flattened)
            if isinstance(value, dict):
                return value

    return {}


def classify_dockerhub_profile_response(username, response, profile_url):
    transport_result = _public_profile_transport_result(
        response,
        "Docker Hub",
    )
    if transport_result:
        return transport_result

    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    canonical_url = _public_profile_canonical(soup)
    expected_username = username.casefold()
    final_matches = _dockerhub_profile_url_matches(response.url, username)
    canonical_matches = _dockerhub_profile_url_matches(
        canonical_url,
        username,
    )

    router_data = _dockerhub_router_data(soup)
    namespace_data = (
        router_data.get("loaderData", {}).get(
            "routes/_layout.u.$namespace",
            {},
        )
        if isinstance(router_data, dict)
        else {}
    )
    if not isinstance(namespace_data, dict):
        namespace_data = {}
    public_profile = namespace_data.get("profile", {})
    if not isinstance(public_profile, dict):
        public_profile = {}

    public_username = str(
        public_profile.get("orgname")
        or public_profile.get("username")
        or ""
    ).casefold()
    stable_id = str(public_profile.get("id", ""))
    stable_uuid = str(public_profile.get("uuid", ""))
    profile_type = str(public_profile.get("type", "")).casefold()
    router_canonical = str(namespace_data.get("canonicalUrl", ""))

    compact_uuid = stable_uuid.replace("-", "").casefold()
    stable_id_valid = bool(
        re.fullmatch(r"[0-9a-f]{32}", stable_id, re.IGNORECASE)
        and re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
            r"[0-9a-f]{4}-[0-9a-f]{12}",
            stable_uuid,
            re.IGNORECASE,
        )
        and stable_id.casefold() == compact_uuid
    )

    profile_page_marker = soup.find(
        attrs={"data-testid": "page_community_profile"}
    )
    profile_header_marker = soup.find(
        attrs={"data-testid": "profile-header"}
    )
    profile_heading = soup.find("h1")

    conflicts = (
        bool(response.url and not final_matches),
        bool(canonical_url and not canonical_matches),
        bool(router_canonical and not _dockerhub_profile_url_matches(
            router_canonical,
            username,
        )),
        bool(public_username and public_username != expected_username),
        bool((stable_id or stable_uuid) and not stable_id_valid),
        bool(profile_type and profile_type not in ("user", "organization")),
    )
    if any(conflicts):
        return UNKNOWN, None, "Docker Hub namespace evidence conflict"

    profile_evidence = any((
        canonical_url,
        public_profile,
        profile_page_marker,
        profile_header_marker,
    ))
    if (
        response.status_code == 404
        and final_matches
        and title.casefold() == "page not found"
        and not profile_evidence
    ):
        return NOT_FOUND, None, "public Docker Hub namespace not found"

    if response.status_code >= 400:
        return (
            UNKNOWN,
            None,
            f"unconfirmed Docker Hub HTTP {response.status_code}",
        )

    if response.status_code != 200 or not final_matches:
        return UNKNOWN, None, "unexpected Docker Hub final URL"

    if (
        canonical_matches
        and _dockerhub_profile_url_matches(router_canonical, username)
        and public_username == expected_username
        and stable_id_valid
        and profile_type in ("user", "organization")
        and title
        and title.casefold() != "page not found"
        and profile_page_marker is not None
        and profile_header_marker is not None
        and profile_heading is not None
        and profile_heading.get_text(" ", strip=True)
    ):
        return (
            FOUND,
            profile_url,
            "public Docker Hub namespace found; identity not verified",
        )

    return (
        POSSIBLE,
        profile_url,
        "incomplete Docker Hub public namespace evidence",
    )


# ============================================================
# SPECJALNA DETEKCJA PROFILI FIVERR
# ============================================================

def _fiverr_profile_url_matches(value, username):
    return _public_profile_url_matches(
        value,
        ("fiverr.com", "www.fiverr.com"),
        f"/{username}",
    )


def _json_script_by_id(soup, script_id):
    node = soup.select_one(f"script#{script_id}")
    if not node:
        return {}

    try:
        value = json.loads(node.string or node.get_text())
    except (TypeError, json.JSONDecodeError):
        return {}

    return value if isinstance(value, dict) else {}


def classify_fiverr_profile_response(username, response, profile_url):
    transport_result = _public_profile_transport_result(response, "Fiverr")
    if transport_result:
        return transport_result

    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    canonical_url = _public_profile_canonical(soup)
    og_url = _public_profile_meta(soup, "property", "og:url")
    expected_username = username.casefold()

    final_matches = _fiverr_profile_url_matches(response.url, username)
    canonical_matches = _fiverr_profile_url_matches(canonical_url, username)
    og_url_matches = _fiverr_profile_url_matches(og_url, username)
    parsed_og_url = urlparse(og_url)
    generic_not_found_og = bool(
        response.status_code == 404
        and parsed_og_url.netloc.casefold()
        in ("fiverr.com", "www.fiverr.com")
        and not unquote(parsed_og_url.path).strip("/")
    )

    profile_pages = [
        document
        for document in _public_json_ld_documents(soup)
        if isinstance(document, dict)
        and document.get("@type") == "ProfilePage"
    ]
    profile_entities = [
        page.get("mainEntity")
        for page in profile_pages
        if isinstance(page.get("mainEntity"), dict)
        and page["mainEntity"].get("@type") == "Person"
    ]
    schema_urls = {
        value
        for page in profile_pages
        for value in (page.get("url"),)
        if isinstance(value, str) and value
    }
    schema_urls.update(
        entity.get("url")
        for entity in profile_entities
        if isinstance(entity.get("url"), str) and entity.get("url")
    )

    public_data = _json_script_by_id(soup, "perseus-initial-props")
    seller = public_data.get("seller", {})
    if not isinstance(seller, dict):
        seller = {}
    seller_user = seller.get("user", {})
    if not isinstance(seller_user, dict):
        seller_user = {}

    public_username = str(seller_user.get("name", "")).casefold()
    stable_ids = {
        str(value)
        for value in (
            seller_user.get("id"),
            public_data.get("localizationData", {}).get("user_id"),
            public_data.get("reviewsData", {}).get(
                "buying_reviews",
                {},
            ).get("user_id"),
            public_data.get("reviewsData", {}).get(
                "selling_reviews",
                {},
            ).get("user_id"),
        )
        if value not in (None, "")
    }
    stable_id_conflict = (
        len(stable_ids) > 1
        or any(not value.isdigit() for value in stable_ids)
    )

    conflicts = (
        bool(response.url and not final_matches),
        bool(canonical_url and not canonical_matches),
        bool(og_url and not og_url_matches and not generic_not_found_og),
        bool(public_username and public_username != expected_username),
        any(
            not _fiverr_profile_url_matches(value, username)
            for value in schema_urls
        ),
        stable_id_conflict,
    )
    if any(conflicts):
        return UNKNOWN, None, "Fiverr profile evidence conflict"

    profile_evidence = any((
        canonical_url,
        profile_pages,
        seller_user,
    ))
    if (
        response.status_code == 404
        and final_matches
        and title.casefold() == "page not found - fiverr"
        and not profile_evidence
    ):
        return NOT_FOUND, None, "public Fiverr profile not found"

    if response.status_code >= 400:
        return UNKNOWN, None, f"unconfirmed Fiverr HTTP {response.status_code}"

    if response.status_code != 200 or not final_matches:
        return UNKNOWN, None, "unexpected Fiverr final URL"

    if (
        canonical_matches
        and og_url_matches
        and title.casefold().endswith("| profile | fiverr")
        and len(profile_pages) == 1
        and len(profile_entities) == 1
        and schema_urls
        and public_username == expected_username
        and len(stable_ids) == 1
        and seller.get("isActive") is True
    ):
        return (
            FOUND,
            profile_url,
            "public Fiverr profile found; identity not verified",
        )

    return (
        POSSIBLE,
        profile_url,
        "incomplete Fiverr public profile evidence",
    )


# ============================================================
# SPECJALNA DETEKCJA PROFILI BEHANCE
# ============================================================

def _behance_profile_url_matches(value, username):
    return _public_profile_url_matches(
        value,
        ("behance.net", "www.behance.net"),
        f"/{username}",
    )


def _behance_primary_profile(store_data):
    profile_state = store_data.get("profile", {})
    if isinstance(profile_state, dict):
        user = profile_state.get("user")
        if isinstance(user, dict):
            return user

    team_state = store_data.get("team", {})
    if isinstance(team_state, dict):
        team_profile = team_state.get("profile")
        if isinstance(team_profile, dict):
            return team_profile

    return {}


def classify_behance_profile_response(username, response, profile_url):
    transport_result = _public_profile_transport_result(response, "Behance")
    if transport_result:
        return transport_result

    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    canonical_url = _public_profile_canonical(soup)
    expected_username = username.casefold()
    final_matches = _behance_profile_url_matches(response.url, username)
    canonical_matches = _behance_profile_url_matches(
        canonical_url,
        username,
    )

    store_data = _json_script_by_id(soup, "beconfig-store_state")
    public_profile = _behance_primary_profile(store_data)
    public_username = str(public_profile.get("username", "")).casefold()
    public_url = str(public_profile.get("url", ""))
    if public_url.startswith("/"):
        public_url = urljoin("https://www.behance.net", public_url)
    stable_id = str(public_profile.get("id", ""))

    schema_people = [
        document
        for document in _public_json_ld_documents(soup)
        if isinstance(document, dict) and document.get("@type") == "Person"
    ]
    schema_urls = {
        item.get("url")
        for item in schema_people
        if isinstance(item.get("url"), str) and item.get("url")
    }
    schema_ids = {
        str(item.get("identifier"))
        for item in schema_people
        if item.get("identifier") not in (None, "")
    }

    username_confirmed = (
        public_username == expected_username
        or _behance_profile_url_matches(public_url, username)
    )
    stable_ids = {
        value
        for value in (stable_id, *schema_ids)
        if value
    }
    stable_id_conflict = (
        len(stable_ids) > 1
        or any(not value.isdigit() for value in stable_ids)
    )

    conflicts = (
        bool(response.url and not final_matches),
        bool(canonical_url and not canonical_matches),
        bool(public_username and public_username != expected_username),
        bool(public_url and not _behance_profile_url_matches(
            public_url,
            username,
        )),
        any(
            not _behance_profile_url_matches(value, username)
            for value in schema_urls
        ),
        stable_id_conflict,
    )
    if any(conflicts):
        return UNKNOWN, None, "Behance profile evidence conflict"

    profile_evidence = any((
        canonical_url,
        public_profile,
        schema_people,
    ))
    if (
        response.status_code == 404
        and final_matches
        and "oops! we can’t find that page." in title.casefold()
        and not profile_evidence
    ):
        return NOT_FOUND, None, "public Behance profile not found"

    if response.status_code >= 400:
        return UNKNOWN, None, f"unconfirmed Behance HTTP {response.status_code}"

    if response.status_code != 200 or not final_matches:
        return UNKNOWN, None, "unexpected Behance final URL"

    if (
        canonical_matches
        and title.casefold().endswith(":: behance")
        and username_confirmed
        and bool(public_profile.get("display_name") or public_profile.get(
            "displayName"
        ))
        and len(stable_ids) == 1
    ):
        return (
            FOUND,
            profile_url,
            "public Behance profile found; identity not verified",
        )

    return (
        POSSIBLE,
        profile_url,
        "incomplete Behance public profile evidence",
    )


# ============================================================
# SPECJALNA DETEKCJA PROFILI VIMEO
# ============================================================

def _vimeo_profile_url_slug(value):
    if not isinstance(value, str) or not value:
        return None

    parsed = urlparse(value)
    if (
        parsed.scheme not in ("http", "https")
        or parsed.netloc.casefold() not in ("vimeo.com", "www.vimeo.com")
    ):
        return None

    path_parts = [part for part in unquote(parsed.path).split("/") if part]
    if len(path_parts) != 1:
        return None

    return path_parts[0].casefold()


def _vimeo_reference_slug(value):
    if not isinstance(value, str) or not value:
        return None

    if value.startswith("/"):
        path_parts = [part for part in unquote(value).split("/") if part]
        return path_parts[0].casefold() if len(path_parts) == 1 else None

    return _vimeo_profile_url_slug(value)


def classify_vimeo_profile_response(username, response, profile_url):
    transport_result = _public_profile_transport_result(response, "Vimeo")
    if transport_result:
        return transport_result

    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    canonical_url = _public_profile_canonical(soup)
    og_url = _public_profile_meta(soup, "property", "og:url")
    expected_username = username.casefold()

    final_slug = _vimeo_profile_url_slug(response.url)
    canonical_slug = _vimeo_profile_url_slug(canonical_url)
    og_slug = _vimeo_profile_url_slug(og_url)

    next_data = _json_script_by_id(soup, "__NEXT_DATA__")
    page_props = next_data.get("props", {}).get("pageProps", {})
    if not isinstance(page_props, dict):
        page_props = {}
    public_slug = str(page_props.get("userId", "")).casefold()
    numeric_user_id = str(page_props.get("numericUserId", ""))
    profile_meta = page_props.get("profileMeta", {})
    if not isinstance(profile_meta, dict):
        profile_meta = {}
    crawlable = profile_meta.get("crawlable", {})
    if not isinstance(crawlable, dict):
        crawlable = {}

    embedded_schema = {}
    serialized_schema = crawlable.get("jsonLd")
    if isinstance(serialized_schema, str):
        try:
            embedded_schema = json.loads(serialized_schema)
        except json.JSONDecodeError:
            embedded_schema = {}

    profile_pages = [
        item
        for item in _walk_public_json(embedded_schema)
        if item.get("@type") == "ProfilePage"
    ]
    profile_entities = [
        item.get("mainEntity")
        for item in profile_pages
        if isinstance(item.get("mainEntity"), dict)
        and item["mainEntity"].get("@type") == "Person"
    ]

    schema_slugs = {
        slug
        for item in profile_entities
        for value in (
            item.get("url"),
            *(item.get("sameAs", []) if isinstance(
                item.get("sameAs"),
                list,
            ) else []),
        )
        for slug in (_vimeo_reference_slug(value),)
        if slug
    }
    schema_usernames = {
        str(item.get("alternateName", "")).casefold()
        for item in profile_entities
        if item.get("alternateName")
    }
    schema_ids = {
        str(item.get("identifier"))
        for item in profile_entities
        if item.get("identifier") not in (None, "")
    }
    crawlable_ids = {
        str(value)
        for value in (crawlable.get("userId"), numeric_user_id)
        if value not in (None, "")
    }
    stable_ids = schema_ids | crawlable_ids

    numeric_alias_match = bool(
        numeric_user_id.isdigit()
        and expected_username == f"user{numeric_user_id}".casefold()
    )
    direct_slug_match = bool(
        public_slug and public_slug == expected_username
    )
    alias_confirmed = direct_slug_match or numeric_alias_match

    meta_canonical_slug = _vimeo_profile_url_slug(
        str(profile_meta.get("canonical", ""))
    )
    crawlable_page_slug = _vimeo_profile_url_slug(
        str(crawlable.get("pageUrl", ""))
    )
    current_slugs = {
        value
        for value in (
            final_slug,
            canonical_slug,
            og_slug,
            meta_canonical_slug,
            crawlable_page_slug,
            public_slug,
        )
        if value
    }

    stable_id_conflict = (
        len(stable_ids) > 1
        or any(not value.isdigit() for value in stable_ids)
    )
    current_slug_conflict = bool(
        public_slug and current_slugs != {public_slug}
    )
    schema_slug_conflict = bool(
        schema_slugs and schema_slugs != {public_slug}
    )
    schema_username_conflict = bool(
        schema_usernames and schema_usernames != {public_slug}
    )

    if any((
        stable_id_conflict,
        current_slug_conflict,
        schema_slug_conflict,
        schema_username_conflict,
    )):
        return UNKNOWN, None, "Vimeo profile evidence conflict"

    profile_evidence = any((
        canonical_url,
        og_url,
        profile_meta,
        profile_pages,
        numeric_user_id,
    ))
    if (
        response.status_code == 404
        and final_slug == expected_username
        and title.casefold() == "vimeo"
        and not profile_evidence
    ):
        return NOT_FOUND, None, "public Vimeo profile not found"

    if response.status_code >= 400:
        return UNKNOWN, None, f"unconfirmed Vimeo HTTP {response.status_code}"

    if response.status_code != 200:
        return UNKNOWN, None, "unexpected Vimeo response"

    if not alias_confirmed:
        return (
            UNKNOWN,
            None,
            "Vimeo vanity redirect is not linked to the requested username",
        )

    title_matches = bool(
        title
        and profile_meta.get("title")
        and title == profile_meta.get("title")
    )
    if (
        len(current_slugs) == 1
        and current_slugs == {public_slug}
        and title_matches
        and len(stable_ids) == 1
        and len(profile_pages) == 1
        and len(profile_entities) == 1
        and schema_usernames == {public_slug}
        and schema_slugs == {public_slug}
    ):
        link = response.url if numeric_alias_match else profile_url
        return (
            FOUND,
            link,
            "public Vimeo profile found; identity not verified",
        )

    return (
        POSSIBLE,
        profile_url,
        "incomplete Vimeo public profile evidence",
    )


# ============================================================
# SPECJALNA DETEKCJA PROFILI DRIBBBLE
# ============================================================

def _dribbble_profile_url_matches(value, username):
    return _public_profile_url_matches(
        value,
        ("dribbble.com", "www.dribbble.com"),
        f"/{username}",
    )


def _profile_image_numeric_id(value, path_marker):
    if not isinstance(value, str):
        return None

    match = re.search(
        rf"{re.escape(path_marker)}/(\d+)/",
        unquote(value),
        re.IGNORECASE,
    )
    return match.group(1) if match else None


def classify_dribbble_profile_response(username, response, profile_url):
    transport_result = _public_profile_transport_result(response, "Dribbble")
    if transport_result:
        return transport_result

    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    canonical_url = _public_profile_canonical(soup)
    og_url = _public_profile_meta(soup, "property", "og:url")
    twitter_creator = _public_profile_meta(soup, "name", "twitter:creator")
    expected_username = username.casefold()

    final_matches = _dribbble_profile_url_matches(response.url, username)
    canonical_matches = _dribbble_profile_url_matches(canonical_url, username)
    og_url_matches = _dribbble_profile_url_matches(og_url, username)

    profile_pages = [
        item
        for document in _public_json_ld_documents(soup)
        for item in _walk_public_json(document)
        if item.get("@type") == "ProfilePage"
    ]
    profile_people = [
        item.get("mainEntity")
        for item in profile_pages
        if isinstance(item.get("mainEntity"), dict)
        and item["mainEntity"].get("@type") == "Person"
    ]
    schema_urls = {
        str(item.get("url"))
        for item in profile_pages
        if item.get("url")
    }
    schema_ids = {
        value
        for item in (*profile_pages, *profile_people)
        for value in (_profile_image_numeric_id(item.get("image"), "/users"),)
        if value
    }
    marker_ids = {
        str(node.get("data-user-id"))
        for node in soup.select("[data-user-id]")
        if node.get("data-user-id")
    }
    creator_username = twitter_creator.removeprefix("@").casefold()
    body_profile_marker = bool(soup.body and soup.body.get("id") == "profile")

    conflicts = (
        bool(response.url and not final_matches),
        bool(canonical_url and not canonical_matches),
        bool(og_url and not og_url_matches),
        bool(creator_username and creator_username != expected_username),
        any(not _dribbble_profile_url_matches(value, username) for value in schema_urls),
        len(schema_ids) > 1,
        len(marker_ids) > 1,
        bool(schema_ids and marker_ids and schema_ids != marker_ids),
        any(not value.isdigit() for value in schema_ids | marker_ids),
    )
    if any(conflicts):
        return UNKNOWN, None, "Dribbble profile evidence conflict"

    profile_evidence = any((
        canonical_url,
        og_url,
        profile_pages,
        body_profile_marker,
        marker_ids,
    ))
    if (
        response.status_code == 404
        and final_matches
        and "page you were looking for doesn't exist" in title.casefold()
        and not profile_evidence
    ):
        return NOT_FOUND, None, "public Dribbble profile not found"

    if response.status_code >= 400:
        return UNKNOWN, None, f"unconfirmed Dribbble HTTP {response.status_code}"
    if response.status_code != 200 or not final_matches:
        return UNKNOWN, None, "unexpected Dribbble final URL"

    if (
        canonical_matches
        and og_url_matches
        and creator_username == expected_username
        and title.casefold().endswith(" | dribbble")
        and len(profile_pages) == 1
        and len(profile_people) == 1
        and len(schema_urls) == 1
        and body_profile_marker
        and len(schema_ids) == 1
        and schema_ids == marker_ids
    ):
        return FOUND, profile_url, "public Dribbble profile found; identity not verified"

    return POSSIBLE, profile_url, "incomplete Dribbble public profile evidence"


# ============================================================
# SPECJALNA DETEKCJA PROFILI ABOUT.ME
# ============================================================

def _aboutme_profile_url_matches(value, username):
    return _public_profile_url_matches(
        value,
        ("about.me", "www.about.me"),
        f"/{username}",
    )


def _aboutme_public_state(soup):
    for node in soup.select('script[type="text/json"]'):
        try:
            value = json.loads(node.string or node.get_text())
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            return value
    return {}


def classify_aboutme_profile_response(username, response, profile_url):
    transport_result = _public_profile_transport_result(response, "About.me")
    if transport_result:
        return transport_result

    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    canonical_url = _public_profile_canonical(soup)
    og_url = _public_profile_meta(soup, "property", "og:url")
    og_type = _public_profile_meta(soup, "property", "og:type")
    expected_username = username.casefold()
    final_matches = _aboutme_profile_url_matches(response.url, username)
    canonical_matches = _aboutme_profile_url_matches(canonical_url, username)
    og_url_matches = _aboutme_profile_url_matches(og_url, username)

    people = [
        item
        for document in _public_json_ld_documents(soup)
        for item in _walk_public_json(document)
        if item.get("@type") == "Person"
    ]
    schema_urls = {
        str(item.get("url"))
        for item in people
        if item.get("url")
    }
    state = _aboutme_public_state(soup)
    page = state.get("page", {}) if isinstance(state, dict) else {}
    public_user = page.get("user", {}) if isinstance(page, dict) else {}
    if not isinstance(public_user, dict):
        public_user = {}
    public_username = str(public_user.get("user_name", "")).casefold()
    stable_id = str(public_user.get("user_id", ""))
    profile_marker = soup.select_one(".profile")

    conflicts = (
        bool(response.url and not final_matches),
        bool(canonical_url and not canonical_matches),
        bool(og_url and not og_url_matches),
        any(not _aboutme_profile_url_matches(value, username) for value in schema_urls),
        bool(public_username and public_username != expected_username),
        bool(stable_id and not stable_id.isdigit()),
        bool(page.get("id") and page.get("id") != "profile"),
    )
    if any(conflicts):
        return UNKNOWN, None, "About.me profile evidence conflict"

    profile_evidence = any((canonical_url, og_url, people, public_user, profile_marker))
    if (
        response.status_code == 404
        and final_matches
        and title.casefold() == "about.me"
        and not profile_evidence
    ):
        return NOT_FOUND, None, "public About.me profile not found"

    if response.status_code >= 400:
        return UNKNOWN, None, f"unconfirmed About.me HTTP {response.status_code}"
    if response.status_code != 200 or not final_matches:
        return UNKNOWN, None, "unexpected About.me final URL"

    if (
        canonical_matches
        and og_url_matches
        and og_type == "aboutme_prod:page"
        and title.casefold().endswith(" | about.me")
        and len(people) == 1
        and len(schema_urls) == 1
        and public_username == expected_username
        and stable_id.isdigit()
        and page.get("id") == "profile"
        and profile_marker is not None
    ):
        return FOUND, profile_url, "public About.me profile found; identity not verified"

    return POSSIBLE, profile_url, "incomplete About.me public profile evidence"


# ============================================================
# SPECJALNA DETEKCJA PROFILI GRAVATAR
# ============================================================

def _gravatar_profile_url_matches(value, username):
    return _public_profile_url_matches(
        value,
        ("gravatar.com", "www.gravatar.com", "en.gravatar.com"),
        f"/{username}",
    )


def _gravatar_public_profile(soup):
    pattern = re.compile(r"const\s+gravatarProfile\s*=\s*(\{.*?\})\s*;", re.DOTALL)
    for node in soup.find_all("script"):
        match = pattern.search(node.string or node.get_text())
        if not match:
            continue
        try:
            value = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def _gravatar_avatar_id(value):
    if not isinstance(value, str):
        return None
    match = re.search(r"/avatar/([0-9a-f]{64})(?:[/?]|$)", value, re.IGNORECASE)
    return match.group(1).casefold() if match else None


def classify_gravatar_profile_response(username, response, profile_url):
    transport_result = _public_profile_transport_result(response, "Gravatar")
    if transport_result:
        return transport_result

    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    canonical_url = _public_profile_canonical(soup)
    og_url = _public_profile_meta(soup, "property", "og:url")
    og_type = _public_profile_meta(soup, "property", "og:type")
    expected_username = username.casefold()
    final_matches = _gravatar_profile_url_matches(response.url, username)
    canonical_matches = _gravatar_profile_url_matches(canonical_url, username)
    og_url_matches = _gravatar_profile_url_matches(og_url, username)

    people = [
        item
        for document in _public_json_ld_documents(soup)
        for item in _walk_public_json(document)
        if item.get("@type") == "Person"
    ]
    schema_urls = {str(item.get("url")) for item in people if item.get("url")}
    avatar_ids = {
        value
        for item in people
        for value in (_gravatar_avatar_id(item.get("image")),)
        if value
    }
    public_profile = _gravatar_public_profile(soup)
    public_username = str(public_profile.get("userLogin", "")).casefold()
    public_url = str(public_profile.get("profileUrl", ""))
    login_id = str(public_profile.get("userLoginMD5", "")).casefold()
    profile_marker = bool(
        soup.body
        and "is-profile" in soup.body.get("class", [])
        and soup.select_one("main.g-profile")
    )

    conflicts = (
        bool(response.url and not final_matches),
        bool(canonical_url and not canonical_matches),
        bool(og_url and not og_url_matches),
        any(not _gravatar_profile_url_matches(value, username) for value in schema_urls),
        bool(public_username and public_username != expected_username),
        bool(public_url and not _gravatar_profile_url_matches(public_url, username)),
        len(avatar_ids) > 1,
        bool(login_id and not re.fullmatch(r"[0-9a-f]{32}", login_id)),
    )
    if any(conflicts):
        return UNKNOWN, None, "Gravatar profile evidence conflict"

    profile_evidence = any((og_url, people, public_profile, profile_marker))
    if response.status_code == 404 and final_matches and not profile_evidence:
        return NOT_FOUND, None, "public Gravatar profile not found"

    if response.status_code >= 400:
        return UNKNOWN, None, f"unconfirmed Gravatar HTTP {response.status_code}"
    if response.status_code != 200 or not final_matches:
        return UNKNOWN, None, "unexpected Gravatar final URL"

    if (
        canonical_matches
        and og_url_matches
        and og_type.casefold() == "profile"
        and title.casefold().endswith(" | gravatar")
        and len(people) == 1
        and len(schema_urls) == 1
        and len(avatar_ids) == 1
        and public_username == expected_username
        and _gravatar_profile_url_matches(public_url, username)
        and re.fullmatch(r"[0-9a-f]{32}", login_id)
        and profile_marker
    ):
        return FOUND, profile_url, "public Gravatar profile found; identity not verified"

    return POSSIBLE, profile_url, "incomplete Gravatar public profile evidence"


# ============================================================
# SPECJALNA DETEKCJA PROFILI DEV.TO
# ============================================================

def _devto_profile_url_matches(value, username):
    return _public_profile_url_matches(
        value,
        ("dev.to", "www.dev.to"),
        f"/{username}",
    )


def classify_devto_profile_response(username, response, profile_url):
    transport_result = _public_profile_transport_result(response, "DEV.to")
    if transport_result:
        return transport_result

    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    canonical_url = _public_profile_canonical(soup)
    og_url = _public_profile_meta(soup, "property", "og:url")
    final_matches = _devto_profile_url_matches(response.url, username)
    canonical_matches = _devto_profile_url_matches(canonical_url, username)
    og_url_matches = _devto_profile_url_matches(og_url, username)

    people = [
        item
        for document in _public_json_ld_documents(soup)
        for item in _walk_public_json(document)
        if item.get("@type") == "Person"
    ]
    public_urls = {
        str(value)
        for item in people
        for value in (
            item.get("url"),
            item.get("mainEntityOfPage", {}).get("@id")
            if isinstance(item.get("mainEntityOfPage"), dict) else None,
        )
        if value
    }
    stable_ids = {
        str(value)
        for item in people
        for value in (
            item.get("identifier"),
            _profile_image_numeric_id(item.get("image"), "/profile_image"),
        )
        if value not in (None, "")
    }
    profile_marker = soup.select_one("header.profile-header")

    conflicts = (
        bool(response.url and not final_matches),
        bool(canonical_url and not canonical_matches),
        bool(og_url and not og_url_matches),
        any(not _devto_profile_url_matches(value, username) for value in public_urls),
        len(stable_ids) > 1,
        any(not value.isdigit() for value in stable_ids),
    )
    if any(conflicts):
        return UNKNOWN, None, "DEV.to profile evidence conflict"

    profile_evidence = any((canonical_url, og_url, people, profile_marker))
    if (
        response.status_code == 404
        and final_matches
        and title.casefold() == "404: page not found"
        and not profile_evidence
    ):
        return NOT_FOUND, None, "public DEV.to profile not found"

    if response.status_code >= 400:
        return UNKNOWN, None, f"unconfirmed DEV.to HTTP {response.status_code}"
    if response.status_code != 200 or not final_matches:
        return UNKNOWN, None, "unexpected DEV.to final URL"

    if (
        canonical_matches
        and og_url_matches
        and title.casefold().endswith(" - dev community")
        and len(people) == 1
        and len(public_urls) == 1
        and len(stable_ids) == 1
        and profile_marker is not None
    ):
        return FOUND, profile_url, "public DEV.to profile found; identity not verified"

    return POSSIBLE, profile_url, "incomplete DEV.to public profile evidence"


# ============================================================
# SPECJALNA DETEKCJA PROFILI DISQUS
# ============================================================

def _disqus_profile_url_matches(value, username):
    return _public_profile_url_matches(
        value,
        ("disqus.com", "www.disqus.com"),
        f"/by/{username}",
    )


def _disqus_deep_link_username(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"disqus://users/([^/?#]+)", value.strip(), re.IGNORECASE)
    return unquote(match.group(1)).casefold() if match else None


def classify_disqus_profile_response(username, response, profile_url):
    transport_result = _public_profile_transport_result(response, "Disqus")
    if transport_result:
        return transport_result

    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    canonical_url = _public_profile_canonical(soup)
    og_url = _public_profile_meta(soup, "property", "og:url")
    og_type = _public_profile_meta(soup, "property", "og:type")
    expected_username = username.casefold()
    final_matches = _disqus_profile_url_matches(response.url, username)
    canonical_matches = _disqus_profile_url_matches(canonical_url, username)
    og_url_matches = _disqus_profile_url_matches(og_url, username)
    deep_links = {
        value
        for node in soup.find_all("meta", attrs={"property": "al:iphone:url"})
        for value in (_disqus_deep_link_username(node.get("content", "")),)
        if value
    }
    title_match = re.fullmatch(
        r"Disqus Profile - (.+)",
        title,
        re.IGNORECASE,
    )
    title_username = title_match.group(1).casefold() if title_match else ""

    conflicts = (
        bool(response.url and not final_matches),
        bool(canonical_url and not canonical_matches),
        bool(og_url and not og_url_matches),
        bool(title_username and title_username != expected_username),
        bool(deep_links and deep_links != {expected_username}),
    )
    if any(conflicts):
        return UNKNOWN, None, "Disqus profile evidence conflict"

    profile_evidence = any((canonical_url, og_url, title_username, deep_links))
    if (
        response.status_code == 404
        and final_matches
        and title.casefold() == "page not found (404) - disqus"
        and not profile_evidence
    ):
        return NOT_FOUND, None, "public Disqus profile not found"

    if response.status_code >= 400:
        return UNKNOWN, None, f"unconfirmed Disqus HTTP {response.status_code}"
    if response.status_code != 200 or not final_matches:
        return UNKNOWN, None, "unexpected Disqus final URL"

    if (
        canonical_matches
        and og_url_matches
        and og_type.casefold() == "profile"
        and title_username == expected_username
        and deep_links == {expected_username}
    ):
        return FOUND, profile_url, "public Disqus profile found; identity not verified"

    return POSSIBLE, profile_url, "incomplete Disqus public profile evidence"


# ============================================================
# SPRAWDZANIE JEDNEGO SERWISU
# ============================================================

def check_username_on_site(username, site_name, site_config):
    url = site_config["url"].replace("{username}", quote(username, safe=""))

    headers = site_config.get("headers", {})
    timeout = site_config.get("timeout_seconds", 5)
    delay = site_config.get("rate_limit_delay", 1)

    request_method = site_config.get("request_method", "GET")

    allow_redirects = (
        site_config.get("redirect_behavior", "follow") == "follow"
    )

    not_found_status_codes = site_config.get(
    "not_found_status_codes",
    []
    )

    found_status_codes = site_config.get(
        "found_status_codes",
        []
    )

    cookies = site_config.get(
        "cookies",
        {}
    )

    not_found_message = site_config.get(
        "not_found_message",
        ""
    ).lower()

    found_message = site_config.get(
        "found_message",
        ""
    ).lower()

    needs_login = site_config.get(
        "needs_login",
        False
    )

    confidence_score = site_config.get(
        "confidence_score",
        0
    )

    checker = site_config.get(
        "checker",
        ""
    )

    try:
        response = requests.request(
            method=request_method,
            url=url,
            headers=headers,
            cookies=cookies,
            timeout=timeout,
            allow_redirects=allow_redirects,
        )

        status_code = response.status_code
        content = response.text.lower()
        final_url = response.url.lower()

        # XVideos wymaga potwierdzenia markerow takze dla HTTP 404,
        # dlatego checker dziala przed generyczna klasyfikacja HTTP.
        if checker == "xvideos_profile":
            status, link, info = classify_xvideos_profile_response(
                username,
                response,
                url,
            )

            return (
                site_name,
                status,
                link,
                info,
            )

        if checker == "xnxx_profile":
            status, link, info = classify_xnxx_profile_response(
                username,
                response,
                url,
            )

            return (
                site_name,
                status,
                link,
                info,
            )

        if checker == "booksusi_profile":
            status, link, info = classify_booksusi_profile_response(
                username,
                response,
                url,
            )

            return (
                site_name,
                status,
                link,
                info,
            )

        if checker == "fansly_profile":
            status, link, info = classify_fansly_profile_response(
                username,
                response,
            )

            return (
                site_name,
                status,
                link,
                info,
            )

        if checker == "pornhub_profile":
            status, link, info = classify_pornhub_profile_response(
                username,
                response,
                url,
            )

            return (
                site_name,
                status,
                link,
                info,
            )

        if checker == "tinder_profile":
            status, link, info = classify_tinder_profile_response(
                username,
                response,
                url,
            )

            return (
                site_name,
                status,
                link,
                info,
            )

        if checker == "youtube_channel":
            status, link, info = classify_youtube_channel_response(
                username,
                response,
                url,
            )

            return (
                site_name,
                status,
                link,
                info,
            )

        if checker == "facebook_profile":
            status, link, info = classify_facebook_profile_response(
                username,
                response,
                url,
            )

            return (
                site_name,
                status,
                link,
                info,
            )

        if checker == "github_profile":
            status, link, info = classify_github_profile_response(
                username,
                response,
                url,
            )

            return (
                site_name,
                status,
                link,
                info,
            )

        if checker == "instagram_profile":
            status, link, info = classify_instagram_profile_response(
                username,
                response,
                url,
            )

            return (
                site_name,
                status,
                link,
                info,
            )

        if checker == "pinterest_profile":
            status, link, info = classify_pinterest_profile_response(
                username,
                response,
                url,
            )

            return site_name, status, link, info

        if checker == "twitch_profile":
            status, link, info = classify_twitch_profile_response(
                username,
                response,
                url,
            )

            return site_name, status, link, info

        if checker == "soundcloud_profile":
            status, link, info = classify_soundcloud_profile_response(
                username,
                response,
                url,
            )

            return site_name, status, link, info

        if checker == "xing_profile":
            status, link, info = classify_xing_profile_response(
                username,
                response,
                url,
            )

            return site_name, status, link, info

        if checker == "snapchat_profile":
            status, link, info = classify_snapchat_profile_response(
                username,
                response,
                url,
            )

            return site_name, status, link, info

        if checker == "gitlab_profile":
            status, link, info = classify_gitlab_profile_response(
                username,
                response,
                url,
            )

            return site_name, status, link, info

        if checker == "dockerhub_profile":
            status, link, info = classify_dockerhub_profile_response(
                username,
                response,
                url,
            )

            return site_name, status, link, info

        if checker == "fiverr_profile":
            status, link, info = classify_fiverr_profile_response(
                username,
                response,
                url,
            )

            return site_name, status, link, info

        if checker == "behance_profile":
            status, link, info = classify_behance_profile_response(
                username,
                response,
                url,
            )

            return site_name, status, link, info

        if checker == "vimeo_profile":
            status, link, info = classify_vimeo_profile_response(
                username,
                response,
                url,
            )

            return site_name, status, link, info

        if checker == "dribbble_profile":
            status, link, info = classify_dribbble_profile_response(
                username,
                response,
                url,
            )

            return site_name, status, link, info

        if checker == "aboutme_profile":
            status, link, info = classify_aboutme_profile_response(
                username,
                response,
                url,
            )

            return site_name, status, link, info

        if checker == "gravatar_profile":
            status, link, info = classify_gravatar_profile_response(
                username,
                response,
                url,
            )

            return site_name, status, link, info

        if checker == "devto_profile":
            status, link, info = classify_devto_profile_response(
                username,
                response,
                url,
            )

            return site_name, status, link, info

        if checker == "disqus_profile":
            status, link, info = classify_disqus_profile_response(
                username,
                response,
                url,
            )

            return site_name, status, link, info

        # ----------------------------------------------------
        # RATE LIMIT
        # ----------------------------------------------------

        if status_code == 429:
            return (
                site_name,
                RATE_LIMIT,
                None,
                "HTTP 429"
            )

        # ----------------------------------------------------
        # BLOKADA / LOGOWANIE
        # ----------------------------------------------------

        if status_code in (401, 403):
            return (
                site_name,
                BLOCKED,
                None,
                f"HTTP {status_code}"
            )

        login_markers = (
            "/login",
            "/signin",
            "/accounts/login",
            "/checkpoint",
            "/challenge",
        )

        if needs_login and any(
            marker in final_url
            for marker in login_markers
        ):
            return (
                site_name,
                BLOCKED,
                None,
                "redirect do logowania"
            )

        # ----------------------------------------------------
        # PEWNE NOT FOUND
        # ----------------------------------------------------

        if (
            status_code == 404
            or status_code in not_found_status_codes
        ):
            return (
                site_name,
                NOT_FOUND,
                None,
                f"HTTP {status_code}"
            )

        # ----------------------------------------------------
        # BLEDY SERWERA
        # ----------------------------------------------------

        if status_code >= 500:
            return (
                site_name,
                ERROR,
                None,
                f"HTTP {status_code}"
            )

        # ----------------------------------------------------
        # INNE BLEDY HTTP
        # ----------------------------------------------------

        if status_code >= 400:
            return (
                site_name,
                UNKNOWN,
                None,
                f"HTTP {status_code}"
            )

        # ----------------------------------------------------
        # SPECJALNE CHECKERY SERWISOW
        # ----------------------------------------------------

        if checker == "x_profile":
            status, link, info = classify_x_profile_response(
                username,
                response,
                url,
            )

            return (
                site_name,
                status,
                link,
                info,
            )

        if checker == "tiktok_profile":
            status, link, info = classify_tiktok_profile_response(
                username,
                response,
                url,
            )

            return (
                site_name,
                status,
                link,
                info,
            )

        # ----------------------------------------------------
        # KOMUNIKAT "NIE ISTNIEJE"
        # ----------------------------------------------------

        if (
            not_found_message
            and not_found_message in content
        ):
            return (
                site_name,
                NOT_FOUND,
                None,
                "not_found_message"
            )

        # ----------------------------------------------------
        # KOMUNIKAT POTWIERDZAJACY ISTNIENIE
        # ----------------------------------------------------

        if (
            found_message
            and found_message in content
        ):
            return (
                site_name,
                FOUND,
                url,
                f"confidence={confidence_score}"
            )

        if status_code in found_status_codes:
            return (
                site_name,
                FOUND,
                url,
                f"HTTP {status_code}"
            )

        # BRAK PEWNEGO DOWODU
        # ----------------------------------------------------

        if 200 <= status_code < 300:
            return (
                site_name,
                POSSIBLE,
                url,
                f"HTTP {status_code}"
            )

        return (
            site_name,
            UNKNOWN,
            None,
            f"HTTP {status_code}"
        )

    except requests.Timeout:
        return (
            site_name,
            ERROR,
            None,
            "timeout"
        )

    except requests.ConnectionError:
        return (
            site_name,
            ERROR,
            None,
            "connection error"
        )

    except requests.RequestException as error:
        return (
            site_name,
            ERROR,
            None,
            type(error).__name__
        )

    finally:
        time.sleep(delay)


# ============================================================
# DODATKOWE MODULY DLA WYBRANYCH SERWISOW
# ============================================================

def run_extra_lookup(site_name, username):
    site = site_name.lower()

    if site == "instagram":

        print(
            "[magenta][+][/magenta] "
            "Running Instagram email lookup..."
        )

        try:
            infind(username)
        except Exception as error:
            print(
                "[yellow][?][/yellow] "
                f"Instagram email lookup failed: "
                f"{type(error).__name__}"
            )

        try:
            instauser(username)
        except Exception as error:
            print(
                "[yellow][?][/yellow] "
                f"Instagram profile lookup failed: "
                f"{type(error).__name__}"
            )

    elif site == "chess.com":

        try:
            chess(username)
        except Exception as error:
            print(
                "[yellow][?][/yellow] "
                f"Chess.com lookup failed: "
                f"{type(error).__name__}"
            )

    elif site == "github":

        try:
            gitinfo(username)
        except Exception as error:
            print(
                "[yellow][?][/yellow] "
                f"GitHub lookup failed: "
                f"{type(error).__name__}"
            )


# ============================================================
# GLOWNA FUNKCJA USERNAME SEARCH
# ============================================================

def main():
    print(
        "[magenta]Enter username or display name to search[/magenta]: ",
        end=""
    )

    query = input().strip()

    username_candidates = generate_username_candidates(query)

    if not username_candidates:
        print("[red][!][/red] Username or display name cannot be empty.")
        return

    sites = load_sites_config()
    display_name_mode = len(username_candidates) > 1

    counters = {
        FOUND: 0,
        NOT_FOUND: 0,
        POSSIBLE: 0,
        UNKNOWN: 0,
        BLOCKED: 0,
        RATE_LIMIT: 0,
        ERROR: 0,
    }

    unknown_sites = []

    if display_name_mode:
        print(
            "\n[cyan]Display name detected. Username candidates:[/cyan]"
        )
        print(", ".join(username_candidates))

    total_checks = len(sites) * len(username_candidates)
    print(
        f"\n[cyan]Scanning {total_checks} service/candidate "
        f"combinations[/cyan]\n"
    )

    with ThreadPoolExecutor(max_workers=10) as executor:

        futures = {
            executor.submit(
                check_username_on_site,
                username_candidate,
                site_name,
                site_config,
            ): username_candidate
            for username_candidate in username_candidates
            for site_name, site_config in sites.items()
        }

        for future in as_completed(futures):

            username_candidate = futures[future]
            site_name, status, link, info = future.result()
            result_label = site_name
            if display_name_mode:
                result_label = f"{site_name} [{username_candidate}]"

            counters[status] += 1

            # ------------------------------------------------
            # FOUND
            # ------------------------------------------------

            if status == FOUND:

                print(
                    f"[bold green][+][/bold green] "
                    f"{result_label}: FOUND"
                )

                print(
                    f"    [green]{link}[/green]"
                )

                run_extra_lookup(
                    site_name,
                    username_candidate
                )

            # ------------------------------------------------
            # POSSIBLE
            # ------------------------------------------------

            elif status == POSSIBLE:

                print(
                    f"[cyan][?][/cyan] "
                    f"{result_label}: POSSIBLE"
                )

                print(
                    f"    [cyan]{link}[/cyan]"
                )

            # ------------------------------------------------
            # BLOCKED
            # ------------------------------------------------

            elif status == BLOCKED:

                print(
                    f"[yellow][🔒][/yellow] "
                    f"{result_label}: BLOCKED / LOGIN "
                    f"({info})"
                )

            # ------------------------------------------------
            # RATE LIMIT
            # ------------------------------------------------

            elif status == RATE_LIMIT:

                print(
                    f"[magenta][!][/magenta] "
                    f"{result_label}: RATE LIMIT "
                    f"({info})"
                )

            # ------------------------------------------------
            # ERROR
            # ------------------------------------------------

            elif status == ERROR:

                print(
                    f"[red][!][/red] "
                    f"{result_label}: ERROR "
                    f"({info})"
                )

            # ------------------------------------------------
            # UNKNOWN
            # ------------------------------------------------

            elif status == UNKNOWN:

                unknown_sites.append(
                    result_label
                )

            # NOT_FOUND celowo nie wypisujemy,
            # bo przy ponad 100 serwisach zasmieciloby terminal.


    # ========================================================
    # PODSUMOWANIE
    # ========================================================

    print("\n[bold cyan]=== Scan Summary ===[/bold cyan]\n")

    print(
        f"[green]FOUND      : "
        f"{counters[FOUND]}[/green]"
    )

    print(
        f"[red]NOT FOUND  : "
        f"{counters[NOT_FOUND]}[/red]"
    )

    print(
        f"[cyan]POSSIBLE   : "
        f"{counters[POSSIBLE]}[/cyan]"
    )

    print(
        f"[yellow]UNKNOWN    : "
        f"{counters[UNKNOWN]}[/yellow]"
    )

    print(
        f"[yellow]BLOCKED    : "
        f"{counters[BLOCKED]}[/yellow]"
    )

    print(
        f"[magenta]RATE LIMIT : "
        f"{counters[RATE_LIMIT]}[/magenta]"
    )

    print(
        f"[red]ERROR      : "
        f"{counters[ERROR]}[/red]"
    )

    if unknown_sites:

        print(
            "\n[yellow][?] Ambiguous services:[/yellow]"
        )

        print(
            ", ".join(
                sorted(unknown_sites)
            )
        )


if __name__ == "__main__":
    main()
