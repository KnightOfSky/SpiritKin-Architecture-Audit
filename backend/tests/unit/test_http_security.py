from __future__ import annotations

import unittest

from backend.security.http import (
    allowed_cors_origin,
    constant_time_equals,
    is_local_request,
    localhost_auth_bypass_enabled,
    token_matches,
)


class FakeHeaders(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class HttpSecurityTests(unittest.TestCase):
    def test_localhost_bypass_is_off_by_default(self):
        self.assertFalse(localhost_auth_bypass_enabled({}))
        self.assertTrue(localhost_auth_bypass_enabled({"SPIRITKIN_ALLOW_LOCALHOST_WITHOUT_TOKEN": "1"}))

    def test_token_matches_header_or_bearer(self):
        self.assertTrue(token_matches(FakeHeaders({"X-Test-Token": "abc"}), expected_token="abc", header_name="X-Test-Token"))
        self.assertTrue(token_matches(FakeHeaders({"Authorization": "Bearer abc"}), expected_token="abc", header_name="X-Test-Token"))
        self.assertFalse(token_matches(FakeHeaders({"X-Test-Token": "wrong"}), expected_token="abc", header_name="X-Test-Token"))

    def test_empty_expected_token_never_authorizes(self):
        self.assertFalse(token_matches(FakeHeaders({}), expected_token="", header_name="X-Test-Token"))
        self.assertFalse(token_matches(FakeHeaders({"X-Test-Token": ""}), expected_token="", header_name="X-Test-Token"))
        self.assertFalse(token_matches(FakeHeaders({"X-Test-Token": "anything"}), expected_token="  ", header_name="X-Test-Token"))

    def test_constant_time_equals(self):
        self.assertTrue(constant_time_equals("abc", "abc"))
        self.assertFalse(constant_time_equals("abc", "abd"))
        self.assertFalse(constant_time_equals("", "abc"))

    def test_is_local_request_prefers_client_ip_over_host_header(self):
        self.assertTrue(is_local_request(FakeHeaders({"Host": "127.0.0.1:8788"})))
        self.assertTrue(is_local_request(FakeHeaders({"Host": "evil.example"}), client_ip="127.0.0.1"))
        # A spoofed local Host header must not make a remote peer look local.
        self.assertFalse(is_local_request(FakeHeaders({"Host": "127.0.0.1:8788"}), client_ip="203.0.113.10"))

    def test_cors_defaults_to_loopback_only(self):
        self.assertEqual(
            allowed_cors_origin(FakeHeaders({"Origin": "http://127.0.0.1:8787"}), env_key="SPIRITKIN_TEST_ORIGINS", environ={}),
            "http://127.0.0.1:8787",
        )
        self.assertEqual(
            allowed_cors_origin(FakeHeaders({"Origin": "https://evil.example"}), env_key="SPIRITKIN_TEST_ORIGINS", environ={}),
            "",
        )

    def test_cors_allows_explicit_origin(self):
        self.assertEqual(
            allowed_cors_origin(
                FakeHeaders({"Origin": "https://console.example"}),
                env_key="SPIRITKIN_TEST_ORIGINS",
                environ={"SPIRITKIN_TEST_ORIGINS": "https://console.example"},
            ),
            "https://console.example",
        )

    def test_cors_any_origin_flag_uses_wildcard_not_reflection(self):
        self.assertEqual(
            allowed_cors_origin(
                FakeHeaders({"Origin": "https://evil.example"}),
                env_key="SPIRITKIN_TEST_ORIGINS",
                environ={"SPIRITKIN_ALLOW_ANY_CORS_ORIGIN": "1"},
            ),
            "*",
        )


if __name__ == "__main__":
    unittest.main()
