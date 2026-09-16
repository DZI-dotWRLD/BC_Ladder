from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("ladder", "0017_merge_production_slices"),
    ]

    operations = [
        migrations.CreateModel(
            name="InviteCode",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=128, unique=True)),
                ("max_uses", models.PositiveIntegerField(default=1)),
                ("uses", models.PositiveIntegerField(default=0)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="created_invite_codes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [models.Index(fields=["expires_at"], name="invite_expiry_idx")],
                "constraints": [
                    models.CheckConstraint(condition=models.Q(("max_uses__gte", 1)), name="invite_max_uses_positive"),
                    models.CheckConstraint(condition=models.Q(("uses__gte", 0)), name="invite_uses_nonnegative"),
                    models.CheckConstraint(condition=models.Q(("uses__lte", models.F("max_uses"))), name="invite_uses_within_limit"),
                ],
            },
        ),
    ]
