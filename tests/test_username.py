import json
import unittest
from unittest.mock import Mock, patch

import requests

from main.username import (
    BLOCKED,
    ERROR,
    FOUND,
    NOT_FOUND,
    POSSIBLE,
    RATE_LIMIT,
    UNKNOWN,
    check_username_on_site,
    generate_username_candidates,
    load_sites_config,
    main as username_main,
)


class UsernameCandidateGenerationTests(unittest.TestCase):
    def test_two_part_display_name(self):
        self.assertEqual(
            generate_username_candidates("Jan Kowalski"),
            [
                "jan kowalski",
                "jankowalski",
                "jan.kowalski",
                "jan_kowalski",
                "jan-kowalski",
                "kowalski.jan",
                "kowalski_jan",
                "kowalski-jan",
                "kowalskijan",
            ],
        )

    def test_three_part_display_name(self):
        self.assertEqual(
            generate_username_candidates("Jan Adam Kowalski"),
            [
                "jan adam kowalski",
                "janadamkowalski",
                "jan.adam.kowalski",
                "jan_adam_kowalski",
                "jan-adam-kowalski",
                "kowalski.adam.jan",
                "kowalski_adam_jan",
                "kowalski-adam-jan",
                "kowalskiadamjan",
            ],
        )

    def test_repeated_whitespace_is_normalized(self):
        self.assertEqual(
            generate_username_candidates("  Jan   Kowalski  "),
            generate_username_candidates("Jan Kowalski"),
        )

    def test_display_name_is_casefolded(self):
        self.assertEqual(
            generate_username_candidates("jAn KoWaLsKi"),
            generate_username_candidates("JAN KOWALSKI"),
        )

    def test_polish_unicode_is_preserved(self):
        candidates = generate_username_candidates("Łukasz Żółć")

        self.assertEqual(candidates[0], "łukasz żółć")
        self.assertIn("łukasz.żółć", candidates)
        self.assertIn("żółć_łukasz", candidates)
        self.assertNotIn("lukasz.zolc", candidates)

    def test_candidates_do_not_contain_duplicates(self):
        candidates = generate_username_candidates("Anna Anna")

        self.assertEqual(len(candidates), len(set(candidates)))
        self.assertEqual(len(candidates), 5)

    def test_single_username_is_not_changed(self):
        self.assertEqual(
            generate_username_candidates("Wscieklosc666"),
            ["Wscieklosc666"],
        )

    def test_empty_input_has_no_candidates(self):
        self.assertEqual(generate_username_candidates(""), [])
        self.assertEqual(generate_username_candidates("   \t\n"), [])

    def test_existing_hyphen_underscore_and_dot_are_preserved(self):
        candidates = generate_username_candidates("Anna-Maria Nowak_Smith")

        self.assertIn("anna-maria.nowak_smith", candidates)
        self.assertIn("nowak_smith-anna-maria", candidates)
        self.assertEqual(
            generate_username_candidates("Anna.Maria_Nowak-Smith"),
            ["Anna.Maria_Nowak-Smith"],
        )

    def test_display_name_candidates_use_normal_site_checker(self):
        site_config = {"url": "https://example.test/{username}"}

        with (
            patch("builtins.input", return_value="Jan Kowalski"),
            patch(
                "main.username.load_sites_config",
                return_value={"Example": site_config},
            ),
            patch(
                "main.username.check_username_on_site",
                return_value=("Example", NOT_FOUND, None, "HTTP 404"),
            ) as check_mock,
            patch("main.username.print"),
        ):
            username_main()

        checked_candidates = [
            invocation.args[0]
            for invocation in check_mock.call_args_list
        ]
        self.assertCountEqual(
            checked_candidates,
            generate_username_candidates("Jan Kowalski"),
        )


class CheckUsernameOnSiteTests(unittest.TestCase):
    def setUp(self):
        self.site_name = "Example"
        self.site_config = {
            "url": "https://example.test/{username}",
            "rate_limit_delay": 0,
        }

    def response(self, status_code, text="", url=None):
        response = Mock()
        response.status_code = status_code
        response.text = text
        response.url = url or "https://example.test/test-user"
        return response

    def check(self, response=None, *, config=None, side_effect=None):
        site_config = {**self.site_config, **(config or {})}

        with (
            patch(
                "main.username.requests.request",
                return_value=response,
                side_effect=side_effect,
            ),
            patch("main.username.time.sleep"),
        ):
            return check_username_on_site(
                "test-user",
                self.site_name,
                site_config,
            )

    def test_200_without_positive_evidence_is_possible(self):
        result = self.check(self.response(200))

        self.assertEqual(result[1], POSSIBLE)

    def test_404_is_not_found(self):
        result = self.check(self.response(404))

        self.assertEqual(result[1], NOT_FOUND)

    def test_403_is_blocked(self):
        result = self.check(self.response(403))

        self.assertEqual(result[1], BLOCKED)

    def test_429_is_rate_limited(self):
        result = self.check(self.response(429))

        self.assertEqual(result[1], RATE_LIMIT)

    def test_timeout_is_error(self):
        result = self.check(side_effect=requests.Timeout())

        self.assertEqual(result[1], ERROR)

    def test_found_message_is_found(self):
        result = self.check(
            self.response(200, text="Verified PROFILE OWNER"),
            config={"found_message": "profile owner"},
        )

        self.assertEqual(result[1], FOUND)

    def test_not_found_message_is_not_found(self):
        result = self.check(
            self.response(200, text="This USER does not exist"),
            config={"not_found_message": "user does not exist"},
        )

        self.assertEqual(result[1], NOT_FOUND)

    def test_found_status_code_is_found(self):
        result = self.check(
            self.response(200),
            config={"found_status_codes": [200]},
        )

        self.assertEqual(result[1], FOUND)

    def test_login_redirect_is_blocked_when_login_is_required(self):
        result = self.check(
            self.response(
                200,
                url="https://example.test/accounts/login?next=/test-user",
            ),
            config={"needs_login": True},
        )

        self.assertEqual(result[1], BLOCKED)


class XProfileDetectionTests(unittest.TestCase):
    def setUp(self):
        self.site_name = "Twitter"
        self.site_config = {
            "url": "https://x.com/{username}",
            "checker": "x_profile",
            "needs_login": True,
            "rate_limit_delay": 0,
        }

    def response(self, status_code, text="", url=None):
        response = Mock()
        response.status_code = status_code
        response.text = text
        response.url = url or "https://x.com/test_user"
        return response

    def check(self, response):
        with (
            patch(
                "main.username.requests.request",
                return_value=response,
            ),
            patch("main.username.time.sleep"),
        ):
            return check_username_on_site(
                "test_user",
                self.site_name,
                self.site_config,
            )

    def x_profile_html(self, username="test_user"):
        profile_url = f"https://x.com/{username}"

        return f"""
            <html>
                <head>
                    <title>Test User (@{username}) / X</title>
                    <link rel="canonical" href="{profile_url}">
                    <meta property="og:url" content="{profile_url}">
                    <meta
                        property="og:title"
                        content="Test User (@{username}) on X"
                    >
                </head>
            </html>
        """

    def test_x_profile_requires_matching_metadata_for_found(self):
        result = self.check(
            self.response(
                200,
                text=self.x_profile_html(),
            )
        )

        self.assertEqual(result[1], FOUND)

    def test_x_bare_200_remains_possible(self):
        result = self.check(self.response(200, text="<html></html>"))

        self.assertEqual(result[1], POSSIBLE)

    def test_x_mismatched_profile_metadata_remains_possible(self):
        result = self.check(
            self.response(
                200,
                text=self.x_profile_html("another_user"),
            )
        )

        self.assertEqual(result[1], POSSIBLE)

    def test_x_not_found_metadata_is_not_found(self):
        html = """
            <html>
                <head>
                    <title>User Profile Not Found - X | 404 Error</title>
                    <meta
                        property="og:title"
                        content="User Profile Not Found - X | 404 Error"
                    >
                </head>
            </html>
        """

        result = self.check(self.response(200, text=html))

        self.assertEqual(result[1], NOT_FOUND)

    def test_x_login_page_is_blocked(self):
        result = self.check(
            self.response(
                200,
                text="<html><title>Log in / X</title></html>",
            )
        )

        self.assertEqual(result[1], BLOCKED)

    def test_x_unicode_username_is_not_found(self):
        with (
            patch(
                "main.username.requests.request",
                return_value=self.response(
                    200,
                    url="https://x.com/za%C5%BC%C3%B3%C5%82%C4%87",
                ),
            ),
            patch("main.username.time.sleep"),
        ):
            result = check_username_on_site(
                "zażółć",
                self.site_name,
                self.site_config,
            )

        self.assertEqual(result[1], NOT_FOUND)


class TikTokProfileDetectionTests(unittest.TestCase):
    def setUp(self):
        self.site_name = "TikTok"
        self.site_config = {
            "url": "https://www.tiktok.com/@{username}",
            "checker": "tiktok_profile",
            "needs_login": True,
            "rate_limit_delay": 0,
        }

    def response(self, status_code, text="", url=None):
        response = Mock()
        response.status_code = status_code
        response.text = text
        response.url = url or "https://www.tiktok.com/@test_user"
        return response

    def check(self, response, username="test_user"):
        with (
            patch(
                "main.username.requests.request",
                return_value=response,
            ),
            patch("main.username.time.sleep"),
        ):
            return check_username_on_site(
                username,
                self.site_name,
                self.site_config,
            )

    def tiktok_profile_html(
        self,
        username="test_user",
        status_code=0,
        status_message="",
    ):
        data = {
            "__DEFAULT_SCOPE__": {
                "webapp.user-detail": {
                    "userInfo": {
                        "user": {
                            "id": "1234567890",
                            "uniqueId": username,
                            "secUid": "MS4wLjABAAAA-test",
                        },
                        "stats": {
                            "followerCount": 1,
                        },
                    },
                    "shareMeta": {
                        "desc": (
                            f"@{username} 1 Followers, 0 Following, "
                            "0 Likes"
                        ),
                    },
                    "statusCode": status_code,
                    "statusMsg": status_message,
                },
            },
        }

        return (
            '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" '
            f'type="application/json">{json.dumps(data)}</script>'
        )

    def tiktok_status_html(self, status_code, status_message=""):
        data = {
            "__DEFAULT_SCOPE__": {
                "webapp.user-detail": {
                    "statusCode": status_code,
                    "statusMsg": status_message,
                },
            },
        }

        return (
            '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" '
            f'type="application/json">{json.dumps(data)}</script>'
        )

    def test_tiktok_matching_embedded_profile_is_found(self):
        result = self.check(
            self.response(
                200,
                text=self.tiktok_profile_html(),
            )
        )

        self.assertEqual(result[1], FOUND)

    def test_tiktok_private_profile_data_is_found(self):
        username = "a12345678901234567890123"
        result = self.check(
            self.response(
                200,
                text=self.tiktok_profile_html(
                    username,
                    status_code=10222,
                    status_message="ErrBizUserSecret",
                ),
                url=f"https://www.tiktok.com/@{username}",
            ),
            username=username,
        )

        self.assertEqual(result[1], FOUND)

    def test_tiktok_bare_200_remains_possible(self):
        result = self.check(self.response(200, text="<html></html>"))

        self.assertEqual(result[1], POSSIBLE)

    def test_tiktok_mismatched_embedded_profile_remains_possible(self):
        result = self.check(
            self.response(
                200,
                text=self.tiktok_profile_html("another_user"),
            )
        )

        self.assertEqual(result[1], POSSIBLE)

    def test_tiktok_missing_profile_is_not_found(self):
        result = self.check(
            self.response(
                200,
                text=self.tiktok_status_html(10221),
            )
        )

        self.assertEqual(result[1], NOT_FOUND)

    def test_tiktok_banned_status_is_blocked(self):
        result = self.check(
            self.response(
                200,
                text=self.tiktok_status_html(
                    10221,
                    "user banned",
                ),
            )
        )

        self.assertEqual(result[1], BLOCKED)

    def test_tiktok_trailing_period_is_not_found(self):
        result = self.check(
            self.response(
                200,
                url="https://www.tiktok.com/@testname.",
            ),
            username="testname.",
        )

        self.assertEqual(result[1], NOT_FOUND)


class XVideosProfileDetectionTests(unittest.TestCase):
    def setUp(self):
        self.site_name = "XVideos"
        self.site_config = {
            "url": "https://www.xvideos.com/profiles/{username}",
            "checker": "xvideos_profile",
            "category": "adult",
            "sensitive": True,
            "rate_limit_delay": 0,
        }

    def response(self, status_code, text="", url=None):
        response = Mock()
        response.status_code = status_code
        response.text = text
        response.url = (
            url
            or "https://www.xvideos.com/profiles/test_user"
        )
        return response

    def check(self, response, username="test_user"):
        with (
            patch(
                "main.username.requests.request",
                return_value=response,
            ),
            patch("main.username.time.sleep"),
        ):
            return check_username_on_site(
                username,
                self.site_name,
                self.site_config,
            )

    def profile_html(self, username="test_user"):
        return f"""
            <html>
                <head>
                    <title>
                        {username} - Profile page - XVIDEOS.COM
                    </title>
                </head>
                <body>
                    <div id="profile-title">
                        <h2>{username} Profile details</h2>
                    </div>
                    <a href="/profiles/{username}">Profile</a>
                </body>
            </html>
        """

    def test_xvideos_complete_public_profile_is_found(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html(),
            )
        )

        self.assertEqual(result[1], FOUND)
        self.assertIn("identity not verified", result[3])

    def test_xvideos_bare_200_remains_possible(self):
        result = self.check(
            self.response(200, text="<html></html>")
        )

        self.assertEqual(result[1], POSSIBLE)

    def test_xvideos_wrong_final_url_is_unknown(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html(),
                url="https://www.xvideos.com/profiles/other_user",
            )
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_xvideos_mismatched_username_is_unknown(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html("other_user"),
            )
        )

        self.assertEqual(result[1], UNKNOWN)
        self.assertIn("another username", result[3])

    def test_xvideos_confirmed_missing_profile_is_not_found(self):
        html = """
            <html>
                <head>
                    <title>Unknown profile - XVIDEOS.COM</title>
                </head>
                <body>
                    <h1>THIS PROFILE DOESN'T EXIST!</h1>
                </body>
            </html>
        """

        result = self.check(self.response(404, text=html))

        self.assertEqual(result[1], NOT_FOUND)
        self.assertIn("no public XVideos profile", result[3])

    def test_xvideos_unconfirmed_404_is_unknown(self):
        result = self.check(
            self.response(404, text="<html></html>")
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_xvideos_403_is_blocked(self):
        result = self.check(self.response(403))

        self.assertEqual(result[1], BLOCKED)

    def test_xvideos_429_is_rate_limited(self):
        result = self.check(self.response(429))

        self.assertEqual(result[1], RATE_LIMIT)


class XNXXProfileDetectionTests(unittest.TestCase):
    def setUp(self):
        self.site_name = "XNXX"
        self.site_config = {
            "url": "https://www.xnxx.com/pornstar/{username}",
            "checker": "xnxx_profile",
            "category": "adult",
            "sensitive": True,
            "rate_limit_delay": 0,
        }

    def response(self, status_code, text="", url=None):
        response = Mock()
        response.status_code = status_code
        response.text = text
        response.url = (
            url
            or "https://www.xnxx.com/pornstar/test_user"
        )
        return response

    def check(self, response, username="test_user"):
        with (
            patch(
                "main.username.requests.request",
                return_value=response,
            ),
            patch("main.username.time.sleep"),
        ):
            return check_username_on_site(
                username,
                self.site_name,
                self.site_config,
            )

    def profile_html(self, user_overrides=None, include_user=True):
        user = {
            "id_user": "123456789",
            "username": "test_user",
            "display": "Test User",
            "model": True,
            "url": "/pornstar/test_user",
        }
        user.update(user_overrides or {})
        data = {"action": "profile"}

        if include_user:
            data["user"] = user

        config = {"data": data}

        return f"""
            <html>
                <head>
                    <title>Test User - Model page - XNXX.COM</title>
                    <link
                        rel="alternate"
                        hreflang="x-default"
                        href="https://www.xnxx.com/pornstar/test_user"
                    >
                </head>
                <body class="profile-page">
                    <h2 id="profile-info-title">
                        <span class="profile-username">Test User</span>
                    </h2>
                    <script>
                        window.xv.conf = {json.dumps(config)};
                    </script>
                </body>
            </html>
        """

    def test_xnxx_complete_public_model_profile_is_found(self):
        result = self.check(
            self.response(200, text=self.profile_html())
        )

        self.assertEqual(result[1], FOUND)
        self.assertEqual(
            result[3],
            "public XNXX model profile found; identity not verified",
        )

    def test_xnxx_missing_data_user_does_not_return_found(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html(include_user=False),
            )
        )

        self.assertEqual(result[1], POSSIBLE)
        self.assertNotEqual(result[1], FOUND)

    def test_xnxx_wrong_embedded_username_is_unknown(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html(
                    {"username": "other_user"},
                ),
            )
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_xnxx_wrong_embedded_profile_url_is_unknown(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html(
                    {"url": "/pornstar/other_user"},
                ),
            )
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_xnxx_missing_profile_id_is_unknown(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html({"id_user": ""}),
            )
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_xnxx_non_model_account_is_unknown(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html({"model": False}),
            )
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_xnxx_confirmed_missing_profile_is_not_found(self):
        config = {"data": {"action": "profile"}}
        html = f"""
            <html>
                <head>
                    <title>Unknown profile - XNXX.COM</title>
                </head>
                <body>
                    <h2>THIS PROFILE DOESN'T EXIST !</h2>
                    <script>
                        window.xv.conf = {json.dumps(config)};
                    </script>
                </body>
            </html>
        """
        result = self.check(self.response(404, text=html))

        self.assertEqual(result[1], NOT_FOUND)
        self.assertIn("no public XNXX model profile", result[3])

    def test_xnxx_403_is_blocked(self):
        result = self.check(self.response(403))

        self.assertEqual(result[1], BLOCKED)

    def test_xnxx_429_is_rate_limited(self):
        result = self.check(self.response(429))

        self.assertEqual(result[1], RATE_LIMIT)


class BookSusiProfileDetectionTests(unittest.TestCase):
    def setUp(self):
        self.site_name = "BookSusi"
        self.site_config = {
            "url": "https://booksusi.com/user/{username}/",
            "checker": "booksusi_profile",
            "category": "adult",
            "sensitive": True,
            "rate_limit_delay": 0,
        }

    def response(self, status_code, text="", url=None):
        response = Mock()
        response.status_code = status_code
        response.text = text
        response.url = (
            url
            or "https://booksusi.com/user/test_user/"
        )
        return response

    def check(self, response, username="test_user"):
        with (
            patch(
                "main.username.requests.request",
                return_value=response,
            ),
            patch("main.username.time.sleep"),
        ):
            return check_username_on_site(
                username,
                self.site_name,
                self.site_config,
            )

    def profile_html(
        self,
        canonical_url=(
            "https://booksusi.com/user/test_user/"
        ),
        entity_url="https://booksusi.com/user/test_user/",
        include_profile_page=True,
        include_person=True,
    ):
        scripts = [
            {
                "@context": "https://schema.org",
                "@type": "Organization",
                "name": "BookSusi",
                "url": "https://booksusi.com/",
            },
            {
                "@context": "https://schema.org",
                "@type": "WebSite",
                "name": "BookSusi",
                "url": "https://booksusi.com/",
            },
        ]

        if include_profile_page:
            main_entity = {
                "name": "Test Display",
                "url": entity_url,
            }

            if include_person:
                main_entity["@type"] = "Person"

            scripts.insert(
                0,
                {
                    "@context": "https://schema.org",
                    "@type": "ProfilePage",
                    "mainEntity": main_entity,
                },
            )

        canonical_html = (
            f'<link rel="canonical" href="{canonical_url}">'
            if canonical_url is not None
            else ""
        )
        scripts_html = "".join(
            '<script type="application/ld+json">'
            f"{json.dumps(payload)}"
            "</script>"
            for payload in scripts
        )

        return f"""
            <html>
                <head>
                    <title>
                        ANBIETER: Test Display - Wien - BookSusi
                    </title>
                    {canonical_html}
                    {scripts_html}
                </head>
                <body>
                    <a href="/login/">Login</a>
                    <a href="/register/">Register</a>
                    <h1 class="profile-name d-block d-sm-none">
                        Test Display
                    </h1>
                    <div id="profile-new" class="profile">
                        <div class="profile__identity">
                            Test Display seit 2020
                        </div>
                    </div>
                </body>
            </html>
        """

    def test_booksusi_complete_public_profile_is_found(self):
        result = self.check(
            self.response(200, text=self.profile_html())
        )

        self.assertEqual(result[1], FOUND)
        self.assertEqual(
            result[3],
            "public BookSusi profile found; identity not verified",
        )

    def test_booksusi_missing_canonical_does_not_return_found(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html(canonical_url=None),
            )
        )

        self.assertEqual(result[1], POSSIBLE)
        self.assertNotEqual(result[1], FOUND)

    def test_booksusi_wrong_canonical_is_unknown(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html(
                    canonical_url=(
                        "https://booksusi.com/user/other_user/"
                    ),
                ),
            )
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_booksusi_wrong_main_entity_url_is_unknown(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html(
                    entity_url=(
                        "https://booksusi.com/user/other_user/"
                    ),
                ),
            )
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_booksusi_missing_profile_page_does_not_return_found(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html(include_profile_page=False),
            )
        )

        self.assertEqual(result[1], POSSIBLE)
        self.assertNotEqual(result[1], FOUND)

    def test_booksusi_missing_person_does_not_return_found(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html(include_person=False),
            )
        )

        self.assertEqual(result[1], POSSIBLE)
        self.assertNotEqual(result[1], FOUND)

    def test_booksusi_confirmed_missing_profile_is_not_found(self):
        scripts = [
            {
                "@context": "https://schema.org",
                "@type": "Organization",
                "name": "BookSusi",
            },
            {
                "@context": "https://schema.org",
                "@type": "WebSite",
                "name": "BookSusi",
            },
        ]
        scripts_html = "".join(
            '<script type="application/ld+json">'
            f"{json.dumps(payload)}"
            "</script>"
            for payload in scripts
        )
        html = f"""
            <html>
                <head><title>BookSusi</title>{scripts_html}</head>
                <body>
                    <h1>Susi</h1>
                    <h1>404!</h1>
                    <p>
                        Die Seite die Sie versucht haben zu öffnen gibt es
                        leider nicht auf unserem Server! Sorry....
                    </p>
                </body>
            </html>
        """
        result = self.check(self.response(404, text=html))

        self.assertEqual(result[1], NOT_FOUND)
        self.assertIn("no public BookSusi profile", result[3])

    def test_booksusi_403_is_blocked(self):
        result = self.check(self.response(403))

        self.assertEqual(result[1], BLOCKED)

    def test_booksusi_429_is_rate_limited(self):
        result = self.check(self.response(429))

        self.assertEqual(result[1], RATE_LIMIT)

    def test_booksusi_username_comparison_uses_casefold(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html(),
                url="https://booksusi.com/user/Test_User/",
            ),
            username="Test_User",
        )

        self.assertEqual(result[1], FOUND)


