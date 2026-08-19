from django.db import migrations, models
from django.db.models import F, Q


AVAILABILITY_CONSTRAINT = "availability_no_overlap_active_player"
RESERVATION_CONSTRAINT = "reservation_no_overlap_active_player"


def add_postgresql_overlap_constraints(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.execute("CREATE EXTENSION IF NOT EXISTS btree_gist;")
        cursor.execute(
            f"""
            ALTER TABLE ladder_availabilityslot
            ADD CONSTRAINT {AVAILABILITY_CONSTRAINT}
            EXCLUDE USING gist (
                player_id WITH =,
                tstzrange(starts_at, ends_at, '[)') WITH &&
            )
            WHERE (
                status = 'active'
                AND starts_at IS NOT NULL
                AND ends_at IS NOT NULL
            );
            """
        )
        cursor.execute(
            f"""
            ALTER TABLE ladder_matchreservation
            ADD CONSTRAINT {RESERVATION_CONSTRAINT}
            EXCLUDE USING gist (
                player_id WITH =,
                tstzrange(starts_at, ends_at, '[)') WITH &&
            )
            WHERE (status = 'active');
            """
        )


def remove_postgresql_overlap_constraints(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            f"ALTER TABLE ladder_matchreservation DROP CONSTRAINT IF EXISTS {RESERVATION_CONSTRAINT};"
        )
        cursor.execute(
            f"ALTER TABLE ladder_availabilityslot DROP CONSTRAINT IF EXISTS {AVAILABILITY_CONSTRAINT};"
        )


class Migration(migrations.Migration):
    dependencies = [
        ("ladder", "0011_scorecorrectionaudit"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="availabilityslot",
            name="availability_interval_valid",
        ),
        migrations.AddConstraint(
            model_name="availabilityslot",
            constraint=models.CheckConstraint(
                condition=(
                    (Q(starts_at__isnull=True) & Q(ends_at__isnull=True))
                    | (Q(starts_at__isnull=False) & Q(ends_at__isnull=False) & Q(starts_at__lt=F("ends_at")))
                ),
                name="availability_interval_valid",
            ),
        ),
        migrations.RunPython(
            add_postgresql_overlap_constraints,
            reverse_code=remove_postgresql_overlap_constraints,
        ),
    ]
