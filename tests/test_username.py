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
    load_sites_config,
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


if __name__ == "__main__":
    unittest.main()
