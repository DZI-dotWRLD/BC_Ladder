from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core import mail
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    AvailabilitySlot,
    InviteCode,
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
    cancel_availability,
    create_match_suggestion,
    find_opponent_suggestions,
    request_membership_change,
    save_availability,
)


class LegacyTeamBackfillMigrationTests(TransactionTestCase):
    migrate_from = [("ladder", "0020_remove_unused_suggestion_version")]
    migrate_to = [("ladder", "0021_remove_legacy_team_and_challenge")]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        User = old_apps.get_model("auth", "User")
        Team = old_apps.get_model("ladder", "Team")
        PlayerProfile = old_apps.get_model("ladder", "PlayerProfile")
        team = Team.objects.create(name="Legacy migration team", division="mens")
        user = User.objects.create(username="legacy-migration-player")
        self.player_id = PlayerProfile.objects.create(user=user, gender="male", team=team).pk

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        MigrationExecutor(connection).migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
        super().tearDown()

    def test_legacy_team_is_backfilled_before_field_removal(self):
        TeamMembership = self.apps.get_model("ladder", "TeamMembership")

        membership = TeamMembership.objects.get(player_id=self.player_id)

        self.assertEqual(membership.status, "active")
        self.assertIsNone(membership.effective_to)


class PhaseARequestTests(TestCase):
    club_tz = ZoneInfo("America/New_York")

    def setUp(self):
        inviter = get_user_model().objects.create_user(username="phase-a-invite-creator")
        self.invite = InviteCode.objects.create(code="phase-a-invite", created_by=inviter, max_uses=10)

    def create_profile(self, username, gender=PlayerProfile.GENDER_MALE):
        user = get_user_model().objects.create_user(username=username, password="pass")
        return PlayerProfile.objects.create(user=user, gender=gender)

    def make_dt(self, year, month, day, hour, minute=0):
        return datetime(year, month, day, hour, minute, tzinfo=self.club_tz)

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

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("ladder:dashboard"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("ladder:login"), response["Location"])

    def test_login_redirects_to_dashboard(self):
        profile = self.create_profile("login-redirect")

        response = self.client.post(
            reverse("ladder:login"),
            {"username": profile.user.username, "password": "pass"},
        )

        self.assertRedirects(response, reverse("ladder:dashboard"))

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_self_registration_creates_inactive_user_profile_and_sends_verification(self):
        response = self.client.post(
            reverse("ladder:register"),
            {
                "username": "new-register",
                "email": "New.Player@Example.com",
                "invite_code": self.invite.code,
                "gender": PlayerProfile.GENDER_FEMALE,
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )

        user = get_user_model().objects.get(username="new-register")
        self.assertRedirects(response, reverse("ladder:verification_sent"))
        self.assertTrue(PlayerProfile.objects.filter(user=user, gender=PlayerProfile.GENDER_FEMALE).exists())
        self.assertEqual(user.email, "new.player@example.com")
        self.assertFalse(user.is_active)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(len(mail.outbox), 1)

    def test_self_registration_rejects_an_email_already_in_use(self):
        get_user_model().objects.create_user(username="existing", email="player@example.com", password="pass")

        response = self.client.post(
            reverse("ladder:register"),
            {
                "username": "duplicate-email",
                "email": "PLAYER@example.com",
                "invite_code": self.invite.code,
                "gender": PlayerProfile.GENDER_FEMALE,
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "An account with this email address already exists.")
        self.assertFalse(get_user_model().objects.filter(username="duplicate-email").exists())

    def test_self_registration_requires_a_valid_email(self):
        response = self.client.post(
            reverse("ladder:register"),
            {
                "username": "no-email-register",
                "email": "not-an-email",
                "invite_code": self.invite.code,
                "gender": PlayerProfile.GENDER_FEMALE,
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "email", "Enter a valid email address.")
        self.assertFalse(get_user_model().objects.filter(username="no-email-register").exists())

    def test_dashboard_redirects_user_without_profile_to_setup(self):
        user = get_user_model().objects.create_user(username="needs-profile", password="pass")
        self.client.force_login(user)

        response = self.client.get(reverse("ladder:dashboard"))

        self.assertRedirects(response, reverse("ladder:profile_setup"))

    def test_profile_setup_creates_profile_for_logged_in_user(self):
        user = get_user_model().objects.create_user(username="new-player", password="pass")
        self.client.force_login(user)

        response = self.client.post(
            reverse("ladder:profile_setup"),
            {"gender": PlayerProfile.GENDER_MALE},
        )

        self.assertRedirects(response, reverse("ladder:dashboard"))
        self.assertTrue(PlayerProfile.objects.filter(user=user, gender=PlayerProfile.GENDER_MALE).exists())

    def test_authenticated_dashboard_renders_player_state(self):
        team, players = self.create_team_with_members("dashboard", 1)
        self.client.force_login(players[0].user)

        response = self.client.get(reverse("ladder:dashboard"))

        self.assertContains(response, "Dashboard")
        self.assertContains(response, "Start here")
        self.assertContains(response, team.name)
        self.assertContains(response, 'class="dashboard-match-board"')
        self.assertContains(response, 'data-fragment="dashboard-overview"')
        self.assertContains(response, 'data-compose-url="/team/"')
        self.assertContains(response, "dashboard-court-bw.jpg")
        self.assertNotContains(response, "dashboard-section-nav")
        self.assertContains(response, 'id="team"')
        self.assertContains(response, "Plan a match")
        self.assertContains(response, 'aria-label="Quick access"')
        self.assertContains(response, 'aria-label="Play shortcuts"')
        self.assertContains(response, 'aria-label="Ladder shortcuts"')
        self.assertContains(response, f'href="{reverse("ladder:ladder", args=["womens"])}"')
        self.assertNotContains(response, "tennis-net-cal-gao.jpg")

    def test_account_fields_keep_help_and_error_descriptions_connected(self):
        response = self.client.get(reverse("ladder:register"))
        self.assertContains(response, 'id="id_username_helptext"')
        self.assertContains(response, 'aria-describedby="id_username_helptext"')
        invalid_response = self.client.post(reverse("ladder:register"), {"username": ""})
        self.assertContains(invalid_response, 'id="id_username_error"')
        self.assertContains(invalid_response, 'aria-invalid="true"')

    def test_shared_shell_supports_skip_navigation_and_marks_current_page(self):
        _team, players = self.create_team_with_members("shell-navigation", 1)
        self.client.force_login(players[0].user)

        dashboard_response = self.client.get(reverse("ladder:dashboard"))
        team_response = self.client.get(reverse("ladder:team"))

        self.assertContains(dashboard_response, 'class="skip-link" href="#main-content"')
        self.assertContains(dashboard_response, 'id="main-content" tabindex="-1"')
        self.assertContains(dashboard_response, "<summary>Menu</summary>", html=True)
        self.assertContains(
            dashboard_response,
            f'href="{reverse("ladder:dashboard")}" aria-current="page"',
        )
        self.assertNotContains(
            dashboard_response,
            f'href="{reverse("ladder:team")}" aria-current="page"',
        )
        self.assertContains(
            team_response,
            f'href="{reverse("ladder:dashboard")}" aria-current="page"',
        )

    def test_dashboard_setup_checklist_shows_pending_join_request(self):
        profile = self.create_profile("dashboard-pending")
        team = Team.objects.create(name="Dashboard Pending Team", division=Team.DIVISION_MENS)
        request_membership_change(profile.user, profile, team, "request_join")
        self.client.force_login(profile.user)

        response = self.client.get(reverse("ladder:dashboard"))

        self.assertContains(response, "Team request pending")
        self.assertContains(response, "Dashboard Pending Team")
        self.assertContains(response, "Add at least one active window.")

    def test_team_join_is_post_only_scoped_to_player_division_and_pending(self):
        profile = self.create_profile("joiner")
        mens_team = Team.objects.create(name="Join Team", division=Team.DIVISION_MENS)
        self.client.force_login(profile.user)

        get_response = self.client.get(reverse("ladder:join_team"))
        post_response = self.client.post(reverse("ladder:join_team"), {"team": mens_team.id})

        profile.refresh_from_db()
        self.assertEqual(get_response.status_code, 302)
        self.assertEqual(post_response.status_code, 302)
        self.assertIsNone(profile.active_team)
        self.assertTrue(
            TeamMembership.objects.filter(
                player=profile,
                team=mens_team,
                status=TeamMembership.STATUS_JOIN_REQUESTED,
            ).exists()
        )

    def test_team_page_shows_pending_join_request_and_blocks_second_request(self):
        profile = self.create_profile("pending-view")
        requested_team = Team.objects.create(name="Requested Team", division=Team.DIVISION_MENS)
        other_team = Team.objects.create(name="Other Requested Team", division=Team.DIVISION_MENS)
        request_membership_change(profile.user, profile, requested_team, "request_join")
        self.client.force_login(profile.user)

        page_response = self.client.get(reverse("ladder:team"))
        post_response = self.client.post(reverse("ladder:join_team"), {"team": other_team.id})

        self.assertContains(page_response, "Requested Team")
        self.assertContains(page_response, "pending admin review")
        self.assertEqual(post_response.status_code, 302)
        self.assertFalse(TeamMembership.objects.filter(player=profile, team=other_team).exists())

    def test_pending_join_request_can_be_cancelled_by_player_post_only(self):
        profile = self.create_profile("pending-cancel")
        requested_team = Team.objects.create(name="Cancel Requested Team", division=Team.DIVISION_MENS)
        request = request_membership_change(profile.user, profile, requested_team, "request_join")
        self.client.force_login(profile.user)

        get_response = self.client.get(reverse("ladder:cancel_join_request"))
        post_response = self.client.post(reverse("ladder:cancel_join_request"))

        request.refresh_from_db()
        profile.refresh_from_db()
        self.assertEqual(get_response.status_code, 302)
        self.assertEqual(post_response.status_code, 302)
        self.assertEqual(request.status, TeamMembership.STATUS_INACTIVE)
        self.assertEqual(request.resolution_note, "Cancelled by player.")
        self.assertIsNone(profile.active_team)

    def test_team_page_shows_capacity_and_full_state(self):
        team, players = self.create_team_with_members("capacity-page", 3)
        self.client.force_login(players[0].user)

        response = self.client.get(reverse("ladder:team"))

        self.assertContains(response, "3/3 members")
        self.assertContains(response, "All team places are filled.")

    def test_team_join_rejects_wrong_division_and_existing_active_team(self):
        womens_profile = self.create_profile("wrong-division", gender=PlayerProfile.GENDER_FEMALE)
        mens_team = Team.objects.create(name="Mens Only", division=Team.DIVISION_MENS)
        self.client.force_login(womens_profile.user)

        wrong_division_response = self.client.post(reverse("ladder:join_team"), {"team": mens_team.id})

        active_profile = self.create_profile("already-active")
        current_team = Team.objects.create(name="Current Team", division=Team.DIVISION_MENS)
        other_team = Team.objects.create(name="Another Team", division=Team.DIVISION_MENS)
        request_membership_change(active_profile.user, active_profile, current_team, "join")
        self.client.force_login(active_profile.user)
        active_response = self.client.post(reverse("ladder:join_team"), {"team": other_team.id})

        self.assertEqual(wrong_division_response.status_code, 302)
        self.assertFalse(TeamMembership.objects.filter(player=womens_profile, team=mens_team).exists())
        self.assertEqual(active_response.status_code, 302)
        self.assertFalse(TeamMembership.objects.filter(player=active_profile, team=other_team).exists())

    def test_team_create_is_post_only_and_adds_creator_as_first_member(self):
        profile = self.create_profile("team-creator")
        self.client.force_login(profile.user)

        get_response = self.client.get(reverse("ladder:create_team"))
        post_response = self.client.post(reverse("ladder:create_team"), {"name": "  Creator Club  "})

        profile.refresh_from_db()
        team = Team.objects.get(name="Creator Club")
        self.assertEqual(get_response.status_code, 302)
        self.assertEqual(post_response.status_code, 302)
        self.assertEqual(team.division, Team.DIVISION_MENS)
        self.assertEqual(profile.active_team, team)
        self.assertTrue(TeamMembership.objects.filter(player=profile, team=team, status=TeamMembership.STATUS_ACTIVE).exists())
        self.assertTrue(LadderStanding.objects.filter(team=team).exists())

    def test_womens_player_created_team_uses_womens_division(self):
        profile = self.create_profile("womens-team-creator", gender=PlayerProfile.GENDER_FEMALE)
        self.client.force_login(profile.user)

        response = self.client.post(reverse("ladder:create_team"), {"name": "Womens Creator Club"})

        team = Team.objects.get(name="Womens Creator Club")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(team.division, Team.DIVISION_WOMENS)

    def test_team_create_requires_profile_and_no_active_team(self):
        user = get_user_model().objects.create_user(username="create-needs-profile", password="pass")
        self.client.force_login(user)

        no_profile_response = self.client.post(reverse("ladder:create_team"), {"name": "No Profile Team"})

        profile = self.create_profile("already-on-team")
        team = Team.objects.create(name="Already Team", division=Team.DIVISION_MENS)
        request_membership_change(profile.user, profile, team, "join")
        self.client.force_login(profile.user)
        existing_team_response = self.client.post(reverse("ladder:create_team"), {"name": "Should Not Exist"})

        self.assertEqual(no_profile_response.status_code, 302)
        self.assertIn(reverse("ladder:profile_setup"), no_profile_response["Location"])
        self.assertEqual(existing_team_response.status_code, 302)
        self.assertFalse(Team.objects.filter(name="No Profile Team").exists())
        self.assertFalse(Team.objects.filter(name="Should Not Exist").exists())

    def test_team_create_rejects_duplicate_or_blank_name_cleanly(self):
        profile = self.create_profile("duplicate-request-player")
        Team.objects.create(name="Existing Team", division=Team.DIVISION_MENS)
        self.client.force_login(profile.user)

        duplicate_response = self.client.post(reverse("ladder:create_team"), {"name": "existing team"})
        blank_response = self.client.post(reverse("ladder:create_team"), {"name": "   "})

        self.assertEqual(duplicate_response.status_code, 302)
        self.assertEqual(blank_response.status_code, 302)
        self.assertIsNone(PlayerProfile.objects.get(pk=profile.pk).active_team)
        self.assertEqual(Team.objects.filter(name__iexact="existing team").count(), 1)

    def test_availability_create_and_cancel_are_owner_scoped(self):
        team, players = self.create_team_with_members("availability-page", 1)
        other = self.create_profile("other")
        self.client.force_login(players[0].user)

        create_response = self.client.post(
            reverse("ladder:availability"),
            {"starts_at": "2026-07-27T18:00", "ends_at": "2026-07-27T20:00"},
        )
        slot = AvailabilitySlot.objects.get(player=players[0])
        cancel_response = self.client.post(reverse("ladder:cancel_availability", args=[slot.id]))

        save_availability(other.user, self.make_dt(2026, 7, 28, 18), self.make_dt(2026, 7, 28, 20))
        other_slot = AvailabilitySlot.objects.get(player=other)
        blocked_response = self.client.post(reverse("ladder:cancel_availability", args=[other_slot.id]))
        other_slot.refresh_from_db()

        self.assertEqual(create_response.status_code, 302)
        self.assertEqual(cancel_response.status_code, 302)
        slot.refresh_from_db()
        self.assertEqual(slot.status, AvailabilitySlot.STATUS_CANCELLED)
        self.assertEqual(blocked_response.status_code, 404)
        self.assertEqual(other_slot.status, AvailabilitySlot.STATUS_ACTIVE)

    def test_cancelled_availability_is_hidden_from_player_window_list(self):
        _team, players = self.create_team_with_members("availability-hidden", 1)
        self.client.force_login(players[0].user)
        cancelled_slot = save_availability(players[0].user, self.make_dt(2026, 7, 29, 18), self.make_dt(2026, 7, 29, 20))
        save_availability(players[0].user, self.make_dt(2026, 7, 30, 18), self.make_dt(2026, 7, 30, 20))
        cancel_availability(players[0].user, cancelled_slot)

        response = self.client.get(reverse("ladder:availability"))

        self.assertContains(response, "Your active times")
        self.assertContains(response, "Jul 30")
        self.assertNotContains(response, "Jul 29")

    def test_suggestion_create_and_dual_acceptance_flow(self):
        team_a, team_a_players = self.create_team_with_members("phase-a", 2)
        team_b, team_b_players = self.create_team_with_members("phase-b", 2)
        starts_at = timezone.now() + timedelta(days=7)
        starts_at = starts_at.replace(hour=18, minute=0, second=0, microsecond=0)
        ends_at = starts_at + timedelta(hours=2)
        for player in team_a_players + team_b_players:
            save_availability(player.user, starts_at, ends_at)

        self.client.force_login(team_a_players[0].user)
        candidate = self.client.get(reverse("ladder:suggestions"), {"discover": "1"}).context["options"][0]["candidate_token"]
        create_response = self.client.post(reverse("ladder:create_suggestion"), {"candidate": candidate})
        suggestion = MatchSuggestion.objects.get()
        first_accept = self.client.post(
            reverse("ladder:accept_suggestion", args=[suggestion.id]),
            {},
        )
        self.client.force_login(team_b_players[0].user)
        second_accept = self.client.post(
            reverse("ladder:accept_suggestion", args=[suggestion.id]),
            {},
        )

        self.assertEqual(create_response.status_code, 302)
        self.assertEqual(first_accept.status_code, 302)
        self.assertEqual(second_accept.status_code, 302)
        self.assertEqual(Match.objects.count(), 1)
        self.assertEqual(MatchReservation.objects.count(), 4)

    def test_suggestions_explain_missing_team_and_missing_members(self):
        no_team_profile = self.create_profile("suggestions-no-team")
        self.client.force_login(no_team_profile.user)

        no_team_response = self.client.get(reverse("ladder:suggestions"))

        one_member_team, one_member_players = self.create_team_with_members("suggestions-one-member", 1)
        self.client.force_login(one_member_players[0].user)
        one_member_response = self.client.get(reverse("ladder:suggestions"), {"discover": "1"})

        self.assertContains(no_team_response, "Join or create a team first")
        self.assertContains(no_team_response, "Suggestions require an active team")
        self.assertContains(one_member_response, "Your team needs two active members")
        self.assertContains(one_member_response, one_member_team.name)

    def test_suggestions_explain_missing_availability_and_missing_shared_lineup(self):
        _team_without_availability, no_availability_players = self.create_team_with_members("suggestions-no-availability", 2)
        self.client.force_login(no_availability_players[0].user)

        no_availability_response = self.client.get(reverse("ladder:suggestions"), {"discover": "1"})

        no_shared_team, no_shared_players = self.create_team_with_members("suggestions-no-shared", 2)
        first_starts_at = timezone.now() + timedelta(days=7)
        first_starts_at = first_starts_at.replace(hour=18, minute=0, second=0, microsecond=0)
        second_starts_at = first_starts_at + timedelta(days=1)
        save_availability(no_shared_players[0].user, first_starts_at, first_starts_at + timedelta(hours=2))
        save_availability(no_shared_players[1].user, second_starts_at, second_starts_at + timedelta(hours=2))
        self.client.force_login(no_shared_players[0].user)
        no_shared_response = self.client.get(reverse("ladder:suggestions"), {"discover": "1"})

        self.assertContains(no_availability_response, "No compatible suggestions yet.")
        self.assertNotContains(no_availability_response, ">Add active availability<")
        self.assertContains(no_availability_response, "At least two teammates need overlapping")
        self.assertContains(no_shared_response, "No shared team availability")
        self.assertContains(no_shared_response, no_shared_team.name)

    def test_suggestions_explain_missing_opponents_and_render_valid_option_details(self):
        solo_team, solo_players = self.create_team_with_members("suggestions-solo", 2)
        starts_at = timezone.now() + timedelta(days=7)
        starts_at = starts_at.replace(hour=18, minute=0, second=0, microsecond=0)
        ends_at = starts_at + timedelta(hours=2)
        for player in solo_players:
            save_availability(player.user, starts_at, ends_at)
        self.client.force_login(solo_players[0].user)

        no_opponents_response = self.client.get(reverse("ladder:suggestions"), {"discover": "1"})

        opponent_team, opponent_players = self.create_team_with_members("suggestions-opponent", 2)
        for player in opponent_players:
            save_availability(player.user, starts_at, ends_at)
        valid_response = self.client.get(reverse("ladder:suggestions"), {"discover": "1"})

        self.assertContains(no_opponents_response, "No opponent teams in this ladder yet")
        self.assertContains(valid_response, solo_team.name)
        self.assertContains(valid_response, opponent_team.name)
        self.assertContains(valid_response, "Your lineup")
        self.assertContains(valid_response, "Opponent lineup")
        self.assertContains(valid_response, "Request match")

    def test_current_suggestion_accept_button_only_shows_for_selected_lineup_players(self):
        team_a, team_a_players = self.create_team_with_members("suggestions-selected-a", 3)
        _team_b, team_b_players = self.create_team_with_members("suggestions-selected-b", 2)
        starts_at = (timezone.now() + timedelta(days=7)).replace(hour=18, minute=0, second=0, microsecond=0)
        ends_at = starts_at + timedelta(hours=2)
        for player in team_a_players[:2] + team_b_players:
            save_availability(player.user, starts_at, ends_at)
        option = find_opponent_suggestions(team_a, (starts_at, ends_at))[0]
        suggestion = create_match_suggestion(option)

        self.client.force_login(team_a_players[2].user)
        third_member_response = self.client.get(reverse("ladder:suggestions"))
        self.client.force_login(team_a_players[0].user)
        selected_player_response = self.client.get(reverse("ladder:suggestions"))

        self.assertContains(third_member_response, "Only the selected lineup players can accept this suggestion.")
        self.assertNotContains(third_member_response, f'action="{reverse("ladder:accept_suggestion", args=[suggestion.id])}"')
        self.assertContains(selected_player_response, f'action="{reverse("ladder:accept_suggestion", args=[suggestion.id])}"')

    def test_match_detail_blocks_unrelated_player_and_score_submission_updates_result(self):
        team_a, team_a_players = self.create_team_with_members("score-page-a", 2)
        team_b, team_b_players = self.create_team_with_members("score-page-b", 2)
        _, outside_players = self.create_team_with_members("score-page-out", 1)
        LadderStanding.objects.create(team=team_a, position=1)
        LadderStanding.objects.create(team=team_b, position=2)
        match = Match.objects.create(
            team_a=team_a,
            team_b=team_b,
            scheduled_week_start_date=date(2026, 7, 27),
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18, 0),
            scheduled_end_time=time(20, 0),
            scheduled_starts_at=self.make_dt(2026, 7, 27, 18),
            scheduled_ends_at=self.make_dt(2026, 7, 27, 20),
        )
        self.add_match_participants(match, team_a_players, team_b_players)

        self.client.force_login(outside_players[0].user)
        denied = self.client.get(reverse("ladder:match_detail", args=[match.id]))
        self.client.force_login(team_a_players[0].user)
        scorecard_response = self.client.get(reverse("ladder:match_detail", args=[match.id]))
        self.assertContains(scorecard_response, 'id="score-submission"')
        matches_response = self.client.get(reverse("ladder:matches"))
        self.assertContains(matches_response, 'id="submit-score"')
        self.assertContains(matches_response, f'href="{reverse("ladder:match_detail", args=[match.id])}#score-submission"')
        first = self.client.post(
            reverse("ladder:submit_score", args=[match.id]),
            {"set1_team_a": 6, "set1_team_b": 4, "set2_team_a": 6, "set2_team_b": 4},
        )
        self.client.force_login(team_b_players[0].user)
        second = self.client.post(
            reverse("ladder:submit_score", args=[match.id]),
            {"set1_team_a": 6, "set1_team_b": 4, "set2_team_a": 6, "set2_team_b": 4},
        )

        match.refresh_from_db()
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(match.status, Match.STATUS_COMPLETED)
        self.assertEqual(PointLedger.objects.filter(match=match).count(), 2)

    def test_selected_participant_confirms_and_performs_match_cancellation(self):
        team_a, team_a_players = self.create_team_with_members("cancel-request-a", 3)
        team_b, team_b_players = self.create_team_with_members("cancel-request-b", 2)
        outsider = self.create_profile("cancel-request-outsider")
        match = Match.objects.create(
            team_a=team_a,
            team_b=team_b,
            scheduled_week_start_date=date(2027, 9, 6),
            scheduled_day_of_week=AvailabilitySlot.DayOfWeek.MONDAY,
            scheduled_start_time=time(18, 0),
            scheduled_end_time=time(20, 0),
            scheduled_starts_at=self.make_dt(2027, 9, 6, 18),
            scheduled_ends_at=self.make_dt(2027, 9, 6, 20),
        )
        self.add_match_participants(match, team_a_players[:2], team_b_players)
        cancel_url = reverse("ladder:cancel_match", args=[match.id])

        self.client.force_login(team_a_players[2].user)
        teammate_detail = self.client.get(reverse("ladder:match_detail", args=[match.id]))
        teammate_cancel = self.client.get(cancel_url)
        self.assertEqual(teammate_detail.status_code, 200)
        self.assertNotContains(teammate_detail, cancel_url)
        self.assertNotContains(teammate_detail, "Submit Score")
        self.assertEqual(teammate_cancel.status_code, 404)

        self.client.force_login(outsider.user)
        self.assertEqual(self.client.get(reverse("ladder:match_detail", args=[match.id])).status_code, 404)

        selected_player = team_a_players[0]
        membership = TeamMembership.objects.get(player=selected_player, status=TeamMembership.STATUS_ACTIVE)
        membership.status = TeamMembership.STATUS_INACTIVE
        membership.effective_to = timezone.now()
        membership.save(update_fields=["status", "effective_to", "updated_at"])
        self.client.force_login(selected_player.user)
        historical_detail = self.client.get(reverse("ladder:match_detail", args=[match.id]))
        confirmation = self.client.get(cancel_url)
        match.refresh_from_db()
        self.assertEqual(historical_detail.status_code, 200)
        self.assertContains(historical_detail, cancel_url)
        self.assertEqual(confirmation.status_code, 200)
        self.assertContains(confirmation, "The cancellation cannot be undone")
        self.assertEqual(match.status, Match.STATUS_SCHEDULED)

        response = self.client.post(cancel_url)

        match.refresh_from_db()
        self.assertRedirects(response, reverse("ladder:match_detail", args=[match.id]))
        self.assertEqual(match.status, Match.STATUS_CANCELLED)
        self.assertEqual(WorkflowEvent.objects.get(event_type=WorkflowEvent.EventType.MATCH_CANCELLED).actor, selected_player.user)

    def test_ladder_page_is_authenticated_and_scoped_by_division(self):
        mens_team = Team.objects.create(name="Mens Ladder Team", division=Team.DIVISION_MENS)
        womens_team = Team.objects.create(name="Womens Ladder Team", division=Team.DIVISION_WOMENS)
        LadderStanding.objects.create(team=mens_team, position=1, points=3)
        LadderStanding.objects.create(team=womens_team, position=1, points=9)
        profile = self.create_profile("ladder-viewer")
        self.client.force_login(profile.user)

        response = self.client.get(reverse("ladder:ladder", args=[Team.DIVISION_MENS]))

        self.assertContains(response, mens_team.name)
        self.assertNotContains(response, womens_team.name)
