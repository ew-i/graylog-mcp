import unittest

from graylog_mcp.infrastructure.config import ConfigError, Settings

BASE = {"GRAYLOG_BASE_URL": "https://gl.example/", "GRAYLOG_API_TOKEN": "tok"}


class SettingsTest(unittest.TestCase):
    def test_defaults(self):
        s = Settings.from_env(BASE)
        self.assertEqual(s.base_url, "https://gl.example")
        self.assertTrue(s.verify_tls)
        self.assertEqual(s.timeout_seconds, 30.0)

    def test_overrides(self):
        s = Settings.from_env(
            {**BASE, "GRAYLOG_VERIFY_TLS": "false", "GRAYLOG_TIMEOUT_SECONDS": "5"}
        )
        self.assertFalse(s.verify_tls)
        self.assertEqual(s.timeout_seconds, 5.0)

    def test_http_requires_explicit_development_override(self):
        with self.assertRaisesRegex(ConfigError, "GRAYLOG_ALLOW_INSECURE_HTTP"):
            Settings.from_env({**BASE, "GRAYLOG_BASE_URL": "http://localhost:9000"})

        settings = Settings.from_env(
            {
                **BASE,
                "GRAYLOG_BASE_URL": "http://localhost:9000",
                "GRAYLOG_ALLOW_INSECURE_HTTP": "true",
            }
        )
        self.assertEqual(settings.base_url, "http://localhost:9000")

    def test_missing_values_are_all_reported(self):
        with self.assertRaises(ConfigError) as ctx:
            Settings.from_env({})
        self.assertIn("GRAYLOG_BASE_URL", str(ctx.exception))
        self.assertIn("GRAYLOG_API_TOKEN", str(ctx.exception))

    def test_invalid_values(self):
        for override in (
            {"GRAYLOG_BASE_URL": "gl.example"},
            {"GRAYLOG_ALLOW_INSECURE_HTTP": "maybe"},
            {"GRAYLOG_VERIFY_TLS": "maybe"},
            {"GRAYLOG_TIMEOUT_SECONDS": "soon"},
            {"GRAYLOG_TIMEOUT_SECONDS": "0"},
        ):
            with self.subTest(**override), self.assertRaises(ConfigError):
                Settings.from_env({**BASE, **override})

    def test_boolean_aliases_and_blank_values(self):
        for value in ("1", "TRUE", "yes", "on"):
            self.assertTrue(Settings.from_env({**BASE, "GRAYLOG_VERIFY_TLS": value}).verify_tls)
        for value in ("0", "FALSE", "no", "off"):
            self.assertFalse(Settings.from_env({**BASE, "GRAYLOG_VERIFY_TLS": value}).verify_tls)
        self.assertTrue(Settings.from_env({**BASE, "GRAYLOG_VERIFY_TLS": " "}).verify_tls)


if __name__ == "__main__":
    unittest.main()
