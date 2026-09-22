import json
import re
import time
import requests

from bs4 import BeautifulSoup
from rich import print
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote, unquote, urlparse

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
        # SPECJALNY CHECKER X
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
