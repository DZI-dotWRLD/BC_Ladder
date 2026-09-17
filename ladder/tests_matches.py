from datetime import date, datetime, time, timedelta
from io import StringIO
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import (
    AdminNotification,
    AvailabilitySlot,
    ConfirmedMatchResult,
    LadderStanding,
    Match,
    MatchParticipant,
    MatchReservation,
    MatchResultSubmission,
    MatchSuggestion,
    PlayerProfile,
    PointLedger,
    ScoreCorrectionAudit,
    Team,
    TeamMembership,
    WorkflowEvent,
)
from .services import (
    AuthorizationFailure,
    StaleState,
    accept_suggestion,
    cancel_join_request,
    cancel_match,
    create_match_suggestion,
    find_opponent_suggestions,
    request_membership_change,
    resolve_membership_request,
    resolve_score_conflict,
    save_availability,
    submit_match_result,
)


class MatchModelTests(TestCase):
    def setUp(self):
        self.week_start_date = date(2026, 6, 1)
        self.team_a = Team.objects.create(name="Challenge Team A", division=Team.DIVISION_MENS)
        self.team_b = Team.objects.create(name="Challenge Team B", division=Team.DIVISION_MENS)
        self.womens_team = Team.objects.create(
            name="Challenge Women's Team",
            division=Team.DIVISION_WOMENS,
        )

    def test_match_can_be_created_for_two_teams_in_same_division(self):
        match = Match(
            team_a=self.team_a,
            team_b=self.team_b,
            scheduled_week_start_date=self.week_start_date,
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18, 0),
            scheduled_end_time=time(19, 0),
        )

        match.full_clean()

        self.assertEqual(match.status, Match.STATUS_SCHEDULED)

    def test_match_team_cannot_play_itself(self):
        match = Match(
            team_a=self.team_a,
            team_b=self.team_a,
            scheduled_week_start_date=self.week_start_date,
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18, 0),
            scheduled_end_time=time(19, 0),
        )

        with self.assertRaises(ValidationError):
            match.full_clean()

    def test_match_teams_must_be_in_same_division(self):
        match = Match(
            team_a=self.team_a,
            team_b=self.womens_team,
            scheduled_week_start_date=self.week_start_date,
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18, 0),
            scheduled_end_time=time(19, 0),
        )

        with self.assertRaises(ValidationError):
            match.full_clean()

    def test_match_start_time_must_be_before_end_time(self):
        match = Match(
            team_a=self.team_a,
            team_b=self.team_b,
            scheduled_week_start_date=self.week_start_date,
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(19, 0),
            scheduled_end_time=time(18, 0),
        )

        with self.assertRaises(ValidationError):
            match.full_clean()

    def test_match_start_time_must_be_not_equal_end_time(self):
        match = Match(
            team_a=self.team_a,
            team_b=self.team_b,
            scheduled_week_start_date=self.week_start_date,
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18, 0),
            scheduled_end_time=time(18, 0),
        )

        with self.assertRaises(ValidationError):
            match.full_clean()


