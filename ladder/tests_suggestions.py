from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .models import (
    AdminNotification,
    AvailabilitySlot,
    ConfirmedMatchResult,
    LadderStanding,
    Match,
    MatchParticipant,
    MatchReservation,
    MatchSuggestion,
    PlayerProfile,
    PointLedger,
    Team,
    TeamMembership,
    WorkflowEvent,
)
from .services import (
    AuthorizationFailure,
    InvalidInput,
    accept_suggestion,
    cancel_join_request,
    create_admin_notification_for_conflict,
    create_match_suggestion,
    create_team_for_player,
    finalize_match_result,
    find_opponent_suggestions,
    generate_team_lineups,
    recalculate_ladder_positions,
    reconcile_ladder_standings,
    request_membership_change,
    resolve_membership_request,
    save_availability,
    submit_match_result,
    validate_match_score,
)


class RemediationServiceTests(TestCase):
    club_tz = ZoneInfo("America/New_York")

    def create_profile(self, username, gender=PlayerProfile.GENDER_MALE, is_staff=False):
        user = get_user_model().objects.create_user(username=username)
        user.is_staff = is_staff
        user.save(update_fields=["is_staff"])
        return PlayerProfile.objects.create(user=user, gender=gender)

    def make_dt(self, year, month, day, hour, minute=0):
        return datetime(year, month, day, hour, minute, tzinfo=self.club_tz)

    def create_team_with_members(self, name, count, division=Team.DIVISION_MENS):
        team = Team.objects.create(name=name, division=division)
        players = []
        for index in range(1, count + 1):
            gender = PlayerProfile.GENDER_MALE if division == Team.DIVISION_MENS else PlayerProfile.GENDER_FEMALE
            profile = self.create_profile(f"{name}-{index}", gender=gender)
            request_membership_change(profile.user, profile, team, "join")
            players.append(profile)
        return team, players

    def add_match_participants(self, match, team_a_players, team_b_players):
        for order, player in enumerate(team_a_players, start=1):
            MatchParticipant.objects.create(
                match=match,
                team=match.team_a,
                player=player,
                side=MatchParticipant.SIDE_A,
                lineup_order=order,
            )
        for order, player in enumerate(team_b_players, start=1):
            MatchParticipant.objects.create(
                match=match,
                team=match.team_b,
                player=player,
                side=MatchParticipant.SIDE_B,
                lineup_order=order,
            )

    def test_membership_join_enforces_one_active_team_and_three_member_limit(self):
        team, players = self.create_team_with_members("members", 3)
        other_team = Team.objects.create(name="other", division=Team.DIVISION_MENS)

        with self.assertRaises(InvalidInput):
            request_membership_change(players[0].user, players[0], other_team, "join")

        fourth = self.create_profile("fourth")
        with self.assertRaises(InvalidInput):
            request_membership_change(fourth.user, fourth, team, "join")

        self.assertEqual(TeamMembership.objects.filter(team=team, status=TeamMembership.STATUS_ACTIVE).count(), 3)

    def test_create_team_for_player_derives_division_membership_and_standing(self):
        male = self.create_profile("create-mens", gender=PlayerProfile.GENDER_MALE)
        female = self.create_profile("create-womens", gender=PlayerProfile.GENDER_FEMALE)

        mens_team, mens_membership = create_team_for_player(male.user, male, "  Black Label  ")
        womens_team, womens_membership = create_team_for_player(female.user, female, "Red Room")

        male.refresh_from_db()
        female.refresh_from_db()
        self.assertEqual(mens_team.name, "Black Label")
        self.assertEqual(mens_team.division, Team.DIVISION_MENS)
        self.assertEqual(womens_team.division, Team.DIVISION_WOMENS)
        self.assertEqual(mens_membership.status, TeamMembership.STATUS_ACTIVE)
        self.assertEqual(womens_membership.status, TeamMembership.STATUS_ACTIVE)
        self.assertEqual(male.active_team, mens_team)
        self.assertEqual(female.active_team, womens_team)
        self.assertTrue(LadderStanding.objects.filter(team=mens_team).exists())
        self.assertTrue(LadderStanding.objects.filter(team=womens_team).exists())

    def test_create_team_for_player_rejects_existing_membership_and_duplicate_name(self):
        team, players = self.create_team_with_members("existing-membership", 1)

        with self.assertRaises(InvalidInput):
            create_team_for_player(players[0].user, players[0], "New Team")

        free_player = self.create_profile("duplicate-team-player")
        with self.assertRaises(InvalidInput):
            create_team_for_player(free_player.user, free_player, team.name.lower())

        self.assertFalse(Team.objects.filter(name="New Team").exists())

    def test_admin_resolves_removal_request_without_deleting_history(self):
        team, players = self.create_team_with_members("remove", 1)
        membership = request_membership_change(players[0].user, players[0], action="request_removal")
        admin_profile = self.create_profile("admin", is_staff=True)

        request = TeamMembership.objects.get(pk=membership.pk)
        resolved = resolve_membership_request(admin_profile.user, request, "approve")

        players[0].refresh_from_db()
        self.assertEqual(resolved.status, TeamMembership.STATUS_INACTIVE)
        self.assertIsNone(players[0].active_team)
        self.assertTrue(TeamMembership.objects.filter(team=team, player=players[0]).exists())

    def test_player_join_request_is_pending_idempotent_and_admin_approved(self):
        team = Team.objects.create(name="join-request-team", division=Team.DIVISION_MENS)
        player = self.create_profile("join-request-player")
        admin_profile = self.create_profile("join-request-admin", is_staff=True)

        first = request_membership_change(player.user, player, team, "request_join")
        second = request_membership_change(player.user, player, team, "request_join")

        player.refresh_from_db()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.status, TeamMembership.STATUS_JOIN_REQUESTED)
        self.assertIsNone(player.active_team)

        approved = resolve_membership_request(admin_profile.user, first, "approve")

        player.refresh_from_db()
        self.assertEqual(approved.status, TeamMembership.STATUS_ACTIVE)
        self.assertEqual(player.active_team, team)

    def test_admin_join_approval_rechecks_capacity_and_rejects_cleanly(self):
        team, _players = self.create_team_with_members("join-full", 2)
        requester = self.create_profile("join-full-requester")
        request = request_membership_change(requester.user, requester, team, "request_join")
        late_player = self.create_profile("join-full-late")
        request_membership_change(late_player.user, late_player, team, "join")
        admin_profile = self.create_profile("join-full-admin", is_staff=True)

        with self.assertRaises(InvalidInput):
            resolve_membership_request(admin_profile.user, request, "approve")

        requester.refresh_from_db()
        request.refresh_from_db()
        self.assertIsNone(requester.active_team)
        self.assertEqual(request.status, TeamMembership.STATUS_JOIN_REQUESTED)
        self.assertFalse(
            WorkflowEvent.objects.filter(
                membership=request,
                event_type=WorkflowEvent.EventType.JOIN_REQUEST_APPROVED,
            ).exists()
        )

    def test_admin_rejects_join_request_without_active_membership(self):
        team = Team.objects.create(name="join-reject-team", division=Team.DIVISION_MENS)
        player = self.create_profile("join-reject-player")
        admin_profile = self.create_profile("join-reject-admin", is_staff=True)
        request = request_membership_change(player.user, player, team, "request_join")

        rejected = resolve_membership_request(admin_profile.user, request, "reject")

        player.refresh_from_db()
        self.assertEqual(rejected.status, TeamMembership.STATUS_INACTIVE)
        self.assertIsNone(player.active_team)

    def test_player_can_cancel_own_join_request_only(self):
        team = Team.objects.create(name="join-cancel-team", division=Team.DIVISION_MENS)
        player = self.create_profile("join-cancel-player")
        other = self.create_profile("join-cancel-other")
        request = request_membership_change(player.user, player, team, "request_join")

        with self.assertRaises(AuthorizationFailure):
            cancel_join_request(other.user, player)

        cancelled = cancel_join_request(player.user, player)

        request.refresh_from_db()
        player.refresh_from_db()
        self.assertEqual(cancelled.pk, request.pk)
        self.assertEqual(request.status, TeamMembership.STATUS_INACTIVE)
        self.assertEqual(request.resolution_note, "Cancelled by player.")
        self.assertIsNone(player.active_team)

    def test_save_availability_is_idempotent_and_rejects_overlap(self):
        team, players = self.create_team_with_members("availability", 1)
        starts_at = self.make_dt(2026, 7, 6, 18)
        ends_at = self.make_dt(2026, 7, 6, 20)

        first = save_availability(players[0].user, starts_at, ends_at)
        second = save_availability(players[0].user, starts_at, ends_at)

        self.assertEqual(first, second)
        with self.assertRaises(InvalidInput):
            save_availability(players[0].user, self.make_dt(2026, 7, 6, 19), self.make_dt(2026, 7, 6, 21))
        self.assertEqual(AvailabilitySlot.objects.filter(player=players[0]).count(), 1)

    def test_generate_team_lineups_returns_three_stable_interval_pairings(self):
        team, players = self.create_team_with_members("lineups", 3)
        starts_at = self.make_dt(2026, 7, 7, 18)
        ends_at = self.make_dt(2026, 7, 7, 20)
        for player in players:
            save_availability(player.user, starts_at, ends_at)

        lineups = generate_team_lineups(team, (starts_at, ends_at))
        lineup_ids = [tuple(player.id for player in lineup["players"]) for lineup in lineups]

        self.assertEqual(
            lineup_ids,
            [
                (players[0].id, players[1].id),
                (players[0].id, players[2].id),
                (players[1].id, players[2].id),
            ],
        )

    def test_dual_acceptance_confirms_match_and_consumes_only_four_players(self):
        team_a, team_a_players = self.create_team_with_members("accept-a", 3)
        team_b, team_b_players = self.create_team_with_members("accept-b", 2)
        starts_at = timezone.make_aware(datetime.combine(timezone.localdate() + timedelta(days=1), time(18, 0)))
        ends_at = starts_at + timedelta(hours=2)
        for player in team_a_players[:2] + team_b_players:
            save_availability(player.user, starts_at, ends_at)
        third_slot = save_availability(team_a_players[2].user, starts_at, ends_at)

        option = find_opponent_suggestions(team_a, (starts_at, ends_at))[0]
        suggestion = create_match_suggestion(option, expires_at=starts_at)

        partial = accept_suggestion(team_a_players[0].user, suggestion)
        match = accept_suggestion(team_b_players[0].user, suggestion)
        retry = accept_suggestion(team_b_players[0].user, suggestion)

        third_slot.refresh_from_db()
        self.assertEqual(partial.status, MatchSuggestion.STATUS_PARTIALLY_ACCEPTED)
        self.assertEqual(match, retry)
        self.assertEqual(MatchReservation.objects.filter(match=match).count(), 4)
        self.assertEqual(AvailabilitySlot.objects.filter(status=AvailabilitySlot.STATUS_CONSUMED).count(), 4)
        self.assertEqual(third_slot.status, AvailabilitySlot.STATUS_ACTIVE)

    def test_non_lineup_teammate_cannot_accept_suggestion(self):
        team_a, team_a_players = self.create_team_with_members("accept-auth-a", 3)
        team_b, team_b_players = self.create_team_with_members("accept-auth-b", 2)
        starts_at = timezone.make_aware(datetime.combine(timezone.localdate() + timedelta(days=1), time(18, 0)))
        ends_at = starts_at + timedelta(hours=2)
        for player in team_a_players + team_b_players:
            save_availability(player.user, starts_at, ends_at)

        option = find_opponent_suggestions(team_a, (starts_at, ends_at))[0]
        suggestion = create_match_suggestion(option, expires_at=starts_at)

        with self.assertRaises(AuthorizationFailure):
            accept_suggestion(team_a_players[2].user, suggestion)

    def test_duplicate_suggestion_returns_existing_active_suggestion(self):
        team_a, team_a_players = self.create_team_with_members("suggest-dup-a", 2)
        team_b, team_b_players = self.create_team_with_members("suggest-dup-b", 2)
        starts_at = self.make_dt(2026, 7, 9, 18)
        ends_at = self.make_dt(2026, 7, 9, 20)
        for player in team_a_players + team_b_players:
            save_availability(player.user, starts_at, ends_at)

        option = find_opponent_suggestions(team_a, (starts_at, ends_at))[0]
        first = create_match_suggestion(option)
        second = create_match_suggestion(option)

        self.assertEqual(first, second)
        self.assertEqual(MatchSuggestion.objects.count(), 1)
        self.assertEqual(first.expires_at, starts_at)

    def test_score_validation_accepts_match_tiebreak_and_rejects_ten_nine(self):
        for breaker in [(10, 8), (11, 9), (12, 10)]:
            score = validate_match_score([(6, 4), (4, 6), breaker])
            self.assertEqual(score["winner_team_side"], "team_a")

        with self.assertRaises(InvalidInput):
            validate_match_score([(6, 4), (4, 6), (10, 9)])
        with self.assertRaises(InvalidInput):
            validate_match_score([(6, 4), (7, 5), (10, 8)])

    def test_score_submission_authorization_conflict_and_exactly_once_points(self):
        team_a, team_a_players = self.create_team_with_members("score-a", 3)
        team_b, team_b_players = self.create_team_with_members("score-b", 2)
        _, outside_players = self.create_team_with_members("score-outside", 1)
        LadderStanding.objects.create(team=team_a, position=1)
        LadderStanding.objects.create(team=team_b, position=2)
        match = Match.objects.create(
            team_a=team_a,
            team_b=team_b,
            scheduled_week_start_date=date(2026, 7, 6),
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18, 0),
            scheduled_end_time=time(20, 0),
            scheduled_starts_at=self.make_dt(2026, 7, 6, 18),
            scheduled_ends_at=self.make_dt(2026, 7, 6, 20),
        )
        self.add_match_participants(match, team_a_players[:2], team_b_players)

        with self.assertRaises(AuthorizationFailure):
            submit_match_result(outside_players[0].user, match, [(6, 4), (6, 4)])
        with self.assertRaises(AuthorizationFailure):
            submit_match_result(team_a_players[2].user, match, [(6, 4), (6, 4)])

        first = submit_match_result(team_a_players[0].user, match, [(6, 4), (6, 4)])
        duplicate = submit_match_result(team_a_players[0].user, match, [(6, 4), (6, 4)])
        self.assertEqual(first, duplicate)

        submit_match_result(team_b_players[0].user, match, [(4, 6), (4, 6)])
        create_admin_notification_for_conflict(match)
        create_admin_notification_for_conflict(match)
        self.assertEqual(AdminNotification.objects.filter(match=match, is_resolved=False).count(), 1)

        confirmed_match = Match.objects.create(
            team_a=team_a,
            team_b=team_b,
            scheduled_week_start_date=date(2026, 7, 13),
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18, 0),
            scheduled_end_time=time(20, 0),
            scheduled_starts_at=self.make_dt(2026, 7, 13, 18),
            scheduled_ends_at=self.make_dt(2026, 7, 13, 20),
        )
        self.add_match_participants(confirmed_match, team_a_players[:2], team_b_players)
        submit_match_result(team_a_players[0].user, confirmed_match, [(6, 4), (6, 4)])
        submit_match_result(team_b_players[0].user, confirmed_match, [(6, 4), (6, 4)])
        finalize_match_result(confirmed_match)
        finalize_match_result(confirmed_match)
        team_a.standing.refresh_from_db()
        team_b.standing.refresh_from_db()

        self.assertEqual(team_a.standing.points, 3)
        self.assertEqual(team_a.standing.wins, 1)
        self.assertEqual(team_b.standing.losses, 1)
        self.assertEqual(ConfirmedMatchResult.objects.count(), 1)
        self.assertEqual(PointLedger.objects.filter(match=confirmed_match).count(), 2)

    def test_ladder_positions_use_approved_tiebreak_order(self):
        alpha = Team.objects.create(name="Alpha Club", division=Team.DIVISION_MENS)
        beta = Team.objects.create(name="Beta Club", division=Team.DIVISION_MENS)
        gamma = Team.objects.create(name="Gamma Club", division=Team.DIVISION_MENS)
        LadderStanding.objects.create(team=gamma, position=1, points=6, wins=2, losses=2, matches_played=4)
        LadderStanding.objects.create(team=beta, position=2, points=6, wins=2, losses=1, matches_played=3)
        LadderStanding.objects.create(team=alpha, position=3, points=6, wins=2, losses=1, matches_played=3)

        recalculate_ladder_positions(Team.DIVISION_MENS)

        ordered = list(LadderStanding.objects.order_by("position").values_list("team__name", flat=True))
        self.assertEqual(ordered, ["Alpha Club", "Beta Club", "Gamma Club"])

    def test_reconcile_ladder_standings_rebuilds_stats_from_confirmed_results(self):
        team_a, team_a_players = self.create_team_with_members("reconcile-a", 2)
        team_b, team_b_players = self.create_team_with_members("reconcile-b", 2)
        LadderStanding.objects.create(team=team_a, position=2, points=99, wins=10, losses=0, matches_played=10)
        LadderStanding.objects.create(team=team_b, position=1, points=99, wins=10, losses=0, matches_played=10)
        match = Match.objects.create(
            team_a=team_a,
            team_b=team_b,
            scheduled_week_start_date=date(2026, 7, 6),
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18, 0),
            scheduled_end_time=time(20, 0),
            scheduled_starts_at=self.make_dt(2026, 7, 6, 18),
            scheduled_ends_at=self.make_dt(2026, 7, 6, 20),
        )
        self.add_match_participants(match, team_a_players, team_b_players)
        submit_match_result(team_a_players[0].user, match, [(6, 4), (6, 4)])
        submit_match_result(team_b_players[0].user, match, [(6, 4), (6, 4)])

        reconcile_ladder_standings(Team.DIVISION_MENS)
        team_a.standing.refresh_from_db()
        team_b.standing.refresh_from_db()

        self.assertEqual(
            (team_a.standing.matches_played, team_a.standing.wins, team_a.standing.losses, team_a.standing.points), (1, 1, 0, 3)
        )
        self.assertEqual(
            (team_b.standing.matches_played, team_b.standing.wins, team_b.standing.losses, team_b.standing.points), (1, 0, 1, 0)
        )
        self.assertEqual(team_a.standing.position, 1)
