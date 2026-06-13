from django.contrib import admin

from .models import AvailabilitySlot, Challenge, LadderStanding, PlayerProfile, Team


class PlayerProfileInline(admin.TabularInline):
    model = PlayerProfile
    extra = 0


class TeamAdmin(admin.ModelAdmin):
    inlines = [PlayerProfileInline]
    list_display = ("name", "division", "created_at")


class PlayerProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "gender", "team", "created_at")


class LadderStandingAdmin(admin.ModelAdmin):
    list_display = ("position", "team", "points", "wins", "losses", "matches_played", "updated_at")
    ordering = ("position",)


class AvailabilitySlotAdmin(admin.ModelAdmin):
    list_display = (
        "player",
        "week_start_date",
        "day_of_week",
        "start_time",
        "end_time",
        "created_at",
    )
    ordering = ("week_start_date", "day_of_week", "start_time")


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


admin.site.register(PlayerProfile, PlayerProfileAdmin)
admin.site.register(Team, TeamAdmin)
admin.site.register(LadderStanding, LadderStandingAdmin)
admin.site.register(AvailabilitySlot, AvailabilitySlotAdmin)
admin.site.register(Challenge, ChallengeAdmin)
