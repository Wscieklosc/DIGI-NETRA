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
        "[magenta]Enter username to search[/magenta]: ",
        end=""
    )

    username = input().strip()

    if not username:
        print("[red][!][/red] Username cannot be empty.")
        return

    sites = load_sites_config()

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

    print(
        f"\n[cyan]Scanning {len(sites)} services "
        f"for username:[/cyan] {username}\n"
    )

    with ThreadPoolExecutor(max_workers=10) as executor:

        futures = [
            executor.submit(
                check_username_on_site,
                username,
                site_name,
                site_config,
            )
            for site_name, site_config
            in sites.items()
        ]

        for future in as_completed(futures):

            site_name, status, link, info = future.result()

            counters[status] += 1

            # ------------------------------------------------
            # FOUND
            # ------------------------------------------------

            if status == FOUND:

                print(
                    f"[bold green][+][/bold green] "
                    f"{site_name}: FOUND"
                )

                print(
                    f"    [green]{link}[/green]"
                )

                run_extra_lookup(
                    site_name,
                    username
                )

            # ------------------------------------------------
            # POSSIBLE
            # ------------------------------------------------

            elif status == POSSIBLE:

                print(
                    f"[cyan][?][/cyan] "
                    f"{site_name}: POSSIBLE"
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
                    f"{site_name}: BLOCKED / LOGIN "
                    f"({info})"
                )

            # ------------------------------------------------
            # RATE LIMIT
            # ------------------------------------------------

            elif status == RATE_LIMIT:

                print(
                    f"[magenta][!][/magenta] "
                    f"{site_name}: RATE LIMIT "
                    f"({info})"
                )

            # ------------------------------------------------
            # ERROR
            # ------------------------------------------------

            elif status == ERROR:

                print(
                    f"[red][!][/red] "
                    f"{site_name}: ERROR "
                    f"({info})"
                )

            # ------------------------------------------------
            # UNKNOWN
            # ------------------------------------------------

            elif status == UNKNOWN:

                unknown_sites.append(
                    site_name
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
