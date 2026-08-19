from django.contrib import admin
from django.contrib import messages

from .services import DomainError, cancel_match, resolve_membership_request, resolve_score_conflict

from .models import (
    AdminNotification,
    AvailabilitySlot,
    Challenge,
    ConfirmedMatchResult,
    LadderStanding,
    Match,
    MatchParticipant,
    MatchReservation,
    MatchResultSet,
    MatchResultSubmission,
    MatchSuggestion,
    PlayerProfile,
    PointLedger,
    ScoreCorrectionAudit,
    SuggestionAcceptance,
    SuggestionParticipant,
    Team,
    TeamMembership,
    WorkflowEvent,
    WorkflowEventRecipient,
)


class PlayerProfileInline(admin.TabularInline):
    model = PlayerProfile
    extra = 0


class TeamAdmin(admin.ModelAdmin):
    inlines = [PlayerProfileInline]
    list_display = ("name", "division", "status", "created_at")
    list_filter = ("division", "status")


class PlayerProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "gender", "team", "created_at")
    list_select_related = ("user", "team")


class TeamMembershipAdmin(admin.ModelAdmin):
    list_display = (
        "player",
        "team",
        "status",
        "is_pending_join_request",
        "is_pending_removal_request",
        "reviewed_by",
        "resolved_at",
    )
    list_filter = ("status", "team__division", "removal_requested_at")
    list_select_related = ("player__user", "team", "reviewed_by")
    readonly_fields = ("created_at", "updated_at", "effective_from", "effective_to", "reviewed_by", "resolved_at")
    actions = ("approve_join_requests", "reject_join_requests", "approve_removal_requests", "reject_removal_requests")

    @admin.display(boolean=True, description="Join pending")
    def is_pending_join_request(self, obj):
        return obj.status == TeamMembership.STATUS_JOIN_REQUESTED

    @admin.display(boolean=True, description="Removal pending")
    def is_pending_removal_request(self, obj):
        return obj.removal_requested_at is not None and obj.status == TeamMembership.STATUS_ACTIVE

    @admin.action(description="Approve selected join requests")
    def approve_join_requests(self, request, queryset):
        completed = 0
        for membership in queryset.filter(status=TeamMembership.STATUS_JOIN_REQUESTED):
            try:
                resolve_membership_request(request.user, membership, "approve")
                completed += 1
            except DomainError as error:
                self.message_user(request, str(error), level=messages.ERROR)
        self.message_user(request, f"Approved {completed} join request(s).")

    @admin.action(description="Reject selected join requests")
    def reject_join_requests(self, request, queryset):
        completed = 0
        for membership in queryset.filter(status=TeamMembership.STATUS_JOIN_REQUESTED):
            try:
                resolve_membership_request(request.user, membership, "reject")
                completed += 1
            except DomainError as error:
                self.message_user(request, str(error), level=messages.ERROR)
        self.message_user(request, f"Rejected {completed} join request(s).")

    @admin.action(description="Approve selected removal requests")
    def approve_removal_requests(self, request, queryset):
        completed = 0
        for membership in queryset.filter(removal_requested_at__isnull=False):
            try:
                resolve_membership_request(request.user, membership, "approve")
                completed += 1
            except DomainError as error:
                self.message_user(request, str(error), level=messages.ERROR)
        self.message_user(request, f"Approved {completed} removal request(s).")

    @admin.action(description="Reject selected removal requests")
    def reject_removal_requests(self, request, queryset):
        completed = 0
        for membership in queryset.filter(removal_requested_at__isnull=False):
            try:
                resolve_membership_request(request.user, membership, "reject")
                completed += 1
            except DomainError as error:
                self.message_user(request, str(error), level=messages.ERROR)
        self.message_user(request, f"Rejected {completed} removal request(s).")

    def has_delete_permission(self, request, obj=None):
        return False


class LadderStandingAdmin(admin.ModelAdmin):
    list_display = ("position", "team", "points", "wins", "losses", "matches_played", "updated_at")
    ordering = ("position",)
    list_select_related = ("team",)
    readonly_fields = ("matches_played", "wins", "losses", "points", "updated_at")

    def has_delete_permission(self, request, obj=None):
        return False


class AvailabilitySlotAdmin(admin.ModelAdmin):
    list_display = (
        "player",
        "week_start_date",
        "day_of_week",
        "start_time",
        "end_time",
        "starts_at",
        "ends_at",
        "status",
        "created_at",
    )
    list_filter = ("status", "week_start_date", "day_of_week")
    list_select_related = ("player__user",)
    ordering = ("starts_at", "week_start_date", "day_of_week", "start_time")


