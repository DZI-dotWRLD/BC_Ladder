from django.contrib import admin

from .models import PlayerProfile, Team


class PlayerProfileInline(admin.TabularInline):
    model = PlayerProfile
    extra = 0 

class TeamAdmin(admin.ModelAdmin):
    inlines = [PlayerProfileInline]
    list_display = ("name", "division", "created_at")

class PlayerProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "gender", "team", "created_at")



admin.site.register(PlayerProfile, PlayerProfileAdmin)
admin.site.register(Team, TeamAdmin)