class FanslyProfileDetectionTests(unittest.TestCase):
    def setUp(self):
        self.site_name = "Fansly"
        self.site_config = {
            "url": (
                "https://apiv3.fansly.com/api/v1/account"
                "?usernames={username}"
            ),
            "checker": "fansly_profile",
            "category": "adult",
            "sensitive": True,
            "rate_limit_delay": 0,
        }

    def response(self, status_code, payload=None, url=None):
        response = Mock()
        response.status_code = status_code
        response.text = json.dumps(payload) if payload is not None else ""
        response.url = (
            url
            or "https://apiv3.fansly.com/api/v1/account"
            "?usernames=test_user"
        )
        response.json.return_value = payload
        return response

    def check(self, response, username="test_user"):
        with (
            patch(
                "main.username.requests.request",
                return_value=response,
            ),
            patch("main.username.time.sleep"),
        ):
            return check_username_on_site(
                username,
                self.site_name,
                self.site_config,
            )

    def test_fansly_complete_public_profile_is_found(self):
        result = self.check(
            self.response(
                200,
                {
                    "success": True,
                    "response": [
                        {
                            "id": "225046783078694912",
                            "username": "test_user",
                        }
                    ],
                },
            )
        )

        self.assertEqual(result[1], FOUND)
        self.assertIn("identity not verified", result[3])

    def test_fansly_empty_result_is_not_found(self):
        result = self.check(
            self.response(
                200,
                {"success": True, "response": []},
            )
        )

        self.assertEqual(result[1], NOT_FOUND)
        self.assertIn("in this lookup", result[3])

    def test_fansly_mismatched_username_is_unknown(self):
        result = self.check(
            self.response(
                200,
                {
                    "success": True,
                    "response": [
                        {
                            "id": "225046783078694912",
                            "username": "other_user",
                        }
                    ],
                },
            )
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_fansly_missing_account_id_is_unknown(self):
        result = self.check(
            self.response(
                200,
                {
                    "success": True,
                    "response": [{"username": "test_user"}],
                },
            )
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_fansly_incomplete_json_is_unknown(self):
        result = self.check(
            self.response(200, {"success": True})
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_fansly_429_is_rate_limited(self):
        result = self.check(self.response(429))

        self.assertEqual(result[1], RATE_LIMIT)

    def test_fansly_403_is_blocked(self):
        result = self.check(self.response(403))

        self.assertEqual(result[1], BLOCKED)


class PornhubProfileDetectionTests(unittest.TestCase):
    def setUp(self):
        self.site_name = "Pornhub"
        self.site_config = {
            "url": "https://www.pornhub.com/pornstar/{username}",
            "checker": "pornhub_profile",
            "category": "adult",
            "sensitive": True,
            "rate_limit_delay": 0,
        }

    def response(
        self,
        status_code,
        text="",
        url=None,
        history=None,
    ):
        response = Mock()
        response.status_code = status_code
        response.text = text
        response.url = (
            url
            or "https://www.pornhub.com/pornstar/test-user"
        )
        response.history = history or []
        return response

    def check(self, response, username="test-user"):
        with (
            patch(
                "main.username.requests.request",
                return_value=response,
            ),
            patch("main.username.time.sleep"),
        ):
            return check_username_on_site(
                username,
                self.site_name,
                self.site_config,
            )

    def profile_html(
        self,
        slug="test-user",
        profile_name="Test User",
        canonical_slug=None,
        link_slug=None,
    ):
        canonical_slug = canonical_slug or slug
        link_slug = link_slug or slug

        return f"""
            <html>
                <head>
                    <title>
                        {profile_name} Porn Videos -
                        Verified Pornstar Profile | Pornhub
                    </title>
                    <link
                        rel="canonical"
                        href="https://www.pornhub.com/pornstar/{canonical_slug}"
                    >
                </head>
                <body>
                    <section class="topProfileHeader">
                        <h1 itemprop="name">{profile_name}</h1>
                        <span
                            class="verified-icon"
                            data-title="Verified Model"
                        ></span>
                    </section>
                    <nav id="mainMenuProfile">
                        <a href="/pornstar/{link_slug}">Home</a>
                    </nav>
                </body>
            </html>
        """

    def test_pornhub_complete_public_profile_is_found(self):
        result = self.check(
            self.response(200, text=self.profile_html())
        )

        self.assertEqual(result[1], FOUND)
        self.assertEqual(
            result[3],
            "public Pornhub profile found; identity not verified",
        )

    def test_pornhub_bare_200_does_not_return_found(self):
        result = self.check(
            self.response(200, text="<html></html>")
        )

        self.assertNotEqual(result[1], FOUND)

    def test_pornhub_wrong_final_url_is_unknown(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html(),
                url="https://www.pornhub.com/pornstar/other-user",
            )
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_pornhub_wrong_canonical_is_unknown(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html(
                    canonical_slug="other-user",
                ),
            )
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_pornhub_mismatched_username_is_unknown(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html(
                    profile_name="Other User",
                    link_slug="other-user",
                ),
            )
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_pornhub_catalog_redirect_is_not_found(self):
        redirect = Mock()
        redirect.status_code = 301
        redirect.url = (
            "https://www.pornhub.com/pornstar/test-user"
        )
        catalog_html = """
            <html>
                <head>
                    <title>Top Pornstars | Pornhub</title>
                    <link
                        rel="canonical"
                        href="https://www.pornhub.com/pornstars"
                    >
                </head>
            </html>
        """
        result = self.check(
            self.response(
                200,
                text=catalog_html,
                url="https://www.pornhub.com/pornstars",
                history=[redirect],
            )
        )

        self.assertEqual(result[1], NOT_FOUND)
        self.assertIn("at this path", result[3])

    def test_pornhub_403_is_blocked(self):
        result = self.check(self.response(403))

        self.assertEqual(result[1], BLOCKED)

    def test_pornhub_429_is_rate_limited(self):
        result = self.check(self.response(429))

        self.assertEqual(result[1], RATE_LIMIT)


class TinderProfileDetectionTests(unittest.TestCase):
    def setUp(self):
        self.site_name = "Tinder"
        self.site_config = {
            "url": "https://tinder.com/@{username}",
            "checker": "tinder_profile",
            "category": "dating",
            "sensitive": True,
            "rate_limit_delay": 0,
        }

    def response(self, status_code, text="", url=None):
        response = Mock()
        response.status_code = status_code
        response.text = text
        response.url = url or "https://tinder.com/@test_user"
        return response

    def check(self, response, username="test_user"):
        with (
            patch(
                "main.username.requests.request",
                return_value=response,
            ),
            patch("main.username.time.sleep"),
        ):
            return check_username_on_site(
                username,
                self.site_name,
                self.site_config,
            )

    def profile_html(
        self,
        username="test_user",
        canonical_username=None,
        og_username=None,
        title_username=None,
        person_username=None,
    ):
        canonical_username = canonical_username or username
        og_username = og_username or username
        title_username = title_username or username
        person_username = person_username or username
        person = {
            "@context": "https://schema.org/",
            "@type": "Person",
            "name": "Test User",
            "alternateName": person_username,
        }

        return f"""
            <html>
                <head>
                    <title>Test User (@{title_username}) | Tinder</title>
                    <link
                        rel="canonical"
                        href="https://tinder.com/@{canonical_username}"
                    >
                    <meta
                        property="og:url"
                        content="https://tinder.com/@{og_username}"
                    >
                    <script type="application/ld+json">
                        {json.dumps(person)}
                    </script>
                </head>
            </html>
        """

    def test_tinder_complete_public_profile_is_found(self):
        result = self.check(
            self.response(200, text=self.profile_html())
        )

        self.assertEqual(result[1], FOUND)
        self.assertIn("identity not verified", result[3])

    def test_tinder_incomplete_200_is_possible(self):
        result = self.check(
            self.response(200, text="<html></html>")
        )

        self.assertEqual(result[1], POSSIBLE)

    def test_tinder_wrong_final_url_is_unknown(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html(),
                url="https://tinder.com/@other_user",
            )
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_tinder_wrong_canonical_is_unknown(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html(
                    canonical_username="other_user",
                ),
            )
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_tinder_wrong_og_url_is_unknown(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html(
                    og_username="other_user",
                ),
            )
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_tinder_mismatched_username_is_unknown(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html(
                    title_username="other_user",
                    person_username="other_user",
                ),
            )
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_tinder_home_page_is_unknown(self):
        home_html = """
            <html>
                <head>
                    <title>
                        Tinder | Dating, Make Friends & Meet New People
                    </title>
                    <link rel="canonical" href="https://tinder.com">
                    <meta property="og:url" content="https://tinder.com">
                </head>
            </html>
        """
        result = self.check(
            self.response(
                200,
                text=home_html,
                url="https://tinder.com/",
            )
        )

        self.assertEqual(result[1], UNKNOWN)
        self.assertIn("account existence unknown", result[3])

    def test_tinder_403_is_blocked(self):
        result = self.check(self.response(403))

        self.assertEqual(result[1], BLOCKED)

    def test_tinder_429_is_rate_limited(self):
        result = self.check(self.response(429))

        self.assertEqual(result[1], RATE_LIMIT)


class YouTubeChannelDetectionTests(unittest.TestCase):
    def setUp(self):
        self.site_name = "YouTube"
        self.site_config = {
            "url": "https://www.youtube.com/@{username}",
            "checker": "youtube_channel",
            "cookies": {"SOCS": "CAI"},
            "rate_limit_delay": 0,
        }
        self.username = "TestChannel"
        self.channel_id = "UCTestChannel1234567890ab"

    def response(self, status_code, text="", url=None):
        response = Mock()
        response.status_code = status_code
        response.text = text
        response.url = (
            url
            or f"https://www.youtube.com/@{self.username}"
        )
        return response

    def check(
        self,
        response=None,
        username=None,
        side_effect=None,
    ):
        checked_username = username or self.username

        with (
            patch(
                "main.username.requests.request",
                return_value=response,
                side_effect=side_effect,
            ),
            patch("main.username.time.sleep"),
        ):
            return check_username_on_site(
                checked_username,
                self.site_name,
                self.site_config,
            )

    def channel_html(
        self,
        *,
        username=None,
        html_channel_id=None,
        json_channel_id=None,
        include_initial_data=True,
        include_microformat=True,
    ):
        username = username or self.username
        html_channel_id = html_channel_id or self.channel_id
        json_channel_id = json_channel_id or html_channel_id
        handle_url = f"https://www.youtube.com/@{username}"
        channel_url = (
            f"https://www.youtube.com/channel/{html_channel_id}"
        )
        json_channel_url = (
            f"https://www.youtube.com/channel/{json_channel_id}"
        )

        initial_data_script = ""

        if include_initial_data:
            microformat = {}

            if include_microformat:
                microformat = {
                    "microformat": {
                        "microformatDataRenderer": {
                            "urlCanonical": json_channel_url,
                            "channelProfileMicroformatDetails": {
                                "profilePage": {
                                    "url": json_channel_url,
                                    "mainEntity": {
                                        "url": json_channel_url,
                                        "alternateName": f"@{username}",
                                    },
                                },
                            },
                        },
                    },
                }

            initial_data = {
                "metadata": {
                    "channelMetadataRenderer": {
                        "externalId": json_channel_id,
                        "channelUrl": json_channel_url,
                        "ownerUrls": [handle_url],
                        "vanityChannelUrl": handle_url,
                    },
                },
                **microformat,
            }
            initial_data_script = (
                "<script>var ytInitialData = "
                f"{json.dumps(initial_data)};"
                "</script>"
            )

        return f"""
            <html>
                <head>
                    <title>{username} - YouTube</title>
                    <link rel="canonical" href="{channel_url}">
                    <meta property="og:url" content="{channel_url}">
                    <meta property="og:type" content="profile">
                    <meta itemprop="identifier" content="{html_channel_id}">
                </head>
                <body>{initial_data_script}</body>
            </html>
        """

    def test_youtube_complete_multisignal_profile_is_found(self):
        result = self.check(
            self.response(200, text=self.channel_html())
        )

        self.assertEqual(result[1], FOUND)
        self.assertEqual(
            result[3],
            "public YouTube channel found; identity not verified",
        )

    def test_youtube_unicode_handle_uses_url_decode_and_casefold(self):
        username = "日本テレビ"
        encoded_url = (
            "https://www.youtube.com/"
            "@%E6%97%A5%E6%9C%AC%E3%83%86%E3%83%AC%E3%83%93"
        )
        result = self.check(
            self.response(
                200,
                text=self.channel_html(username=username),
                url=encoded_url,
            ),
            username=username,
        )

        self.assertEqual(result[1], FOUND)

    def test_youtube_incomplete_200_is_possible(self):
        result = self.check(
            self.response(
                200,
                text="<html><title>TestChannel - YouTube</title></html>",
            )
        )

        self.assertEqual(result[1], POSSIBLE)

    def test_youtube_missing_microformat_handle_is_possible(self):
        result = self.check(
            self.response(
                200,
                text=self.channel_html(include_microformat=False),
            )
        )

        self.assertEqual(result[1], POSSIBLE)

    def test_youtube_conflicting_handle_is_unknown(self):
        result = self.check(
            self.response(
                200,
                text=self.channel_html(username="AnotherChannel"),
            )
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_youtube_conflicting_channel_id_is_unknown(self):
        result = self.check(
            self.response(
                200,
                text=self.channel_html(
                    json_channel_id="UCAnotherChannel123456789",
                ),
            )
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_youtube_wrong_final_handle_is_unknown(self):
        result = self.check(
            self.response(
                200,
                text=self.channel_html(),
                url="https://www.youtube.com/@AnotherChannel",
            )
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_youtube_confirmed_404_is_not_found(self):
        result = self.check(
            self.response(
                404,
                text="<html><title>404 Not Found</title></html>",
            )
        )

        self.assertEqual(result[1], NOT_FOUND)

    def test_youtube_404_with_profile_data_is_unknown(self):
        result = self.check(
            self.response(
                404,
                text=self.channel_html(),
            )
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_youtube_consent_redirect_is_blocked(self):
        result = self.check(
            self.response(
                200,
                text="<html><title>Before you continue</title></html>",
                url="https://consent.youtube.com/m?continue=profile",
            )
        )

        self.assertEqual(result[1], BLOCKED)

    def test_youtube_403_is_blocked(self):
        result = self.check(self.response(403))

        self.assertEqual(result[1], BLOCKED)

    def test_youtube_429_is_rate_limited(self):
        result = self.check(self.response(429))

        self.assertEqual(result[1], RATE_LIMIT)

    def test_youtube_5xx_is_error(self):
        result = self.check(self.response(503))

        self.assertEqual(result[1], ERROR)

    def test_youtube_timeout_is_error(self):
        result = self.check(side_effect=requests.Timeout())

        self.assertEqual(result[1], ERROR)

    def test_youtube_network_failure_is_error(self):
        result = self.check(side_effect=requests.ConnectionError())

        self.assertEqual(result[1], ERROR)

    def test_youtube_config_has_checker_without_found_status_200(self):
        config = load_sites_config()["YouTube"]

        self.assertEqual(config["checker"], "youtube_channel")
        self.assertNotIn("found_status_codes", config)
        self.assertEqual(config["cookies"], {"SOCS": "CAI"})


class RedditDetectionRegressionTests(unittest.TestCase):
    def check(self, response):
        config = {
            **load_sites_config()["Reddit"],
            "rate_limit_delay": 0,
        }

        with (
            patch(
                "main.username.requests.request",
                return_value=response,
            ),
            patch("main.username.time.sleep"),
        ):
            return check_username_on_site(
                "test_user",
                "Reddit",
                config,
            )

    def response(self, status_code, text=""):
        response = Mock()
        response.status_code = status_code
        response.text = text
        response.url = "https://www.reddit.com/user/test_user"
        return response

    def test_reddit_trophy_case_alone_is_not_found_evidence(self):
        result = self.check(
            self.response(200, "<html>Trophy Case</html>")
        )

        self.assertEqual(result[1], POSSIBLE)

    def test_reddit_403_remains_blocked(self):
        result = self.check(self.response(403, "Trophy Case"))

        self.assertEqual(result[1], BLOCKED)


class FacebookProfileDetectionTests(unittest.TestCase):
    def setUp(self):
        self.site_name = "Facebook"
        self.username = "TestUser"
        self.profile_id = "123456789"
        self.site_config = {
            "url": "https://www.facebook.com/{username}",
            "checker": "facebook_profile",
            "needs_login": True,
            "rate_limit_delay": 0,
        }

    def response(self, status_code, text="", url=None):
        response = Mock()
        response.status_code = status_code
        response.text = text
        response.url = (
            url
            or f"https://www.facebook.com/{self.username}"
        )
        return response

    def check(self, response=None, side_effect=None):
        with (
            patch(
                "main.username.requests.request",
                return_value=response,
                side_effect=side_effect,
            ),
            patch("main.username.time.sleep"),
        ):
            return check_username_on_site(
                self.username,
                self.site_name,
                self.site_config,
            )

    def test_config_uses_dedicated_checker(self):
        config = load_sites_config()[self.site_name]
        self.assertEqual(config["checker"], self.site_config["checker"])

    def profile_html(
        self,
        *,
        canonical_username=None,
        og_username=None,
        route_username=None,
        deep_link_profile_id=None,
        route_profile_id=None,
        include_canonical=True,
        include_profile_id=True,
        include_profile_root=True,
    ):
        canonical_username = canonical_username or self.username
        og_username = og_username or self.username
        route_username = route_username or self.username
        deep_link_profile_id = (
            deep_link_profile_id or self.profile_id
        )
        route_profile_id = route_profile_id or self.profile_id
        title = "Test User"

        canonical = ""

        if include_canonical:
            canonical = (
                '<link rel="canonical" '
                f'href="https://www.facebook.com/{canonical_username}/">'
            )

        deep_links = ""
        route_id = ""

        if include_profile_id:
            deep_links = f"""
                <meta property="al:android:url"
                    content="fb://profile/{deep_link_profile_id}">
                <meta property="al:ios:url"
                    content="fb://profile/{deep_link_profile_id}">
            """
            route_id = f'"userID":"{route_profile_id}",'

        profile_root = (
            '"resource":{"__dr":'
            '"ProfilePlusCometLoggedOutRoot.react"},'
            if include_profile_root
            else ""
        )
        route_name = (
            '"canonicalRouteName":'
            '"comet.fbweb.CometProfilePlusLoggedOutRoute"'
            if include_profile_root
            else '"canonicalRouteName":"unrecognized.route"'
        )
        route_data = (
            "{"
            f"{profile_root}"
            f'"props":{{{route_id}'
            f'"userVanity":"{route_username}"}},'
            f'"url":"/{route_username}",'
            f'"params":{{"vanity":"{route_username}"}},'
            f"{route_name}"
            "}"
        )

        return f"""
            <html>
                <head>
                    <title>{title}</title>
                    {canonical}
                    <meta property="og:url"
                        content="https://www.facebook.com/{og_username}/">
                    <meta property="og:title" content="{title}">
                    {deep_links}
                </head>
                <body><script>{route_data}</script></body>
            </html>
        """

    def test_facebook_complete_public_profile_is_found(self):
        result = self.check(
            self.response(200, text=self.profile_html())
        )

        self.assertEqual(result[1], FOUND)
        self.assertEqual(
            result[3],
            "public Facebook profile found; identity not verified",
        )

    def test_facebook_missing_canonical_does_not_return_found(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html(include_canonical=False),
            )
        )

        self.assertEqual(result[1], POSSIBLE)

    def test_facebook_wrong_canonical_is_unknown(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html(
                    canonical_username="AnotherUser",
                ),
            )
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_facebook_wrong_og_url_is_unknown(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html(og_username="AnotherUser"),
            )
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_facebook_vanity_conflict_is_unknown(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html(route_username="AnotherUser"),
            )
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_facebook_profile_id_conflict_is_unknown(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html(route_profile_id="987654321"),
            )
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_facebook_missing_profile_id_does_not_return_found(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html(include_profile_id=False),
            )
        )

        self.assertEqual(result[1], POSSIBLE)

    def test_facebook_missing_one_deep_link_does_not_return_found(self):
        html = self.profile_html().replace(
            '<meta property="al:ios:url"',
            '<meta property="removed:ios:url"',
        )
        result = self.check(self.response(200, text=html))

        self.assertEqual(result[1], POSSIBLE)

    def test_facebook_comet_error_route_is_unknown(self):
        html = """
            <html>
                <head><title>Facebook</title></head>
                <body><script>
                    {"privacy":true,"tracePolicy":"comet.error",
                    "canonicalRouteName":"comet.fbweb.CometErrorRoute"}
                </script></body>
            </html>
        """
        result = self.check(self.response(200, text=html))

        self.assertEqual(result[1], UNKNOWN)
        self.assertIn("account existence unknown", result[3])

    def test_facebook_login_redirect_is_blocked(self):
        result = self.check(
            self.response(
                200,
                text="<html><title>Log into Facebook</title></html>",
                url=(
                    "https://www.facebook.com/login/"
                    "?next=%2FTestUser"
                ),
            )
        )

        self.assertEqual(result[1], BLOCKED)

    def test_facebook_bundle_words_alone_do_not_mean_blocked(self):
        html = """
            <html><head><title>Test User</title></head>
            <body>login checkpoint captcha challenge</body></html>
        """
        result = self.check(self.response(200, text=html))

        self.assertEqual(result[1], POSSIBLE)

    def test_facebook_404_is_unknown_not_not_found(self):
        result = self.check(
            self.response(
                404,
                text="<html><title>Facebook</title></html>",
            )
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_facebook_429_is_rate_limited(self):
        result = self.check(self.response(429))

        self.assertEqual(result[1], RATE_LIMIT)

    def test_facebook_5xx_is_error(self):
        result = self.check(self.response(503))

        self.assertEqual(result[1], ERROR)

    def test_facebook_timeout_is_error(self):
        result = self.check(side_effect=requests.Timeout())

        self.assertEqual(result[1], ERROR)

    def test_facebook_network_failure_is_error(self):
        result = self.check(side_effect=requests.ConnectionError())

        self.assertEqual(result[1], ERROR)

    def test_facebook_config_uses_dedicated_checker(self):
        config = load_sites_config()["Facebook"]

        self.assertEqual(config["checker"], "facebook_profile")


class GitHubProfileDetectionTests(unittest.TestCase):
    def setUp(self):
        self.site_name = "GitHub"
        self.username = "TestUser"
        self.user_id = "123456789"
        self.site_config = {
            "url": "https://github.com/{username}",
            "checker": "github_profile",
            "rate_limit_delay": 0,
        }

    def response(self, status_code, text="", url=None):
        response = Mock()
        response.status_code = status_code
        response.text = text
        response.url = url or f"https://github.com/{self.username}"
        return response

    def check(self, response):
        with (
            patch(
                "main.username.requests.request",
                return_value=response,
            ),
            patch("main.username.time.sleep"),
        ):
            return check_username_on_site(
                self.username,
                self.site_name,
                self.site_config,
            )

    def profile_html(
        self,
        *,
        canonical_username=None,
        og_username=None,
        metadata_username=None,
        metadata_user_id=None,
        avatar_user_id=None,
        include_profile_marker=True,
    ):
        canonical_username = canonical_username or self.username
        og_username = og_username or self.username
        metadata_username = metadata_username or self.username
        metadata_user_id = metadata_user_id or self.user_id
        avatar_user_id = avatar_user_id or self.user_id
        marker = (
            '<main itemtype="https://schema.org/Person"></main>'
            if include_profile_marker
            else ""
        )

        return f"""
            <html>
                <head>
                    <title>{self.username} (Test User) · GitHub</title>
                    <link rel="canonical"
                        href="https://github.com/{canonical_username}">
                    <meta property="og:url"
                        content="https://github.com/{og_username}">
                    <meta property="og:type" content="profile">
                    <meta property="og:title"
                        content="{self.username} - Overview">
                    <meta property="og:image"
                        content="https://avatars.githubusercontent.com/u/{avatar_user_id}?v=4">
                    <meta property="profile:username"
                        content="{metadata_username}">
                    <meta name="octolytics-dimension-user_login"
                        content="{metadata_username}">
                    <meta name="octolytics-dimension-user_id"
                        content="{metadata_user_id}">
                </head>
                <body>{marker}</body>
            </html>
        """

    def test_github_complete_public_profile_is_found(self):
        result = self.check(
            self.response(200, text=self.profile_html())
        )

        self.assertEqual(result[1], FOUND)
        self.assertEqual(
            result[3],
            "public GitHub profile found; identity not verified",
        )

    def test_github_confirmed_404_is_not_found(self):
        result = self.check(self.response(404, text="Not Found"))

        self.assertEqual(result[1], NOT_FOUND)

    def test_github_username_conflict_is_unknown(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html(metadata_username="AnotherUser"),
            )
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_github_canonical_conflict_is_unknown(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html(
                    canonical_username="AnotherUser",
                ),
            )
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_github_og_url_conflict_is_unknown(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html(og_username="AnotherUser"),
            )
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_github_stable_id_conflict_is_unknown(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html(avatar_user_id="987654321"),
            )
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_github_missing_profile_marker_is_possible(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html(include_profile_marker=False),
            )
        )

        self.assertEqual(result[1], POSSIBLE)

    def test_github_403_is_blocked(self):
        result = self.check(self.response(403))

        self.assertEqual(result[1], BLOCKED)

    def test_github_429_is_rate_limited(self):
        result = self.check(self.response(429))

        self.assertEqual(result[1], RATE_LIMIT)

    def test_github_5xx_is_error(self):
        result = self.check(self.response(503))

        self.assertEqual(result[1], ERROR)

    def test_github_config_uses_dedicated_checker(self):
        config = load_sites_config()["GitHub"]

        self.assertEqual(config["checker"], "github_profile")
        self.assertEqual(config["not_found_status_codes"], [404])


class InstagramProfileDetectionTests(unittest.TestCase):
    def setUp(self):
        self.site_name = "Instagram"
        self.username = "TestUser"
        self.profile_id = "123456789"
        self.site_config = {
            "url": "https://www.instagram.com/{username}",
            "checker": "instagram_profile",
            "needs_login": True,
            "rate_limit_delay": 0,
        }

    def response(self, status_code, text="", url=None):
        response = Mock()
        response.status_code = status_code
        response.text = text
        response.url = (
            url
            or f"https://www.instagram.com/{self.username}/"
        )
        return response

    def check(self, response):
        with (
            patch(
                "main.username.requests.request",
                return_value=response,
            ),
            patch("main.username.time.sleep"),
        ):
            return check_username_on_site(
                self.username,
                self.site_name,
                self.site_config,
            )

    def profile_html(
        self,
        *,
        canonical_username=None,
        og_username=None,
        route_username=None,
        props_profile_id=None,
        page_profile_id=None,
        logging_profile_id=None,
        include_profile_json=True,
    ):
        canonical_username = canonical_username or self.username
        og_username = og_username or self.username
        route_username = route_username or self.username
        props_profile_id = props_profile_id or self.profile_id
        page_profile_id = page_profile_id or props_profile_id
        logging_profile_id = logging_profile_id or props_profile_id
        profile_json = ""

        if include_profile_json:
            profile_json = f"""
                <script>
                    {{"resource":{{"__dr":
                    "PolarisProfilePostsTabRoot.react"}},
                    "props":{{"id":"{props_profile_id}",
                    "page_logging":{{"name":"profilePage",
                    "params":{{"page_id":
                    "profilePage_{page_profile_id}",
                    "profile_id":"{logging_profile_id}"}}}}}},
                    "root":"PolarisLoggedOutDesktopWWWProfileRoot.react",
                    "canonicalRouteName":
                    "comet.igweb.PolarisLoggedOutDesktopWWWProfileRoute",
                    "url":"/{route_username}/",
                    "params":{{"username":"{route_username}"}}}}
                </script>
            """

        return f"""
            <html>
                <head>
                    <title>Test User (@{self.username}) • Instagram photos and videos</title>
                    <link rel="canonical"
                        href="https://www.instagram.com/{canonical_username}/">
                    <meta property="og:url"
                        content="https://www.instagram.com/{og_username}/">
                    <meta property="og:type" content="profile">
                    <meta property="og:title"
                        content="Test User (@{self.username}) • Instagram photos and videos">
                    <meta property="al:ios:url"
                        content="instagram://user?username={route_username}">
                    <meta property="al:android:url"
                        content="https://instagram.com/_u/{route_username}/">
                </head>
                <body>{profile_json}</body>
            </html>
        """

    def test_instagram_complete_public_profile_is_found(self):
        result = self.check(
            self.response(200, text=self.profile_html())
        )

        self.assertEqual(result[1], FOUND)
        self.assertEqual(
            result[3],
            "public Instagram profile found; identity not verified",
        )

    def test_instagram_soft_404_http_200_is_not_found_evidence(self):
        result = self.check(
            self.response(200, text="<html><title>Instagram</title></html>")
        )

        self.assertEqual(result[1], UNKNOWN)
        self.assertNotEqual(result[1], FOUND)

    def test_instagram_username_conflict_is_unknown(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html(route_username="AnotherUser"),
            )
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_instagram_canonical_conflict_is_unknown(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html(
                    canonical_username="AnotherUser",
                ),
            )
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_instagram_og_url_conflict_is_unknown(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html(og_username="AnotherUser"),
            )
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_instagram_stable_id_conflict_is_unknown(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html(page_profile_id="987654321"),
            )
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_instagram_incomplete_profile_data_is_possible(self):
        result = self.check(
            self.response(
                200,
                text=self.profile_html(include_profile_json=False),
            )
        )

        self.assertEqual(result[1], POSSIBLE)

    def test_instagram_login_redirect_is_blocked(self):
        result = self.check(
            self.response(
                200,
                text="<html><title>Login • Instagram</title></html>",
                url=(
                    "https://www.instagram.com/accounts/login/"
                    "?next=%2FTestUser%2F"
                ),
            )
        )

        self.assertEqual(result[1], BLOCKED)

    def test_instagram_429_is_rate_limited(self):
        result = self.check(self.response(429))

        self.assertEqual(result[1], RATE_LIMIT)

    def test_instagram_5xx_is_error(self):
        result = self.check(self.response(503))

        self.assertEqual(result[1], ERROR)

    def test_instagram_config_uses_dedicated_checker(self):
        config = load_sites_config()["Instagram"]

        self.assertEqual(config["checker"], "instagram_profile")


class PublicProfileCheckerMixin:
    username = "Test_User"
    site_name = ""
    site_config = {}
    default_url = ""

    def response(self, status_code, text="", url=None):
        response = Mock()
        response.status_code = status_code
        response.text = text
        response.url = url or self.default_url
        return response

    def check(self, response):
        with (
            patch(
                "main.username.requests.request",
                return_value=response,
            ),
            patch("main.username.time.sleep"),
        ):
            return check_username_on_site(
                self.username,
                self.site_name,
                self.site_config,
            )


class PinterestProfileDetectionTests(
    PublicProfileCheckerMixin,
    unittest.TestCase,
):
    site_name = "Pinterest"
    default_url = "https://www.pinterest.com/Test_User/"
    site_config = {
        "url": "https://www.pinterest.com/{username}/",
        "checker": "pinterest_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(
        self,
        *,
        canonical_username="Test_User",
        schema_username="Test_User",
        user_ids=("12345",),
        include_schema=True,
        include_initial_props=True,
    ):
        canonical_url = (
            f"https://www.pinterest.com/{canonical_username}/"
        )
        schema = ""
        if include_schema:
            schema = f"""
                <script type="application/ld+json">
                {{
                    "@type": "ProfilePage",
                    "mainEntity": {{
                        "@type": "Person",
                        "alternateName": "{schema_username}",
                        "url": "https://www.pinterest.com/{schema_username}/",
                        "identifier": "https://www.pinterest.com/{schema_username}/"
                    }}
                }}
                </script>
            """
        initial_props = ""
        if include_initial_props:
            users = [
                {
                    "type": "user",
                    "username": self.username,
                    "id": user_id,
                }
                for user_id in user_ids
            ]
            initial_props = (
                '<script id="__PWS_INITIAL_PROPS__" '
                f'type="application/json">{json.dumps({"users": users})}'
                "</script>"
            )

        return f"""
            <html><head>
                <title>Test ({self.username}) - Profile | Pinterest</title>
                <link rel="canonical" href="{canonical_url}">
                <meta property="og:url" content="{canonical_url}">
                <meta property="og:type" content="profile">
                {schema}
                {initial_props}
            </head></html>
        """

    def test_pinterest_certain_found(self):
        result = self.check(self.response(200, self.profile_html()))
        self.assertEqual(result[1], FOUND)

    def test_pinterest_canonical_conflict_is_unknown(self):
        result = self.check(
            self.response(
                200,
                self.profile_html(canonical_username="Other_User"),
            )
        )
        self.assertEqual(result[1], UNKNOWN)

    def test_pinterest_username_conflict_is_unknown(self):
        result = self.check(
            self.response(
                200,
                self.profile_html(schema_username="Other_User"),
            )
        )
        self.assertEqual(result[1], UNKNOWN)

    def test_pinterest_stable_id_conflict_is_unknown(self):
        result = self.check(
            self.response(200, self.profile_html(user_ids=("123", "456")))
        )
        self.assertEqual(result[1], UNKNOWN)

    def test_pinterest_incomplete_data_is_possible(self):
        result = self.check(
            self.response(
                200,
                self.profile_html(include_initial_props=False),
            )
        )
        self.assertEqual(result[1], POSSIBLE)

    def test_pinterest_soft_404_is_unknown(self):
        result = self.check(self.response(200, "<html><title></title></html>"))
        self.assertEqual(result[1], UNKNOWN)

    def test_pinterest_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_pinterest_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)


class TwitchProfileDetectionTests(
    PublicProfileCheckerMixin,
    unittest.TestCase,
):
    site_name = "Twitch"
    default_url = "https://www.twitch.tv/Test_User"
    site_config = {
        "url": "https://www.twitch.tv/{username}",
        "checker": "twitch_profile",
        "rate_limit_delay": 0,
    }
    profile_id = "11111111-2222-3333-4444-555555555555"

    def profile_html(
        self,
        *,
        canonical_username="Test_User",
        schema_username="Test_User",
        schema_id=None,
        og_id=None,
        include_schema=True,
    ):
        schema_id = schema_id or self.profile_id
        og_id = og_id or self.profile_id
        canonical_url = f"https://www.twitch.tv/{canonical_username}"
        schema = ""
        if include_schema:
            schema = f"""
                <script type="application/ld+json">
                {{
                    "@type": "ProfilePage",
                    "mainEntity": {{
                        "@type": "Person",
                        "alternateName": "{schema_username}",
                        "url": "https://www.twitch.tv/{schema_username}",
                        "image": "https://static-cdn.jtvnw.net/jtv_user_pictures/{schema_id}-profile_image-300x300.png"
                    }}
                }}
                </script>
            """
        return f"""
            <html><head>
                <title>Test User - Twitch</title>
                <link rel="canonical" href="{canonical_url}">
                <meta property="og:url" content="{canonical_url}">
                <meta property="og:type" content="profile">
                <meta property="og:image" content="https://static-cdn.jtvnw.net/jtv_user_pictures/{og_id}-profile_image-300x300.png">
                {schema}
            </head></html>
        """

    def test_twitch_certain_found(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html()))[1],
            FOUND,
        )

    def test_twitch_canonical_conflict_is_unknown(self):
        result = self.check(
            self.response(
                200,
                self.profile_html(canonical_username="Other_User"),
            )
        )
        self.assertEqual(result[1], UNKNOWN)

    def test_twitch_username_conflict_is_unknown(self):
        result = self.check(
            self.response(
                200,
                self.profile_html(schema_username="Other_User"),
            )
        )
        self.assertEqual(result[1], UNKNOWN)

    def test_twitch_stable_id_conflict_is_unknown(self):
        result = self.check(
            self.response(
                200,
                self.profile_html(
                    og_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                ),
            )
        )
        self.assertEqual(result[1], UNKNOWN)

    def test_twitch_incomplete_data_is_possible(self):
        result = self.check(
            self.response(200, self.profile_html(include_schema=False))
        )
        self.assertEqual(result[1], POSSIBLE)

    def test_twitch_soft_404_is_unknown(self):
        html = '<html><head><title>Twitch</title><meta property="og:type" content="website"></head></html>'
        self.assertEqual(self.check(self.response(200, html))[1], UNKNOWN)

    def test_twitch_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_twitch_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)


class SoundCloudProfileDetectionTests(
    PublicProfileCheckerMixin,
    unittest.TestCase,
):
    site_name = "SoundCloud"
    default_url = "https://soundcloud.com/Test_User"
    site_config = {
        "url": "https://soundcloud.com/{username}",
        "checker": "soundcloud_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(
        self,
        *,
        canonical_username="Test_User",
        permalink="Test_User",
        user_id="12345",
        urn_id="12345",
        deep_link_id="12345",
        include_deep_links=True,
    ):
        canonical_url = f"https://soundcloud.com/{canonical_username}"
        hydration = [{
            "hydratable": "user",
            "data": {
                "id": int(user_id) if user_id.isdigit() else user_id,
                "kind": "user",
                "permalink": permalink,
                "permalink_url": f"https://soundcloud.com/{permalink}",
                "url": f"/{permalink}",
                "urn": f"soundcloud:users:{urn_id}",
                "uri": (
                    "https://api.soundcloud.com/users/"
                    f"soundcloud%3Ausers%3A{urn_id}"
                ),
            },
        }]
        deep_links = ""
        if include_deep_links:
            deep_links = f"""
                <meta property="al:ios:url" content="soundcloud://users:{deep_link_id}">
                <meta property="al:android:url" content="soundcloud://users:{deep_link_id}">
            """
        return f"""
            <html><head>
                <title>Stream Test User on SoundCloud</title>
                <link rel="canonical" href="{canonical_url}">
                <meta property="og:url" content="{canonical_url}">
                <meta property="og:type" content="music.musician">
                {deep_links}
                <script>window.__sc_hydration = {json.dumps(hydration)};</script>
            </head></html>
        """

    def test_soundcloud_certain_found(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html()))[1],
            FOUND,
        )

    def test_soundcloud_canonical_conflict_is_unknown(self):
        result = self.check(
            self.response(
                200,
                self.profile_html(canonical_username="Other_User"),
            )
        )
        self.assertEqual(result[1], UNKNOWN)

    def test_soundcloud_username_conflict_is_unknown(self):
        result = self.check(
            self.response(200, self.profile_html(permalink="Other_User"))
        )
        self.assertEqual(result[1], UNKNOWN)

    def test_soundcloud_stable_id_conflict_is_unknown(self):
        result = self.check(
            self.response(200, self.profile_html(urn_id="99999"))
        )
        self.assertEqual(result[1], UNKNOWN)

    def test_soundcloud_incomplete_data_is_possible(self):
        result = self.check(
            self.response(
                200,
                self.profile_html(include_deep_links=False),
            )
        )
        self.assertEqual(result[1], POSSIBLE)

    def test_soundcloud_confirmed_404_is_not_found(self):
        html = "<html><title>SoundCloud - Hear the world’s sounds</title></html>"
        self.assertEqual(self.check(self.response(404, html))[1], NOT_FOUND)

    def test_soundcloud_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_soundcloud_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)