class ChallengeAdmin(admin.ModelAdmin):
    list_display = (
        "challenger_team",
        "opponent_team",
        "proposed_week_start_date",
        "proposed_day_of_week",
        "proposed_start_time",
        "proposed_end_time",
        "status",
    )
    filter_horizontal = ("challenger_players", "opponent_players")
    list_filter = ("status", "proposed_week_start_date", "proposed_day_of_week")
    ordering = ("proposed_week_start_date", "proposed_day_of_week", "proposed_start_time")


class MatchAdmin(admin.ModelAdmin):
    list_display = (
        "team_a",
        "team_b",
        "scheduled_week_start_date",
        "scheduled_day_of_week",
        "scheduled_start_time",
        "scheduled_end_time",
        "scheduled_starts_at",
        "scheduled_ends_at",
        "status",
    )
    list_filter = ("status", "scheduled_week_start_date", "scheduled_day_of_week")
    list_select_related = ("team_a", "team_b", "source_suggestion")
    readonly_fields = ("status", "source_suggestion", "created_at", "updated_at")
    ordering = ("scheduled_starts_at", "scheduled_week_start_date", "scheduled_day_of_week", "scheduled_start_time")
    actions = ("cancel_selected_matches",)

    @admin.action(description="Cancel selected scheduled matches")
    def cancel_selected_matches(self, request, queryset):
        completed = 0
        for match in queryset:
            try:
                cancel_match(request.user, match)
                completed += 1
            except DomainError as error:
                self.message_user(request, str(error), level=messages.ERROR)
        self.message_user(request, f"Cancelled {completed} match(es).")

    def has_delete_permission(self, request, obj=None):
        return False


class MatchResultSubmissionAdmin(admin.ModelAdmin):
    list_display = (
        "match",
        "submitting_team",
        "submitting_user",
        "team_a_sets_won",
        "team_b_sets_won",
        "created_at",
    )
    list_filter = ("submitting_team", "created_at")
    list_select_related = ("match", "submitting_team", "submitting_user")
    readonly_fields = ("match", "submitting_team", "submitting_user", "team_a_sets_won", "team_b_sets_won", "created_at", "updated_at")
    actions = ("use_selected_submission_as_official_score",)

    @admin.action(description="Use selected submission as official score")
    def use_selected_submission_as_official_score(self, request, queryset):
        completed = 0
        for submission in queryset.select_related("match"):
            notification = submission.match.admin_notifications.filter(
                notification_type=AdminNotification.TYPE_SCORE_CONFLICT,
                is_resolved=False,
            ).first()
            if notification is None:
                self.message_user(request, f"No unresolved score conflict for {submission.match}.", level=messages.ERROR)
                continue
            try:
                resolve_score_conflict(request.user, notification, submission)
                completed += 1
            except DomainError as error:
                self.message_user(request, str(error), level=messages.ERROR)
        self.message_user(request, f"Resolved {completed} score conflict(s).")


class AdminNotificationAdmin(admin.ModelAdmin):
    list_display = (
        "match",
        "notification_type",
        "message",
        "is_resolved",
        "created_at",
        "updated_at",
    )
    list_filter = ("notification_type", "is_resolved", "created_at")
    list_select_related = ("match",)
    ordering = ("is_resolved", "-created_at")


class MatchSuggestionAdmin(admin.ModelAdmin):
    list_display = ("team_a", "team_b", "starts_at", "ends_at", "status", "version", "expires_at")
    list_filter = ("status", "team_a__division")
    list_select_related = ("team_a", "team_b")
    readonly_fields = ("status", "version", "created_at", "updated_at")


class SuggestionParticipantAdmin(admin.ModelAdmin):
    list_display = ("suggestion", "side", "lineup_order", "team", "player")
    list_select_related = ("suggestion", "team", "player__user")


class SuggestionAcceptanceAdmin(admin.ModelAdmin):
    list_display = ("suggestion", "team", "accepted_by", "accepted_version", "created_at")
    list_select_related = ("suggestion", "team", "accepted_by")
    readonly_fields = ("suggestion", "team", "accepted_by", "accepted_version", "created_at")


class MatchParticipantAdmin(admin.ModelAdmin):
    list_display = ("match", "side", "lineup_order", "team", "player")
    list_select_related = ("match", "team", "player__user")
    readonly_fields = ("match", "side", "lineup_order", "team", "player")

    def has_delete_permission(self, request, obj=None):
        return False


