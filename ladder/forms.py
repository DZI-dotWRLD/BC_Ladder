from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm

from .models import PlayerProfile, Team
from .registration import EMAIL_CONFLICT, users_with_email


class CandidateCommandForm(forms.Form):
    candidate = forms.CharField(max_length=8192, widget=forms.HiddenInput)


class AvailabilityForm(forms.Form):
    starts_at = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )
    ends_at = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )


class TeamJoinForm(forms.Form):
    team = forms.ModelChoiceField(
        queryset=Team.active.none(),
        empty_label="Choose a team",
        label="Team",
    )

    def __init__(self, *args, profile=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = Team.active.order_by("name", "id")
        if profile:
            if profile.gender == PlayerProfile.GENDER_MALE:
                queryset = queryset.filter(division=Team.DIVISION_MENS)
            elif profile.gender == PlayerProfile.GENDER_FEMALE:
                queryset = queryset.filter(division=Team.DIVISION_WOMENS)
        self.fields["team"].queryset = queryset


class TeamCreateForm(forms.Form):
    name = forms.CharField(
        label="Team name",
        max_length=50,
        widget=forms.TextInput(attrs={"placeholder": "e.g. The Red Room"}),
    )

    def clean_name(self):
        name = " ".join(self.cleaned_data["name"].split())
        if not name:
            raise forms.ValidationError("Team name is required.")
        if Team.objects.filter(name__iexact=name).exists():
            raise forms.ValidationError("A team with this name already exists.")
        return name


class ProfileSetupForm(forms.ModelForm):
    class Meta:
        model = PlayerProfile
        fields = ["gender"]


class PlayerRegistrationForm(UserCreationForm):
    email = forms.EmailField(
        help_text="Used for account recovery, match requests, and important ladder notifications.",
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )
    gender = forms.ChoiceField(choices=PlayerProfile.GENDER_CHOICES)

    class Meta:
        model = get_user_model()
        fields = ("username", "email", "gender", "password1", "password2")

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if users_with_email(email).exists():
            raise forms.ValidationError(EMAIL_CONFLICT)
        return email


class VerificationResendForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "email"}))

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()


class ScoreSubmissionForm(forms.Form):
    score_widget = forms.NumberInput(attrs={"inputmode": "numeric", "min": "0"})

    set1_team_a = forms.IntegerField(min_value=0, max_value=7, widget=score_widget)
    set1_team_b = forms.IntegerField(min_value=0, max_value=7, widget=score_widget)
    set2_team_a = forms.IntegerField(min_value=0, max_value=7, widget=score_widget)
    set2_team_b = forms.IntegerField(min_value=0, max_value=7, widget=score_widget)
    set3_team_a = forms.IntegerField(min_value=0, max_value=99, required=False, widget=score_widget)
    set3_team_b = forms.IntegerField(min_value=0, max_value=99, required=False, widget=score_widget)

    def normalized_sets(self):
        sets = [
            (self.cleaned_data["set1_team_a"], self.cleaned_data["set1_team_b"]),
            (self.cleaned_data["set2_team_a"], self.cleaned_data["set2_team_b"]),
        ]
        set3_a = self.cleaned_data.get("set3_team_a")
        set3_b = self.cleaned_data.get("set3_team_b")
        if set3_a is not None or set3_b is not None:
            sets.append((set3_a, set3_b))
        return sets
