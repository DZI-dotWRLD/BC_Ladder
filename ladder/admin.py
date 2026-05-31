from django.contrib import admin

from .models import PlayerProfile, Team, LadderStanding, AvailabilitySlot


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


admin.site.register(PlayerProfile, PlayerProfileAdmin)
admin.site.register(Team, TeamAdmin)
admin.site.register(LadderStanding,LadderStandingAdmin)
admin.site.register(AvailabilitySlot, AvailabilitySlotAdmin)
