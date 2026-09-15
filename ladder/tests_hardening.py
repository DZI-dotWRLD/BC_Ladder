import os
import subprocess
import sys

from django.test import SimpleTestCase


class FailClosedSettingsTests(SimpleTestCase):
    def test_unset_debug_without_production_configuration_fails_startup(self):
        environment = os.environ.copy()
        for name in (
            "DJANGO_DEBUG",
            "DJANGO_SECRET_KEY",
            "DJANGO_ALLOWED_HOSTS",
            "DJANGO_CSRF_TRUSTED_ORIGINS",
            "DATABASE_URL",
            "DJANGO_DB_ENGINE",
            "DJANGO_DB_NAME",
            "DJANGO_DB_USER",
            "DJANGO_DB_PASSWORD",
            "DJANGO_DB_HOST",
            "DJANGO_DB_PORT",
            "DJANGO_SECURE_SSL_REDIRECT",
            "DJANGO_SESSION_COOKIE_SECURE",
            "DJANGO_CSRF_COOKIE_SECURE",
            "RENDER_EXTERNAL_HOSTNAME",
        ):
            environment.pop(name, None)

        result = subprocess.run(
            [sys.executable, "-c", "import config.settings"],
            cwd=os.fspath(os.path.dirname(os.path.dirname(__file__))),
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ImproperlyConfigured", result.stderr)
        self.assertIn("Unsafe production settings", result.stderr)
