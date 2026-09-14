"""Protect stock auth.User without replacing the historical user model."""

from django.conf import settings
from django.db import migrations, models
from django.db.models import Count, Value
from django.db.models.functions import Lower, NullIf, Trim


CONSTRAINT_NAME = "auth_user_normalized_email_unique"


def constraint():
    # Unique indexes allow multiple NULL values: blank legacy emails survive.
    return models.UniqueConstraint(NullIf(Lower(Trim("email")), Value("")), name=CONSTRAINT_NAME)


def add_constraint(apps, schema_editor):
    user = apps.get_model(settings.AUTH_USER_MODEL)
    if not schema_editor.connection.features.supports_expression_indexes:
        raise RuntimeError("Normalized email uniqueness requires expression-index support.")
    users = user.objects.using(schema_editor.connection.alias).annotate(normalized_email=NullIf(Lower(Trim("email")), Value("")))
    duplicates = list(users.exclude(normalized_email=None).values("normalized_email").annotate(total=Count("pk")).filter(total__gt=1))
    if duplicates:
        ids = [list(users.filter(normalized_email=row["normalized_email"]).order_by("pk").values_list("pk", flat=True)) for row in duplicates]
        raise RuntimeError(f"Duplicate normalized emails for user ID groups {ids}; administrator remediation required. No accounts changed.")
    schema_editor.add_constraint(user, constraint())


def remove_constraint(apps, schema_editor):
    schema_editor.remove_constraint(apps.get_model(settings.AUTH_USER_MODEL), constraint())


class Migration(migrations.Migration):
    dependencies = [
        ("ladder", "0015_workflowevent_suggestion_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]
    operations = [migrations.RunPython(add_constraint, remove_constraint)]
