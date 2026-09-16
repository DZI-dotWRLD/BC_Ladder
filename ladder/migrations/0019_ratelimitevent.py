from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ladder", "0018_invitecode")]

    operations = [
        migrations.CreateModel(
            name="RateLimitEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"indexes": [models.Index(fields=["key", "created_at"], name="rate_limit_key_time_idx")]},
        )
    ]
