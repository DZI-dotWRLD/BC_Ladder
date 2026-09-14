from datetime import timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    AvailabilitySlot,
    LadderStanding,
    Match,
    MatchReservation,
    MatchSuggestion,
    PlayerProfile,
    Team,
    TeamMembership,
    WorkflowEvent,
)
from .services import candidate_identity, find_opponent_suggestions, request_membership_change, save_availability


class CandidateCommandTests(TestCase):
    def setUp(self):
        self.start = (
            (timezone.now() + timedelta(days=5)).astimezone(ZoneInfo("America/New_York")).replace(hour=8, minute=0, second=0, microsecond=0)
        )
        self.end = self.start + timedelta(hours=2)
        self.team, self.players = self.make_team("own", 3)
        self.opponent, self.others = self.make_team("opponent", 2)
        self.client.force_login(self.players[0].user)

    def make_team(self, name, count, *, offset=0, division=Team.DIVISION_MENS):
        team = Team.objects.create(name=name, division=division)
        players = []
        for index in range(count):
            user = get_user_model().objects.create_user(username=f"{name}-{index}")
            player = PlayerProfile.objects.create(
                user=user, gender=PlayerProfile.GENDER_MALE if division == Team.DIVISION_MENS else PlayerProfile.GENDER_FEMALE
            )
            request_membership_change(user, player, team, "join")
            save_availability(user, self.start + timedelta(hours=offset), self.end + timedelta(hours=offset))
            players.append(player)
        return team, players

    def chosen(self):
        return self.client.get(reverse("ladder:suggestions")).context["options"][0]

    def post(self, option, *, token=None):
        return self.client.post(
            reverse("ladder:create_suggestion", args=[999]), {"candidate": token or option["candidate_token"]}, follow=True
        )

    def test_rank_change_does_not_change_selected_candidate_and_retry_is_idempotent(self):
        chosen = self.chosen()
        expected = candidate_identity(chosen)
        new_team, _ = self.make_team("new-first", 2)
        LadderStanding.objects.update_or_create(team=self.team, defaults={"points": 100, "position": 1})
        LadderStanding.objects.update_or_create(team=self.opponent, defaults={"points": 0, "position": 3})
        LadderStanding.objects.update_or_create(team=new_team, defaults={"points": 100, "position": 2})
        self.assertEqual(self.chosen()["team_b"].pk, new_team.pk)
        self.post(chosen)
        self.post(chosen)
        suggestion = MatchSuggestion.objects.get()
        self.assertEqual([suggestion.team_a_id, suggestion.team_b_id], expected["teams"])
        self.assertEqual(suggestion.starts_at.isoformat(), expected["start"])
        self.assertEqual(suggestion.ends_at.isoformat(), expected["end"])
        self.assertEqual(
            list(suggestion.participants.order_by("side", "lineup_order").values_list("player_id", flat=True)), sum(expected["players"], [])
        )
        self.assertEqual(WorkflowEvent.objects.filter(event_type=WorkflowEvent.EventType.MATCH_REQUEST_CREATED).count(), 1)

    def test_cancelled_source_rejects_without_selecting_another_lineup(self):
        chosen = self.chosen()
        AvailabilitySlot.objects.filter(pk=chosen["availability"][self.players[0].pk].pk).update(status=AvailabilitySlot.STATUS_CANCELLED)
        self.assertContains(self.post(chosen), "Refresh and choose again")
        self.assertFalse(MatchSuggestion.objects.exists())

    def test_changed_source_bounds_reject(self):
        chosen = self.chosen()
        AvailabilitySlot.objects.filter(pk=next(iter(chosen["availability"].values())).pk).update(ends_at=self.end + timedelta(hours=1))
        self.post(chosen)
        self.assertFalse(MatchSuggestion.objects.exists())

    def test_tampered_missing_and_other_actor_token_reject(self):
        chosen = self.chosen()
        self.post(chosen, token=chosen["candidate_token"] + "tampered")
        self.client.post(reverse("ladder:create_suggestion", args=[0]))
        self.client.force_login(self.players[1].user)
        self.post(chosen)
        self.assertFalse(MatchSuggestion.objects.exists())

    def test_removed_selected_member_rejects(self):
        chosen = self.chosen()
        TeamMembership.objects.filter(player=self.players[0]).update(status=TeamMembership.STATUS_INACTIVE)
        self.post(chosen)
        self.assertFalse(MatchSuggestion.objects.exists())

    def test_expired_command_rejects(self):
        chosen = self.chosen()
        import time

        with patch("django.core.signing.time.time", return_value=time.time() + 1801):
            self.post(chosen)
        self.assertFalse(MatchSuggestion.objects.exists())

    def test_new_reservation_rejects_exact_candidate(self):
        chosen = self.chosen()
        match = Match.objects.create(
            team_a=self.team,
            team_b=self.opponent,
            scheduled_week_start_date=self.start.date(),
            scheduled_day_of_week="monday",
            scheduled_start_time=self.start.time(),
            scheduled_end_time=self.end.time(),
            scheduled_starts_at=self.start,
            scheduled_ends_at=self.end,
        )
        MatchReservation.objects.create(match=match, player=self.players[0], starts_at=self.start, ends_at=self.end)
        self.post(chosen)
        self.assertFalse(MatchSuggestion.objects.exists())

    def test_queries_remain_four_with_more_teams_and_nonoverlapping_windows(self):
        with self.assertNumQueries(4):
            initial = find_opponent_suggestions(self.team, (self.start, self.end))
        for index in range(8):
            self.make_team(f"extra-{index}", 3, offset=10)
        with self.assertNumQueries(4):
            expanded = find_opponent_suggestions(self.team, (self.start, self.end + timedelta(days=1)))
        self.assertEqual([candidate_identity(item) for item in initial], [candidate_identity(item) for item in expanded])
        self.assertEqual(len(initial), 3)

    def test_partial_overlap_boundary_and_cross_ladder_eligibility(self):
        partial, _ = self.make_team("partial", 2, offset=1)
        boundary, _ = self.make_team("boundary", 2, offset=2)
        other_ladder, _ = self.make_team("women", 2, division=Team.DIVISION_WOMENS)
        options = find_opponent_suggestions(self.team, (self.start, self.end + timedelta(days=1)))
        self.assertEqual({item["team_b"].pk for item in options}, {self.opponent.pk, partial.pk})
        self.assertNotIn(boundary.pk, {item["team_b"].pk for item in options})
        self.assertNotIn(other_ladder.pk, {item["team_b"].pk for item in options})
        self.assertTrue(all(item["starts_at"] == self.start + timedelta(hours=1) for item in options if item["team_b"].pk == partial.pk))