class MatchReservationAdmin(admin.ModelAdmin):
    list_display = ("match", "player", "starts_at", "ends_at", "status")
    list_filter = ("status",)
    list_select_related = ("match", "player__user", "availability")
    readonly_fields = ("match", "player", "availability", "starts_at", "ends_at", "created_at")

    def has_delete_permission(self, request, obj=None):
        return False


class MatchResultSetAdmin(admin.ModelAdmin):
    list_display = ("submission", "set_order", "set_type", "team_a_score", "team_b_score")
    list_select_related = ("submission",)
    readonly_fields = ("submission", "set_order", "set_type", "team_a_score", "team_b_score")

    def has_delete_permission(self, request, obj=None):
        return False


class ConfirmedMatchResultAdmin(admin.ModelAdmin):
    list_display = ("match", "winning_team", "losing_team", "confirmed_at")
    list_select_related = ("match", "winning_team", "losing_team", "confirmed_from_submission")
    readonly_fields = ("match", "winning_team", "losing_team", "confirmed_from_submission", "confirmed_at")

    def has_delete_permission(self, request, obj=None):
        return False


class PointLedgerAdmin(admin.ModelAdmin):
    list_display = ("match", "team", "points_delta", "reason", "created_at")
    list_select_related = ("match", "team")
    readonly_fields = ("match", "team", "points_delta", "reason", "created_at")

    def has_delete_permission(self, request, obj=None):
        return False


class ScoreCorrectionAuditAdmin(admin.ModelAdmin):
    list_display = (
        "match",
        "corrected_by",
        "official_submission",
        "previous_winning_team",
        "new_winning_team",
        "reason",
        "created_at",
    )
    list_filter = ("reason", "created_at")
    list_select_related = (
        "match",
        "corrected_by",
        "official_submission",
        "previous_winning_team",
        "previous_losing_team",
        "new_winning_team",
        "new_losing_team",
    )
    readonly_fields = (
        "match",
        "corrected_by",
        "official_submission",
        "previous_winning_team",
        "previous_losing_team",
        "new_winning_team",
        "new_losing_team",
        "reason",
        "note",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class WorkflowEventAdmin(admin.ModelAdmin):
    list_display = ("event_type", "actor", "membership", "match", "submission", "created_at")
    list_filter = ("event_type", "created_at")
    list_select_related = (
        "actor",
        "membership__player__user",
        "membership__team",
        "match__team_a",
        "match__team_b",
        "submission",
        "score_correction_audit",
    )
    readonly_fields = (
        "event_type",
        "dedupe_key",
        "actor",
        "membership",
        "match",
        "submission",
        "score_correction_audit",
        "previous_state",
        "new_state",
        "metadata",
        "created_at",
    )
    ordering = ("-created_at", "-id")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class WorkflowEventRecipientAdmin(admin.ModelAdmin):
    list_display = ("event", "user", "created_at")
    list_select_related = ("event", "user")
    readonly_fields = ("event", "user", "created_at")
    ordering = ("-created_at", "-id")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(PlayerProfile, PlayerProfileAdmin)
admin.site.register(Team, TeamAdmin)
admin.site.register(TeamMembership, TeamMembershipAdmin)
admin.site.register(LadderStanding, LadderStandingAdmin)
admin.site.register(AvailabilitySlot, AvailabilitySlotAdmin)
admin.site.register(Challenge, ChallengeAdmin)
admin.site.register(MatchSuggestion, MatchSuggestionAdmin)
admin.site.register(SuggestionParticipant, SuggestionParticipantAdmin)
admin.site.register(SuggestionAcceptance, SuggestionAcceptanceAdmin)
admin.site.register(Match, MatchAdmin)
admin.site.register(MatchParticipant, MatchParticipantAdmin)
admin.site.register(MatchReservation, MatchReservationAdmin)
admin.site.register(MatchResultSubmission, MatchResultSubmissionAdmin)
admin.site.register(MatchResultSet, MatchResultSetAdmin)
admin.site.register(ConfirmedMatchResult, ConfirmedMatchResultAdmin)
admin.site.register(PointLedger, PointLedgerAdmin)
admin.site.register(ScoreCorrectionAudit, ScoreCorrectionAuditAdmin)
admin.site.register(AdminNotification, AdminNotificationAdmin)
admin.site.register(WorkflowEvent, WorkflowEventAdmin)
admin.site.register(WorkflowEventRecipient, WorkflowEventRecipientAdmin)