class PhaseA5OperationsTests(TestCase):
    club_tz = ZoneInfo("America/New_York")

    def create_profile(self, username, gender=PlayerProfile.GENDER_MALE, is_staff=False):
        user = get_user_model().objects.create_user(username=username, password="pass")
        user.is_staff = is_staff
        user.save(update_fields=["is_staff"])
        return PlayerProfile.objects.create(user=user, gender=gender)

    def create_team_with_members(self, name, count):
        team = Team.objects.create(name=name, division=Team.DIVISION_MENS)
        players = []
        for index in range(1, count + 1):
            profile = self.create_profile(f"{name}-{index}")
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

    def make_dt(self, year, month, day, hour, minute=0):
        return datetime(year, month, day, hour, minute, tzinfo=self.club_tz)

    def test_admin_can_reject_and_approve_removal_requests(self):
        admin_profile = self.create_profile("ops-admin", is_staff=True)
        team, players = self.create_team_with_members("ops-remove", 2)
        first_request = request_membership_change(players[0].user, players[0], action="request_removal")

        rejected = resolve_membership_request(admin_profile.user, first_request, "reject")
        rejected.refresh_from_db()

        self.assertEqual(rejected.status, TeamMembership.STATUS_ACTIVE)
        self.assertIsNone(rejected.removal_requested_at)

        second_request = request_membership_change(players[0].user, players[0], action="request_removal")
        approved = resolve_membership_request(admin_profile.user, second_request, "approve")
        players[0].refresh_from_db()

        self.assertEqual(approved.status, TeamMembership.STATUS_INACTIVE)
        self.assertIsNone(players[0].active_team)
        self.assertTrue(TeamMembership.objects.filter(team=team, player=players[0]).exists())

    def test_membership_workflow_events_are_deduplicated_and_snapshot_recipients(self):
        admin = self.create_profile("event-admin", is_staff=True)
        other_admin = self.create_profile("event-other-admin", is_staff=True)
        inactive_admin = self.create_profile("event-inactive-admin", is_staff=True)
        inactive_admin.user.is_active = False
        inactive_admin.user.save(update_fields=["is_active"])
        team = Team.objects.create(name="event-membership-team", division=Team.DIVISION_MENS)

        cancelled_player = self.create_profile("event-cancelled-player")
        cancelled_request = request_membership_change(cancelled_player.user, cancelled_player, team, "request_join")
        repeated_request = request_membership_change(cancelled_player.user, cancelled_player, team, "request_join")

        self.assertEqual(repeated_request.pk, cancelled_request.pk)
        created_event = WorkflowEvent.objects.get(event_type=WorkflowEvent.EventType.JOIN_REQUEST_CREATED)
        self.assertEqual(created_event.actor, cancelled_player.user)
        self.assertEqual(created_event.membership, cancelled_request)
        self.assertEqual(
            set(created_event.recipients.values_list("user_id", flat=True)),
            {cancelled_player.user_id, admin.user_id, other_admin.user_id},
        )

        cancelled_membership = cancel_join_request(cancelled_player.user, cancelled_player)
        repeated_cancel = cancel_join_request(cancelled_player.user, cancelled_player)
        self.assertEqual(repeated_cancel.pk, cancelled_membership.pk)
        cancelled_event = WorkflowEvent.objects.get(event_type=WorkflowEvent.EventType.JOIN_REQUEST_CANCELLED)
        self.assertEqual(cancelled_event.previous_state, TeamMembership.STATUS_JOIN_REQUESTED)
        self.assertEqual(cancelled_event.new_state, TeamMembership.STATUS_INACTIVE)
        self.assertEqual(
            set(cancelled_event.recipients.values_list("user_id", flat=True)),
            {cancelled_player.user_id, admin.user_id, other_admin.user_id},
        )

        approved_player = self.create_profile("event-approved-player")
        approved_request = request_membership_change(approved_player.user, approved_player, team, "request_join")
        resolve_membership_request(admin.user, approved_request, "approve")
        resolve_membership_request(admin.user, approved_request, "approve")
        approved_event = WorkflowEvent.objects.get(event_type=WorkflowEvent.EventType.JOIN_REQUEST_APPROVED)
        self.assertEqual(
            set(approved_event.recipients.values_list("user_id", flat=True)),
            {approved_player.user_id, admin.user_id},
        )

        rejected_player = self.create_profile("event-rejected-player")
        rejected_request = request_membership_change(rejected_player.user, rejected_player, team, "request_join")
        resolve_membership_request(admin.user, rejected_request, "reject")
        resolve_membership_request(admin.user, rejected_request, "reject")
        rejected_event = WorkflowEvent.objects.get(event_type=WorkflowEvent.EventType.JOIN_REQUEST_REJECTED)
        self.assertEqual(
            set(rejected_event.recipients.values_list("user_id", flat=True)),
            {rejected_player.user_id, admin.user_id},
        )
        self.assertEqual(WorkflowEvent.objects.filter(event_type=WorkflowEvent.EventType.JOIN_REQUEST_CREATED).count(), 3)
        self.assertEqual(WorkflowEvent.objects.filter(event_type=WorkflowEvent.EventType.JOIN_REQUEST_CANCELLED).count(), 1)
        self.assertEqual(WorkflowEvent.objects.filter(event_type=WorkflowEvent.EventType.JOIN_REQUEST_APPROVED).count(), 1)
        self.assertEqual(WorkflowEvent.objects.filter(event_type=WorkflowEvent.EventType.JOIN_REQUEST_REJECTED).count(), 1)

    def test_match_and_score_workflow_events_snapshot_exact_recipients(self):
        admin = self.create_profile("score-event-admin", is_staff=True)
        other_admin = self.create_profile("score-event-other-admin", is_staff=True)
        team_a, team_a_players = self.create_team_with_members("score-event-a", 2)
        team_b, team_b_players = self.create_team_with_members("score-event-b", 2)
        starts_at = self.make_dt(2027, 8, 3, 18)
        ends_at = self.make_dt(2027, 8, 3, 20)
        for player in team_a_players + team_b_players:
            save_availability(player.user, starts_at, ends_at)
        option = find_opponent_suggestions(team_a, (starts_at, ends_at))[0]
        suggestion = create_match_suggestion(option, expires_at=self.make_dt(2027, 8, 10, 18))

        accept_suggestion(team_a_players[0].user, suggestion)
        match = accept_suggestion(team_b_players[0].user, suggestion)
        repeated_match = accept_suggestion(team_b_players[0].user, suggestion)
        participant_user_ids = {player.user_id for player in team_a_players + team_b_players}

        self.assertEqual(repeated_match.pk, match.pk)
        confirmed_event = WorkflowEvent.objects.get(event_type=WorkflowEvent.EventType.MATCH_CONFIRMED)
        self.assertEqual(confirmed_event.actor, team_b_players[0].user)
        self.assertEqual(set(confirmed_event.recipients.values_list("user_id", flat=True)), participant_user_ids)
        self.assertEqual(WorkflowEvent.objects.filter(event_type=WorkflowEvent.EventType.MATCH_CONFIRMED).count(), 1)

        first_submission = submit_match_result(team_a_players[0].user, match, [(6, 4), (6, 4)])
        repeated_submission = submit_match_result(team_a_players[0].user, match, [(6, 4), (6, 4)])

        self.assertEqual(repeated_submission.pk, first_submission.pk)
        self.assertEqual(WorkflowEvent.objects.filter(event_type=WorkflowEvent.EventType.SCORE_SUBMITTED).count(), 1)

        second_submission = submit_match_result(team_b_players[0].user, match, [(4, 6), (4, 6)])
        score_events = WorkflowEvent.objects.filter(event_type=WorkflowEvent.EventType.SCORE_SUBMITTED).order_by("submission_id")
        self.assertEqual(score_events.count(), 2)
        self.assertEqual({event.submission_id for event in score_events}, {first_submission.id, second_submission.id})
        for event in score_events:
            self.assertEqual(set(event.recipients.values_list("user_id", flat=True)), participant_user_ids)

        notification = AdminNotification.objects.get(match=match, is_resolved=False)
        conflict_event = WorkflowEvent.objects.get(event_type=WorkflowEvent.EventType.SCORE_CONFLICT_CREATED)
        self.assertEqual(conflict_event.actor, team_b_players[0].user)
        self.assertEqual(
            set(conflict_event.recipients.values_list("user_id", flat=True)),
            participant_user_ids | {admin.user_id, other_admin.user_id},
        )

        audit = resolve_score_conflict(admin.user, notification, first_submission)
        repeated_audit = resolve_score_conflict(admin.user, notification, first_submission)
        self.assertEqual(repeated_audit.pk, audit.pk)
        resolved_event = WorkflowEvent.objects.get(event_type=WorkflowEvent.EventType.SCORE_CONFLICT_RESOLVED)
        self.assertEqual(resolved_event.score_correction_audit, audit)
        self.assertEqual(
            set(resolved_event.recipients.values_list("user_id", flat=True)),
            participant_user_ids | {admin.user_id},
        )
        self.assertEqual(WorkflowEvent.objects.filter(event_type=WorkflowEvent.EventType.SCORE_CONFLICT_RESOLVED).count(), 1)

    def test_cancel_match_releases_reservations_and_restores_availability(self):
        admin_profile = self.create_profile("cancel-admin", is_staff=True)
        team_a, team_a_players = self.create_team_with_members("cancel-a", 2)
        team_b, team_b_players = self.create_team_with_members("cancel-b", 2)
        starts_at = self.make_dt(2026, 8, 3, 18)
        ends_at = self.make_dt(2026, 8, 3, 20)
        for player in team_a_players + team_b_players:
            save_availability(player.user, starts_at, ends_at)
        option = find_opponent_suggestions(team_a, (starts_at, ends_at))[0]
        suggestion = create_match_suggestion(option, expires_at=self.make_dt(2027, 8, 10, 18))
        accept_suggestion(team_a_players[0].user, suggestion)
        match = accept_suggestion(team_b_players[0].user, suggestion)

        cancelled = cancel_match(admin_profile.user, match)

        self.assertEqual(cancelled.status, Match.STATUS_CANCELLED)
        self.assertEqual(MatchReservation.objects.filter(match=match, status=MatchReservation.STATUS_RELEASED).count(), 4)
        self.assertEqual(AvailabilitySlot.objects.filter(status=AvailabilitySlot.STATUS_ACTIVE).count(), 4)
        cancelled_event = WorkflowEvent.objects.get(event_type=WorkflowEvent.EventType.MATCH_CANCELLED)
        self.assertEqual(cancelled_event.actor, admin_profile.user)
        self.assertEqual(
            set(cancelled_event.recipients.values_list("user_id", flat=True)),
            {player.user_id for player in team_a_players + team_b_players},
        )
        cancel_match(admin_profile.user, match)
        self.assertEqual(WorkflowEvent.objects.filter(event_type=WorkflowEvent.EventType.MATCH_CANCELLED).count(), 1)

    def test_selected_participant_can_cancel_but_other_players_cannot(self):
        team_a, team_a_players = self.create_team_with_members("participant-cancel-a", 3)
        team_b, team_b_players = self.create_team_with_members("participant-cancel-b", 2)
        outsider = self.create_profile("participant-cancel-outsider")
        starts_at = self.make_dt(2027, 8, 17, 18)
        ends_at = self.make_dt(2027, 8, 17, 20)
        for player in team_a_players + team_b_players:
            save_availability(player.user, starts_at, ends_at)
        option = find_opponent_suggestions(team_a, (starts_at, ends_at))[0]
        suggestion = create_match_suggestion(option, expires_at=self.make_dt(2027, 8, 24, 18))
        selected_player_ids = {player.id for player in option["team_a_players"] + option["team_b_players"]}
        selected_player = next(player for player in team_a_players if player.id in selected_player_ids)
        non_lineup_teammate = next(player for player in team_a_players if player.id not in selected_player_ids)
        accept_suggestion(option["team_a_players"][0].user, suggestion)
        match = accept_suggestion(option["team_b_players"][0].user, suggestion)

        with self.assertRaises(AuthorizationFailure):
            cancel_match(non_lineup_teammate.user, match)
        with self.assertRaises(AuthorizationFailure):
            cancel_match(outsider.user, match)

        cancelled = cancel_match(selected_player.user, match)

        self.assertEqual(cancelled.status, Match.STATUS_CANCELLED)
        self.assertEqual(WorkflowEvent.objects.get(event_type=WorkflowEvent.EventType.MATCH_CANCELLED).actor, selected_player.user)

    def test_match_cancellation_is_blocked_after_first_score_submission_for_everyone(self):
        admin = self.create_profile("score-cutoff-admin", is_staff=True)
        team_a, team_a_players = self.create_team_with_members("score-cutoff-a", 2)
        team_b, team_b_players = self.create_team_with_members("score-cutoff-b", 2)
        match = Match.objects.create(
            team_a=team_a,
            team_b=team_b,
            scheduled_week_start_date=date(2027, 8, 23),
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18, 0),
            scheduled_end_time=time(20, 0),
            scheduled_starts_at=self.make_dt(2027, 8, 23, 18),
            scheduled_ends_at=self.make_dt(2027, 8, 23, 20),
        )
        self.add_match_participants(match, team_a_players, team_b_players)
        submit_match_result(team_a_players[0].user, match, [(6, 4), (6, 4)])

        with self.assertRaises(StaleState):
            cancel_match(team_a_players[0].user, match)
        with self.assertRaises(StaleState):
            cancel_match(admin.user, match)

        match.refresh_from_db()
        self.assertEqual(match.status, Match.STATUS_SCHEDULED)
        self.assertFalse(WorkflowEvent.objects.filter(event_type=WorkflowEvent.EventType.MATCH_CANCELLED).exists())

    def test_cancellation_preserves_overlapping_replacement_availability(self):
        team_a, team_a_players = self.create_team_with_members("overlap-cancel-a", 2)
        team_b, team_b_players = self.create_team_with_members("overlap-cancel-b", 2)
        starts_at = self.make_dt(2027, 8, 30, 18)
        ends_at = self.make_dt(2027, 8, 30, 20)
        original_slots = {player.id: save_availability(player.user, starts_at, ends_at) for player in team_a_players + team_b_players}
        option = find_opponent_suggestions(team_a, (starts_at, ends_at))[0]
        suggestion = create_match_suggestion(option, expires_at=self.make_dt(2027, 9, 6, 18))
        accept_suggestion(team_a_players[0].user, suggestion)
        match = accept_suggestion(team_b_players[0].user, suggestion)
        replacement_player = team_a_players[0]
        replacement = save_availability(
            replacement_player.user,
            self.make_dt(2027, 8, 30, 18, 30),
            self.make_dt(2027, 8, 30, 20, 30),
        )

        cancel_match(replacement_player.user, match)

        for slot in original_slots.values():
            slot.refresh_from_db()
        replacement.refresh_from_db()
        suggestion.refresh_from_db()
        event = WorkflowEvent.objects.get(event_type=WorkflowEvent.EventType.MATCH_CANCELLED)
        self.assertEqual(original_slots[replacement_player.id].status, AvailabilitySlot.STATUS_CANCELLED)
        self.assertEqual(replacement.status, AvailabilitySlot.STATUS_ACTIVE)
        self.assertEqual(
            event.metadata["superseded_availability_ids"],
            [original_slots[replacement_player.id].id],
        )
        self.assertEqual(suggestion.status, MatchSuggestion.STATUS_CONFIRMED)

    def test_completed_match_cannot_be_cancelled(self):
        admin_profile = self.create_profile("completed-admin", is_staff=True)
        team_a, players_a = self.create_team_with_members("completed-a", 2)
        team_b, players_b = self.create_team_with_members("completed-b", 2)
        LadderStanding.objects.create(team=team_a, position=1)
        LadderStanding.objects.create(team=team_b, position=2)
        match = Match.objects.create(
            team_a=team_a,
            team_b=team_b,
            scheduled_week_start_date=date(2026, 8, 3),
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18, 0),
            scheduled_end_time=time(20, 0),
            scheduled_starts_at=self.make_dt(2026, 8, 3, 18),
            scheduled_ends_at=self.make_dt(2026, 8, 3, 20),
        )
        self.add_match_participants(match, players_a, players_b)
        submit_match_result(players_a[0].user, match, [(6, 4), (6, 4)])
        submit_match_result(players_b[0].user, match, [(6, 4), (6, 4)])

        with self.assertRaises(StaleState):
            cancel_match(admin_profile.user, match)

    def test_admin_can_resolve_score_conflict_notification(self):
        admin_profile = self.create_profile("conflict-admin", is_staff=True)
        team_a, players_a = self.create_team_with_members("conflict-a", 2)
        team_b, players_b = self.create_team_with_members("conflict-b", 2)
        match = Match.objects.create(
            team_a=team_a,
            team_b=team_b,
            scheduled_week_start_date=date(2026, 8, 3),
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18, 0),
            scheduled_end_time=time(20, 0),
            scheduled_starts_at=self.make_dt(2026, 8, 3, 18),
            scheduled_ends_at=self.make_dt(2026, 8, 3, 20),
        )
        self.add_match_participants(match, players_a, players_b)
        submit_match_result(players_a[0].user, match, [(6, 4), (6, 4)])
        submit_match_result(players_b[0].user, match, [(4, 6), (4, 6)])
        notification = AdminNotification.objects.get(match=match)
        official_submission = MatchResultSubmission.objects.get(match=match, submitting_team=team_a)

        audit = resolve_score_conflict(admin_profile.user, notification, official_submission)
        notification.refresh_from_db()
        match.refresh_from_db()

        self.assertTrue(notification.is_resolved)
        self.assertEqual(audit.official_submission, official_submission)
        self.assertEqual(audit.new_winning_team, team_a)
        self.assertEqual(match.status, Match.STATUS_COMPLETED)
        self.assertEqual(ScoreCorrectionAudit.objects.count(), 1)
        self.assertEqual(PointLedger.objects.filter(match=match).count(), 2)

    def test_seed_demo_is_idempotent_and_populates_core_flows(self):
        first_output = StringIO()
        second_output = StringIO()

        call_command("seed_demo", allow_non_debug=True, stdout=first_output)
        counts_after_first = {
            "users": get_user_model().objects.count(),
            "profiles": PlayerProfile.objects.count(),
            "teams": Team.objects.count(),
            "memberships": TeamMembership.objects.count(),
            "standings": LadderStanding.objects.count(),
            "matches": Match.objects.count(),
        }
        call_command("seed_demo", allow_non_debug=True, stdout=second_output)

        self.assertEqual(counts_after_first["users"], get_user_model().objects.count())
        self.assertEqual(counts_after_first["profiles"], PlayerProfile.objects.count())
        self.assertEqual(counts_after_first["teams"], Team.objects.count())
        self.assertEqual(counts_after_first["memberships"], TeamMembership.objects.count())
        self.assertEqual(counts_after_first["standings"], LadderStanding.objects.count())
        self.assertEqual(counts_after_first["matches"], Match.objects.count())
        self.assertGreaterEqual(Match.objects.count(), 2)
        self.assertTrue(MatchSuggestion.objects.exists())
        for standing in LadderStanding.objects.select_related("team"):
            self.assertEqual(standing.matches_played, standing.wins + standing.losses)
            self.assertEqual(standing.points, standing.wins * 3)

        demo_mens_team = Team.objects.get(name="Men Demo Team 1")
        starts_at = timezone.now()
        ends_at = starts_at + timedelta(days=30)
        self.assertTrue(find_opponent_suggestions(demo_mens_team, (starts_at, ends_at)))
        self.assertIn("Demo data is ready", second_output.getvalue())

    @override_settings(DEBUG=False)
    def test_seed_demo_refuses_non_debug_without_override(self):
        with self.assertRaisesRegex(CommandError, "disabled when DEBUG is false"):
            call_command("seed_demo")

        self.assertFalse(get_user_model().objects.filter(username__startswith="demo-").exists())


