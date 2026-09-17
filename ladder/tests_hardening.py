import os
import re
import subprocess
import sys
import unittest
from datetime import date, time, timedelta
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.contrib.messages import get_messages
from django.core import mail
from django.core.management import CommandError, call_command
from django.db import DatabaseError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode

from config.sentry import FILTERED_VALUE, initialize_sentry, scrub_sentry_event

from .forms import ScoreSubmissionForm
from .models import AvailabilitySlot, Match, MatchParticipant, PlayerProfile, RateLimitEvent, Team
from .services import InvalidInput, validate_match_score


class HeaderAndSessionHardeningTests(TestCase):
    def test_login_response_enforces_content_security_policy(self):
        response = self.client.get(reverse("ladder:login"))

        directives = set(response.headers["Content-Security-Policy"].split("; "))
        self.assertEqual(
            directives,
            {
                "default-src 'self'",
                "img-src 'self' data:",
                "style-src 'self'",
                "script-src 'self'",
                "form-action 'self'",
                "frame-ancestors 'none'",
            },
        )

    def test_session_and_csrf_cookie_policy_is_lax(self):
        self.assertEqual(settings.SESSION_COOKIE_AGE, 14 * 24 * 60 * 60)
        self.assertEqual(settings.SESSION_COOKIE_SAMESITE, "Lax")
        self.assertEqual(settings.CSRF_COOKIE_SAMESITE, "Lax")

    def test_session_cookie_age_reads_environment(self):
        environment = os.environ.copy()
        environment.update({"DJANGO_DEBUG": "true", "DJANGO_SESSION_COOKIE_AGE": "3600"})

        result = subprocess.run(
            [sys.executable, "-c", "import config.settings as settings; print(settings.SESSION_COOKIE_AGE)"],
            cwd=os.fspath(os.path.dirname(os.path.dirname(__file__))),
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "3600")


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


class ScoreInputBoundsTests(TestCase):
    def test_service_rejects_oversized_match_tiebreak(self):
        with self.assertRaises(InvalidInput):
            validate_match_score([(6, 0), (0, 6), (40000, 0)])

    def test_form_rejects_oversized_score(self):
        form = ScoreSubmissionForm(
            {
                "set1_team_a": 6,
                "set1_team_b": 0,
                "set2_team_a": 0,
                "set2_team_b": 6,
                "set3_team_a": 40000,
                "set3_team_b": 0,
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("set3_team_a", form.errors)

    def test_oversized_score_post_redirects_with_error(self):
        user = get_user_model().objects.create_user("score-bounds-player")
        team_a = Team.objects.create(name="Score Bounds A", division=Team.DIVISION_MENS)
        team_b = Team.objects.create(name="Score Bounds B", division=Team.DIVISION_MENS)
        profile = PlayerProfile.objects.create(user=user, gender=PlayerProfile.GENDER_MALE)
        match = Match.objects.create(
            team_a=team_a,
            team_b=team_b,
            scheduled_week_start_date=date(2026, 9, 14),
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18),
            scheduled_end_time=time(20),
        )
        MatchParticipant.objects.create(
            match=match,
            team=team_a,
            player=profile,
            side=MatchParticipant.SIDE_A,
            lineup_order=1,
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("ladder:submit_score", args=[match.pk]),
            {
                "set1_team_a": 6,
                "set1_team_b": 0,
                "set2_team_a": 0,
                "set2_team_b": 6,
                "set3_team_a": 40000,
                "set3_team_b": 0,
            },
        )

        self.assertRedirects(response, reverse("ladder:match_detail", args=[match.pk]), fetch_redirect_response=False)
        self.assertEqual([str(message) for message in get_messages(response.wsgi_request)], ["Enter valid numeric scores."])


class BruteForceProtectionTests(TestCase):
    def test_five_failed_logins_lock_username(self):
        user = get_user_model().objects.create_user("locked-player", password="CorrectPass123!")
        PlayerProfile.objects.create(user=user, gender=PlayerProfile.GENDER_MALE)
        response = None
        for _ in range(5):
            response = self.client.post(
                reverse("ladder:login"),
                {"username": user.username, "password": "wrong-password"},
                REMOTE_ADDR="198.51.100.10",
            )

        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Too many", status_code=429)
        correct_from_another_ip = self.client.post(
            reverse("ladder:login"),
            {"username": user.username, "password": "CorrectPass123!"},
            REMOTE_ADDR="198.51.100.11",
        )
        self.assertEqual(correct_from_another_ip.status_code, 429)

    def test_purge_rate_limit_events_removes_only_expired_rows(self):
        old = RateLimitEvent.objects.create(key="old")
        current = RateLimitEvent.objects.create(key="current")
        RateLimitEvent.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(hours=25))
        output = StringIO()

        call_command("purge_rate_limit_events", stdout=output)

        self.assertFalse(RateLimitEvent.objects.filter(pk=old.pk).exists())
        self.assertTrue(RateLimitEvent.objects.filter(pk=current.pk).exists())
        self.assertIn("Deleted 1", output.getvalue())


