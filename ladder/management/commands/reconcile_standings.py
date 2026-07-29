from django.core.management.base import BaseCommand

from ladder.models import Team
from ladder.services import reconcile_ladder_standings


class Command(BaseCommand):
    help = "Recalculate ladder standings from confirmed match results."

    def add_arguments(self, parser):
        parser.add_argument(
            "--division",
            choices=[Team.DIVISION_MENS, Team.DIVISION_WOMENS],
            help="Limit reconciliation to one ladder division.",
        )

    def handle(self, *args, **options):
        stats = reconcile_ladder_standings(options.get("division"))
        self.stdout.write(self.style.SUCCESS(f"Reconciled {len(stats)} standing(s)."))
