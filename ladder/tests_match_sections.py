from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Match, MatchParticipant, MatchResultSubmission, PlayerProfile, Team, TeamMembership


class MatchSectionTests(TestCase):
    """Open matches leave "Your matches" once the viewer's side has scored or the match is settled."""

    def setUp(self):
        self.team = Team.objects.create(name="Home", division=Team.DIVISION_MENS)
        self.opponent = Team.objects.create(name="Away", division=Team.DIVISION_MENS)
        self.player = PlayerProfile.objects.create(
            user=get_user_model().objects.create_user(username="selected"),
            gender=PlayerProfile.GENDER_MALE,
        )
        TeamMembership.objects.create(player=self.player, team=self.team, effective_from=timezone.now())
        self.client.force_login(self.player.user)

    def make_match(self, starts_in, status=Match.STATUS_SCHEDULED, selected=True):
        starts_at = timezone.now() + starts_in
        match = Match.objects.create(
            team_a=self.team,
            team_b=self.opponent,
            scheduled_week_start_date=starts_at.date(),
            scheduled_day_of_week="Monday",
            scheduled_start_time="18:00",
            scheduled_end_time="20:00",
            scheduled_starts_at=starts_at,
            scheduled_ends_at=starts_at + timedelta(hours=2),
            status=status,
        )
        if selected:
            MatchParticipant.objects.create(match=match, team=self.team, player=self.player, side="a", lineup_order=1)
        return match

    def submit(self, match, team):
        MatchResultSubmission.objects.create(match=match, submitting_team=team, team_a_sets_won=2, team_b_sets_won=0)

    def sections(self):
        response = self.client.get(reverse("ladder:matches"))
        open_ids = [card["match"].id for card in response.context["open_cards"]]
        history_ids = [card["match"].id for card in response.context["history_cards"]]
        return response, open_ids, history_ids

    def test_own_submission_and_settled_matches_move_to_history(self):
        upcoming = self.make_match(timedelta(days=2))
        needs_score = self.make_match(-timedelta(days=1))
        own_scored = self.make_match(-timedelta(days=2))
        opponent_scored = self.make_match(-timedelta(days=3))
        completed = self.make_match(-timedelta(days=4), status=Match.STATUS_COMPLETED)
        cancelled = self.make_match(timedelta(days=4), status=Match.STATUS_CANCELLED)
        self.submit(own_scored, self.team)
        self.submit(opponent_scored, self.opponent)

        response, open_ids, history_ids = self.sections()

        # Open matches run soonest first, so overdue scorecards lead the list.
        self.assertEqual(open_ids, [opponent_scored.id, needs_score.id, upcoming.id])
        self.assertCountEqual(history_ids, [own_scored.id, completed.id, cancelled.id])
        self.assertContains(response, "Past matches")
        self.assertContains(response, "Your team submitted its score. Waiting for the opponent.")

    def test_non_selected_teammate_uses_current_team_submission(self):
        team_match = self.make_match(-timedelta(days=1), selected=False)
        _response, open_ids, _history = self.sections()
        self.assertEqual(open_ids, [team_match.id])

        self.submit(team_match, self.team)
        _response, open_ids, history_ids = self.sections()
        self.assertEqual(open_ids, [])
        self.assertEqual(history_ids, [team_match.id])

    def test_empty_open_list_invites_a_new_match(self):
        self.make_match(-timedelta(days=4), status=Match.STATUS_COMPLETED)
        response, open_ids, _history = self.sections()
        self.assertEqual(open_ids, [])
        self.assertContains(response, "No upcoming matches.")


class NextMatchTests(TestCase):
    """Home's next match is the soonest scheduled match that has not finished."""

    def setUp(self):
        self.team = Team.objects.create(name="Home", division=Team.DIVISION_MENS)
        self.opponent = Team.objects.create(name="Away", division=Team.DIVISION_MENS)
        self.player = PlayerProfile.objects.create(
            user=get_user_model().objects.create_user(username="next-player"),
            gender=PlayerProfile.GENDER_MALE,
        )
        TeamMembership.objects.create(player=self.player, team=self.team, effective_from=timezone.now())
        self.client.force_login(self.player.user)

    def make_match(self, starts_in, duration=timedelta(hours=2), status=Match.STATUS_SCHEDULED):
        starts_at = timezone.now() + starts_in
        match = Match.objects.create(
            team_a=self.team,
            team_b=self.opponent,
            scheduled_week_start_date=starts_at.date(),
            scheduled_day_of_week="Monday",
            scheduled_start_time="18:00",
            scheduled_end_time="20:00",
            scheduled_starts_at=starts_at,
            scheduled_ends_at=starts_at + duration,
            status=status,
        )
        MatchParticipant.objects.create(match=match, team=self.team, player=self.player, side="a", lineup_order=1)
        return match

    def next_match(self):
        response = self.client.get(reverse("ladder:dashboard"))
        return response, response.context["next_match"], response.context["next_match_in_progress"]

    def test_past_matches_never_become_the_next_match(self):
        self.make_match(-timedelta(days=3))
        response, next_match, in_progress = self.next_match()
        self.assertIsNone(next_match)
        self.assertFalse(in_progress)
        self.assertNotContains(response, "View match")

    def test_soonest_upcoming_match_is_next(self):
        self.make_match(-timedelta(days=1))
        later = self.make_match(timedelta(days=5))
        sooner = self.make_match(timedelta(days=2))
        self.make_match(timedelta(days=1), status=Match.STATUS_CANCELLED)
        _response, next_match, in_progress = self.next_match()
        self.assertEqual(next_match, sooner)
        self.assertNotEqual(next_match, later)
        self.assertFalse(in_progress)

    def test_match_in_progress_stays_next_until_it_ends(self):
        playing = self.make_match(-timedelta(minutes=30))
        self.make_match(timedelta(days=1))
        response, next_match, in_progress = self.next_match()
        self.assertEqual(next_match, playing)
        self.assertTrue(in_progress)
        self.assertContains(response, "On court now")

        Match.objects.filter(pk=playing.pk).update(scheduled_ends_at=timezone.now() - timedelta(minutes=1))
        _response, next_match, _in_progress = self.next_match()
        self.assertNotEqual(next_match, playing)