@override_settings(ALLOWED_HOSTS=["ladder.example.com"])
class BootstrapAdminCommandTests(TestCase):
    def setUp(self):
        self.environment = {
            "DJANGO_BOOTSTRAP_ADMIN_USERNAME": "first-admin",
            "DJANGO_BOOTSTRAP_ADMIN_EMAIL": "admin@example.com",
        }

    def test_command_creates_unusable_superuser_and_sends_valid_reset_link(self):
        output = StringIO()

        with unittest.mock.patch.dict(os.environ, self.environment, clear=False):
            call_command("bootstrap_admin", stdout=output)

        user = get_user_model().objects.get(username="first-admin")
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_active)
        self.assertFalse(user.has_usable_password())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["admin@example.com"])
        match = re.search(r"/accounts/reset/(?P<uid>[^/]+)/(?P<token>[^/]+)/", mail.outbox[0].body)
        self.assertIsNotNone(match)
        self.assertEqual(force_str(urlsafe_base64_decode(match.group("uid"))), str(user.pk))
        self.assertTrue(default_token_generator.check_token(user, match.group("token")))
        self.assertIn("Administrator provisioned", output.getvalue())

    def test_command_is_idempotent_after_first_superuser(self):
        with unittest.mock.patch.dict(os.environ, self.environment, clear=False):
            call_command("bootstrap_admin", stdout=StringIO())
            mail.outbox.clear()
            output = StringIO()
            call_command("bootstrap_admin", stdout=output)

        self.assertEqual(get_user_model().objects.filter(is_superuser=True).count(), 1)
        self.assertEqual(mail.outbox, [])
        self.assertIn("already provisioned", output.getvalue())

    def test_command_requires_environment_when_no_superuser_exists(self):
        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DJANGO_BOOTSTRAP_ADMIN_USERNAME", None)
            os.environ.pop("DJANGO_BOOTSTRAP_ADMIN_EMAIL", None)
            with self.assertRaisesMessage(CommandError, "username and email are required"):
                call_command("bootstrap_admin")

    def test_email_failure_rolls_back_new_administrator(self):
        with (
            unittest.mock.patch.dict(os.environ, self.environment, clear=False),
            unittest.mock.patch("ladder.services.send_mail", side_effect=OSError("SMTP unavailable")),
            self.assertRaisesMessage(CommandError, "email could not be sent"),
        ):
            call_command("bootstrap_admin")

        self.assertFalse(get_user_model().objects.filter(is_superuser=True).exists())


class ReadinessCheckTests(TestCase):
    def test_readiness_is_public_queries_database_and_is_not_cached(self):
        response = self.client.get("/health/ready/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ready"})
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_readiness_returns_degraded_when_database_query_fails(self):
        with unittest.mock.patch("config.views.connection.cursor", side_effect=DatabaseError("database unavailable")):
            response = self.client.get("/health/ready/")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "degraded"})
        self.assertEqual(response["Cache-Control"], "no-store")


class OperationsScheduleConfigurationTests(SimpleTestCase):
    def test_render_blueprint_declares_required_cron_jobs(self):
        blueprint = (Path(__file__).resolve().parent.parent / "render.yaml").read_text(encoding="utf-8")

        self.assertEqual(blueprint.count("  - type: cron"), 4)
        for schedule, command in (
            ('schedule: "* * * * *"', "startCommand: python manage.py send_notification_emails --retry-failed"),
            ('schedule: "*/5 * * * *"', "startCommand: python manage.py expire_suggestions"),
            ('schedule: "0 2 * * *"', "startCommand: python manage.py audit_data_integrity"),
            ('schedule: "30 2 * * *"', "startCommand: python manage.py purge_rate_limit_events"),
        ):
            with self.subTest(command=command):
                self.assertIn(schedule, blueprint)
                self.assertIn(command, blueprint)


class SentryConfigurationTests(SimpleTestCase):
    def test_unset_dsn_does_not_initialize_sdk(self):
        with unittest.mock.patch("sentry_sdk.init") as init:
            enabled = initialize_sentry("")

        self.assertFalse(enabled)
        init.assert_not_called()

    def test_configured_dsn_disables_default_pii_and_installs_scrubber(self):
        with unittest.mock.patch("sentry_sdk.init") as init:
            enabled = initialize_sentry("https://public@example.invalid/1")

        self.assertTrue(enabled)
        options = init.call_args.kwargs
        self.assertFalse(options["send_default_pii"])
        self.assertIs(options["before_send"], scrub_sentry_event)
        self.assertEqual(options["dsn"], "https://public@example.invalid/1")
        self.assertEqual(options["integrations"][0].__class__.__name__, "DjangoIntegration")

    def test_scrubber_filters_identity_and_credential_fields_recursively(self):
        event = {
            "request": {
                "headers": {"Authorization": "Bearer secret", "Cookie": "session=secret", "Accept": "text/html"},
                "data": {"email": "player@example.com", "new_password": "secret", "team_id": 9},
            },
            "extra": [{"recipient_email": "other@example.com"}],
        }

        scrubbed = scrub_sentry_event(event)

        self.assertEqual(scrubbed["request"]["headers"]["Authorization"], FILTERED_VALUE)
        self.assertEqual(scrubbed["request"]["headers"]["Cookie"], FILTERED_VALUE)
        self.assertEqual(scrubbed["request"]["data"]["email"], FILTERED_VALUE)
        self.assertEqual(scrubbed["request"]["data"]["new_password"], FILTERED_VALUE)
        self.assertEqual(scrubbed["extra"][0]["recipient_email"], FILTERED_VALUE)
        self.assertEqual(scrubbed["request"]["headers"]["Accept"], "text/html")
        self.assertEqual(scrubbed["request"]["data"]["team_id"], 9)