class XingProfileDetectionTests(
    PublicProfileCheckerMixin,
    unittest.TestCase,
):
    site_name = "Xing"
    default_url = "https://www.xing.com/profile/Test_User"
    site_config = {
        "url": "https://www.xing.com/profile/{username}",
        "checker": "xing_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(
        self,
        *,
        canonical_username="test_user",
        record_username="Test_User",
        stable_id="12345.abc123",
        include_runtime=True,
    ):
        canonical_url = (
            f"https://www.xing.com/profile/{canonical_username}"
        )
        runtime = ""
        if include_runtime:
            reference = f"XingId:{stable_id}"
            crate = {
                "serverData": {
                    "APOLLO_STATE": {
                        "ROOT_QUERY": {
                            'xingIdWithError({"id":"Test_User"})': {
                                "__ref": reference,
                            },
                        },
                        reference: {
                            "__typename": "XingId",
                            "id": stable_id,
                            "pageName": record_username,
                        },
                    },
                },
            }
            runtime = (
                '<script id="runtime-config">window.crate='
                f"{json.dumps(crate)}</script>"
            )
        return f"""
            <html><head>
                <title>Test User - Developer | XING</title>
                <link rel="canonical" href="{canonical_url}">
                <meta property="og:url" content="{canonical_url}">
                <meta property="og:type" content="profile">
                {runtime}
            </head></html>
        """

    def test_xing_certain_found_and_casefolded_canonical(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html()))[1],
            FOUND,
        )

    def test_xing_canonical_conflict_is_unknown(self):
        result = self.check(
            self.response(
                200,
                self.profile_html(canonical_username="Other_User"),
            )
        )
        self.assertEqual(result[1], UNKNOWN)

    def test_xing_username_conflict_is_unknown(self):
        result = self.check(
            self.response(
                200,
                self.profile_html(record_username="Other_User"),
            )
        )
        self.assertEqual(result[1], UNKNOWN)

    def test_xing_stable_id_conflict_is_unknown(self):
        result = self.check(
            self.response(200, self.profile_html(stable_id="invalid"))
        )
        self.assertEqual(result[1], UNKNOWN)

    def test_xing_incomplete_data_is_possible(self):
        result = self.check(
            self.response(200, self.profile_html(include_runtime=False))
        )
        self.assertEqual(result[1], POSSIBLE)

    def test_xing_confirmed_404_is_not_found(self):
        html = "<html><title>404 - Not Found | XING</title></html>"
        self.assertEqual(self.check(self.response(404, html))[1], NOT_FOUND)

    def test_xing_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_xing_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)


class SnapchatProfileDetectionTests(
    PublicProfileCheckerMixin,
    unittest.TestCase,
):
    site_name = "Snapchat"
    default_url = "https://www.snapchat.com/@Test_User"
    site_config = {
        "url": "https://www.snapchat.com/@{username}",
        "checker": "snapchat_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(
        self,
        *,
        canonical_username="Test_User",
        public_username="Test_User",
        stable_id="11111111-2222-3333-4444-555555555555",
        include_schema=True,
    ):
        canonical_url = (
            f"https://www.snapchat.com/@{canonical_username}"
        )
        next_data = {
            "props": {
                "pageProps": {
                    "userProfile": {
                        "$case": "publicProfileInfo",
                        "publicProfileInfo": {
                            "username": public_username,
                            "title": "Test User",
                            "hostUserId": stable_id,
                        },
                    },
                },
            },
        }
        schema = ""
        if include_schema:
            schema = f"""
                <script type="application/ld+json">
                {{
                    "@type": "ProfilePage",
                    "mainEntity": {{
                        "@type": "Person",
                        "alternateName": "{public_username}",
                        "url": "https://www.snapchat.com/@{public_username}"
                    }}
                }}
                </script>
            """
        return f"""
            <html><head>
                <title>Test User (@{self.username}) | Snapchat</title>
                <link rel="canonical" href="{canonical_url}">
                <meta property="og:url" content="{canonical_url}">
                {schema}
                <script id="__NEXT_DATA__" type="application/json">{json.dumps(next_data)}</script>
            </head></html>
        """

    def test_snapchat_certain_found(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html()))[1],
            FOUND,
        )

    def test_snapchat_canonical_conflict_is_unknown(self):
        result = self.check(
            self.response(
                200,
                self.profile_html(canonical_username="Other_User"),
            )
        )
        self.assertEqual(result[1], UNKNOWN)

    def test_snapchat_username_conflict_is_unknown(self):
        result = self.check(
            self.response(
                200,
                self.profile_html(public_username="Other_User"),
            )
        )
        self.assertEqual(result[1], UNKNOWN)

    def test_snapchat_stable_id_conflict_is_unknown(self):
        result = self.check(
            self.response(200, self.profile_html(stable_id="invalid"))
        )
        self.assertEqual(result[1], UNKNOWN)

    def test_snapchat_incomplete_data_is_possible(self):
        result = self.check(
            self.response(200, self.profile_html(include_schema=False))
        )
        self.assertEqual(result[1], POSSIBLE)

    def test_snapchat_404_is_unknown_not_account_not_found(self):
        html = "<html><title>Snapchat</title></html>"
        self.assertEqual(self.check(self.response(404, html))[1], UNKNOWN)

    def test_snapchat_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_snapchat_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)

    def test_snapchat_config_uses_new_public_profile_endpoint(self):
        config = load_sites_config()["Snapchat"]
        self.assertEqual(config["url"], "https://www.snapchat.com/@{username}")
        self.assertEqual(config["checker"], "snapchat_profile")


class GitLabProfileDetectionTests(
    PublicProfileCheckerMixin,
    unittest.TestCase,
):
    site_name = "GitLab"
    default_url = "https://gitlab.com/Test_User"
    site_config = {
        "url": "https://gitlab.com/{username}",
        "checker": "gitlab_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(
        self,
        *,
        metadata_username="Test_User",
        user_ids=("12345", "12345"),
        include_profile_marker=True,
    ):
        profile_marker = ""
        if include_profile_marker:
            profile_marker = (
                '<div data-testid="user-profile-header"></div>'
            )
        return f"""
            <html><head>
                <title>Test User · GitLab</title>
                <meta property="og:url" content="https://gitlab.com/{metadata_username}">
                <meta property="og:type" content="object">
                <script type="application/ld+json">
                {{
                    "@context": "https://schema.org",
                    "@type": "BreadcrumbList",
                    "itemListElement": [{{
                        "@type": "ListItem",
                        "position": 1,
                        "name": "Test User",
                        "item": "https://gitlab.com/{metadata_username}"
                    }}]
                }}
                </script>
            </head>
            <body data-page="users:show">
                {profile_marker}
                <div class="js-user-profile-actions"
                     data-rss-subscription-path="/{metadata_username}.atom"
                     data-user-id="{user_ids[0]}"></div>
                <div id="js-user-achievements"
                     data-user-id="{user_ids[1]}"></div>
            </body></html>
        """

    def test_gitlab_certain_found(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html()))[1],
            FOUND,
        )

    def test_gitlab_username_and_url_conflict_is_unknown(self):
        result = self.check(
            self.response(
                200,
                self.profile_html(metadata_username="Other_User"),
            )
        )
        self.assertEqual(result[1], UNKNOWN)

    def test_gitlab_stable_id_conflict_is_unknown(self):
        result = self.check(
            self.response(
                200,
                self.profile_html(user_ids=("12345", "98765")),
            )
        )
        self.assertEqual(result[1], UNKNOWN)

    def test_gitlab_incomplete_data_is_possible(self):
        result = self.check(
            self.response(
                200,
                self.profile_html(include_profile_marker=False),
            )
        )
        self.assertEqual(result[1], POSSIBLE)

    def test_gitlab_sign_in_redirect_is_blocked(self):
        result = self.check(
            self.response(
                403,
                "<html><title>Just a moment...</title></html>",
                url="https://gitlab.com/users/sign_in",
            )
        )
        self.assertEqual(result[1], BLOCKED)

    def test_gitlab_unconfirmed_404_is_unknown(self):
        self.assertEqual(self.check(self.response(404))[1], UNKNOWN)

    def test_gitlab_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)


class DockerHubProfileDetectionTests(
    PublicProfileCheckerMixin,
    unittest.TestCase,
):
    site_name = "Docker Hub"
    default_url = "https://hub.docker.com/u/Test_User"
    site_config = {
        "url": "https://hub.docker.com/u/{username}",
        "checker": "dockerhub_profile",
        "rate_limit_delay": 0,
    }
    compact_id = "11111111222233334444555555555555"
    profile_uuid = "11111111-2222-3333-4444-555555555555"

    @staticmethod
    def flattened_router_data(value):
        flattened = []

        def add(item):
            index = len(flattened)
            flattened.append(None)
            if isinstance(item, dict):
                encoded = {}
                flattened[index] = encoded
                for key, child in item.items():
                    key_index = add(str(key))
                    child_index = add(child)
                    encoded[f"_{key_index}"] = child_index
            elif isinstance(item, list):
                flattened[index] = [add(child) for child in item]
            else:
                flattened[index] = item
            return index

        add(value)
        return flattened

    def profile_html(
        self,
        *,
        canonical_username="Test_User",
        public_username="Test_User",
        compact_id=None,
        profile_uuid=None,
        include_router=True,
    ):
        compact_id = compact_id or self.compact_id
        profile_uuid = profile_uuid or self.profile_uuid
        canonical_url = (
            f"https://hub.docker.com/u/{canonical_username}"
        )
        router_script = ""
        if include_router:
            router_data = {
                "loaderData": {
                    "routes/_layout.u.$namespace": {
                        "canonicalUrl": canonical_url,
                        "profile": {
                            "id": compact_id,
                            "uuid": profile_uuid,
                            "type": "organization",
                            "orgname": public_username,
                            "full_name": "Test Organization",
                        },
                    },
                },
            }
            flattened = self.flattened_router_data(router_data)
            serialized = json.dumps(json.dumps(flattened))
            router_script = (
                "<script>window.__reactRouterContext.streamController."
                f"enqueue({serialized})</script>"
            )

        return f"""
            <html><head>
                <title>Test Organization</title>
                <link rel="canonical" href="{canonical_url}">
            </head><body>
                <div data-testid="page_community_profile">
                    <div data-testid="profile-header">
                        <h1>Test Organization</h1>
                    </div>
                </div>
                {router_script}
            </body></html>
        """

    def test_dockerhub_certain_found(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html()))[1],
            FOUND,
        )

    def test_dockerhub_canonical_conflict_is_unknown(self):
        result = self.check(
            self.response(
                200,
                self.profile_html(canonical_username="Other_User"),
            )
        )
        self.assertEqual(result[1], UNKNOWN)

    def test_dockerhub_username_conflict_is_unknown(self):
        result = self.check(
            self.response(
                200,
                self.profile_html(public_username="Other_User"),
            )
        )
        self.assertEqual(result[1], UNKNOWN)

    def test_dockerhub_stable_id_conflict_is_unknown(self):
        result = self.check(
            self.response(
                200,
                self.profile_html(
                    profile_uuid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                ),
            )
        )
        self.assertEqual(result[1], UNKNOWN)

    def test_dockerhub_incomplete_data_is_possible(self):
        result = self.check(
            self.response(200, self.profile_html(include_router=False))
        )
        self.assertEqual(result[1], POSSIBLE)

    def test_dockerhub_confirmed_404_is_not_found(self):
        html = "<html><title>Page Not Found</title></html>"
        self.assertEqual(self.check(self.response(404, html))[1], NOT_FOUND)

    def test_dockerhub_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_dockerhub_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)


class FiverrProfileDetectionTests(
    PublicProfileCheckerMixin,
    unittest.TestCase,
):
    site_name = "Fiverr"
    default_url = "https://www.fiverr.com/Test_User"
    site_config = {
        "url": "https://www.fiverr.com/{username}",
        "checker": "fiverr_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(
        self,
        *,
        canonical_username="Test_User",
        public_username="Test_User",
        seller_id="12345",
        secondary_id="12345",
        include_public_json=True,
    ):
        canonical_url = f"https://www.fiverr.com/{canonical_username}"
        public_json = ""
        if include_public_json:
            data = {
                "seller": {
                    "user": {"id": seller_id, "name": public_username},
                    "isActive": True,
                },
                "localizationData": {"user_id": secondary_id},
                "reviewsData": {
                    "buying_reviews": {"user_id": secondary_id},
                    "selling_reviews": {"user_id": secondary_id},
                },
            }
            public_json = (
                '<script id="perseus-initial-props" '
                f'type="application/json">{json.dumps(data)}</script>'
            )
        return f"""
            <html><head>
                <title>Test User | Profile | Fiverr</title>
                <link rel="canonical" href="{canonical_url}">
                <meta property="og:url" content="{canonical_url}">
                <script type="application/ld+json">
                {{
                    "@context": "https://schema.org",
                    "@type": "ProfilePage",
                    "url": "https://www.fiverr.com/{public_username}",
                    "mainEntity": {{
                        "@type": "Person",
                        "name": "Test User",
                        "url": "https://www.fiverr.com/{public_username}"
                    }}
                }}
                </script>
                {public_json}
            </head></html>
        """

    def test_fiverr_certain_found(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html()))[1],
            FOUND,
        )

    def test_fiverr_canonical_conflict_is_unknown(self):
        result = self.check(
            self.response(
                200,
                self.profile_html(canonical_username="Other_User"),
            )
        )
        self.assertEqual(result[1], UNKNOWN)

    def test_fiverr_username_conflict_is_unknown(self):
        result = self.check(
            self.response(
                200,
                self.profile_html(public_username="Other_User"),
            )
        )
        self.assertEqual(result[1], UNKNOWN)

    def test_fiverr_stable_id_conflict_is_unknown(self):
        result = self.check(
            self.response(200, self.profile_html(secondary_id="98765"))
        )
        self.assertEqual(result[1], UNKNOWN)

    def test_fiverr_incomplete_data_is_possible(self):
        result = self.check(
            self.response(
                200,
                self.profile_html(include_public_json=False),
            )
        )
        self.assertEqual(result[1], POSSIBLE)

    def test_fiverr_confirmed_404_is_not_found(self):
        html = """
            <html><head>
                <title>Page not found - Fiverr</title>
                <meta property="og:url" content="https://www.fiverr.com">
            </head></html>
        """
        self.assertEqual(self.check(self.response(404, html))[1], NOT_FOUND)

    def test_fiverr_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_fiverr_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)


