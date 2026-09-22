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


if __name__ == "__main__":
    unittest.main()
