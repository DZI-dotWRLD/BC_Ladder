from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    Match,
    MatchParticipant,
    MatchReservation,
    MatchResultSubmission,
    MatchSuggestion,
    PlayerProfile,
    SuggestionAcceptance,
    SuggestionParticipant,
    Team,
    WorkflowEvent,
)
from .services import AuthorizationFailure, StaleState, accept_suggestion


class ConfirmedExpiryRetryTests(TransactionTestCase):
    def setUp(self):
        self.team_a = Team.objects.create(name="confirmed-a", division=Team.DIVISION_MENS)
        self.team_b = Team.objects.create(name="confirmed-b", division=Team.DIVISION_MENS)
        start = timezone.now() - timedelta(days=1)
        self.suggestion = MatchSuggestion.objects.create(
            team_a=self.team_a,
            team_b=self.team_b,
            starts_at=start,
            ends_at=start + timedelta(hours=2),
            expires_at=start,
            status=MatchSuggestion.STATUS_CONFIRMED,
        )
        self.match = Match.objects.create(
            team_a=self.team_a,
            team_b=self.team_b,
            source_suggestion=self.suggestion,
            scheduled_week_start_date=start.date(),
            scheduled_day_of_week="Monday",
            scheduled_start_time=start.time(),
            scheduled_end_time=(start + timedelta(hours=2)).time(),
            scheduled_starts_at=start,
            scheduled_ends_at=start + timedelta(hours=2),
        )
        self.players = []
        for side, team in ((SuggestionParticipant.SIDE_A, self.team_a), (SuggestionParticipant.SIDE_B, self.team_b)):
            for order in (1, 2):
                player = PlayerProfile.objects.create(user=get_user_model().objects.create_user(username=f"{side}-{order}"), gender="M")
                self.players.append(player)
                SuggestionParticipant.objects.create(suggestion=self.suggestion, team=team, player=player, side=side, lineup_order=order)
                MatchParticipant.objects.create(match=self.match, team=team, player=player, side=side, lineup_order=order)
            SuggestionAcceptance.objects.create(suggestion=self.suggestion, team=team, accepted_by=player.user)

    def assert_history_unchanged(self, status):
        self.suggestion.refresh_from_db()
        self.match.refresh_from_db()
        self.assertEqual(self.suggestion.status, MatchSuggestion.STATUS_CONFIRMED)
        self.assertEqual(self.match.status, status)
        self.assertEqual(Match.objects.count(), 1)
        self.assertEqual(SuggestionAcceptance.objects.count(), 2)
        self.assertEqual(MatchParticipant.objects.count(), 4)
        for model in (MatchReservation, MatchResultSubmission, WorkflowEvent):
            self.assertEqual(model.objects.count(), 0)

    def test_service_retries_expired_confirmed_scheduled_and_cancelled_match(self):
        for status in (Match.STATUS_SCHEDULED, Match.STATUS_CANCELLED):
            self.match.status = status
            self.match.save(update_fields=["status"])
            for _ in range(2):
                result = accept_suggestion(self.players[0].user, self.suggestion)
                self.assertEqual(result.pk, self.match.pk)
                self.assert_history_unchanged(status)

    def test_request_retries_redirect_to_existing_scheduled_and_cancelled_match(self):
        self.client.force_login(self.players[0].user)
        for status in (Match.STATUS_SCHEDULED, Match.STATUS_CANCELLED):
            self.match.status = status
            self.match.save(update_fields=["status"])
            response = self.client.post(reverse("ladder:accept_suggestion", args=[self.suggestion.pk]), {})
            self.assertRedirects(response, reverse("ladder:match_detail", args=[self.match.pk]), fetch_redirect_response=False)
            self.assert_history_unchanged(status)

    def test_unauthorized_service_and_request_cannot_alter_confirmed(self):
        outsider = PlayerProfile.objects.create(user=get_user_model().objects.create_user(username="outsider"), gender="M")
        with self.assertRaises(AuthorizationFailure):
            accept_suggestion(outsider.user, self.suggestion)
        self.client.force_login(outsider.user)
        response = self.client.post(reverse("ladder:accept_suggestion", args=[self.suggestion.pk]), {})
        self.assertEqual(response.status_code, 404)
        self.assert_history_unchanged(Match.STATUS_SCHEDULED)

    def test_confirmed_without_existing_match_is_controlled_and_not_expired(self):
        self.match.delete()
        with self.assertRaises(StaleState):
            accept_suggestion(self.players[0].user, self.suggestion)
        self.suggestion.refresh_from_db()
        self.assertEqual(self.suggestion.status, MatchSuggestion.STATUS_CONFIRMED)
        self.assertEqual(SuggestionAcceptance.objects.count(), 2)
        self.assertEqual(WorkflowEvent.objects.count(), 0)