class BehanceProfileDetectionTests(
    PublicProfileCheckerMixin,
    unittest.TestCase,
):
    site_name = "Behance"
    default_url = "https://www.behance.net/Test_User"
    site_config = {
        "url": "https://www.behance.net/{username}",
        "checker": "behance_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(
        self,
        *,
        canonical_username="Test_User",
        public_username="Test_User",
        stable_id="12345",
        schema_id="12345",
        include_store=True,
    ):
        canonical_url = (
            f"https://www.behance.net/{canonical_username}"
        )
        store_script = ""
        if include_store:
            store = {
                "profile": {
                    "user": {
                        "id": int(stable_id),
                        "username": public_username,
                        "displayName": "Test User",
                        "url": f"https://www.behance.net/{public_username}",
                    },
                },
            }
            store_script = (
                '<script id="beconfig-store_state" '
                f'type="application/json">{json.dumps(store)}</script>'
            )
        return f"""
            <html><head>
                <title>Test User - Designer :: Behance</title>
                <link rel="canonical" href="{canonical_url}">
                <script type="application/ld+json">
                {{
                    "@context": "http://schema.org",
                    "@type": "Person",
                    "name": "Test User",
                    "identifier": {schema_id},
                    "url": "https://www.behance.net/{public_username}"
                }}
                </script>
                {store_script}
            </head></html>
        """

    def test_behance_certain_found(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html()))[1],
            FOUND,
        )

    def test_behance_canonical_conflict_is_unknown(self):
        result = self.check(
            self.response(
                200,
                self.profile_html(canonical_username="Other_User"),
            )
        )
        self.assertEqual(result[1], UNKNOWN)

    def test_behance_username_conflict_is_unknown(self):
        result = self.check(
            self.response(
                200,
                self.profile_html(public_username="Other_User"),
            )
        )
        self.assertEqual(result[1], UNKNOWN)

    def test_behance_stable_id_conflict_is_unknown(self):
        result = self.check(
            self.response(200, self.profile_html(schema_id="98765"))
        )
        self.assertEqual(result[1], UNKNOWN)

    def test_behance_incomplete_data_is_possible(self):
        result = self.check(
            self.response(200, self.profile_html(include_store=False))
        )
        self.assertEqual(result[1], POSSIBLE)

    def test_behance_confirmed_404_is_not_found(self):
        html = (
            "<html><title>Oops! We can’t find that page. :: "
            "Behance</title></html>"
        )
        self.assertEqual(self.check(self.response(404, html))[1], NOT_FOUND)

    def test_behance_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_behance_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)


class VimeoProfileDetectionTests(
    PublicProfileCheckerMixin,
    unittest.TestCase,
):
    site_name = "Vimeo"
    default_url = "https://vimeo.com/Test_User"
    site_config = {
        "url": "https://vimeo.com/{username}",
        "checker": "vimeo_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(
        self,
        *,
        public_username="Test_User",
        stable_id="12345",
        schema_id="12345",
        canonical_username=None,
        include_schema=True,
    ):
        canonical_username = canonical_username or public_username
        canonical_url = f"https://vimeo.com/{canonical_username}"
        schema = {}
        if include_schema:
            schema = {
                "@context": "http://schema.org",
                "@graph": [{
                    "@type": "ProfilePage",
                    "url": canonical_url,
                    "mainEntity": {
                        "@type": "Person",
                        "name": "Test User",
                        "identifier": int(schema_id),
                        "alternateName": public_username,
                        "url": f"/{public_username}",
                        "sameAs": [f"https://vimeo.com/{public_username}"],
                    },
                }],
            }
        next_data = {
            "props": {
                "pageProps": {
                    "userId": public_username,
                    "numericUserId": int(stable_id),
                    "profileMeta": {
                        "title": "Test User",
                        "canonical": canonical_url,
                        "crawlable": {
                            "pageUrl": canonical_url,
                            "name": "Test User",
                            "userId": int(stable_id),
                            "jsonLd": json.dumps(schema),
                        },
                    },
                },
            },
            "page": "/profile/[userId]",
            "query": {"userId": public_username},
        }
        return f"""
            <html><head>
                <title>Test User</title>
                <link rel="canonical" href="{canonical_url}">
                <meta property="og:url" content="{canonical_url}">
                <script id="__NEXT_DATA__" type="application/json">{json.dumps(next_data)}</script>
            </head></html>
        """

    def test_vimeo_certain_found(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html()))[1],
            FOUND,
        )

    def test_vimeo_numeric_alias_is_confirmed_by_stable_id(self):
        self.username = "user12345"
        self.default_url = "https://vimeo.com/CurrentVanity"
        result = self.check(
            self.response(
                200,
                self.profile_html(public_username="CurrentVanity"),
            )
        )
        self.assertEqual(result[1], FOUND)
        self.assertEqual(result[2], "https://vimeo.com/CurrentVanity")

    def test_vimeo_unconfirmed_vanity_redirect_is_unknown(self):
        result = self.check(
            self.response(
                200,
                self.profile_html(public_username="Other_User"),
                url="https://vimeo.com/Other_User",
            )
        )
        self.assertEqual(result[1], UNKNOWN)

    def test_vimeo_url_conflict_is_unknown(self):
        result = self.check(
            self.response(
                200,
                self.profile_html(canonical_username="Other_User"),
            )
        )
        self.assertEqual(result[1], UNKNOWN)

    def test_vimeo_stable_id_conflict_is_unknown(self):
        result = self.check(
            self.response(200, self.profile_html(schema_id="98765"))
        )
        self.assertEqual(result[1], UNKNOWN)

    def test_vimeo_incomplete_data_is_possible(self):
        result = self.check(
            self.response(200, self.profile_html(include_schema=False))
        )
        self.assertEqual(result[1], POSSIBLE)

    def test_vimeo_confirmed_404_is_not_found(self):
        html = """
            <html><head><title>Vimeo</title>
            <script id="__NEXT_DATA__" type="application/json">
                {"props": {}, "page": "/404"}
            </script></head></html>
        """
        self.assertEqual(self.check(self.response(404, html))[1], NOT_FOUND)

    def test_vimeo_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_vimeo_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)


class NewPublicProfileCheckerConfigTests(unittest.TestCase):
    def test_five_services_use_dedicated_checkers(self):
        config = load_sites_config()
        expected = {
            "GitLab": "gitlab_profile",
            "Docker Hub": "dockerhub_profile",
            "Fiverr": "fiverr_profile",
            "Behance": "behance_profile",
            "Vimeo": "vimeo_profile",
        }

        for service_name, checker in expected.items():
            with self.subTest(service=service_name):
                self.assertEqual(config[service_name]["checker"], checker)


class DribbbleProfileDetectionTests(
    PublicProfileCheckerMixin,
    unittest.TestCase,
):
    site_name = "Dribbble"
    default_url = "https://dribbble.com/Test_User"
    site_config = {
        "url": "https://dribbble.com/{username}",
        "checker": "dribbble_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(
        self,
        *,
        public_username="Test_User",
        stable_id="12345",
        marker_id="12345",
        include_schema=True,
    ):
        schema = ""
        if include_schema:
            schema = f"""
                <script type="application/ld+json">
                {{
                    "@type": "ProfilePage",
                    "url": "https://dribbble.com/{public_username}",
                    "image": "https://cdn.dribbble.com/users/{stable_id}/avatars/normal/avatar.png",
                    "mainEntity": {{
                        "@type": "Person",
                        "name": "Test User",
                        "image": "https://cdn.dribbble.com/users/{stable_id}/avatars/normal/avatar.png"
                    }}
                }}
                </script>
            """
        return f"""
            <html><head>
                <title>Test User | Dribbble</title>
                <link rel="canonical" href="https://dribbble.com/{public_username}">
                <meta property="og:url" content="https://dribbble.com/{public_username}">
                <meta name="twitter:creator" content="@{public_username}">
                {schema}
            </head><body id="profile">
                <div data-user-id="{marker_id}"></div>
            </body></html>
        """

    def test_dribbble_certain_found(self):
        self.assertEqual(self.check(self.response(200, self.profile_html()))[1], FOUND)

    def test_dribbble_confirmed_404_is_not_found(self):
        html = "<html><title>Sorry, the page you were looking for doesn't exist. (404)</title></html>"
        self.assertEqual(self.check(self.response(404, html))[1], NOT_FOUND)

    def test_dribbble_username_or_url_conflict_is_unknown(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html(public_username="Other_User")))[1],
            UNKNOWN,
        )

    def test_dribbble_stable_id_conflict_is_unknown(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html(marker_id="98765")))[1],
            UNKNOWN,
        )

    def test_dribbble_incomplete_data_is_possible(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html(include_schema=False)))[1],
            POSSIBLE,
        )

    def test_dribbble_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_dribbble_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)


class AboutMeProfileDetectionTests(
    PublicProfileCheckerMixin,
    unittest.TestCase,
):
    site_name = "About.me"
    default_url = "https://about.me/Test_User"
    site_config = {
        "url": "https://about.me/{username}",
        "checker": "aboutme_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(
        self,
        *,
        public_username="Test_User",
        stable_id=12345,
        include_state=True,
    ):
        state = ""
        if include_state:
            data = {
                "page": {
                    "id": "profile",
                    "user": {
                        "user_id": stable_id,
                        "user_name": public_username,
                    },
                },
            }
            state = f'<script type="text/json">{json.dumps(data)}</script>'
        return f"""
            <html><head>
                <title>Test User | about.me</title>
                <link rel="canonical" href="https://about.me/{public_username}">
                <meta property="og:url" content="https://about.me/{public_username}">
                <meta property="og:type" content="aboutme_prod:page">
                <script type="application/ld+json">
                {{"@type":"Person","name":"Test User","url":"https://about.me/{public_username}"}}
                </script>
                {state}
            </head><body><main class="profile"></main></body></html>
        """

    def test_aboutme_certain_found(self):
        self.assertEqual(self.check(self.response(200, self.profile_html()))[1], FOUND)

    def test_aboutme_confirmed_404_is_not_found(self):
        self.assertEqual(
            self.check(self.response(404, "<html><title>about.me</title></html>"))[1],
            NOT_FOUND,
        )

    def test_aboutme_username_or_url_conflict_is_unknown(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html(public_username="Other_User")))[1],
            UNKNOWN,
        )

    def test_aboutme_stable_id_conflict_is_unknown(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html(stable_id="invalid")))[1],
            UNKNOWN,
        )

    def test_aboutme_incomplete_data_is_possible(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html(include_state=False)))[1],
            POSSIBLE,
        )

    def test_aboutme_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_aboutme_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)


class GravatarProfileDetectionTests(
    PublicProfileCheckerMixin,
    unittest.TestCase,
):
    site_name = "Gravatar"
    default_url = "https://gravatar.com/Test_User"
    site_config = {
        "url": "https://en.gravatar.com/{username}",
        "checker": "gravatar_profile",
        "rate_limit_delay": 0,
    }
    login_id = "0123456789abcdef0123456789abcdef"
    avatar_id = "a" * 64

    def profile_html(
        self,
        *,
        public_username="Test_User",
        login_id=None,
        include_public_profile=True,
    ):
        login_id = self.login_id if login_id is None else login_id
        public_profile = ""
        if include_public_profile:
            data = {
                "profileUrl": f"https://gravatar.com/{public_username}",
                "userLogin": public_username,
                "userLoginMD5": login_id,
            }
            public_profile = f"<script>const gravatarProfile = {json.dumps(data)};</script>"
        return f"""
            <html><head>
                <title>Test_User | Gravatar</title>
                <link rel="canonical" href="https://gravatar.com/{public_username}">
                <meta property="og:url" content="https://gravatar.com/{public_username}">
                <meta property="og:type" content="profile">
                <script type="application/ld+json">
                {{
                    "@type":"Person",
                    "url":"https://gravatar.com/{public_username}",
                    "name":"Test User",
                    "image":"https://0.gravatar.com/avatar/{self.avatar_id}"
                }}
                </script>
                {public_profile}
            </head><body class="is-profile"><main class="g-profile"></main></body></html>
        """

    def test_gravatar_allowed_domain_redirect_found(self):
        self.assertEqual(self.check(self.response(200, self.profile_html()))[1], FOUND)

    def test_gravatar_confirmed_404_is_not_found_even_with_canonical(self):
        html = '<html><head><title>Gravatar - Globally Recognized Avatars</title><link rel="canonical" href="https://gravatar.com/Test_User"></head></html>'
        self.assertEqual(self.check(self.response(404, html))[1], NOT_FOUND)

    def test_gravatar_username_or_url_conflict_is_unknown(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html(public_username="Other_User")))[1],
            UNKNOWN,
        )

    def test_gravatar_stable_id_conflict_is_unknown(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html(login_id="invalid")))[1],
            UNKNOWN,
        )

    def test_gravatar_incomplete_data_is_possible(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html(include_public_profile=False)))[1],
            POSSIBLE,
        )

    def test_gravatar_unrelated_redirect_is_unknown(self):
        response = self.response(200, self.profile_html(), url="https://gravatar.com/Other_User")
        self.assertEqual(self.check(response)[1], UNKNOWN)

    def test_gravatar_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_gravatar_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)


class DevToProfileDetectionTests(
    PublicProfileCheckerMixin,
    unittest.TestCase,
):
    site_name = "DEV.to"
    default_url = "https://dev.to/Test_User"
    site_config = {
        "url": "https://dev.to/{username}",
        "checker": "devto_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(
        self,
        *,
        public_username="Test_User",
        image_id="12345",
        identifier="12345",
        include_marker=True,
    ):
        marker = '<header class="profile-header"></header>' if include_marker else ""
        image = (
            "https://dev-to-uploads.s3.amazonaws.com/uploads/user/"
            f"profile_image/{image_id}/avatar.png"
        )
        return f"""
            <html><head>
                <title>Test User - DEV Community</title>
                <link rel="canonical" href="https://dev.to/{public_username}">
                <meta property="og:url" content="https://dev.to/{public_username}">
                <script type="application/ld+json">
                {{
                    "@type":"Person",
                    "identifier":"{identifier}",
                    "url":"https://dev.to/{public_username}",
                    "mainEntityOfPage":{{"@type":"WebPage","@id":"https://dev.to/{public_username}"}},
                    "image":"{image}"
                }}
                </script>
            </head><body>{marker}</body></html>
        """

    def test_devto_certain_found(self):
        self.assertEqual(self.check(self.response(200, self.profile_html()))[1], FOUND)

    def test_devto_confirmed_404_is_not_found(self):
        html = "<html><title>404: Page Not Found</title></html>"
        self.assertEqual(self.check(self.response(404, html))[1], NOT_FOUND)

    def test_devto_username_or_url_conflict_is_unknown(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html(public_username="Other_User")))[1],
            UNKNOWN,
        )

    def test_devto_stable_id_conflict_is_unknown(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html(image_id="98765")))[1],
            UNKNOWN,
        )

    def test_devto_incomplete_data_is_possible(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html(include_marker=False)))[1],
            POSSIBLE,
        )

    def test_devto_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_devto_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)


class DisqusProfileDetectionTests(
    PublicProfileCheckerMixin,
    unittest.TestCase,
):
    site_name = "Disqus"
    default_url = "https://disqus.com/by/Test_User/"
    site_config = {
        "url": "https://disqus.com/by/{username}",
        "checker": "disqus_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(self, *, public_username="Test_User", include_canonical=True):
        url = f"https://disqus.com/by/{public_username}/"
        canonical = f'<link rel="canonical" href="{url}">' if include_canonical else ""
        return f"""
            <html><head>
                <title>Disqus Profile - {public_username}</title>
                {canonical}
                <meta property="og:url" content="{url}">
                <meta property="og:type" content="profile">
                <meta property="al:iphone:url" content="disqus://users/{public_username}">
            </head></html>
        """

    def test_disqus_trailing_slash_redirect_found(self):
        self.assertEqual(self.check(self.response(200, self.profile_html()))[1], FOUND)

    def test_disqus_confirmed_404_is_not_found(self):
        html = "<html><title>Page not found (404) - Disqus</title></html>"
        self.assertEqual(self.check(self.response(404, html))[1], NOT_FOUND)

    def test_disqus_username_or_url_conflict_is_unknown(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html(public_username="Other_User")))[1],
            UNKNOWN,
        )

    def test_disqus_incomplete_data_is_possible(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html(include_canonical=False)))[1],
            POSSIBLE,
        )

    def test_disqus_unrelated_redirect_is_unknown(self):
        response = self.response(200, self.profile_html(), url="https://disqus.com/by/Other_User/")
        self.assertEqual(self.check(response)[1], UNKNOWN)

    def test_disqus_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_disqus_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)


class SecondPublicProfileCheckerConfigTests(unittest.TestCase):
    def test_five_services_use_dedicated_checkers(self):
        config = load_sites_config()
        expected = {
            "Dribbble": "dribbble_profile",
            "About.me": "aboutme_profile",
            "Gravatar": "gravatar_profile",
            "Gravatar (alt)": "gravatar_profile",
            "DEV.to": "devto_profile",
            "Disqus": "disqus_profile",
        }

        for service_name, checker in expected.items():
            with self.subTest(service=service_name):
                self.assertEqual(config[service_name]["checker"], checker)


class ChessComProfileDetectionTests(
    PublicProfileCheckerMixin,
    unittest.TestCase,
):
    site_name = "Chess.com"
    default_url = "https://www.chess.com/member/Test_User"
    site_config = {
        "url": "https://www.chess.com/member/{username}",
        "checker": "chesscom_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(
        self,
        *,
        public_username="Test_User",
        dom_id="12345",
        script_id="12345",
        include_script=True,
    ):
        script = ""
        if include_script:
            script = f'''<script>
                window.chesscom.user = {{
                    userId: {script_id},
                    username: "{public_username}",
                    uuid: "11111111-2222-3333-4444-555555555555",
                }};
            </script>'''
        return f"""
            <html><head>
                <title>GM Test Name ({public_username}) - Chess Profile - Chess.com</title>
                <link rel="canonical" href="https://www.chess.com/member/{public_username}">
                <meta property="og:url" content="https://www.chess.com/member/{public_username}">
                <meta property="og:title" content="GM Test Name ({public_username}) - Chess Profile">
            </head><body>
                <div class="profile-header-container" data-username="{public_username}"
                     data-user-id="{dom_id}"
                     data-user-uuid="11111111-2222-3333-4444-555555555555">
                    <div class="profile-header"></div>
                </div>
                <div id="view-profile" data-username="{public_username}"
                     data-user-id="{dom_id}"
                     data-user-uuid="11111111-2222-3333-4444-555555555555"></div>
                {script}
            </body></html>
        """

    def test_chesscom_certain_found(self):
        self.assertEqual(self.check(self.response(200, self.profile_html()))[1], FOUND)

    def test_chesscom_confirmed_404_is_not_found(self):
        html = "<html><head><title>Missing Page - Chess.com</title></head></html>"
        self.assertEqual(self.check(self.response(404, html))[1], NOT_FOUND)

    def test_chesscom_username_conflict_is_unknown(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html(public_username="Other_User")))[1],
            UNKNOWN,
        )

    def test_chesscom_url_conflict_is_unknown(self):
        response = self.response(200, self.profile_html(), url="https://www.chess.com/member/Other_User")
        self.assertEqual(self.check(response)[1], UNKNOWN)

    def test_chesscom_stable_id_conflict_is_unknown(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html(script_id="98765")))[1],
            UNKNOWN,
        )

    def test_chesscom_incomplete_data_is_possible(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html(include_script=False)))[1],
            POSSIBLE,
        )

    def test_chesscom_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_chesscom_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)


class RobloxProfileDetectionTests(
    PublicProfileCheckerMixin,
    unittest.TestCase,
):
    site_name = "Roblox"
    default_url = "https://www.roblox.com/users/12345/profile"
    site_config = {
        "url": "https://www.roblox.com/user.aspx?username={username}",
        "checker": "roblox_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(
        self,
        *,
        public_username="Test_User",
        url_id="12345",
        marker_id="12345",
        include_marker=True,
    ):
        marker = ""
        if include_marker:
            marker = (
                '<div class="profile-platform-container" '
                f'data-profile-type="User" data-profile-id="{marker_id}"></div>'
            )
        return f"""
            <html><head>
                <title>{public_username} - Roblox</title>
                <link rel="canonical" href="https://www.roblox.com/users/{url_id}/profile">
                <meta property="og:url" content="https://www.roblox.com/users/{url_id}/profile">
                <meta property="og:type" content="profile">
                <meta property="og:title" content="{public_username}'s Profile">
            </head><body>{marker}</body></html>
        """

    def test_roblox_certain_found_after_resolver_redirect(self):
        self.assertEqual(self.check(self.response(200, self.profile_html()))[1], FOUND)

    def test_roblox_confirmed_request_error_is_not_found(self):
        response = self.response(
            404,
            "<html><head><title>Roblox</title></head></html>",
            url="https://www.roblox.com/request-error?code=404",
        )
        self.assertEqual(self.check(response)[1], NOT_FOUND)

    def test_roblox_username_conflict_is_unknown(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html(public_username="Other_User")))[1],
            UNKNOWN,
        )

    def test_roblox_url_conflict_is_unknown(self):
        response = self.response(
            200,
            self.profile_html(),
            url="https://www.roblox.com/users/98765/profile",
        )
        self.assertEqual(self.check(response)[1], UNKNOWN)

    def test_roblox_stable_id_conflict_is_unknown(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html(marker_id="98765")))[1],
            UNKNOWN,
        )

    def test_roblox_incomplete_data_is_possible(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html(include_marker=False)))[1],
            POSSIBLE,
        )

    def test_roblox_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_roblox_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)


class FlickrProfileDetectionTests(
    PublicProfileCheckerMixin,
    unittest.TestCase,
):
    site_name = "Flickr"
    default_url = "https://www.flickr.com/people/Test_User"
    site_config = {
        "url": "https://www.flickr.com/people/{username}",
        "checker": "flickr_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(
        self,
        *,
        public_username="Test_User",
        state_nsid="12345@N01",
        person_nsid="12345@N01",
        include_canonical=True,
    ):
        canonical = (
            f'<link rel="canonical" href="https://www.flickr.com/people/{public_username}/">'
            if include_canonical else ""
        )
        state = {
            "pathAlias": public_username,
            "nsid": state_nsid,
            "personModel": {
                "pathAlias": public_username,
                "username": "Display Name",
                "nsid": person_nsid,
                "url": f"/photos/{public_username}/",
            },
        }
        return f"""
            <html><head>
                <title>About Display Name | Flickr</title>
                {canonical}
                <meta property="og:url" content="https://www.flickr.com/people/{public_username}/">
                <meta property="og:type" content="article">
            </head><body><script>
                app = {{
                    name: 'profile-page-view',
                    params: {json.dumps(state)},
                    layout: 'scrolling'
                }};
            </script></body></html>
        """

    def test_flickr_certain_found(self):
        self.assertEqual(self.check(self.response(200, self.profile_html()))[1], FOUND)

    def test_flickr_confirmed_structural_404_is_not_found(self):
        html = '<html class="html-fluid-error-page-view"><head><title>Flickr</title></head></html>'
        self.assertEqual(self.check(self.response(404, html))[1], NOT_FOUND)

    def test_flickr_username_conflict_is_unknown(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html(public_username="Other_User")))[1],
            UNKNOWN,
        )

    def test_flickr_url_conflict_is_unknown(self):
        response = self.response(200, self.profile_html(), url="https://www.flickr.com/people/Other_User")
        self.assertEqual(self.check(response)[1], UNKNOWN)

    def test_flickr_stable_id_conflict_is_unknown(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html(person_nsid="98765@N01")))[1],
            UNKNOWN,
        )

    def test_flickr_missing_canonical_is_possible(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html(include_canonical=False)))[1],
            POSSIBLE,
        )

    def test_flickr_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_flickr_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)


class PatreonProfileDetectionTests(
    PublicProfileCheckerMixin,
    unittest.TestCase,
):
    site_name = "Patreon"
    default_url = "https://www.patreon.com/cw/Test_User"
    site_config = {
        "url": "https://www.patreon.com/{username}",
        "checker": "patreon_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(
        self,
        *,
        public_username="Test_User",
        current_username="Test_User",
        schema_id="12345",
        og_id="12345",
        include_schema=True,
    ):
        schema = ""
        if include_schema:
            data = {
                "@type": "ProfilePage",
                "@id": f"https://www.patreon.com/{public_username}#profilepage",
                "mainEntity": {
                    "@type": "Person",
                    "@id": f"https://www.patreon.com/{public_username}#person",
                    "url": f"https://www.patreon.com/{public_username}",
                    "alternateName": public_username,
                    "name": "Test Creator",
                    "image": {
                        "contentUrl": (
                            "https://c10.patreonusercontent.com/4/patreon-media/"
                            f"p/campaign/{schema_id}/image.png"
                        ),
                    },
                },
            }
            schema = f'<script type="application/ld+json">{json.dumps(data)}</script>'
        current_url = f"https://www.patreon.com/cw/{current_username}"
        return f"""
            <html><head>
                <title>Test Creator | Patreon</title>
                <link rel="canonical" href="{current_url}">
                <meta property="og:url" content="{current_url}">
                <meta property="og:type" content="profile">
                <meta property="og:title" content="Test Creator">
                <meta property="og:image" content="https://www.patreon.com/ig/card-teaser-image/creator/{og_id}.png">
                <meta property="profile:username" content="{public_username}">
                {schema}
            </head><body>
                <h1 data-is-key-element="true" elementtiming="Creator World : Home tab : Creator Name">Test Creator</h1>
            </body></html>
        """

    def test_patreon_certain_found_after_cw_redirect(self):
        self.assertEqual(self.check(self.response(200, self.profile_html()))[1], FOUND)

    def test_patreon_confirmed_404_is_not_found(self):
        html = '<html><head><title>Not found | Patreon</title><meta property="og:type" content="article"></head></html>'
        response = self.response(404, html, url="https://www.patreon.com/Test_User")
        self.assertEqual(self.check(response)[1], NOT_FOUND)

    def test_patreon_username_conflict_is_unknown(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html(public_username="Other_User")))[1],
            UNKNOWN,
        )

    def test_patreon_url_conflict_is_unknown(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html(current_username="Other_User")))[1],
            UNKNOWN,
        )

    def test_patreon_stable_id_conflict_is_unknown(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html(og_id="98765")))[1],
            UNKNOWN,
        )

    def test_patreon_incomplete_data_is_possible(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html(include_schema=False)))[1],
            POSSIBLE,
        )

    def test_patreon_unlinked_redirect_is_unknown(self):
        response = self.response(
            200,
            self.profile_html(public_username="Other_User", current_username="Other_User"),
            url="https://www.patreon.com/cw/Other_User",
        )
        self.assertEqual(self.check(response)[1], UNKNOWN)

    def test_patreon_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_patreon_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)


