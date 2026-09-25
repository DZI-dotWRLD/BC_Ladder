from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    LadderStanding,
    Match,
    MatchParticipant,
    MatchResultSubmission,
    MatchSuggestion,
    PlayerProfile,
    SuggestionParticipant,
    Team,
    TeamMembership,
)
from .services import StaleState, submit_match_result


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


class ScoreOpensAtStartTests(TestCase):
    """Scores can be entered only once a match has started; legacy matches without a start stay open."""

    setUp = MatchSectionTests.setUp
    make_match = MatchSectionTests.make_match
    sections = MatchSectionTests.sections

    def test_future_match_offers_no_score_entry(self):
        upcoming = self.make_match(timedelta(days=7))
        response, open_ids, _history = self.sections()
        self.assertEqual(open_ids, [upcoming.id])
        self.assertNotContains(response, "Enter score")
        self.assertContains(response, "Scoring opens when the match starts.")
        detail = self.client.get(reverse("ladder:match_detail", args=[upcoming.id]))
        self.assertNotContains(detail, 'id="score-submission"')

    def test_started_match_offers_score_entry(self):
        playing = self.make_match(-timedelta(minutes=5))
        response = self.client.get(reverse("ladder:matches"))
        self.assertContains(response, f"{reverse('ladder:match_detail', args=[playing.id])}#score-submission")

    def test_service_and_view_reject_early_scores(self):
        upcoming = self.make_match(timedelta(days=7))
        with self.assertRaisesMessage(StaleState, "Scores open when the match starts."):
            submit_match_result(self.player.user, upcoming, [(6, 4), (6, 4)])
        response = self.client.post(
            reverse("ladder:submit_score", args=[upcoming.id]),
            {"set1_team_a": "6", "set1_team_b": "4", "set2_team_a": "6", "set2_team_b": "4"},
            follow=True,
        )
        self.assertContains(response, "Scores open when the match starts.")
        self.assertFalse(MatchResultSubmission.objects.filter(match=upcoming).exists())

    def test_legacy_match_without_start_accepts_scores(self):
        legacy = self.make_match(timedelta(days=7))
        Match.objects.filter(pk=legacy.pk).update(scheduled_starts_at=None, scheduled_ends_at=None)
        legacy.refresh_from_db()
        submission = submit_match_result(self.player.user, legacy, [(6, 4), (6, 4)])
        self.assertEqual(submission.submitting_team, self.team)


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


class OwnTeamFirstTests(TestCase):
    """Match and request cards name the viewer's own team first, even when it is stored as team B."""

    def setUp(self):
        MatchSectionTests.setUp(self)
        self.opponent_player = PlayerProfile.objects.create(
            user=get_user_model().objects.create_user(username="opponent"),
            gender=PlayerProfile.GENDER_MALE,
        )
        TeamMembership.objects.create(player=self.opponent_player, team=self.opponent, effective_from=timezone.now())

    def make_away_match(self, selected=True):
        starts_at = timezone.now() + timedelta(days=2)
        match = Match.objects.create(
            team_a=self.opponent,
            team_b=self.team,
            scheduled_week_start_date=starts_at.date(),
            scheduled_day_of_week="Monday",
            scheduled_start_time="18:00",
            scheduled_end_time="20:00",
            scheduled_starts_at=starts_at,
            scheduled_ends_at=starts_at + timedelta(hours=2),
        )
        MatchParticipant.objects.create(match=match, team=self.opponent, player=self.opponent_player, side="a", lineup_order=1)
        if selected:
            MatchParticipant.objects.create(match=match, team=self.team, player=self.player, side="b", lineup_order=1)
        return match

    def test_match_row_names_own_team_first_for_both_sides(self):
        self.make_away_match()
        response = self.client.get(reverse("ladder:matches"))
        self.assertContains(response, 'Home <span class="vs">vs</span> Away', html=False)

        self.client.force_login(self.opponent_player.user)
        response = self.client.get(reverse("ladder:matches"))
        self.assertContains(response, 'Away <span class="vs">vs</span> Home', html=False)

    def test_non_selected_teammate_sees_own_team_first(self):
        self.make_away_match(selected=False)
        card = self.client.get(reverse("ladder:matches")).context["open_cards"][0]
        self.assertEqual((card["first_team"], card["second_team"]), (self.team, self.opponent))

    def test_next_match_card_puts_own_side_first(self):
        self.make_away_match()
        sides = self.client.get(reverse("ladder:dashboard")).context["next_match_sides"]
        self.assertEqual([side["team"] for side in sides], [self.team, self.opponent])
        self.assertEqual([item.player for item in sides[0]["players"]], [self.player])

    def test_request_card_puts_own_team_and_lineup_first(self):
        starts_at = timezone.now() + timedelta(days=3)
        suggestion = MatchSuggestion.objects.create(
            team_a=self.opponent,
            team_b=self.team,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=2),
            expires_at=starts_at,
        )
        SuggestionParticipant.objects.create(
            suggestion=suggestion, team=self.opponent, player=self.opponent_player, side=SuggestionParticipant.SIDE_A, lineup_order=1
        )
        SuggestionParticipant.objects.create(
            suggestion=suggestion, team=self.team, player=self.player, side=SuggestionParticipant.SIDE_B, lineup_order=1
        )
        card = self.client.get(reverse("ladder:suggestions")).context["existing_cards"][0]
        self.assertEqual((card["first_team"], card["second_team"]), (self.team, self.opponent))
        self.assertIn("selected", card["first_lineup"])
        self.assertIn("opponent", card["second_lineup"])

    def test_ladder_marks_only_own_team(self):
        LadderStanding.objects.create(team=self.team, position=1)
        LadderStanding.objects.create(team=self.opponent, position=2)
        response = self.client.get(reverse("ladder:ladder", args=["mens"]))
        self.assertEqual(response.context["own_team_id"], self.team.id)
        self.assertContains(response, 'own-team"', count=1)
        self.assertContains(response, 'aria-current="true"', count=1)
