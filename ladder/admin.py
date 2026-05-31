from django.contrib import admin

from .models import PlayerProfile, Team, LadderStanding


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



admin.site.register(PlayerProfile, PlayerProfileAdmin)
admin.site.register(Team, TeamAdmin)
admin.site.register(LadderStanding,LadderStandingAdmin)
