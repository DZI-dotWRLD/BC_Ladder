from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "ladder"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("accounts/register/", views.register, name="register"),
    path("accounts/login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("profile/setup/", views.profile_setup, name="profile_setup"),
    path("team/", views.team_detail, name="team"),
    path("team/create/", views.create_team, name="create_team"),
    path("team/join/", views.join_team, name="join_team"),
    path("team/join/cancel/", views.cancel_join_request_view, name="cancel_join_request"),
    path("team/request-removal/", views.request_team_removal, name="request_team_removal"),
    path("availability/", views.availability, name="availability"),
    path("availability/<int:slot_id>/cancel/", views.cancel_availability_view, name="cancel_availability"),
    path("suggestions/", views.suggestions, name="suggestions"),
    path("suggestions/create/<int:option_index>/", views.create_suggestion_view, name="create_suggestion"),
    path("suggestions/<int:suggestion_id>/accept/", views.accept_suggestion_view, name="accept_suggestion"),
    path("matches/", views.match_history, name="matches"),
    path("matches/<int:match_id>/", views.match_detail, name="match_detail"),
    path("matches/<int:match_id>/cancel/", views.cancel_match_view, name="cancel_match"),
    path("matches/<int:match_id>/score/", views.submit_score, name="submit_score"),
    path("ladders/<str:division>/", views.ladder, name="ladder"),
]
