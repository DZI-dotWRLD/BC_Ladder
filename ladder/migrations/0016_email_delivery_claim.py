from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ladder", "0015_workflowevent_suggestion_and_more")]

    operations = [
        migrations.AddField(model_name="emailnotificationdelivery", name="claim_token", field=models.UUIDField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name="emailnotificationdelivery", name="claim_expires_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AlterField(model_name="emailnotificationdelivery", name="attempts", field=models.PositiveIntegerField(default=0)),
        migrations.AlterField(model_name="emailnotificationdelivery", name="status", field=models.CharField(choices=[("pending", "Pending"), ("sending", "Sending"), ("sent", "Sent"), ("failed", "Failed"), ("skipped", "Skipped")], default="pending", max_length=20)),
        migrations.AddIndex(model_name="emailnotificationdelivery", index=models.Index(fields=["status", "claim_expires_at"], name="email_delivery_claim_idx")),
    ]
