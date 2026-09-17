from datetime import date, time

from django.core.exceptions import ValidationError
from django.test import TestCase

from .models import (
    AdminNotification,
    AvailabilitySlot,
    Match,
    MatchResultSubmission,
    Team,
)
from .services import (
    create_admin_notification_for_conflict,
    get_match_status,
    get_match_winner_and_loser,
)


class MatchResultSubmissionTests(TestCase):
    def setUp(self):
        self.week_start_date = date(2026, 6, 1)
        self.team_a = Team.objects.create(
            name="Team A",
            division=Team.DIVISION_MENS,
        )
        self.team_b = Team.objects.create(
            name="Team B",
            division=Team.DIVISION_MENS,
        )
        self.outside_team = Team.objects.create(
            name="Outside Team",
            division=Team.DIVISION_MENS,
        )

        self.match = Match.objects.create(
            team_a=self.team_a,
            team_b=self.team_b,
            scheduled_week_start_date=self.week_start_date,
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18, 0),
            scheduled_end_time=time(19, 0),
        )

    def test_result_submission_can_be_created_by_team_a(self):
        submission = MatchResultSubmission(
            match=self.match,
            submitting_team=self.team_a,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )

        submission.full_clean()

    def test_result_submission_can_be_created_by_team_b(self):
        submission = MatchResultSubmission(
            match=self.match,
            submitting_team=self.team_b,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )

        submission.full_clean()

    def test_result_submission_rejects_team_not_in_match(self):
        submission = MatchResultSubmission(
            match=self.match,
            submitting_team=self.outside_team,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )

        with self.assertRaises(ValidationError):
            submission.full_clean()

    def test_same_team_cannot_submit_twice_for_same_match(self):
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_a,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )

        duplicate_submission = MatchResultSubmission(
            match=self.match,
            submitting_team=self.team_a,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )

        with self.assertRaises(ValidationError):
            duplicate_submission.full_clean()


class MatchResultStatusServiceTests(TestCase):
    def setUp(self):
        self.week_start_date = date(2026, 6, 1)
        self.team_a = Team.objects.create(
            name="Team A",
            division=Team.DIVISION_MENS,
        )
        self.team_b = Team.objects.create(
            name="Team B",
            division=Team.DIVISION_MENS,
        )

        self.match = Match.objects.create(
            team_a=self.team_a,
            team_b=self.team_b,
            scheduled_week_start_date=self.week_start_date,
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18, 0),
            scheduled_end_time=time(19, 0),
        )

    def test_match_result_status_waiting_when_less_than_two_submissions(self):
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_a,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )

        status = get_match_status(self.match)

        self.assertEqual(status, "waiting_for_submissions")

    def test_match_result_status_confirmed_when_submissions_match(self):
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_a,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_b,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )

        status = get_match_status(self.match)

        self.assertEqual(status, "confirmed")

    def test_match_result_status_conflict_when_submissions_disagree(self):
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_a,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )

        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_b,
            team_a_sets_won=1,
            team_b_sets_won=2,
        )

        status = get_match_status(self.match)

        self.assertEqual(status, "conflict")

    def test_get_match_winner_and_loser_returns_team_a_when_team_a_wins(self):
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_a,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_b,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )

        result = get_match_winner_and_loser(self.match)

        self.assertEqual(result, [self.team_a, self.team_b])

    def test_get_match_winner_and_loser_returns_team_b_when_team_b_wins(self):
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_a,
            team_a_sets_won=1,
            team_b_sets_won=2,
        )
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_b,
            team_a_sets_won=1,
            team_b_sets_won=2,
        )

        result = get_match_winner_and_loser(self.match)

        self.assertEqual(result, [self.team_b, self.team_a])

    def test_get_match_winner_and_loser_returns_none_when_result_conflicts(self):
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_a,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_b,
            team_a_sets_won=1,
            team_b_sets_won=2,
        )

        winner = get_match_winner_and_loser(self.match)

        self.assertEqual(winner, [])

    def test_get_match_winner__and_loser_returns_none_when_waiting_for_submissions(self):
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_a,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )

        winner = get_match_winner_and_loser(self.match)

        self.assertEqual(winner, [])


class AdminNotificationTests(TestCase):
    def setUp(self):
        self.week_start_date = date(2026, 6, 1)

        self.team_a = Team.objects.create(
            name="Team A",
            division=Team.DIVISION_MENS,
        )
        self.team_b = Team.objects.create(
            name="Team B",
            division=Team.DIVISION_MENS,
        )

        self.match = Match.objects.create(
            team_a=self.team_a,
            team_b=self.team_b,
            scheduled_week_start_date=self.week_start_date,
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18, 0),
            scheduled_end_time=time(19, 0),
        )

    def test_admin_notification_can_be_created_for_match(self):
        notification = AdminNotification(
            match=self.match,
            message="Scores do not match.",
        )

        notification.full_clean()
        notification.save()

        self.assertEqual(notification.match, self.match)

    def test_admin_notification_defaults_to_unresolved(self):
        notification = AdminNotification.objects.create(
            match=self.match,
            message="Scores do not match.",
        )

        self.assertFalse(notification.is_resolved)

    def test_match_can_access_admin_notifications(self):
        notification = AdminNotification.objects.create(
            match=self.match,
            message="Scores do not match.",
        )

        self.assertIn(notification, self.match.admin_notifications.all())

    def test_conflict_notification_is_created_when_result_conflict(self):
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_a,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_b,
            team_a_sets_won=1,
            team_b_sets_won=2,
        )

        notification = create_admin_notification_for_conflict(self.match)

        self.assertIsNotNone(notification)
        self.assertEqual(notification.match, self.match)
        self.assertEqual(AdminNotification.objects.count(), 1)

    def test_conflict_notification_is_not_created_when_result_confirmed(self):
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_a,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_b,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )

        notification = create_admin_notification_for_conflict(self.match)

        self.assertIsNone(notification)
        self.assertEqual(AdminNotification.objects.count(), 0)

    def test_conflict_notification_is_not_created_when_waiting_for_submissions(self):
        MatchResultSubmission.objects.create(
            match=self.match,
            submitting_team=self.team_a,
            team_a_sets_won=2,
            team_b_sets_won=1,
        )

        notification = create_admin_notification_for_conflict(self.match)

        self.assertIsNone(notification)
        self.assertEqual(AdminNotification.objects.count(), 0)