class ThemeForestProfileDetectionTests(
    PublicProfileCheckerMixin,
    unittest.TestCase,
):
    site_name = "ThemeForest"
    default_url = "https://themeforest.net/user/Test_User"
    site_config = {
        "url": "https://themeforest.net/user/{username}",
        "checker": "themeforest_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(
        self,
        *,
        public_username="Test_User",
        canonical_username="Test_User",
        author_ids=(),
        include_header=True,
    ):
        author_markers = "".join(
            f'<span data-author-id="{author_id}"></span>'
            for author_id in author_ids
        )
        header = ""
        if include_header:
            header = f"""
                <div class="user-info-header">
                    <h1>{public_username}</h1>
                    <a href="/user/{public_username}">Profile</a>
                    <a href="/user/{public_username}/portfolio">View Portfolio</a>
                    {author_markers}
                </div>
            """
        return f"""
            <html><head>
                <title>{public_username}'s profile on ThemeForest</title>
                <link rel="canonical" href="https://themeforest.net/user/{canonical_username}">
                <meta property="og:url" content="https://themeforest.net/user/{canonical_username}">
                <meta property="og:title" content="{public_username}'s profile on ThemeForest">
            </head><body>{header}</body></html>
        """

    def test_themeforest_certain_found(self):
        self.assertEqual(self.check(self.response(200, self.profile_html()))[1], FOUND)

    def test_themeforest_confirmed_404_is_not_found(self):
        error_url = "https://themeforest.net/404?username=Test_User"
        html = f"""
            <html><head><title>Page Not Found | ThemeForest</title>
            <link rel="canonical" href="{error_url}">
            <meta property="og:url" content="{error_url}"></head></html>
        """
        self.assertEqual(self.check(self.response(404, html))[1], NOT_FOUND)

    def test_themeforest_username_conflict_is_unknown(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html(public_username="Other_User")))[1],
            UNKNOWN,
        )

    def test_themeforest_url_conflict_is_unknown(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html(canonical_username="Other_User")))[1],
            UNKNOWN,
        )

    def test_themeforest_stable_id_conflict_is_unknown(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html(author_ids=("123", "987"))))[1],
            UNKNOWN,
        )

    def test_themeforest_incomplete_data_is_possible(self):
        self.assertEqual(
            self.check(self.response(200, self.profile_html(include_header=False)))[1],
            POSSIBLE,
        )

    def test_themeforest_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_themeforest_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)


class ThirdPublicProfileCheckerConfigTests(unittest.TestCase):
    def test_five_services_use_dedicated_checkers(self):
        config = load_sites_config()
        expected = {
            "Chess.com": "chesscom_profile",
            "Roblox": "roblox_profile",
            "Flickr": "flickr_profile",
            "Patreon": "patreon_profile",
            "ThemeForest": "themeforest_profile",
        }

        for service_name, checker in expected.items():
            with self.subTest(service=service_name):
                self.assertEqual(config[service_name]["checker"], checker)


class IssuuProfileDetectionTests(
    PublicProfileCheckerMixin,
    unittest.TestCase,
):
    site_name = "Issuu"
    default_url = "https://issuu.com/Test_User"
    site_config = {
        "url": "https://issuu.com/{username}",
        "checker": "issuu_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(
        self,
        *,
        public_username="Test_User",
        ids=None,
        include_publisher=True,
    ):
        publisher = {
            "displayName": "Test Publisher",
            "username": public_username,
            "kind": "user",
        }
        publisher.update(ids or {})
        payload = (
            f'7:["$",null,null,{{"publisher":{json.dumps(publisher)}}}]'
            if include_publisher else "7:null"
        )
        flight = json.dumps([1, payload])
        return f"""
            <html><head>
                <title>{public_username} Publisher Publications - Issuu</title>
                <link rel="canonical" href="https://issuu.com/{public_username}">
                <meta property="og:url" content="https://issuu.com/{public_username}">
                <meta property="og:type" content="website">
            </head><body>
                <h1 class="ProductHeading__product-heading">Test Publisher</h1>
                <script>self.__next_f.push({flight})</script>
            </body></html>
        """

    def test_issuu_certain_found(self):
        self.assertEqual(self.check(self.response(200, self.profile_html()))[1], FOUND)

    def test_issuu_confirmed_404_is_not_found(self):
        flight = json.dumps([1, '7:E{"digest":"NEXT_HTTP_ERROR_FALLBACK;404"}'])
        html = f'<html><head><title>Issuu</title></head><body><script>self.__next_f.push({flight})</script></body></html>'
        self.assertEqual(self.check(self.response(404, html))[1], NOT_FOUND)

    def test_issuu_username_conflict_is_unknown(self):
        html = self.profile_html(public_username="Other_User")
        self.assertEqual(self.check(self.response(200, html))[1], UNKNOWN)

    def test_issuu_url_conflict_is_unknown(self):
        response = self.response(200, self.profile_html(), url="https://issuu.com/Other_User")
        self.assertEqual(self.check(response)[1], UNKNOWN)

    def test_issuu_stable_id_conflict_is_unknown(self):
        html = self.profile_html(ids={"id": "123", "publisherId": "987"})
        self.assertEqual(self.check(self.response(200, html))[1], UNKNOWN)

    def test_issuu_incomplete_data_is_possible(self):
        html = self.profile_html(include_publisher=False)
        self.assertEqual(self.check(self.response(200, html))[1], POSSIBLE)

    def test_issuu_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_issuu_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)


class SlideshareProfileDetectionTests(
    PublicProfileCheckerMixin,
    unittest.TestCase,
):
    site_name = "Slideshare"
    default_url = "https://www.slideshare.net/Test_User"
    site_config = {
        "url": "https://www.slideshare.net/{username}",
        "checker": "slideshare_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(
        self,
        *,
        public_username="Test_User",
        user_id="12345",
        result_id="12345",
        include_schema=True,
    ):
        user = {
            "id": user_id,
            "name": "Test Publisher",
            "login": public_username,
        }
        data = {
            "props": {"pageProps": {
                "metadata": {"title": "Test Publisher"},
                "name": "profilePage",
                "user": user,
                "results": {"initialResults": [{
                    "user": {
                        "id": result_id,
                        "name": "Test Publisher",
                        "login": public_username,
                    },
                }]},
            }},
            "page": "/[username]",
            "query": {"username": "Test_User"},
        }
        schema = ""
        if include_schema:
            schema = '<script type="application/ld+json">{"@type":"Person","name":"Test Publisher"}</script>'
        return f"""
            <html><head><title>Test Publisher</title>{schema}
            <script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>
            </head><body><h1 class="user-name">Test Publisher</h1></body></html>
        """

    def soft_404_html(self):
        data = {
            "props": {"pageProps": {
                "name": "profilePage",
                "user": None,
                "blankProfile": True,
            }},
            "page": "/[username]",
            "query": {"username": "Test_User"},
        }
        return f"""
            <html><head><title>Page no longer exists</title>
            <script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>
            </head><body><main class="ErrorPage-module__g6r4pG__root">
            <h1>Page no longer exists</h1></main></body></html>
        """

    def test_slideshare_certain_found(self):
        self.assertEqual(self.check(self.response(200, self.profile_html()))[1], FOUND)

    def test_slideshare_structural_soft_404_is_not_found(self):
        self.assertEqual(self.check(self.response(200, self.soft_404_html()))[1], NOT_FOUND)

    def test_slideshare_username_conflict_is_unknown(self):
        html = self.profile_html(public_username="Other_User")
        self.assertEqual(self.check(self.response(200, html))[1], UNKNOWN)

    def test_slideshare_url_conflict_is_unknown(self):
        response = self.response(200, self.profile_html(), url="https://www.slideshare.net/Other_User")
        self.assertEqual(self.check(response)[1], UNKNOWN)

    def test_slideshare_stable_id_conflict_is_unknown(self):
        html = self.profile_html(result_id="98765")
        self.assertEqual(self.check(self.response(200, html))[1], UNKNOWN)

    def test_slideshare_incomplete_data_is_possible(self):
        html = self.profile_html(include_schema=False)
        self.assertEqual(self.check(self.response(200, html))[1], POSSIBLE)

    def test_slideshare_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_slideshare_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)


class HashnodeProfileDetectionTests(
    PublicProfileCheckerMixin,
    unittest.TestCase,
):
    site_name = "Hashnode"
    default_url = "https://hashnode.com/@Test_User"
    site_config = {
        "url": "https://hashnode.com/@{username}",
        "checker": "hashnode_profile",
        "rate_limit_delay": 0,
    }

    stable_id = "54b81b232807bee71445ebba"

    def profile_html(
        self,
        *,
        public_username="Test_User",
        stable_ids=None,
    ):
        person = {
            "@type": "Person",
            "name": "Test Author",
            "alternateName": public_username,
            "url": f"https://hashnode.com/@{public_username}",
            "sameAs": [f"https://hashnode.com/@{public_username}"],
        }
        schema = {"@type": "ProfilePage", "mainEntity": person}
        stable_ids = [self.stable_id] if stable_ids is None else stable_ids
        payload = "\n".join(
            f'7:[{{"userId":"{stable_id}"}}]'
            for stable_id in stable_ids
        )
        flight = json.dumps([1, payload])
        return f"""
            <html><head>
                <title>Test Author (@{public_username}) | Hashnode</title>
                <link rel="canonical" href="https://hashnode.com/@{public_username}">
                <meta property="og:url" content="https://hashnode.com/@{public_username}">
                <script type="application/ld+json">{json.dumps(schema)}</script>
            </head><body><h1 class="text-3xl">Test Author</h1>
            <script>self.__next_f.push({flight})</script></body></html>
        """

    def soft_404_html(self):
        payload = '4:E{"digest":"NEXT_HTTP_ERROR_FALLBACK;404"}'
        flight = json.dumps([1, payload])
        return f"""
            <html><head><title>User not found | Hashnode</title>
            <meta property="og:title" content="User not found | Hashnode"></head>
            <body><script>self.__next_f.push({flight})</script></body></html>
        """

    def test_hashnode_certain_found(self):
        self.assertEqual(self.check(self.response(200, self.profile_html()))[1], FOUND)

    def test_hashnode_structural_soft_404_is_not_found(self):
        self.assertEqual(self.check(self.response(200, self.soft_404_html()))[1], NOT_FOUND)

    def test_hashnode_username_conflict_is_unknown(self):
        html = self.profile_html(public_username="Other_User")
        self.assertEqual(self.check(self.response(200, html))[1], UNKNOWN)

    def test_hashnode_url_conflict_is_unknown(self):
        response = self.response(200, self.profile_html(), url="https://hashnode.com/@Other_User")
        self.assertEqual(self.check(response)[1], UNKNOWN)

    def test_hashnode_stable_id_conflict_is_unknown(self):
        html = self.profile_html(stable_ids=[self.stable_id, "64b81b232807bee71445ebba"])
        self.assertEqual(self.check(self.response(200, html))[1], UNKNOWN)

    def test_hashnode_incomplete_data_is_possible(self):
        html = self.profile_html(stable_ids=[])
        self.assertEqual(self.check(self.response(200, html))[1], POSSIBLE)

    def test_hashnode_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_hashnode_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)


class CodewarsProfileDetectionTests(
    PublicProfileCheckerMixin,
    unittest.TestCase,
):
    site_name = "Codewars"
    default_url = "https://www.codewars.com/users/Test_User"
    site_config = {
        "url": "https://www.codewars.com/users/{username}",
        "checker": "codewars_profile",
        "rate_limit_delay": 0,
    }

    stable_id = "545207bac8e60b30fc000942"

    def profile_html(
        self,
        *,
        public_username="Test_User",
        config_id=None,
        marker_id=None,
        include_config=True,
    ):
        config_id = config_id or self.stable_id
        marker_id = marker_id or self.stable_id
        script = ""
        if include_config:
            config = {"profile": {"id": config_id, "username": public_username}}
            serialized = json.dumps(json.dumps(config))
            script = f"<script>App.setup({{config: JSON.parse({serialized})}});</script>"
        return f"""
            <html><head><title>{public_username} | Codewars</title>
            <meta property="og:title" content="{public_username}">
            <meta property="og:url" content="https://www.codewars.com">
            </head><body><section class="user-profile">
                <a href="/users/{public_username}"><img src="https://www.codewars.com/avatars/{config_id}"></a>
                <div data-user-profile-actions-id-value="{marker_id}"></div>
            </section>{script}</body></html>
        """

    def test_codewars_certain_found_ignores_generic_og_url(self):
        self.assertEqual(self.check(self.response(200, self.profile_html()))[1], FOUND)

    def test_codewars_confirmed_404_is_not_found(self):
        html = """
            <html><head><title>Codewars | Achieve Mastery Through Challenge</title></head>
            <body><main id="shell_content">404 Whoops! The page you were looking for doesn't seem to exist.</main></body></html>
        """
        self.assertEqual(self.check(self.response(404, html))[1], NOT_FOUND)

    def test_codewars_username_conflict_is_unknown(self):
        html = self.profile_html(public_username="Other_User")
        self.assertEqual(self.check(self.response(200, html))[1], UNKNOWN)

    def test_codewars_url_conflict_is_unknown(self):
        response = self.response(200, self.profile_html(), url="https://www.codewars.com/users/Other_User")
        self.assertEqual(self.check(response)[1], UNKNOWN)

    def test_codewars_stable_id_conflict_is_unknown(self):
        html = self.profile_html(marker_id="645207bac8e60b30fc000942")
        self.assertEqual(self.check(self.response(200, html))[1], UNKNOWN)

    def test_codewars_incomplete_data_is_possible(self):
        html = self.profile_html(include_config=False)
        self.assertEqual(self.check(self.response(200, html))[1], POSSIBLE)

    def test_codewars_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_codewars_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)


class AudiomackProfileDetectionTests(
    PublicProfileCheckerMixin,
    unittest.TestCase,
):
    site_name = "Audiomack"
    default_url = "https://audiomack.com/Test_User"
    site_config = {
        "url": "https://audiomack.com/{username}",
        "checker": "audiomack_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(
        self,
        *,
        public_username="Test_User",
        artist_id="12345",
        schema_id=None,
        include_artist=True,
    ):
        schema = {
            "@type": "MusicGroup",
            "name": "Test Artist",
            "url": f"https://audiomack.com/{public_username}",
            "sameAs": [f"https://audiomack.com/{public_username}"],
        }
        if schema_id is not None:
            schema["identifier"] = schema_id
        payload = '8:["$","div",null,{"className":"ArtistPage-content"}]'
        if include_artist:
            artist = {
                "id": int(artist_id),
                "url_slug": public_username,
                "name": "Test Artist",
                "type": "artist",
                "status": "active",
            }
            payload += f'\n9:["$",null,null,{{"artist":{json.dumps(artist)}}}]'
        flight = json.dumps([1, payload])
        return f"""
            <html><head><title>Test Artist - Listen Free on Audiomack</title>
                <link rel="canonical" href="https://audiomack.com/{public_username}">
                <meta property="og:url" content="https://audiomack.com/{public_username}">
                <meta property="og:type" content="profile">
                <meta property="profile:username" content="{public_username}">
                <script type="application/ld+json">{json.dumps(schema)}</script>
            </head><body><h1 class="ArtistInfo-name">Test Artist</h1>
            <script>self.__next_f.push({flight})</script></body></html>
        """

    def generic_soft_404_html(self):
        return """
            <html><head><title>Audiomack - Music platform empowering artists &amp; fans | Audiomack</title>
            <meta property="og:url" content="https://audiomack.com/">
            <meta property="og:type" content="website"></head><body>
            <section class="NotFoundPage"><h1 class="NotFoundPage-title">Page Not Found</h1></section>
            </body></html>
        """

    def test_audiomack_certain_found(self):
        self.assertEqual(self.check(self.response(200, self.profile_html()))[1], FOUND)

    def test_audiomack_generic_soft_404_is_unknown(self):
        self.assertEqual(self.check(self.response(200, self.generic_soft_404_html()))[1], UNKNOWN)

    def test_audiomack_username_conflict_is_unknown(self):
        html = self.profile_html(public_username="Other_User")
        self.assertEqual(self.check(self.response(200, html))[1], UNKNOWN)

    def test_audiomack_url_conflict_is_unknown(self):
        response = self.response(200, self.profile_html(), url="https://audiomack.com/Other_User")
        self.assertEqual(self.check(response)[1], UNKNOWN)

    def test_audiomack_stable_id_conflict_is_unknown(self):
        html = self.profile_html(schema_id="98765")
        self.assertEqual(self.check(self.response(200, html))[1], UNKNOWN)

    def test_audiomack_incomplete_data_is_possible(self):
        html = self.profile_html(include_artist=False)
        self.assertEqual(self.check(self.response(200, html))[1], POSSIBLE)

    def test_audiomack_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_audiomack_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)


class BitbucketProfileDetectionTests(unittest.TestCase):
    username = "Test_User"
    site_name = "Bitbucket"
    site_config = {
        "url": "https://bitbucket.org/{username}/",
        "checker": "bitbucket_profile",
        "rate_limit_delay": 0,
    }
    stable_uuid = "12345678-1234-1234-1234-123456789abc"

    def response(self, status_code, text="", url=None, json_data=None):
        response = Mock()
        response.status_code = status_code
        response.text = text
        response.url = url or "https://bitbucket.org/Test_User/"
        if json_data is not None:
            response.json.return_value = json_data
        else:
            response.json.side_effect = ValueError
        return response

    def web_html(self, *, marker_uuid=None):
        marker_uuid = marker_uuid or self.stable_uuid
        return f"""
            <html><head><title>Bitbucket</title></head><body>
            <meta data-target-workspace-uuid="{marker_uuid}">
            <div id="workspace-repositories"></div>
            </body></html>
        """

    def api_data(self, *, slug="Test_User", uuid=None):
        uuid = uuid or self.stable_uuid
        return {
            "uuid": f"{{{uuid}}}",
            "name": "Test Workspace",
            "slug": slug,
            "type": "workspace",
            "links": {
                "html": {"href": f"https://bitbucket.org/{slug}/"},
            },
        }

    def check(self, web_response, api_response):
        with (
            patch(
                "main.username.requests.request",
                side_effect=[web_response, api_response],
            ),
            patch("main.username.time.sleep"),
        ):
            return check_username_on_site(
                self.username,
                self.site_name,
                self.site_config,
            )

    def test_bitbucket_certain_found_after_workspace_redirect(self):
        web = self.response(
            200,
            self.web_html(),
            "https://bitbucket.org/Test_User/workspace/repositories/",
        )
        api = self.response(200, json_data=self.api_data())
        self.assertEqual(self.check(web, api)[1], FOUND)

    def test_bitbucket_confirmed_404_is_not_found(self):
        html = "<html><head><title>404 — Bitbucket</title></head><body><h1>Resource not found</h1></body></html>"
        web = self.response(404, html)
        api = self.response(404, '{"type":"error"}')
        self.assertEqual(self.check(web, api)[1], NOT_FOUND)

    def test_bitbucket_username_conflict_is_unknown(self):
        web = self.response(200, self.web_html(), "https://bitbucket.org/Test_User/workspace/repositories/")
        api = self.response(200, json_data=self.api_data(slug="Other_User"))
        self.assertEqual(self.check(web, api)[1], UNKNOWN)

    def test_bitbucket_url_conflict_is_unknown(self):
        web = self.response(200, self.web_html(), "https://bitbucket.org/Other_User/workspace/repositories/")
        api = self.response(200, json_data=self.api_data())
        self.assertEqual(self.check(web, api)[1], UNKNOWN)

    def test_bitbucket_stable_id_conflict_is_unknown(self):
        web = self.response(200, self.web_html(marker_uuid="aaaaaaaa-1234-1234-1234-123456789abc"), "https://bitbucket.org/Test_User/workspace/repositories/")
        api = self.response(200, json_data=self.api_data())
        self.assertEqual(self.check(web, api)[1], UNKNOWN)

    def test_bitbucket_incomplete_data_is_possible(self):
        web = self.response(200, "<html><head><title>Bitbucket</title></head></html>", "https://bitbucket.org/Test_User/workspace/repositories/")
        api = self.response(200, json_data=self.api_data())
        self.assertEqual(self.check(web, api)[1], POSSIBLE)

    def test_bitbucket_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403), self.response(200))[1], BLOCKED)

    def test_bitbucket_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429), self.response(200))[1], RATE_LIMIT)


class DeviantArtProfileDetectionTests(PublicProfileCheckerMixin, unittest.TestCase):
    site_name = "DeviantArt"
    default_url = "https://www.deviantart.com/Test_User"
    site_config = {
        "url": "https://www.deviantart.com/{username}",
        "checker": "deviantart_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(
        self,
        *,
        current_username="Test_User",
        alias=None,
        identifiers=(),
        include_schema=True,
    ):
        profile_url = f"https://www.deviantart.com/{current_username}"
        schema = ""
        if include_schema:
            person = {
                "@type": "Person",
                "@id": f"{profile_url}#person",
                "name": current_username,
                "url": profile_url,
            }
            if alias:
                person["alternateName"] = alias
            if identifiers:
                person["identifier"] = identifiers[-1]
            page = {
                "@type": "ProfilePage",
                "@id": profile_url,
                "url": profile_url,
                "name": f"{current_username} on DeviantArt",
                "mainEntity": person,
            }
            if identifiers:
                page["identifier"] = identifiers[0]
            schema = f'<script type="application/ld+json">{json.dumps(page)}</script>'
        return f"""
            <html><head><title>{current_username} on DeviantArt</title>
            <link rel="canonical" href="{profile_url}">
            <meta property="og:url" content="{profile_url}">{schema}
            </head><body><h1>{current_username}</h1></body></html>
        """

    def test_deviantart_certain_found(self):
        self.assertEqual(self.check(self.response(200, self.profile_html()))[1], FOUND)

    def test_deviantart_confirmed_alias_found(self):
        html = self.profile_html(current_username="New_User", alias="Test_User")
        response = self.response(200, html, "https://www.deviantart.com/New_User")
        self.assertEqual(self.check(response)[1], FOUND)

    def test_deviantart_unconfirmed_alias_is_unknown(self):
        html = self.profile_html(current_username="New_User")
        response = self.response(200, html, "https://www.deviantart.com/New_User")
        self.assertEqual(self.check(response)[1], UNKNOWN)

    def test_deviantart_confirmed_404_is_not_found(self):
        html = "<html><head><title>DeviantArt: 404</title></head><body><h1>Llama Not Found</h1></body></html>"
        self.assertEqual(self.check(self.response(404, html))[1], NOT_FOUND)

    def test_deviantart_username_conflict_is_unknown(self):
        html = self.profile_html(current_username="Other_User")
        self.assertEqual(self.check(self.response(200, html))[1], UNKNOWN)

    def test_deviantart_url_conflict_is_unknown(self):
        response = self.response(200, self.profile_html(), "https://www.deviantart.com/Other_User")
        self.assertEqual(self.check(response)[1], UNKNOWN)

    def test_deviantart_stable_id_conflict_is_unknown(self):
        html = self.profile_html(identifiers=("12345", "98765"))
        self.assertEqual(self.check(self.response(200, html))[1], UNKNOWN)

    def test_deviantart_incomplete_data_is_possible(self):
        html = self.profile_html(include_schema=False)
        self.assertEqual(self.check(self.response(200, html))[1], POSSIBLE)

    def test_deviantart_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_deviantart_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)


