import os
import subprocess
import sys
from datetime import date, time, timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from .forms import ScoreSubmissionForm
from .models import AvailabilitySlot, Match, MatchParticipant, PlayerProfile, RateLimitEvent, Team
from .services import InvalidInput, validate_match_score


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
        profile = PlayerProfile.objects.create(user=user, gender=PlayerProfile.GENDER_MALE, team=team_a)
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
