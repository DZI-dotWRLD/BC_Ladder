from datetime import timedelta

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.utils import timezone

from .admin import PlayerProfileAdmin, TeamAdmin
from .models import Match, MatchSuggestion, PlayerProfile, SuggestionParticipant, Team, TeamMembership


class AdminInvariantTests(TestCase):
    def setUp(self):
        self.request = RequestFactory().get("/admin/")
        self.request.user = get_user_model().objects.create_superuser(
            username="admin-invariants",
            email="admin@example.com",
            password="test-password",
        )
        self.team_admin = TeamAdmin(Team, admin.site)
        self.profile_admin = PlayerProfileAdmin(PlayerProfile, admin.site)
        self.team = Team.objects.create(name="Admin Team", division=Team.DIVISION_MENS)
        self.user = get_user_model().objects.create_user(username="admin-player")
        self.profile = PlayerProfile.objects.create(user=self.user, gender=PlayerProfile.GENDER_MALE)

    def test_team_division_is_readonly_after_any_membership_history(self):
        self.assertNotIn("division", self.team_admin.get_readonly_fields(self.request, self.team))
        TeamMembership.objects.create(
            player=self.profile,
            team=self.team,
            status=TeamMembership.STATUS_INACTIVE,
            effective_from=timezone.now() - timedelta(days=2),
            effective_to=timezone.now() - timedelta(days=1),
        )

        readonly = self.team_admin.get_readonly_fields(self.request, self.team)

        self.assertIn("division", readonly)
        self.assertNotIn("status", readonly)

    def test_team_status_is_readonly_with_active_membership(self):
        TeamMembership.objects.create(
            player=self.profile,
            team=self.team,
            status=TeamMembership.STATUS_ACTIVE,
            effective_from=timezone.now(),
        )

        self.assertIn("status", self.team_admin.get_readonly_fields(self.request, self.team))

    def test_team_status_is_readonly_with_scheduled_match(self):
        opponent = Team.objects.create(name="Admin Opponent", division=Team.DIVISION_MENS)
        starts_at = timezone.now() + timedelta(days=2)
        Match.objects.create(
            team_a=self.team,
            team_b=opponent,
            scheduled_week_start_date=starts_at.date(),
            scheduled_day_of_week="monday",
            scheduled_start_time=starts_at.time(),
            scheduled_end_time=(starts_at + timedelta(hours=1)).time(),
            scheduled_starts_at=starts_at,
            scheduled_ends_at=starts_at + timedelta(hours=1),
        )

        self.assertIn("status", self.team_admin.get_readonly_fields(self.request, self.team))

    def test_profile_gender_is_readonly_after_membership_or_participation(self):
        self.assertNotIn("gender", self.profile_admin.get_readonly_fields(self.request, self.profile))
        TeamMembership.objects.create(
            player=self.profile,
            team=self.team,
            status=TeamMembership.STATUS_INACTIVE,
            effective_from=timezone.now() - timedelta(days=2),
            effective_to=timezone.now() - timedelta(days=1),
        )
        self.assertIn("gender", self.profile_admin.get_readonly_fields(self.request, self.profile))

        participant = PlayerProfile.objects.create(
            user=get_user_model().objects.create_user(username="admin-participant"),
            gender=PlayerProfile.GENDER_MALE,
        )
        opponent = Team.objects.create(name="Participant Opponent", division=Team.DIVISION_MENS)
        starts_at = timezone.now() + timedelta(days=2)
        suggestion = MatchSuggestion.objects.create(
            team_a=self.team,
            team_b=opponent,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1),
            expires_at=starts_at + timedelta(days=1),
        )
        SuggestionParticipant.objects.create(
            suggestion=suggestion,
            team=self.team,
            player=participant,
            side=SuggestionParticipant.SIDE_A,
            lineup_order=1,
        )
        self.assertIn("gender", self.profile_admin.get_readonly_fields(self.request, participant))

    def test_team_and_profile_cannot_be_deleted_in_admin(self):
        self.assertFalse(self.team_admin.has_delete_permission(self.request, self.team))
        self.assertFalse(self.profile_admin.has_delete_permission(self.request, self.profile))
