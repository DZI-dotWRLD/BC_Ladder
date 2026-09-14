from datetime import date, time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.views.defaults import server_error

from .models import Match, MatchParticipant, MatchResultSubmission, PlayerProfile, Team


class FrontendHardeningTests(TestCase):
    def setUp(self):
        self.team = Team.objects.create(name="Home", division=Team.DIVISION_MENS)
        self.opponent = Team.objects.create(name="Away", division=Team.DIVISION_MENS)
        self.player = PlayerProfile.objects.create(
            user=get_user_model().objects.create_user(username="selected"),
            gender=PlayerProfile.GENDER_MALE,
            team=self.team,
        )
        self.client.force_login(self.player.user)

    def make_match(self, selected=True):
        match = Match.objects.create(
            team_a=self.team,
            team_b=self.opponent,
            scheduled_week_start_date=date(2030, 1, 7),
            scheduled_day_of_week="Monday",
            scheduled_start_time=time(18),
            scheduled_end_time=time(19),
        )
        if selected:
            MatchParticipant.objects.create(match=match, team=self.team, player=self.player, side="a", lineup_order=1)
        return match

    def test_match_actions_require_selection_and_missing_team_submission(self):
        selected = self.make_match()
        self.make_match(selected=False)
        response = self.client.get(reverse("ladder:matches"))
        self.assertContains(response, "Enter score", count=1)
        MatchResultSubmission.objects.create(match=selected, submitting_team=self.team, team_a_sets_won=2, team_b_sets_won=0)
        response = self.client.get(reverse("ladder:matches"))
        self.assertNotContains(response, "Enter score")
        self.assertContains(response, "Waiting for the opponent")
        detail = self.client.get(reverse("ladder:match_detail", args=[selected.id]))
        self.assertNotContains(detail, 'id="score-submission"')

    def test_match_pagination_has_stable_tie_breaker_and_bounded_queries(self):
        matches = [self.make_match() for _ in range(21)]
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("ladder:matches"))
        self.assertLessEqual(len(queries), 12)
        self.assertEqual([item.id for item in response.context["matches"]], [item.id for item in reversed(matches[1:])])
        second = self.client.get(reverse("ladder:matches"), {"page": "2"})
        self.assertEqual([item.id for item in second.context["matches"]], [matches[0].id])
        self.assertContains(response, "Next page")

    @patch("ladder.views.find_opponent_suggestions")
    def test_matching_is_explicit_not_run_on_every_visit(self, matcher):
        response = self.client.get(reverse("ladder:suggestions"))
        self.assertEqual(response.status_code, 200)
        matcher.assert_not_called()
        self.assertContains(response, "Find compatible opponents")

    @override_settings(DEBUG=False)
    def test_not_found_and_forbidden_pages_are_safe_and_actionable(self):
        missing = self.client.get("/not-a-page/")
        self.assertEqual(missing.status_code, 404)
        self.assertContains(missing, "Page not found", status_code=404)
        denied = self.client.get(reverse("ladder:ladder", args=["unknown"]))
        self.assertEqual(denied.status_code, 403)
        self.assertContains(denied, "Access denied", status_code=403)

    def test_conflict_is_review_not_another_score_action(self):
        match = self.make_match()
        MatchResultSubmission.objects.create(match=match, submitting_team=self.team, team_a_sets_won=2, team_b_sets_won=0)
        MatchResultSubmission.objects.create(match=match, submitting_team=self.opponent, team_a_sets_won=0, team_b_sets_won=2)
        response = self.client.get(reverse("ladder:match_detail", args=[match.id]))
        self.assertContains(response, "Scores differ. An administrator")
        self.assertNotContains(response, 'id="score-submission"')

    def test_server_error_template_renders_without_request_context(self):
        response = server_error(RequestFactory().get("/"))
        self.assertContains(response, "Something went wrong", status_code=500)
        self.assertContains(response, "Check your match state", status_code=500)