class BuyMeACoffeeProfileDetectionTests(PublicProfileCheckerMixin, unittest.TestCase):
    site_name = "BuyMeACoffee"
    default_url = "https://buymeacoffee.com/Test_User"
    site_config = {
        "url": "https://www.buymeacoffee.com/{username}",
        "checker": "buymeacoffee_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(
        self,
        *,
        public_username="Test_User",
        user_id="12345",
        include_creator=True,
        support_heading=False,
    ):
        creator = {}
        if include_creator:
            creator = {
                "user_id": int(user_id) if user_id.isdigit() else user_id,
                "name": "Test Creator",
                "slug": public_username,
            }
        data = {
            "component": "Home/HomeLayout",
            "props": {"creator_data": {"data": creator}},
        }
        heading = (
            "Support Test Creator"
            if support_heading
            else "Buy Test Creator a coffee"
        )
        return f"""
            <html><head><title>Test Creator</title>
            <link rel="canonical" href="https://buymeacoffee.com/{public_username}">
            <meta property="og:url" content="https://buymeacoffee.com/{public_username}">
            </head><body><h1>{heading}</h1>
            <script data-page="app" type="application/json">{json.dumps(data)}</script>
            </body></html>
        """

    def test_buymeacoffee_certain_found_after_domain_redirect(self):
        self.assertEqual(self.check(self.response(200, self.profile_html()))[1], FOUND)

    def test_buymeacoffee_support_heading_is_found(self):
        html = self.profile_html(support_heading=True)
        self.assertEqual(self.check(self.response(200, html))[1], FOUND)

    def test_buymeacoffee_confirmed_404_is_not_found(self):
        data = {"component": "Error", "props": {"status": 404}}
        html = f'<html><head><title>Not found | Buy Me a Coffee</title></head><body><script data-page="app" type="application/json">{json.dumps(data)}</script></body></html>'
        self.assertEqual(self.check(self.response(404, html))[1], NOT_FOUND)

    def test_buymeacoffee_username_conflict_is_unknown(self):
        self.assertEqual(self.check(self.response(200, self.profile_html(public_username="Other_User")))[1], UNKNOWN)

    def test_buymeacoffee_url_conflict_is_unknown(self):
        response = self.response(200, self.profile_html(), "https://buymeacoffee.com/Other_User")
        self.assertEqual(self.check(response)[1], UNKNOWN)

    def test_buymeacoffee_stable_id_conflict_is_unknown(self):
        self.assertEqual(self.check(self.response(200, self.profile_html(user_id="invalid")))[1], UNKNOWN)

    def test_buymeacoffee_incomplete_data_is_possible(self):
        self.assertEqual(self.check(self.response(200, self.profile_html(include_creator=False)))[1], POSSIBLE)

    def test_buymeacoffee_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_buymeacoffee_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)


class InstructablesProfileDetectionTests(PublicProfileCheckerMixin, unittest.TestCase):
    site_name = "Instructables"
    default_url = "https://www.instructables.com/member/Test_User"
    site_config = {
        "url": "https://www.instructables.com/member/{username}",
        "checker": "instructables_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(self, *, public_username="Test_User", profile_id="ABC123", dom_id=None, include_profile=True):
        context = {"remoteHost": "https://www.instructables.com"}
        if include_profile:
            context["memberProfile"] = {
                "screenName": public_username,
                "id": profile_id,
                "status": "OK",
            }
        marker = f'<div data-member-id="{dom_id}"></div>' if dom_id else ""
        return f"""
            <html><head><title>{public_username}</title>
            <link rel="canonical" href="https://www.instructables.com/member/{public_username}">
            <meta property="og:title" content="{public_username}"></head>
            <body>{marker}<script id="js-page-context" type="application/json">{json.dumps(context)}</script></body></html>
        """

    def test_instructables_certain_found(self):
        self.assertEqual(self.check(self.response(200, self.profile_html()))[1], FOUND)

    def test_instructables_confirmed_404_is_not_found(self):
        html = """
            <html><head><title>Page Not Found - Instructables</title>
            <link rel="canonical" href="https://www.instructables.com/member/Test_User"></head>
            <body><main>404: We're sorry, things break sometimes</main></body></html>
        """
        self.assertEqual(self.check(self.response(404, html))[1], NOT_FOUND)

    def test_instructables_username_conflict_is_unknown(self):
        self.assertEqual(self.check(self.response(200, self.profile_html(public_username="Other_User")))[1], UNKNOWN)

    def test_instructables_url_conflict_is_unknown(self):
        response = self.response(200, self.profile_html(), "https://www.instructables.com/member/Other_User")
        self.assertEqual(self.check(response)[1], UNKNOWN)

    def test_instructables_stable_id_conflict_is_unknown(self):
        html = self.profile_html(dom_id="XYZ987")
        self.assertEqual(self.check(self.response(200, html))[1], UNKNOWN)

    def test_instructables_incomplete_data_is_possible(self):
        self.assertEqual(self.check(self.response(200, self.profile_html(include_profile=False)))[1], POSSIBLE)

    def test_instructables_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_instructables_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)


class ScribdProfileDetectionTests(PublicProfileCheckerMixin, unittest.TestCase):
    site_name = "Scribd"
    default_url = "https://www.scribd.com/user/12345/Test_User"
    site_config = {
        "url": "https://scribd.com/{username}",
        "checker": "scribd_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(
        self,
        *,
        public_username="Test_User",
        final_id="12345",
        canonical_id=None,
        image_id=None,
        include_marker=True,
        include_image=True,
        parenthesized_description=True,
    ):
        canonical_id = canonical_id or final_id
        image_id = image_id or final_id
        marker = ""
        if include_marker:
            image = (
                f'<div class="profile_image_container"><img src="https://img.scribdassets.com/img/word_user/{image_id}/72x72/x/1"></div>'
                if include_image
                else '<div class="profile_image_container"><img alt=""></div>'
            )
            marker = f'<div class="auto__profiles_show">{image}<h1 class="profile_name">{public_username}</h1></div>'
        description_name = (
            f"{public_username} ({public_username})"
            if parenthesized_description
            else public_username
        )
        return f"""
            <html><head><title>{public_username} | Scribd</title>
            <meta name="description" content="{description_name} has uploaded 0 documents on Scribd.">
            <link rel="canonical" href="https://www.scribd.com/user/{canonical_id}/{public_username}">
            <link rel="alternate" hreflang="x-default" href="https://www.scribd.com/user/{canonical_id}/{public_username}">
            </head><body>{marker}</body></html>
        """

    def test_scribd_certain_found_after_numeric_redirect(self):
        self.assertEqual(self.check(self.response(200, self.profile_html()))[1], FOUND)

    def test_scribd_profile_without_avatar_is_found(self):
        html = self.profile_html(
            include_image=False,
            parenthesized_description=False,
        )
        self.assertEqual(self.check(self.response(200, html))[1], FOUND)

    def test_scribd_confirmed_404_is_not_found(self):
        html = "<html><head><title>Page not found | Scribd</title></head><body><h1>Page not found</h1></body></html>"
        response = self.response(404, html, "https://www.scribd.com/Test_User")
        self.assertEqual(self.check(response)[1], NOT_FOUND)

    def test_scribd_username_conflict_is_unknown(self):
        html = self.profile_html(public_username="Other_User")
        response = self.response(200, html, "https://www.scribd.com/user/12345/Other_User")
        self.assertEqual(self.check(response)[1], UNKNOWN)

    def test_scribd_url_conflict_is_unknown(self):
        response = self.response(200, self.profile_html(), "https://www.scribd.com/Other_User")
        self.assertEqual(self.check(response)[1], UNKNOWN)

    def test_scribd_stable_id_conflict_is_unknown(self):
        html = self.profile_html(canonical_id="98765")
        self.assertEqual(self.check(self.response(200, html))[1], UNKNOWN)

    def test_scribd_incomplete_data_is_possible(self):
        html = self.profile_html(include_marker=False)
        self.assertEqual(self.check(self.response(200, html))[1], POSSIBLE)

    def test_scribd_unconfirmed_alias_redirect_is_unknown(self):
        html = self.profile_html(public_username="New_User")
        response = self.response(200, html, "https://www.scribd.com/user/12345/New_User")
        self.assertEqual(self.check(response)[1], UNKNOWN)

    def test_scribd_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_scribd_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)


class ShareChatProfileDetectionTests(PublicProfileCheckerMixin, unittest.TestCase):
    site_name = "ShareChat"
    default_url = "https://sharechat.com/profile/Test_User"
    site_config = {
        "url": "https://sharechat.com/profile/{username}",
        "checker": "sharechat_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(
        self,
        *,
        public_username="Test_User",
        canonical_username="Test_User",
        profile_id="168412794",
        include_svelte=True,
    ):
        svelte = ""
        if include_svelte:
            svelte = f'''<script>data:{{handle:"{public_username}",profile:{{h:"{public_username}",i:"{profile_id}",n:"Test Name"}}}},uses:{{params:["handle"]}}</script>'''
        schema = {
            "@context": "https://schema.org",
            "@type": "Person",
            "name": "Test Name",
            "url": f"/profile/{public_username}",
            "alternateName": public_username,
        }
        return f'''
            <html><head><title>Test Name (@{public_username}) – Videos &amp; Posts | ShareChat</title>
            <link rel="canonical" href="https://sharechat.com/profile/{canonical_username}">
            <meta property="og:url" content="sharechat.com/profile/{canonical_username}">
            <script type="application/ld+json">{json.dumps(schema)}</script></head>
            <body><main><h1>Test Name</h1></main>{svelte}</body></html>
        '''

    def test_sharechat_certain_found(self):
        self.assertEqual(self.check(self.response(200, self.profile_html()))[1], FOUND)

    def test_sharechat_confirmed_410_is_not_found(self):
        html = '<html><head><title>Page Not Found</title><meta property="og:url" content="sharechat.com/profile/Test_User"></head></html>'
        self.assertEqual(self.check(self.response(410, html))[1], NOT_FOUND)

    def test_sharechat_username_conflict_is_unknown(self):
        self.assertEqual(self.check(self.response(200, self.profile_html(public_username="Other_User")))[1], UNKNOWN)

    def test_sharechat_url_conflict_is_unknown(self):
        self.assertEqual(self.check(self.response(200, self.profile_html(canonical_username="Other_User")))[1], UNKNOWN)

    def test_sharechat_stable_id_conflict_is_unknown(self):
        self.assertEqual(self.check(self.response(200, self.profile_html(profile_id="bad-id")))[1], UNKNOWN)

    def test_sharechat_incomplete_data_is_possible(self):
        self.assertEqual(self.check(self.response(200, self.profile_html(include_svelte=False)))[1], POSSIBLE)

    def test_sharechat_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_sharechat_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)

    def test_sharechat_5xx_is_error(self):
        self.assertEqual(self.check(self.response(503))[1], ERROR)


class PeerlistProfileDetectionTests(PublicProfileCheckerMixin, unittest.TestCase):
    site_name = "Peerlist"
    default_url = "https://peerlist.io/Test_User"
    site_config = {
        "url": "https://peerlist.io/{username}",
        "checker": "peerlist_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(
        self,
        *,
        public_username="Test_User",
        canonical_username="Test_User",
        user_id="UHOKDMGADB6P9G82A6NKQOQKRQLL",
        nested_id=None,
        include_marker=True,
        bundle_text="",
    ):
        user = {
            "profileHandle": public_username,
            "id": user_id,
            "displayName": "Test Name",
            "enabled": True,
            "published": True,
        }
        if nested_id:
            user["projects"] = [{
                "creator": {"profileHandle": public_username, "id": nested_id}
            }]
        data = {
            "props": {"pageProps": {"user": user}},
            "page": "/[username]",
            "query": {"username": public_username},
        }
        marker = '<button id="follow-profile">Follow</button>' if include_marker else ""
        return f'''
            <html><head><title>Test Name • Peerlist</title>
            <link rel="canonical" href="https://peerlist.io/{canonical_username}">
            <meta property="og:url" content="https://peerlist.io/{canonical_username}">
            <meta property="og:title" content="Test Name • Peerlist"></head>
            <body><h1>Test Name</h1>{marker}<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script><script>{bundle_text}</script></body></html>
        '''

    def test_peerlist_certain_found(self):
        self.assertEqual(self.check(self.response(200, self.profile_html()))[1], FOUND)

    def test_peerlist_confirmed_404_is_not_found(self):
        data = {"props": {"pageProps": {}}, "page": "/404", "query": {}}
        html = f'<html><head><title>Peerlist | 404 Page Not Found</title></head><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></body></html>'
        self.assertEqual(self.check(self.response(404, html))[1], NOT_FOUND)

    def test_peerlist_username_conflict_is_unknown(self):
        self.assertEqual(self.check(self.response(200, self.profile_html(public_username="Other_User")))[1], UNKNOWN)

    def test_peerlist_url_conflict_is_unknown(self):
        self.assertEqual(self.check(self.response(200, self.profile_html(canonical_username="Other_User")))[1], UNKNOWN)

    def test_peerlist_stable_id_conflict_is_unknown(self):
        self.assertEqual(self.check(self.response(200, self.profile_html(nested_id="OTHERID123456789")))[1], UNKNOWN)

    def test_peerlist_incomplete_data_is_possible(self):
        self.assertEqual(self.check(self.response(200, self.profile_html(include_marker=False)))[1], POSSIBLE)

    def test_peerlist_bundle_challenge_word_does_not_block(self):
        html = self.profile_html(bundle_text='const label = "challenge";')
        self.assertEqual(self.check(self.response(200, html))[1], FOUND)

    def test_peerlist_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_peerlist_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)

    def test_peerlist_5xx_is_error(self):
        self.assertEqual(self.check(self.response(502))[1], ERROR)


class CodementorProfileDetectionTests(PublicProfileCheckerMixin, unittest.TestCase):
    site_name = "Codementor"
    default_url = "https://www.codementor.io/@Test_User"
    site_config = {
        "url": "https://www.codementor.io/@{username}",
        "checker": "codementor_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(
        self,
        *,
        public_username="Test_User",
        canonical_username="Test_User",
        user_id="fd7b0104-f876-4fd8-9376-419f309b321a",
        include_heading=True,
        title="Test Name's Developer Profile on Codementor",
    ):
        user = {
            "username": public_username,
            "uuid": user_id,
            "notFound": False,
            "name": "Test Name",
            "level": "mentor",
        }
        data = {
            "props": {"pageProps": {"initialState": {"userProfile": {"targetUser": user}}}},
            "page": "/[atUsername]",
            "query": {"atUsername": f"@{public_username}"},
        }
        heading = "<h1>Test Name</h1>" if include_heading else ""
        return f'''
            <html><head><title>{title}</title>
            <link rel="canonical" href="https://www.codementor.io/@{canonical_username}">
            <meta property="og:url" content="https://www.codementor.io/@{canonical_username}">
            <meta property="og:title" content="{title}">
            <meta property="og:type" content="profile"></head><body>{heading}
            <script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></body></html>
        '''

    def test_codementor_certain_found(self):
        self.assertEqual(self.check(self.response(200, self.profile_html()))[1], FOUND)

    def test_codementor_mentor_title_is_found(self):
        html = self.profile_html(title="Test Name - Python Expert and Mentor")
        self.assertEqual(self.check(self.response(200, html))[1], FOUND)

    def test_codementor_confirmed_404_is_not_found(self):
        html = '''<html><head><title>Codementor - Instant 1-on-1 Mentor for Programming &amp; Design</title><link href="https://assets.codementor.io/404/bundle.css"></head><body><div class="universe"></div></body></html>'''
        self.assertEqual(self.check(self.response(404, html))[1], NOT_FOUND)

    def test_codementor_username_conflict_is_unknown(self):
        self.assertEqual(self.check(self.response(200, self.profile_html(public_username="Other_User")))[1], UNKNOWN)

    def test_codementor_url_conflict_is_unknown(self):
        self.assertEqual(self.check(self.response(200, self.profile_html(canonical_username="Other_User")))[1], UNKNOWN)

    def test_codementor_stable_id_conflict_is_unknown(self):
        self.assertEqual(self.check(self.response(200, self.profile_html(user_id="bad-id")))[1], UNKNOWN)

    def test_codementor_incomplete_data_is_possible(self):
        self.assertEqual(self.check(self.response(200, self.profile_html(include_heading=False)))[1], POSSIBLE)

    def test_codementor_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_codementor_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)

    def test_codementor_5xx_is_error(self):
        self.assertEqual(self.check(self.response(500))[1], ERROR)


class BuzzFeedProfileDetectionTests(PublicProfileCheckerMixin, unittest.TestCase):
    site_name = "Buzzfeed"
    default_url = "https://www.buzzfeed.com/Test_User"
    site_config = {
        "url": "https://buzzfeed.com/{username}",
        "checker": "buzzfeed_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(
        self,
        *,
        public_username="Test_User",
        canonical_username="Test_User",
        user_id="14929546",
        nested_id=None,
        include_main=True,
    ):
        user = {
            "id": int(user_id) if user_id.isdigit() else user_id,
            "username": public_username,
            "displayName": "Test Name",
            "deleted": False,
        }
        page_props = {
            "user_uuid": "39b1b297-9a92-4636-89cd-e2fee1e8acb9",
            "user": user,
        }
        if nested_id:
            page_props["comments"] = {"items": [{
                "user": {"id": int(nested_id), "username": public_username}
            }]}
        data = {"props": {"pageProps": page_props}, "page": "/[username]"}
        main = "<main><h1>Test Name</h1></main>" if include_main else "<h1>Test Name</h1>"
        return f'''
            <html><head><title>Test Name on BuzzFeed</title>
            <link rel="canonical" href="https://www.buzzfeed.com/{canonical_username}">
            <meta name="description" content="Test Name ({public_username}) on BuzzFeed"></head>
            <body>{main}<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></body></html>
        '''

    def test_buzzfeed_certain_found(self):
        self.assertEqual(self.check(self.response(200, self.profile_html()))[1], FOUND)

    def test_buzzfeed_confirmed_404_is_not_found(self):
        html = '''<html><head><title>Page Not Found</title></head><body><h1>Oops.</h1><h2>We can't find the page you're looking for.</h2></body></html>'''
        self.assertEqual(self.check(self.response(404, html))[1], NOT_FOUND)

    def test_buzzfeed_username_conflict_is_unknown(self):
        self.assertEqual(self.check(self.response(200, self.profile_html(public_username="Other_User")))[1], UNKNOWN)

    def test_buzzfeed_url_conflict_is_unknown(self):
        self.assertEqual(self.check(self.response(200, self.profile_html(canonical_username="Other_User")))[1], UNKNOWN)

    def test_buzzfeed_stable_id_conflict_is_unknown(self):
        self.assertEqual(self.check(self.response(200, self.profile_html(nested_id="777")))[1], UNKNOWN)

    def test_buzzfeed_incomplete_data_is_possible(self):
        self.assertEqual(self.check(self.response(200, self.profile_html(include_main=False)))[1], POSSIBLE)

    def test_buzzfeed_unconfirmed_redirect_is_unknown(self):
        response = self.response(200, self.profile_html(), "https://www.buzzfeed.com/Other_User")
        self.assertEqual(self.check(response)[1], UNKNOWN)

    def test_buzzfeed_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_buzzfeed_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)

    def test_buzzfeed_5xx_is_error(self):
        self.assertEqual(self.check(self.response(503))[1], ERROR)


class HouzzProfileDetectionTests(PublicProfileCheckerMixin, unittest.TestCase):
    site_name = "Houzz"
    default_url = "https://www.houzz.com/user/Test_User"
    site_config = {
        "url": "https://houzz.com/user/{username}",
        "checker": "houzz_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(
        self,
        *,
        public_username="Test_User",
        canonical_username="Test_User",
        user_id="1633546",
        second_id=None,
        include_marker=True,
        professional=False,
    ):
        user = {
            "id": int(user_id) if user_id.isdigit() else user_id,
            "userId": int(second_id or user_id) if str(second_id or user_id).isdigit() else second_id or user_id,
            "userName": public_username,
        }
        store_name = "ProProfileStore" if professional else "UserProfileStore"
        page_name = "proProfile" if professional else "userProjects"
        stores = {store_name: {"data": {"user": user}}}
        if not professional:
            stores["PageStore"] = {"data": {"canonicalUrl": f"https://www.houzz.com/user/{canonical_username}"}}
        else:
            stores["PageStore"] = {"data": {"canonicalUrl": self.default_url}}
        data = {"data": {"pageName": page_name, "stores": {"data": stores}}}
        if professional:
            marker = '<nav id="profile-navi"></nav>' if include_marker else ""
            schema = {
                "@context": "https://schema.org",
                "@type": "LocalBusiness",
                "url": self.default_url,
            }
        else:
            marker = '<div class="hz-profile-simplified-header"></div>' if include_marker else ""
            schema = {}
        schema_html = f'<script type="application/ld+json">{json.dumps(schema)}</script>' if schema else ""
        return f'''
            <html><head><title>Test Name | Houzz</title>{schema_html}</head><body>
            <h1>Test Name</h1>{marker}<script id="hz-ctx" type="application/json">{json.dumps(data)}</script></body></html>
        '''

    def test_houzz_certain_found(self):
        self.assertEqual(self.check(self.response(200, self.profile_html()))[1], FOUND)

    def test_houzz_confirmed_404_is_not_found(self):
        data = {"data": {"pageName": "pageNotFound", "stores": {"data": {}}}}
        html = f'<html><head><title>Page Not Found</title></head><body><h1>The page you requested was not found.</h1><script id="hz-ctx" type="application/json">{json.dumps(data)}</script></body></html>'
        self.assertEqual(self.check(self.response(404, html))[1], NOT_FOUND)

    def test_houzz_username_conflict_is_unknown(self):
        self.assertEqual(self.check(self.response(200, self.profile_html(public_username="Other_User")))[1], UNKNOWN)

    def test_houzz_url_conflict_is_unknown(self):
        self.assertEqual(self.check(self.response(200, self.profile_html(canonical_username="Other_User")))[1], UNKNOWN)

    def test_houzz_stable_id_conflict_is_unknown(self):
        self.assertEqual(self.check(self.response(200, self.profile_html(second_id="999999")))[1], UNKNOWN)

    def test_houzz_incomplete_data_is_possible(self):
        self.assertEqual(self.check(self.response(200, self.profile_html(include_marker=False)))[1], POSSIBLE)

    def test_houzz_confirmed_professional_redirect_is_found(self):
        final_url = "https://www.houzz.com/professionals/designers/test-profile-pf~123"
        old_default = self.default_url
        try:
            self.default_url = final_url
            html = self.profile_html(professional=True)
        finally:
            self.default_url = old_default
        self.assertEqual(self.check(self.response(200, html, final_url))[1], FOUND)

    def test_houzz_unconfirmed_redirect_is_unknown(self):
        response = self.response(200, self.profile_html(), "https://www.houzz.com/something/else")
        self.assertEqual(self.check(response)[1], UNKNOWN)

    def test_houzz_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_houzz_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)

    def test_houzz_5xx_is_error(self):
        self.assertEqual(self.check(self.response(500))[1], ERROR)


class MyspaceProfileDetectionTests(PublicProfileCheckerMixin, unittest.TestCase):
    site_name = "Myspace"
    default_url = "https://myspace.com/Test_User"
    site_config = {
        "url": "https://myspace.com/{username}",
        "checker": "myspace_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(
        self,
        *,
        public_username="Test_User",
        canonical_username="Test_User",
        profile_id="29122115",
        dom_profile_id=None,
        artist_id="41742275",
        include_context=True,
        pfc="Profile",
    ):
        dom_profile_id = dom_profile_id or profile_id
        context = {
            "text": {},
            "displayProfileId": int(profile_id) if profile_id.isdigit() else profile_id,
            "artistId": int(artist_id) if artist_id.isdigit() else artist_id,
            "pfc": pfc,
            "streamUrl": f"/ajax/{public_username}/latest/all",
            "filterStreamUrl": f"/ajax/{public_username}/latest/",
            "hasProfileDetails": True,
        }
        context_script = (
            f"<script>context = {json.dumps(context)};</script>"
            if include_context
            else ""
        )
        return f'''
            <html><head><title>Test Name ({public_username}) on Myspace</title>
            <link rel="canonical" href="https://myspace.com/{canonical_username}">
            <meta property="og:url" content="https://myspace.com/{canonical_username}">
            <meta property="og:title" content="Test Name ({public_username}) on Myspace">
            <meta property="og:type" content="profile">
            <meta name="description" content="Test Name ({public_username})'s profile on Myspace."></head>
            <body class="unifiedNav profile sidebar"><h1>Test Name</h1>
            <div class="connectButton" data-id="{dom_profile_id}" data-artist-id="{artist_id}"></div>
            {context_script}</body></html>
        '''

    def test_myspace_certain_found(self):
        self.assertEqual(self.check(self.response(200, self.profile_html()))[1], FOUND)

    def test_myspace_normalizing_trailing_slash_redirect_is_found(self):
        response = self.response(200, self.profile_html(), "https://myspace.com/Test_User/")
        self.assertEqual(self.check(response)[1], FOUND)

    def test_myspace_confirmed_404_is_not_found(self):
        context = {"text": {}, "pfc": "404"}
        html = f'<html><head><title>Myspace</title></head><body class="unifiedNav"><h1>Page Not Found</h1><script>context = {json.dumps(context)};</script></body></html>'
        self.assertEqual(self.check(self.response(404, html))[1], NOT_FOUND)

    def test_myspace_username_conflict_is_unknown(self):
        html = self.profile_html(public_username="Other_User")
        self.assertEqual(self.check(self.response(200, html))[1], UNKNOWN)

    def test_myspace_url_conflict_is_unknown(self):
        html = self.profile_html(canonical_username="Other_User")
        self.assertEqual(self.check(self.response(200, html))[1], UNKNOWN)

    def test_myspace_stable_id_conflict_is_unknown(self):
        html = self.profile_html(dom_profile_id="99999999")
        self.assertEqual(self.check(self.response(200, html))[1], UNKNOWN)

    def test_myspace_incomplete_data_is_possible(self):
        html = self.profile_html(include_context=False)
        self.assertEqual(self.check(self.response(200, html))[1], POSSIBLE)

    def test_myspace_soft_error_200_is_unknown(self):
        html = self.profile_html(pfc="404")
        self.assertEqual(self.check(self.response(200, html))[1], UNKNOWN)

    def test_myspace_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_myspace_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)

    def test_myspace_5xx_is_error(self):
        self.assertEqual(self.check(self.response(503))[1], ERROR)