class DataIntegrityAuditCommandTests(TestCase):
    club_tz = ZoneInfo("America/New_York")

    def create_player(self, username, team):
        user = get_user_model().objects.create_user(username=username)
        player = PlayerProfile.objects.create(user=user, gender=PlayerProfile.GENDER_MALE)
        TeamMembership.objects.create(
            player=player,
            team=team,
            status=TeamMembership.STATUS_ACTIVE,
            effective_from=timezone.now(),
        )
        return player

    def create_match(self, team_a, team_b):
        return Match.objects.create(
            team_a=team_a,
            team_b=team_b,
            scheduled_week_start_date=date(2026, 10, 5),
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18, 0),
            scheduled_end_time=time(20, 0),
            scheduled_starts_at=datetime(2026, 10, 5, 18, tzinfo=self.club_tz),
            scheduled_ends_at=datetime(2026, 10, 5, 20, tzinfo=self.club_tz),
        )

    def test_audit_data_integrity_passes_for_empty_database(self):
        output = StringIO()

        call_command("audit_data_integrity", stdout=output)

        self.assertIn("Data integrity audit passed", output.getvalue())

    def test_audit_data_integrity_fails_for_overlapping_active_availability(self):
        if connection.vendor == "postgresql":
            self.skipTest("PostgreSQL exclusion constraints reject overlapping active availability before audit.")
        team = Team.objects.create(name="Audit Team", division=Team.DIVISION_MENS)
        player = self.create_player("audit-player", team)
        first_start = datetime(2026, 10, 5, 18, tzinfo=self.club_tz)
        first_end = datetime(2026, 10, 5, 20, tzinfo=self.club_tz)
        second_start = datetime(2026, 10, 5, 19, tzinfo=self.club_tz)
        second_end = datetime(2026, 10, 5, 21, tzinfo=self.club_tz)
        AvailabilitySlot.objects.create(
            player=player,
            week_start_date=date(2026, 10, 5),
            day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            start_time=time(18, 0),
            end_time=time(20, 0),
            starts_at=first_start,
            ends_at=first_end,
            status=AvailabilitySlot.STATUS_ACTIVE,
        )
        AvailabilitySlot.objects.create(
            player=player,
            week_start_date=date(2026, 10, 5),
            day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            start_time=time(19, 0),
            end_time=time(21, 0),
            starts_at=second_start,
            ends_at=second_end,
            status=AvailabilitySlot.STATUS_ACTIVE,
        )
        output = StringIO()

        with self.assertRaises(CommandError):
            call_command("audit_data_integrity", stdout=output)

        self.assertIn("availability.overlap_active", output.getvalue())

    def test_audit_data_integrity_fails_for_standing_mismatch_and_missing_ledger(self):
        team_a = Team.objects.create(name="Audit A", division=Team.DIVISION_MENS)
        team_b = Team.objects.create(name="Audit B", division=Team.DIVISION_MENS)
        match = self.create_match(team_a, team_b)
        submission = MatchResultSubmission.objects.create(
            match=match,
            submitting_team=team_a,
            team_a_sets_won=2,
            team_b_sets_won=0,
        )
        ConfirmedMatchResult.objects.create(
            match=match,
            winning_team=team_a,
            losing_team=team_b,
            confirmed_from_submission=submission,
        )
        LadderStanding.objects.create(team=team_a, position=1, matches_played=0, wins=0, losses=0, points=0)
        LadderStanding.objects.create(team=team_b, position=2, matches_played=0, wins=0, losses=0, points=0)
        output = StringIO()

        with self.assertRaises(CommandError):
            call_command("audit_data_integrity", stdout=output)

        audit_output = output.getvalue()
        self.assertIn("standing.stats_mismatch", audit_output)
        self.assertIn("ledger.missing_match_result", audit_output)

    def test_audit_data_integrity_fails_for_cross_linked_confirmed_result_and_extra_ledger(self):
        team_a = Team.objects.create(name="Audit Cross A", division=Team.DIVISION_MENS)
        team_b = Team.objects.create(name="Audit Cross B", division=Team.DIVISION_MENS)
        team_c = Team.objects.create(name="Audit Cross C", division=Team.DIVISION_MENS)
        team_d = Team.objects.create(name="Audit Cross D", division=Team.DIVISION_MENS)
        match = self.create_match(team_a, team_b)
        submission = MatchResultSubmission.objects.create(
            match=match,
            submitting_team=team_a,
            team_a_sets_won=2,
            team_b_sets_won=0,
        )
        ConfirmedMatchResult.objects.create(
            match=match,
            winning_team=team_c,
            losing_team=team_d,
            confirmed_from_submission=submission,
        )
        LadderStanding.objects.create(team=team_a, position=1)
        LadderStanding.objects.create(team=team_b, position=2)
        LadderStanding.objects.create(team=team_c, position=3, matches_played=1, wins=1, points=3)
        LadderStanding.objects.create(team=team_d, position=4, matches_played=1, losses=1)
        PointLedger.objects.create(match=match, team=team_c, points_delta=3, reason="match_result")
        PointLedger.objects.create(match=match, team=team_d, points_delta=0, reason="match_result")
        PointLedger.objects.create(match=match, team=team_a, points_delta=0, reason="match_result")
        output = StringIO()

        with self.assertRaises(CommandError):
            call_command("audit_data_integrity", stdout=output)

        audit_output = output.getvalue()
        self.assertIn("result.teams_mismatch", audit_output)
        self.assertIn("result.winner_mismatch", audit_output)
        self.assertIn("ledger.unexpected_match_result", audit_output)

    def test_audit_data_integrity_fails_for_submission_from_different_match(self):
        team_a = Team.objects.create(name="Audit Submission A", division=Team.DIVISION_MENS)
        team_b = Team.objects.create(name="Audit Submission B", division=Team.DIVISION_MENS)
        first_match = self.create_match(team_a, team_b)
        second_match = self.create_match(team_a, team_b)
        submission = MatchResultSubmission.objects.create(
            match=second_match,
            submitting_team=team_a,
            team_a_sets_won=2,
            team_b_sets_won=0,
        )
        ConfirmedMatchResult.objects.create(
            match=first_match,
            winning_team=team_a,
            losing_team=team_b,
            confirmed_from_submission=submission,
        )
        PointLedger.objects.create(match=first_match, team=team_a, points_delta=3, reason="match_result")
        PointLedger.objects.create(match=first_match, team=team_b, points_delta=0, reason="match_result")
        output = StringIO()

        with self.assertRaises(CommandError):
            call_command("audit_data_integrity", stdout=output)

        self.assertIn("result.submission_match_mismatch", output.getvalue())
