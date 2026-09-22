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


if __name__ == "__main__":
    unittest.main()