class HubPagesProfileDetectionTests(PublicProfileCheckerMixin, unittest.TestCase):
    site_name = "HubPages"
    default_url = "https://hubpages.com/@Test_User"
    site_config = {
        "url": "https://hubpages.com/@{username}",
        "checker": "hubpages_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(
        self,
        *,
        public_username="Test_User",
        profile_id="2256391",
        tracking_id=None,
        include_marker=True,
    ):
        tracking_id = tracking_id or profile_id
        page_values = {
            "cm": "hubpages",
            "hp_site": "hubpages",
            "pagetype": "profile",
            "path": f"/@{public_username}",
            "contentitemid": f"hp-profile-{profile_id}",
            "loggedin": "0",
        }
        marker = '<div class="bio_stats"><h1><span class="author_primary_name">Test Name</span> <span class="author_secondary_name">(Display Name)</span></h1></div>' if include_marker else "<h1>Test Name</h1>"
        return f'''
            <html><head><title>Test Name on HubPages</title></head>
            <body class="hubpages profilepage">{marker}
            <script>var hpstdata = {{ hp_tracking_type: 'p', hp_tracking_id: {tracking_id}, tracking: '' }};</script>
            <script>window.pageKeyValues = {json.dumps(page_values)};</script>
            <script>const request = {{ section: 'articles', uId: {profile_id} }};</script>
            </body></html>
        '''

    def test_hubpages_certain_found(self):
        self.assertEqual(self.check(self.response(200, self.profile_html()))[1], FOUND)

    def test_hubpages_confirmed_404_is_not_found(self):
        html = '<html><head><title>Page Not Found</title></head><body><h1>404. Page does not exist</h1></body></html>'
        self.assertEqual(self.check(self.response(404, html))[1], NOT_FOUND)

    def test_hubpages_username_conflict_is_unknown(self):
        html = self.profile_html(public_username="Other_User")
        self.assertEqual(self.check(self.response(200, html))[1], UNKNOWN)

    def test_hubpages_url_conflict_is_unknown(self):
        response = self.response(200, self.profile_html(), "https://hubpages.com/@Other_User")
        self.assertEqual(self.check(response)[1], UNKNOWN)

    def test_hubpages_stable_id_conflict_is_unknown(self):
        html = self.profile_html(tracking_id="9999999")
        self.assertEqual(self.check(self.response(200, html))[1], UNKNOWN)

    def test_hubpages_incomplete_data_is_possible(self):
        html = self.profile_html(include_marker=False)
        self.assertEqual(self.check(self.response(200, html))[1], POSSIBLE)

    def test_hubpages_soft_404_200_is_unknown(self):
        html = '<html><head><title>Page Not Found</title></head><body><h1>404. Page does not exist</h1></body></html>'
        self.assertEqual(self.check(self.response(200, html))[1], UNKNOWN)

    def test_hubpages_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_hubpages_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)

    def test_hubpages_5xx_is_error(self):
        self.assertEqual(self.check(self.response(500))[1], ERROR)


class GabProfileDetectionTests(unittest.TestCase):
    username = "Test_User"
    site_name = "Gab"
    site_config = {
        "url": "https://gab.com/{username}",
        "checker": "gab_profile",
        "rate_limit_delay": 0,
    }
    account_id = "123"

    def response(self, status_code, text="", url=None, json_data=None):
        response = Mock()
        response.status_code = status_code
        response.text = text
        response.url = url or "https://gab.com/Test_User"
        if json_data is None:
            response.json.side_effect = ValueError
        else:
            response.json.return_value = json_data
        return response

    def profile_html(
        self,
        *,
        public_username="Test_User",
        canonical_username="Test_User",
        avatar_id="123",
        include_marker=True,
    ):
        avatar = f"https://m3.gab.com/accounts/avatars/000/000/{avatar_id}/original/avatar.png"
        profile_name = f"Test Name (@{public_username})"
        schema = {
            "@context": "https://schema.org",
            "@type": "ProfilePage",
            "url": f"https://gab.com/{public_username}",
            "name": profile_name,
            "mainEntity": {
                "@type": "Person",
                "name": "Test Name",
                "alternateName": f"@{public_username}",
                "identifier": public_username,
                "url": f"https://gab.com/{public_username}",
                "image": avatar,
            },
        }
        marker = '<div class="seo-ssr-content" role="main"><h1>{}</h1></div>'.format(profile_name) if include_marker else f"<h1>{profile_name}</h1>"
        return f'''
            <html><head><title>{profile_name} · Gab.com - Gab Social</title>
            <link rel="canonical" href="https://gab.com/{canonical_username}">
            <meta property="og:url" content="https://gab.com/{canonical_username}">
            <meta property="og:title" content="{profile_name} · Gab.com">
            <meta property="og:type" content="profile">
            <meta property="og:image" content="{avatar}">
            <meta property="profile:username" content="{public_username}@gab.com">
            <script type="application/ld+json">{json.dumps(schema)}</script></head>
            <body>{marker}</body></html>
        '''

    def api_data(
        self,
        *,
        public_username="Test_User",
        account_id="123",
        avatar_id="123",
    ):
        return {
            "id": account_id,
            "username": public_username,
            "acct": public_username,
            "display_name": "Test Name",
            "url": f"https://gab.com/{public_username}",
            "avatar": f"https://m3.gab.com/accounts/avatars/000/000/{avatar_id}/original/avatar.png",
        }

    def check(self, web_response, api_response=None):
        responses = [web_response]
        if api_response is not None:
            responses.append(api_response)
        with (
            patch("main.username.requests.request", side_effect=responses),
            patch("main.username.time.sleep"),
        ):
            return check_username_on_site(
                self.username,
                self.site_name,
                self.site_config,
            )

    def test_gab_certain_found(self):
        web = self.response(200, self.profile_html())
        api = self.response(200, json_data=self.api_data())
        self.assertEqual(self.check(web, api)[1], FOUND)

    def test_gab_confirmed_404_is_not_found(self):
        web = self.response(404, "")
        api = self.response(404, json_data={"error": "Record not found"})
        self.assertEqual(self.check(web, api)[1], NOT_FOUND)

    def test_gab_username_conflict_is_unknown(self):
        web = self.response(200, self.profile_html())
        api = self.response(200, json_data=self.api_data(public_username="Other_User"))
        self.assertEqual(self.check(web, api)[1], UNKNOWN)

    def test_gab_url_conflict_is_unknown(self):
        web = self.response(200, self.profile_html(canonical_username="Other_User"))
        api = self.response(200, json_data=self.api_data())
        self.assertEqual(self.check(web, api)[1], UNKNOWN)

    def test_gab_stable_id_conflict_is_unknown(self):
        web = self.response(200, self.profile_html())
        api = self.response(200, json_data=self.api_data(account_id="999"))
        self.assertEqual(self.check(web, api)[1], UNKNOWN)

    def test_gab_incomplete_data_is_possible(self):
        web = self.response(200, self.profile_html(include_marker=False))
        api = self.response(200, json_data=self.api_data())
        self.assertEqual(self.check(web, api)[1], POSSIBLE)

    def test_gab_unconfirmed_redirect_is_unknown(self):
        web = self.response(200, self.profile_html(), "https://gab.com/Other_User")
        api = self.response(200, json_data=self.api_data())
        self.assertEqual(self.check(web, api)[1], UNKNOWN)

    def test_gab_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_gab_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)

    def test_gab_public_api_429_is_rate_limited(self):
        web = self.response(200, self.profile_html())
        api = self.response(429)
        self.assertEqual(self.check(web, api)[1], RATE_LIMIT)

    def test_gab_5xx_is_error(self):
        self.assertEqual(self.check(self.response(503))[1], ERROR)

    def test_gab_public_api_network_failure_is_error(self):
        web = self.response(200, self.profile_html())
        with (
            patch(
                "main.username.requests.request",
                side_effect=[
                    web,
                    requests.ConnectionError("network unavailable"),
                ],
            ),
            patch("main.username.time.sleep"),
        ):
            result = check_username_on_site(
                self.username,
                self.site_name,
                self.site_config,
            )
        self.assertEqual(result[1], ERROR)


class HackerRankProfileDetectionTests(unittest.TestCase):
    username = "Test_User"
    site_name = "HackerRank"
    site_config = {
        "url": "https://www.hackerrank.com/profile/{username}",
        "checker": "hackerrank_profile",
        "rate_limit_delay": 0,
    }

    def response(self, status_code, text="", url=None, json_data=None):
        response = Mock()
        response.status_code = status_code
        response.text = text
        response.url = url or "https://www.hackerrank.com/profile/Test_User"
        if json_data is None:
            response.json.side_effect = ValueError
        else:
            response.json.return_value = json_data
        return response

    def api_data(self, *, username="Test_User", profile_id=12345, **overrides):
        model = {
            "id": profile_id,
            "username": username,
            "created_at": "2020-01-02T03:04:05.000Z",
            "deleted": False,
            "level": 3,
            "avatar": "https://hrcdn.net/avatar.png",
        }
        model.update(overrides)
        return {"model": model}

    def check(self, web_response, api_response=None):
        responses = [web_response]
        if api_response is not None:
            responses.append(api_response)
        with (
            patch("main.username.requests.request", side_effect=responses),
            patch("main.username.time.sleep"),
        ):
            return check_username_on_site(
                self.username,
                self.site_name,
                self.site_config,
            )

    def test_hackerrank_certain_found(self):
        web = self.response(200, "<html><body><div id='content'></div></body></html>")
        api = self.response(200, json_data=self.api_data())
        self.assertEqual(self.check(web, api)[1], FOUND)

    def test_hackerrank_confirmed_not_found(self):
        web = self.response(200, "<html><body><div id='content'></div></body></html>")
        api = self.response(404, json_data={"error": "Not Found"})
        self.assertEqual(self.check(web, api)[1], NOT_FOUND)

    def test_hackerrank_username_conflict_is_unknown(self):
        web = self.response(200)
        api = self.response(200, json_data=self.api_data(username="Other_User"))
        self.assertEqual(self.check(web, api)[1], UNKNOWN)

    def test_hackerrank_url_conflict_is_unknown(self):
        web = self.response(200, url="https://www.hackerrank.com/profile/Other_User")
        api = self.response(200, json_data=self.api_data())
        self.assertEqual(self.check(web, api)[1], UNKNOWN)

    def test_hackerrank_invalid_stable_id_is_unknown(self):
        web = self.response(200)
        api = self.response(200, json_data=self.api_data(profile_id="different-id"))
        self.assertEqual(self.check(web, api)[1], UNKNOWN)

    def test_hackerrank_incomplete_data_is_possible(self):
        web = self.response(200)
        api = self.response(200, json_data=self.api_data(created_at=""))
        self.assertEqual(self.check(web, api)[1], POSSIBLE)

    def test_hackerrank_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_hackerrank_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)

    def test_hackerrank_5xx_is_error(self):
        self.assertEqual(self.check(self.response(503))[1], ERROR)

    def test_hackerrank_public_api_5xx_is_error(self):
        web = self.response(200)
        api = self.response(503)
        self.assertEqual(self.check(web, api)[1], ERROR)

    def test_hackerrank_public_api_network_failure_is_error(self):
        web = self.response(200)
        with (
            patch(
                "main.username.requests.request",
                side_effect=[
                    web,
                    requests.ConnectionError("network unavailable"),
                ],
            ),
            patch("main.username.time.sleep"),
        ):
            result = check_username_on_site(
                self.username,
                self.site_name,
                self.site_config,
            )
        self.assertEqual(result[1], ERROR)


class WellfoundProfileDetectionTests(PublicProfileCheckerMixin, unittest.TestCase):
    site_name = "Wellfound"
    default_url = "https://wellfound.com/p/Test_User"
    site_config = {
        "url": "https://wellfound.com/p/{username}",
        "checker": "wellfound_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(
        self,
        *,
        canonical_username="Test_User",
        og_username="Test_User",
        user_id="12345",
        image_id=None,
        include_marker=True,
        include_person=True,
        current_route="p",
    ):
        image_id = image_id or user_id
        marker = (
            '<div data-_tn="profiles/show/structure">'
            f'<div data-user_id="{user_id}"></div></div>'
            if include_marker
            else ""
        )
        person = (
            '<script type="application/ld+json">'
            + json.dumps({"@context": "https://schema.org", "@type": "Person", "name": "Test Name"})
            + "</script>"
            if include_person
            else ""
        )
        og = (
            f'<meta property="og:url" content="https://wellfound.com/{current_route}/{og_username}">'
            if og_username is not None
            else ""
        )
        body_attributes = 'id="profiles" class="show_new"' if include_marker else ""
        return f'''
            <html><head><title>Test Name | Wellfound</title>
            <link rel="canonical" href="https://wellfound.com/{current_route}/{canonical_username}">
            {og}{person}</head><body {body_attributes}>
            <h1>Test Name</h1>{marker}
            <img src="https://photos.wellfound.com/users/{image_id}-large.jpg">
            </body></html>
        '''

    def test_wellfound_certain_found(self):
        self.assertEqual(self.check(self.response(200, self.profile_html()))[1], FOUND)

    def test_wellfound_confirmed_u_route_redirect_is_found(self):
        html = self.profile_html(
            current_route="u",
            og_username=None,
            include_person=False,
        )
        response = self.response(200, html, "https://wellfound.com/u/Test_User")
        self.assertEqual(self.check(response)[1], FOUND)

    def test_wellfound_confirmed_404_is_not_found(self):
        html = "<html><head><title>Page not found - 404 | Wellfound</title></head><body></body></html>"
        self.assertEqual(self.check(self.response(404, html))[1], NOT_FOUND)

    def test_wellfound_username_conflict_is_unknown(self):
        html = self.profile_html(canonical_username="Other_User")
        self.assertEqual(self.check(self.response(200, html))[1], UNKNOWN)

    def test_wellfound_url_conflict_is_unknown(self):
        response = self.response(200, self.profile_html(), "https://wellfound.com/p/Other_User")
        self.assertEqual(self.check(response)[1], UNKNOWN)

    def test_wellfound_stable_id_conflict_is_unknown(self):
        html = self.profile_html(image_id="99999")
        self.assertEqual(self.check(self.response(200, html))[1], UNKNOWN)

    def test_wellfound_incomplete_data_is_possible(self):
        html = self.profile_html(include_marker=False)
        self.assertEqual(self.check(self.response(200, html))[1], POSSIBLE)

    def test_wellfound_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_wellfound_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)

    def test_wellfound_5xx_is_error(self):
        self.assertEqual(self.check(self.response(502))[1], ERROR)


class ReverbNationProfileDetectionTests(PublicProfileCheckerMixin, unittest.TestCase):
    site_name = "ReverbNation"
    default_url = "https://legacy.reverbnation.com/Test_User"
    site_config = {
        "url": "https://www.reverbnation.com/{username}",
        "checker": "reverbnation_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(
        self,
        *,
        public_username="Test_User",
        canonical_username="Test_User",
        profile_id="12345",
        dom_id=None,
        include_marker=True,
    ):
        dom_id = dom_id or profile_id
        artist = {
            "id": int(profile_id) if profile_id.isdigit() else profile_id,
            "name": "Test Artist",
            "homepage": public_username,
            "homepage_url": f"//legacy.reverbnation.com/{public_username}",
            "type": "artist",
        }
        marker = '<div id="page_object_profile_header"></div>' if include_marker else ""
        return f'''
            <html><head><title>Test Artist | ReverbNation</title>
            <link rel="canonical" href="https://legacy.reverbnation.com/{canonical_username}">
            <meta property="og:url" content="http://legacy.reverbnation.com/{canonical_username}">
            <meta property="og:type" content="band"></head><body>
            {marker}<h1 class="qa-artist-name">Test Artist</h1>
            <div data-page-object-id="artist_{dom_id}"></div>
            <script>var config = {json.dumps({"ARTIST": artist})};</script>
            </body></html>
        '''.replace(
            json.dumps({"ARTIST": artist}),
            '{"ARTIST":' + json.dumps(artist) + ',"ARTIST_EXTRA_DATA":{}}',
        )

    def test_reverbnation_confirmed_legacy_redirect_is_found(self):
        self.assertEqual(self.check(self.response(200, self.profile_html()))[1], FOUND)

    def test_reverbnation_confirmed_404_is_not_found(self):
        html = "<html><head><title>The page you were looking for doesn't exist (404 Not found)</title></head></html>"
        response = self.response(404, html, "https://www.reverbnation.com/Test_User")
        self.assertEqual(self.check(response)[1], NOT_FOUND)

    def test_reverbnation_username_conflict_is_unknown(self):
        html = self.profile_html(public_username="Other_User")
        self.assertEqual(self.check(self.response(200, html))[1], UNKNOWN)

    def test_reverbnation_url_conflict_is_unknown(self):
        html = self.profile_html(canonical_username="Other_User")
        self.assertEqual(self.check(self.response(200, html))[1], UNKNOWN)

    def test_reverbnation_stable_id_conflict_is_unknown(self):
        html = self.profile_html(dom_id="99999")
        self.assertEqual(self.check(self.response(200, html))[1], UNKNOWN)

    def test_reverbnation_incomplete_data_is_possible(self):
        html = self.profile_html(include_marker=False)
        self.assertEqual(self.check(self.response(200, html))[1], POSSIBLE)

    def test_reverbnation_unconfirmed_redirect_is_unknown(self):
        response = self.response(200, self.profile_html(), "https://legacy.reverbnation.com/Other_User")
        self.assertEqual(self.check(response)[1], UNKNOWN)

    def test_reverbnation_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_reverbnation_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)

    def test_reverbnation_5xx_is_error(self):
        self.assertEqual(self.check(self.response(503))[1], ERROR)


class CouchsurfingProfileDetectionTests(PublicProfileCheckerMixin, unittest.TestCase):
    site_name = "Couchsurfing"
    default_url = "https://www.couchsurfing.com/c/users/Test_User"
    site_config = {
        "url": "https://www.couchsurfing.com/c/users/{username}",
        "checker": "couchsurfing_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(
        self,
        *,
        public_username="Test_User",
        canonical_username="Test_User",
        profile_ids=("profile123_pf",),
        include_marker=True,
    ):
        schema = {
            "@context": "https://schema.org",
            "@type": "Person",
            "name": "Test Name",
            "url": f"https://www.couchsurfing.com/c/users/{public_username}",
        }
        ids = "".join(
            f'<script type="application/json">{{"profileId":"{profile_id}"}}</script>'
            for profile_id in profile_ids
        )
        marker = '<main data-testid="user-profile-container"><h1>Test Name</h1></main>' if include_marker else "<h1>Test Name</h1>"
        return f'''
            <html><head><title>Test Name - Profile - Couchsurfing</title>
            <link rel="canonical" href="https://www.couchsurfing.com/c/users/{canonical_username}">
            <meta property="og:url" content="https://www.couchsurfing.com/c/users/{canonical_username}">
            <meta property="og:type" content="profile">
            <meta property="profile:username" content="{public_username}">
            <script type="application/ld+json">{json.dumps(schema)}</script>
            </head><body>{marker}{ids}</body></html>
        '''

    def test_couchsurfing_certain_found(self):
        self.assertEqual(self.check(self.response(200, self.profile_html()))[1], FOUND)

    def test_couchsurfing_confirmed_410_is_not_found(self):
        html = '''<html><head><title>Couchsurfing</title>
            <meta property="og:url" content="https://www.couchsurfing.com">
            <meta property="og:type" content="website"></head><body></body></html>'''
        self.assertEqual(self.check(self.response(410, html))[1], NOT_FOUND)

    def test_couchsurfing_current_confirmed_404_is_not_found(self):
        html = '''<html><head><title>Couchsurfing</title>
            <meta property="og:url" content="https://www.couchsurfing.com">
            <meta property="og:type" content="website"></head><body></body></html>'''
        self.assertEqual(self.check(self.response(404, html))[1], NOT_FOUND)

    def test_couchsurfing_username_conflict_is_unknown(self):
        html = self.profile_html(public_username="Other_User")
        self.assertEqual(self.check(self.response(200, html))[1], UNKNOWN)

    def test_couchsurfing_url_conflict_is_unknown(self):
        html = self.profile_html(canonical_username="Other_User")
        self.assertEqual(self.check(self.response(200, html))[1], UNKNOWN)

    def test_couchsurfing_stable_id_conflict_is_unknown(self):
        html = self.profile_html(profile_ids=("profile123_pf", "other456_pf"))
        self.assertEqual(self.check(self.response(200, html))[1], UNKNOWN)

    def test_couchsurfing_incomplete_data_is_possible(self):
        html = self.profile_html(include_marker=False)
        self.assertEqual(self.check(self.response(200, html))[1], POSSIBLE)

    def test_couchsurfing_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_couchsurfing_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)

    def test_couchsurfing_5xx_is_error(self):
        self.assertEqual(self.check(self.response(500))[1], ERROR)


class WikidotProfileDetectionTests(PublicProfileCheckerMixin, unittest.TestCase):
    site_name = "Wikidot"
    default_url = "https://www.wikidot.com/user:info/Test_User"
    site_config = {
        "url": "https://www.wikidot.com/user:info/{username}",
        "checker": "wikidot_profile",
        "rate_limit_delay": 0,
    }

    def profile_html(
        self,
        *,
        public_username="Test_User",
        avatar_id="2462",
        flag_id=None,
        include_profile_box=True,
    ):
        flag_id = flag_id or avatar_id
        profile_box = '<div id="user-info-area"><div class="profile-box"></div></div>' if include_profile_box else ""
        return f'''
            <html><head><title>Wikidot.com: {public_username}</title></head><body>
            <a onclick="UserInfoModule.listeners.flagUser(event,{flag_id})">flag user</a>
            <h1 class="profile-title"><img src="https://www.wikidot.com/avatar.php?userid={avatar_id}">{public_username}</h1>
            {profile_box}</body></html>
        '''

    def test_wikidot_certain_found(self):
        self.assertEqual(self.check(self.response(200, self.profile_html()))[1], FOUND)

    def test_wikidot_structural_soft_404_is_not_found(self):
        html = '''<html><head><title>User Information - Wikidot - Free and Pro Wiki Hosting</title></head>
            <body><div id="page-content"><div class="error-block">User does not exist.</div></div></body></html>'''
        self.assertEqual(self.check(self.response(200, html))[1], NOT_FOUND)

    def test_wikidot_username_conflict_is_unknown(self):
        html = self.profile_html(public_username="Other_User")
        self.assertEqual(self.check(self.response(200, html))[1], UNKNOWN)

    def test_wikidot_url_conflict_is_unknown(self):
        response = self.response(200, self.profile_html(), "https://www.wikidot.com/user:info/Other_User")
        self.assertEqual(self.check(response)[1], UNKNOWN)

    def test_wikidot_stable_id_conflict_is_unknown(self):
        html = self.profile_html(flag_id="9999")
        self.assertEqual(self.check(self.response(200, html))[1], UNKNOWN)

    def test_wikidot_incomplete_data_is_possible(self):
        html = self.profile_html(include_profile_box=False)
        self.assertEqual(self.check(self.response(200, html))[1], POSSIBLE)

    def test_wikidot_unrecognized_soft_error_is_unknown(self):
        html = '<html><head><title>User Information</title></head><body><div id="page-content"><div class="error-block">Temporary error.</div></div></body></html>'
        self.assertEqual(self.check(self.response(200, html))[1], UNKNOWN)

    def test_wikidot_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_wikidot_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)

    def test_wikidot_5xx_is_error(self):
        self.assertEqual(self.check(self.response(503))[1], ERROR)


