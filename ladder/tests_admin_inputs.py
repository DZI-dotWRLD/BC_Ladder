from datetime import timedelta

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from . import models
from .services import create_match_suggestion, find_opponent_suggestions, request_membership_change, save_availability


class AdminInputBoundaryTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_superuser("boundary-admin", password="pass")
        self.request = RequestFactory().get("/admin/")
        self.request.user = self.staff

    def test_workflow_admins_forbid_direct_writes_even_for_superuser(self):
        names = (
            "TeamMembership",
            "LadderStanding",
            "AvailabilitySlot",
            "Challenge",
            "MatchSuggestion",
            "SuggestionParticipant",
            "SuggestionAcceptance",
            "Match",
            "MatchParticipant",
            "MatchReservation",
            "MatchResultSubmission",
            "MatchResultSet",
            "ConfirmedMatchResult",
            "PointLedger",
            "ScoreCorrectionAudit",
            "AdminNotification",
            "WorkflowEvent",
            "WorkflowEventRecipient",
            "EmailNotificationDelivery",
        )
        for name in names:
            with self.subTest(model=name):
                model_admin = admin.site._registry[getattr(models, name)]
                self.assertFalse(model_admin.has_add_permission(self.request))
                self.assertFalse(model_admin.has_change_permission(self.request))
                self.assertFalse(model_admin.has_delete_permission(self.request))
                self.assertTrue(model_admin.has_view_permission(self.request))

    def test_service_actions_remain_available(self):
        for model, action in (
            (models.TeamMembership, "approve_join_requests"),
            (models.Match, "cancel_selected_matches"),
            (models.MatchResultSubmission, "use_selected_submission_as_official_score"),
        ):
            self.assertIn(action, admin.site._registry[model].get_actions(self.request))

    def test_profile_admin_cannot_reassign_membership(self):
        self.assertIn("team", admin.site._registry[models.PlayerProfile].get_readonly_fields(self.request))
        self.assertEqual(admin.site._registry[models.Team].inlines, ())

    def test_staff_can_create_and_revoke_invites_but_not_edit_usage_or_delete(self):
        model_admin = admin.site._registry[models.InviteCode]
        invite = models.InviteCode.objects.create(code="admin-boundary", created_by=self.staff)
        self.assertTrue(model_admin.has_add_permission(self.request))
        self.assertTrue(model_admin.has_change_permission(self.request, invite))
        self.assertFalse(model_admin.has_delete_permission(self.request, invite))
        self.assertIn("uses", model_admin.get_readonly_fields(self.request, invite))

        self.client.force_login(self.staff)
        self.client.post(
            reverse("admin:ladder_invitecode_changelist"),
            {"action": "revoke_invite_codes", "_selected_action": [invite.pk]},
        )

        invite.refresh_from_db()
        self.assertIsNotNone(invite.revoked_at)

    def test_view_only_staff_cannot_run_operational_actions(self):
        user = get_user_model().objects.create_user("view-only-admin", password="pass", is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename="view_teammembership"))
        self.request.user = user
        model_admin = admin.site._registry[models.TeamMembership]
        self.assertNotIn("approve_join_requests", model_admin.get_actions(self.request))
        team = models.Team.objects.create(name="Pending boundary", division=models.Team.DIVISION_MENS)
        player_user = get_user_model().objects.create_user("pending-boundary", password="pass")
        player = models.PlayerProfile.objects.create(user=player_user, gender=models.PlayerProfile.GENDER_MALE)
        membership = models.TeamMembership.objects.create(
            player=player, team=team, status=models.TeamMembership.STATUS_JOIN_REQUESTED, effective_from=timezone.now()
        )
        self.client.force_login(user)
        self.client.post(
            reverse("admin:ladder_teammembership_changelist"),
            {
                "action": "approve_join_requests",
                "_selected_action": [membership.pk],
            },
        )
        membership.refresh_from_db()
        self.assertEqual(membership.status, models.TeamMembership.STATUS_JOIN_REQUESTED)

    def test_superuser_service_action_resolves_membership_with_event(self):
        team = models.Team.objects.create(name="Action boundary", division=models.Team.DIVISION_MENS)
        user = get_user_model().objects.create_user("action-boundary", password="pass")
        player = models.PlayerProfile.objects.create(user=user, gender=models.PlayerProfile.GENDER_MALE)
        membership = models.TeamMembership.objects.create(
            player=player, team=team, status=models.TeamMembership.STATUS_JOIN_REQUESTED, effective_from=timezone.now()
        )
        self.client.force_login(self.staff)
        self.client.post(
            reverse("admin:ladder_teammembership_changelist"),
            {
                "action": "approve_join_requests",
                "_selected_action": [membership.pk],
            },
        )
        membership.refresh_from_db()
        self.assertEqual(membership.status, models.TeamMembership.STATUS_ACTIVE)
        self.assertTrue(models.WorkflowEvent.objects.filter(membership=membership).exists())

    def make_suggestion(self):
        players = []
        teams = []
        start = timezone.now() + timedelta(days=7)
        end = start + timedelta(hours=1)
        for side in ("a", "b"):
            team = models.Team.objects.create(name=f"Boundary {side}", division=models.Team.DIVISION_MENS)
            teams.append(team)
            for index in range(2):
                user = get_user_model().objects.create_user(f"boundary-{side}-{index}", password="pass")
                profile = models.PlayerProfile.objects.create(user=user, gender=models.PlayerProfile.GENDER_MALE)
                request_membership_change(user, profile, team, "join")
                save_availability(user, start, end)
                players.append(profile)
        suggestion = create_match_suggestion(find_opponent_suggestions(teams[0], (start, end))[0], actor=players[0].user)
        return suggestion, players

    def test_unrelated_acceptance_and_availability_have_scoped_lookup(self):
        suggestion, players = self.make_suggestion()
        self.client.force_login(self.staff)
        models.PlayerProfile.objects.create(user=self.staff, gender=models.PlayerProfile.GENDER_MALE)
        response = self.client.post(reverse("ladder:accept_suggestion", args=[suggestion.pk]), {})
        self.assertEqual(response.status_code, 404)
        slot = players[0].availability_slots.first()
        response = self.client.post(reverse("ladder:cancel_availability", args=[slot.pk]))
        self.assertEqual(response.status_code, 404)
        slot.refresh_from_db()
        self.assertEqual(slot.status, models.AvailabilitySlot.STATUS_ACTIVE)

    def test_forged_admin_change_post_cannot_mutate_match(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse("admin:ladder_match_add"), {"status": models.Match.STATUS_CANCELLED})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(models.Match.objects.count(), 0)