class TableauPublicProfileDetectionTests(unittest.TestCase):
    username = "Test_User"
    site_name = "Tableau Public"
    site_config = {
        "url": "https://public.tableau.com/app/profile/{username}",
        "checker": "tableau_profile",
        "rate_limit_delay": 0,
    }

    def response(self, status_code, text="", url=None, json_data=None):
        response = Mock()
        response.status_code = status_code
        response.text = text
        response.url = (
            url
            or "https://public.tableau.com/app/profile/Test_User"
        )
        if json_data is None:
            response.json.side_effect = ValueError
        else:
            response.json.return_value = json_data
        return response

    def app_shell(self):
        return '''
            <html><head>
            <script type="module" src="/app/assets/main-example.js"></script>
            </head><body><div id="root"></div></body></html>
        '''

    def profile_data(self, *, username="Test_User", **overrides):
        data = {
            "profileName": username,
            "name": "Test User",
            "title": "Data Analyst",
            "organization": "Example",
            "address": '{"city":"Warsaw","country":"Poland"}',
            "bio": "Public Tableau author",
            "avatarUrl": (
                "https://public.tableau.com/avatar/"
                "11111111-2222-3333-4444-555555555555.jpeg"
            ),
            "visibleWorkbookCount": 3,
            "visibleDataSourceCount": 1,
            "totalNumberOfFollowers": 5,
            "totalNumberOfFollowing": 2,
            "searchable": True,
            "freelance": False,
            "askMeAboutMyViz": True,
            "hideNewWorkbooks": False,
            "showWebsites": True,
            "profileView": "PUBLISHED_DATE",
            "createdAt": 1600000000000,
            "websites": [],
            "achievements": [],
        }
        data.update(overrides)
        return data

    def author_data(self, *, username="Test_User", **overrides):
        data = {
            "profileName": username,
            "name": "Test User",
            "title": "Data Analyst",
            "organization": "Example",
            "address": '{"city":"Warsaw","country":"Poland"}',
            "bio": "Public Tableau author",
            "avatarUrl": (
                "https://public.tableau.com/avatar/"
                "11111111-2222-3333-4444-555555555555.jpeg"
            ),
            "freelance": False,
            "askMeAboutMyViz": True,
        }
        data.update(overrides)
        return data

    def check(self, web_response, profile_response=None, author_response=None):
        responses = [web_response]
        if profile_response is not None:
            responses.append(profile_response)
        if author_response is not None:
            responses.append(author_response)
        with (
            patch("main.username.requests.request", side_effect=responses),
            patch("main.username.time.sleep"),
        ):
            return check_username_on_site(
                self.username,
                self.site_name,
                self.site_config,
            )

    def test_tableau_public_certain_found(self):
        web = self.response(200, self.app_shell())
        profile = self.response(200, json_data=self.profile_data())
        author = self.response(200, json_data=self.author_data())

        result = self.check(web, profile, author)

        self.assertEqual(result[1], FOUND)
        self.assertEqual(
            result[3],
            "public Tableau Public profile found; identity not verified",
        )

    def test_tableau_public_confirmed_not_found(self):
        web = self.response(200, self.app_shell())
        profile = self.response(
            404,
            json_data={
                "error": {
                    "message": "Author profile not found: Test_User",
                    "id": "request-one",
                }
            },
        )
        author = self.response(
            404,
            json_data={
                "error": {
                    "message": "No such object",
                    "id": "request-two",
                }
            },
        )

        self.assertEqual(self.check(web, profile, author)[1], NOT_FOUND)

    def test_tableau_public_username_conflict_is_unknown(self):
        web = self.response(200, self.app_shell())
        profile = self.response(
            200,
            json_data=self.profile_data(username="Other_User"),
        )
        author = self.response(200, json_data=self.author_data())

        self.assertEqual(self.check(web, profile, author)[1], UNKNOWN)

    def test_tableau_public_url_conflict_is_unknown(self):
        web = self.response(
            200,
            self.app_shell(),
            "https://public.tableau.com/app/profile/Other_User",
        )
        profile = self.response(200, json_data=self.profile_data())
        author = self.response(200, json_data=self.author_data())

        self.assertEqual(self.check(web, profile, author)[1], UNKNOWN)

    def test_tableau_public_cross_api_conflict_is_unknown(self):
        web = self.response(200, self.app_shell())
        profile = self.response(200, json_data=self.profile_data())
        author = self.response(
            200,
            json_data=self.author_data(
                avatarUrl=(
                    "https://public.tableau.com/avatar/"
                    "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jpeg"
                )
            ),
        )

        self.assertEqual(self.check(web, profile, author)[1], UNKNOWN)

    def test_tableau_public_incomplete_data_is_possible(self):
        web = self.response(200, self.app_shell())
        incomplete = self.profile_data()
        incomplete.pop("profileView")
        profile = self.response(200, json_data=incomplete)
        author = self.response(200, json_data=self.author_data())

        self.assertEqual(self.check(web, profile, author)[1], POSSIBLE)

    def test_tableau_public_generic_shell_200_is_not_found_evidence(self):
        web = self.response(200, self.app_shell())
        profile = self.response(200, json_data={})
        author = self.response(200, json_data={})

        self.assertEqual(self.check(web, profile, author)[1], POSSIBLE)

    def test_tableau_public_unconfirmed_404_is_unknown(self):
        web = self.response(200, self.app_shell())
        profile = self.response(
            404,
            json_data={"error": {"message": "Temporary lookup error"}},
        )
        author = self.response(
            404,
            json_data={"error": {"message": "No such object"}},
        )

        self.assertEqual(self.check(web, profile, author)[1], UNKNOWN)

    def test_tableau_public_profile_api_200_author_api_404_is_unknown(self):
        web = self.response(200, self.app_shell())
        profile = self.response(200, json_data=self.profile_data())
        author = self.response(
            404,
            json_data={"error": {"message": "No such object"}},
        )

        self.assertEqual(self.check(web, profile, author)[1], UNKNOWN)

    def test_tableau_public_profile_api_404_author_api_200_is_unknown(self):
        web = self.response(200, self.app_shell())
        profile = self.response(
            404,
            json_data={
                "error": {
                    "message": "Author profile not found: Test_User",
                }
            },
        )
        author = self.response(200, json_data=self.author_data())

        self.assertEqual(self.check(web, profile, author)[1], UNKNOWN)

    def test_tableau_public_api_403_is_blocked(self):
        web = self.response(200, self.app_shell())
        profile = self.response(403)
        author = self.response(200, json_data=self.author_data())

        self.assertEqual(self.check(web, profile, author)[1], BLOCKED)

    def test_tableau_public_api_429_is_rate_limited(self):
        web = self.response(200, self.app_shell())
        profile = self.response(200, json_data=self.profile_data())
        author = self.response(429)

        self.assertEqual(self.check(web, profile, author)[1], RATE_LIMIT)

    def test_tableau_public_http_final_url_is_unknown(self):
        web = self.response(
            200,
            self.app_shell(),
            "http://public.tableau.com/app/profile/Test_User",
        )
        profile = self.response(200, json_data=self.profile_data())
        author = self.response(200, json_data=self.author_data())

        self.assertEqual(self.check(web, profile, author)[1], UNKNOWN)

    def test_tableau_public_query_final_url_is_unknown(self):
        web = self.response(
            200,
            self.app_shell(),
            "https://public.tableau.com/app/profile/Test_User?source=test",
        )
        profile = self.response(200, json_data=self.profile_data())
        author = self.response(200, json_data=self.author_data())

        self.assertEqual(self.check(web, profile, author)[1], UNKNOWN)

    def test_tableau_public_fragment_final_url_is_unknown(self):
        web = self.response(
            200,
            self.app_shell(),
            "https://public.tableau.com/app/profile/Test_User#profile",
        )
        profile = self.response(200, json_data=self.profile_data())
        author = self.response(200, json_data=self.author_data())

        self.assertEqual(self.check(web, profile, author)[1], UNKNOWN)

    def test_tableau_public_404_with_profile_data_is_unknown(self):
        web = self.response(200, self.app_shell())
        profile = self.response(
            404,
            json_data={
                "error": {
                    "message": "Author profile not found: Test_User",
                },
                "profileName": "Test_User",
            },
        )
        author = self.response(
            404,
            json_data={"error": {"message": "No such object"}},
        )

        self.assertEqual(self.check(web, profile, author)[1], UNKNOWN)

    def test_tableau_public_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_tableau_public_429_is_rate_limited(self):
        self.assertEqual(self.check(self.response(429))[1], RATE_LIMIT)

    def test_tableau_public_5xx_is_error(self):
        self.assertEqual(self.check(self.response(503))[1], ERROR)

    def test_tableau_public_api_5xx_is_error(self):
        web = self.response(200, self.app_shell())
        profile = self.response(503)
        author = self.response(200, json_data=self.author_data())

        self.assertEqual(self.check(web, profile, author)[1], ERROR)

    def test_tableau_public_network_failure_is_error(self):
        web = self.response(200, self.app_shell())
        with (
            patch(
                "main.username.requests.request",
                side_effect=[
                    web,
                    requests.ConnectionError("network unavailable"),
                ],
            ),
            patch("main.username.time.sleep"),
        ):
            result = check_username_on_site(
                self.username,
                self.site_name,
                self.site_config,
            )

        self.assertEqual(result[1], ERROR)

    def test_tableau_public_config_uses_current_endpoint_and_checker(self):
        config = load_sites_config()["Tableau Public"]

        self.assertEqual(
            config["url"],
            "https://public.tableau.com/app/profile/{username}",
        )
        self.assertEqual(config["checker"], "tableau_profile")


class DailymotionProfileDetectionTests(unittest.TestCase):
    username = "Test_User"
    site_name = "Dailymotion"
    site_config = {
        "url": "https://www.dailymotion.com/{username}",
        "checker": "dailymotion_profile",
        "rate_limit_delay": 0,
    }

    def response(self, status_code, text="", url=None, json_data=None):
        response = Mock()
        response.status_code = status_code
        response.text = text
        response.url = url or "https://www.dailymotion.com/Test_User"
        if json_data is None:
            response.json.side_effect = ValueError
        else:
            response.json.return_value = json_data
        return response

    def profile_data(self, **overrides):
        data = {
            "id": "x123abc",
            "screenname": "Test User",
            "username": "Test_User",
            "url": "https://www.dailymotion.com/Test_User",
            "description": "Public profile",
            "avatar_360_url": "https://s1.dmcdn.net/u/example/360x360",
        }
        data.update(overrides)
        return data

    def not_found_data(self, **overrides):
        error_data = {
            "reason": "object_not_found",
            "object_type": "user",
            "object_id": "Test_User",
            "param": "id",
        }
        error_data.update(overrides)
        return {
            "error": {
                "code": 404,
                "message": "Can't find object user for `id' parameter",
                "type": "not_found",
                "error_data": error_data,
            }
        }

    def check(self, web_response, api_response=None):
        responses = [web_response]
        if api_response is not None:
            responses.append(api_response)
        with (
            patch("main.username.requests.request", side_effect=responses),
            patch("main.username.time.sleep"),
        ):
            return check_username_on_site(
                self.username,
                self.site_name,
                self.site_config,
            )

    def test_dailymotion_certain_found(self):
        result = self.check(
            self.response(200),
            self.response(200, json_data=self.profile_data()),
        )

        self.assertEqual(result[1], FOUND)
        self.assertEqual(
            result[3],
            "public Dailymotion profile found; identity not verified",
        )

    def test_dailymotion_confirmed_not_found(self):
        result = self.check(
            self.response(200),
            self.response(404, json_data=self.not_found_data()),
        )

        self.assertEqual(result[1], NOT_FOUND)

    def test_dailymotion_page_200_without_api_confirmation_is_possible(self):
        result = self.check(
            self.response(200),
            self.response(200, json_data={}),
        )

        self.assertEqual(result[1], POSSIBLE)

    def test_dailymotion_username_conflict_is_unknown(self):
        result = self.check(
            self.response(200),
            self.response(
                200,
                json_data=self.profile_data(username="Other_User"),
            ),
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_dailymotion_empty_id_is_possible(self):
        result = self.check(
            self.response(200),
            self.response(200, json_data=self.profile_data(id="")),
        )

        self.assertEqual(result[1], POSSIBLE)

    def test_dailymotion_api_url_conflict_is_unknown(self):
        result = self.check(
            self.response(200),
            self.response(
                200,
                json_data=self.profile_data(
                    url="https://www.dailymotion.com/Other_User",
                ),
            ),
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_dailymotion_final_url_conflict_is_unknown(self):
        result = self.check(
            self.response(
                200,
                url="https://www.dailymotion.com/Other_User",
            ),
            self.response(200, json_data=self.profile_data()),
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_dailymotion_unconfirmed_404_is_unknown(self):
        result = self.check(
            self.response(200),
            self.response(
                404,
                json_data=self.not_found_data(reason="temporary_error"),
            ),
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_dailymotion_404_with_profile_data_is_unknown(self):
        payload = self.not_found_data()
        payload["username"] = "Test_User"
        result = self.check(
            self.response(200),
            self.response(404, json_data=payload),
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_dailymotion_page_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_dailymotion_api_403_is_blocked(self):
        result = self.check(
            self.response(200),
            self.response(403),
        )

        self.assertEqual(result[1], BLOCKED)

    def test_dailymotion_api_429_is_rate_limited(self):
        result = self.check(
            self.response(200),
            self.response(429),
        )

        self.assertEqual(result[1], RATE_LIMIT)

    def test_dailymotion_api_5xx_is_error(self):
        result = self.check(
            self.response(200),
            self.response(503),
        )

        self.assertEqual(result[1], ERROR)

    def test_dailymotion_network_failure_is_error(self):
        web = self.response(200)
        with (
            patch(
                "main.username.requests.request",
                side_effect=[
                    web,
                    requests.ConnectionError("network unavailable"),
                ],
            ),
            patch("main.username.time.sleep"),
        ):
            result = check_username_on_site(
                self.username,
                self.site_name,
                self.site_config,
            )

        self.assertEqual(result[1], ERROR)

    def test_dailymotion_incomplete_200_is_possible(self):
        incomplete = self.profile_data()
        incomplete.pop("avatar_360_url")
        result = self.check(
            self.response(200),
            self.response(200, json_data=incomplete),
        )

        self.assertEqual(result[1], POSSIBLE)

    def test_dailymotion_config_uses_strict_checker(self):
        config = load_sites_config()["Dailymotion"]

        self.assertEqual(
            config["url"],
            "https://www.dailymotion.com/{username}",
        )
        self.assertEqual(config["checker"], "dailymotion_profile")


class FiveHundredPxProfileDetectionTests(unittest.TestCase):
    username = "Test_User"
    site_name = "500px"
    site_config = {
        "url": "https://500px.com/p/{username}",
        "checker": "fivehundredpx_profile",
        "rate_limit_delay": 0,
    }
    missing_json = object()

    def response(
        self,
        status_code,
        text="",
        url=None,
        json_data=missing_json,
    ):
        response = Mock()
        response.status_code = status_code
        response.text = text
        response.url = url or "https://500px.com/p/Test_User"
        if json_data is self.missing_json:
            response.json.side_effect = ValueError
        else:
            response.json.return_value = json_data
        return response

    def profile_data(self, **overrides):
        data = {
            "__typename": "User",
            "id": "stable-user-id-123",
            "username": "TEST_user",
            "displayName": "Test User",
        }
        data.update(overrides)
        return {"data": {"getUser": data}}

    def not_found_data(self, **data_overrides):
        data = {"getUser": None}
        data.update(data_overrides)
        return {"data": data}

    def check(self, web_response, api_response=None):
        responses = [web_response]
        if api_response is not None:
            if api_response.url == "https://500px.com/p/Test_User":
                api_response.url = "https://api-neo.500px.com/graphql"
            responses.append(api_response)
        with (
            patch(
                "main.username.requests.request",
                side_effect=responses,
            ),
            patch("main.username.time.sleep"),
        ):
            return check_username_on_site(
                self.username,
                self.site_name,
                self.site_config,
            )

    def test_500px_certain_found(self):
        result = self.check(
            self.response(200),
            self.response(200, json_data=self.profile_data()),
        )

        self.assertEqual(result[1], FOUND)
        self.assertEqual(
            result[3],
            "public 500px profile found; identity not verified",
        )

    def test_500px_confirmed_not_found(self):
        result = self.check(
            self.response(200),
            self.response(200, json_data=self.not_found_data()),
        )

        self.assertEqual(result[1], NOT_FOUND)

    def test_500px_page_200_without_valid_api_is_not_found_result(self):
        result = self.check(
            self.response(200),
            self.response(200, json_data={}),
        )

        self.assertEqual(result[1], UNKNOWN)
        self.assertNotEqual(result[1], FOUND)

    def test_500px_username_conflict_is_unknown(self):
        result = self.check(
            self.response(200),
            self.response(
                200,
                json_data=self.profile_data(username="Other_User"),
            ),
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_500px_empty_id_is_possible(self):
        result = self.check(
            self.response(200),
            self.response(200, json_data=self.profile_data(id="")),
        )

        self.assertEqual(result[1], POSSIBLE)

    def test_500px_non_string_id_is_unknown(self):
        result = self.check(
            self.response(200),
            self.response(200, json_data=self.profile_data(id=123)),
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_500px_wrong_typename_is_unknown(self):
        result = self.check(
            self.response(200),
            self.response(
                200,
                json_data=self.profile_data(__typename="Photo"),
            ),
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_500px_final_url_conflict_is_unknown(self):
        result = self.check(
            self.response(200, url="https://500px.com/p/Other_User"),
            self.response(200, json_data=self.profile_data()),
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_500px_graphql_url_conflict_is_unknown(self):
        result = self.check(
            self.response(200),
            self.response(
                200,
                url="https://api-neo.500px.com/other",
                json_data=self.profile_data(),
            ),
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_500px_http_final_url_is_unknown(self):
        result = self.check(
            self.response(200, url="http://500px.com/p/Test_User"),
            self.response(200, json_data=self.profile_data()),
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_500px_query_in_final_url_is_unknown(self):
        result = self.check(
            self.response(
                200,
                url="https://500px.com/p/Test_User?source=test",
            ),
            self.response(200, json_data=self.profile_data()),
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_500px_fragment_in_final_url_is_unknown(self):
        result = self.check(
            self.response(
                200,
                url="https://500px.com/p/Test_User#about",
            ),
            self.response(200, json_data=self.profile_data()),
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_500px_graphql_errors_with_user_data_are_unknown(self):
        payload = self.profile_data()
        payload["errors"] = [{"message": "partial failure"}]
        result = self.check(
            self.response(200),
            self.response(200, json_data=payload),
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_500px_graphql_errors_with_null_user_are_unknown(self):
        payload = self.not_found_data()
        payload["errors"] = [{"message": "lookup failed"}]
        result = self.check(
            self.response(200),
            self.response(200, json_data=payload),
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_500px_invalid_json_is_unknown(self):
        result = self.check(
            self.response(200),
            self.response(200),
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_500px_incomplete_user_is_possible(self):
        incomplete = self.profile_data()
        incomplete["data"]["getUser"].pop("displayName")
        result = self.check(
            self.response(200),
            self.response(200, json_data=incomplete),
        )

        self.assertEqual(result[1], POSSIBLE)

    def test_500px_null_user_with_extra_data_is_unknown(self):
        result = self.check(
            self.response(200),
            self.response(
                200,
                json_data=self.not_found_data(other={"id": "unexpected"}),
            ),
        )

        self.assertEqual(result[1], UNKNOWN)

    def test_500px_page_403_is_blocked(self):
        self.assertEqual(self.check(self.response(403))[1], BLOCKED)

    def test_500px_structural_challenge_page_is_blocked(self):
        result = self.check(
            self.response(
                200,
                text=(
                    "<html><head><title>Just a moment...</title></head>"
                    "<body><form id='challenge-form'></form></body></html>"
                ),
            ),
        )

        self.assertEqual(result[1], BLOCKED)

    def test_500px_bundle_word_challenge_does_not_block_found(self):
        result = self.check(
            self.response(
                200,
                text="<div id='root'></div><script>const challenge = 1;</script>",
            ),
            self.response(200, json_data=self.profile_data()),
        )

        self.assertEqual(result[1], FOUND)

    def test_500px_api_401_is_blocked(self):
        result = self.check(
            self.response(200),
            self.response(401),
        )

        self.assertEqual(result[1], BLOCKED)

    def test_500px_api_429_is_rate_limited(self):
        result = self.check(
            self.response(200),
            self.response(429),
        )

        self.assertEqual(result[1], RATE_LIMIT)

    def test_500px_api_5xx_is_error(self):
        result = self.check(
            self.response(200),
            self.response(503),
        )

        self.assertEqual(result[1], ERROR)

    def test_500px_network_failure_is_error(self):
        web = self.response(200)
        with (
            patch(
                "main.username.requests.request",
                side_effect=[
                    web,
                    requests.ConnectionError("network unavailable"),
                ],
            ),
            patch("main.username.time.sleep"),
        ):
            result = check_username_on_site(
                self.username,
                self.site_name,
                self.site_config,
            )

        self.assertEqual(result[1], ERROR)

    def test_500px_graphql_request_uses_public_operation(self):
        web = self.response(200)
        api = self.response(
            200,
            url="https://api-neo.500px.com/graphql",
            json_data=self.profile_data(),
        )
        with (
            patch(
                "main.username.requests.request",
                side_effect=[web, api],
            ) as request_mock,
            patch("main.username.time.sleep"),
        ):
            result = check_username_on_site(
                self.username,
                self.site_name,
                self.site_config,
            )

        self.assertEqual(result[1], FOUND)
        api_call = request_mock.call_args_list[1]
        self.assertEqual(api_call.kwargs["method"], "POST")
        self.assertEqual(
            api_call.kwargs["url"],
            "https://api-neo.500px.com/graphql",
        )
        self.assertEqual(
            api_call.kwargs["json"]["operationName"],
            "getUserProfile",
        )
        self.assertEqual(
            api_call.kwargs["json"]["variables"],
            {"username": self.username},
        )

    def test_500px_config_uses_current_endpoint_and_checker(self):
        config = load_sites_config()["500px"]

        self.assertEqual(
            config["url"],
            "https://500px.com/p/{username}",
        )
        self.assertEqual(config["checker"], "fivehundredpx_profile")


class EighthPublicProfileNetworkErrorTests(unittest.TestCase):
    def test_network_errors_are_error_for_all_five_checkers(self):
        configs = {
            "HackerRank": ("https://www.hackerrank.com/profile/{username}", "hackerrank_profile"),
            "Wellfound": ("https://wellfound.com/p/{username}", "wellfound_profile"),
            "ReverbNation": ("https://www.reverbnation.com/{username}", "reverbnation_profile"),
            "Couchsurfing": ("https://www.couchsurfing.com/c/users/{username}", "couchsurfing_profile"),
            "Wikidot": ("https://www.wikidot.com/user:info/{username}", "wikidot_profile"),
        }

        for service_name, (url, checker) in configs.items():
            with self.subTest(service=service_name), patch(
                "main.username.requests.request",
                side_effect=requests.ConnectionError("network unavailable"),
            ), patch("main.username.time.sleep"):
                result = check_username_on_site(
                    "Test_User",
                    service_name,
                    {
                        "url": url,
                        "checker": checker,
                        "rate_limit_delay": 0,
                    },
                )
                self.assertEqual(result[1], ERROR)


class SeventhPublicProfileNetworkErrorTests(unittest.TestCase):
    def test_network_errors_are_error_for_all_three_checkers(self):
        configs = {
            "Myspace": ("https://myspace.com/{username}", "myspace_profile"),
            "HubPages": ("https://hubpages.com/@{username}", "hubpages_profile"),
            "Gab": ("https://gab.com/{username}", "gab_profile"),
        }

        for service_name, (url, checker) in configs.items():
            with self.subTest(service=service_name), patch(
                "main.username.requests.request",
                side_effect=requests.ConnectionError("network unavailable"),
            ), patch("main.username.time.sleep"):
                result = check_username_on_site(
                    "Test_User",
                    service_name,
                    {
                        "url": url,
                        "checker": checker,
                        "rate_limit_delay": 0,
                    },
                )
                self.assertEqual(result[1], ERROR)


class SixthPublicProfileNetworkErrorTests(unittest.TestCase):
    def test_network_errors_are_error_for_all_five_checkers(self):
        configs = {
            "ShareChat": ("https://sharechat.com/profile/{username}", "sharechat_profile"),
            "Peerlist": ("https://peerlist.io/{username}", "peerlist_profile"),
            "Codementor": ("https://www.codementor.io/@{username}", "codementor_profile"),
            "Buzzfeed": ("https://buzzfeed.com/{username}", "buzzfeed_profile"),
            "Houzz": ("https://houzz.com/user/{username}", "houzz_profile"),
        }

        for service_name, (url, checker) in configs.items():
            with self.subTest(service=service_name), patch(
                "main.username.requests.request",
                side_effect=requests.ConnectionError("network unavailable"),
            ), patch("main.username.time.sleep"):
                result = check_username_on_site(
                    "Test_User",
                    service_name,
                    {
                        "url": url,
                        "checker": checker,
                        "rate_limit_delay": 0,
                    },
                )
                self.assertEqual(result[1], ERROR)


class FourthPublicProfileCheckerConfigTests(unittest.TestCase):
    def test_five_services_use_dedicated_checkers(self):
        config = load_sites_config()
        expected = {
            "Issuu": "issuu_profile",
            "Slideshare": "slideshare_profile",
            "Hashnode": "hashnode_profile",
            "Codewars": "codewars_profile",
            "Audiomack": "audiomack_profile",
        }

        for service_name, checker in expected.items():
            with self.subTest(service=service_name):
                self.assertEqual(config[service_name]["checker"], checker)


class FifthPublicProfileCheckerConfigTests(unittest.TestCase):
    def test_five_services_use_dedicated_checkers(self):
        config = load_sites_config()
        expected = {
            "Bitbucket": "bitbucket_profile",
            "DeviantArt": "deviantart_profile",
            "BuyMeACoffee": "buymeacoffee_profile",
            "Instructables": "instructables_profile",
            "Scribd": "scribd_profile",
        }

        for service_name, checker in expected.items():
            with self.subTest(service=service_name):
                self.assertEqual(config[service_name]["checker"], checker)


class SixthPublicProfileCheckerConfigTests(unittest.TestCase):
    def test_five_services_use_dedicated_checkers(self):
        config = load_sites_config()
        expected = {
            "ShareChat": "sharechat_profile",
            "Peerlist": "peerlist_profile",
            "Codementor": "codementor_profile",
            "Buzzfeed": "buzzfeed_profile",
            "Houzz": "houzz_profile",
        }

        for service_name, checker in expected.items():
            with self.subTest(service=service_name):
                self.assertEqual(config[service_name]["checker"], checker)


class SeventhPublicProfileCheckerConfigTests(unittest.TestCase):
    def test_three_services_use_dedicated_checkers(self):
        config = load_sites_config()
        expected = {
            "Myspace": "myspace_profile",
            "HubPages": "hubpages_profile",
            "Gab": "gab_profile",
        }

        for service_name, checker in expected.items():
            with self.subTest(service=service_name):
                self.assertEqual(config[service_name]["checker"], checker)


class EighthPublicProfileCheckerConfigTests(unittest.TestCase):
    def test_five_services_use_correct_endpoints_and_checkers(self):
        config = load_sites_config()
        expected = {
            "HackerRank": (
                "https://www.hackerrank.com/profile/{username}",
                "hackerrank_profile",
            ),
            "Wellfound": (
                "https://wellfound.com/p/{username}",
                "wellfound_profile",
            ),
            "ReverbNation": (
                "https://www.reverbnation.com/{username}",
                "reverbnation_profile",
            ),
            "Couchsurfing": (
                "https://www.couchsurfing.com/c/users/{username}",
                "couchsurfing_profile",
            ),
            "Wikidot": (
                "https://www.wikidot.com/user:info/{username}",
                "wikidot_profile",
            ),
        }

        self.assertNotIn("AngelList", config)
        for service_name, (url, checker) in expected.items():
            with self.subTest(service=service_name):
                self.assertEqual(config[service_name]["url"], url)
                self.assertEqual(config[service_name]["checker"], checker)


if __name__ == "__main__":
    unittest.main()
