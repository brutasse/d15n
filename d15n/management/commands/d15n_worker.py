from django.core.management.base import BaseCommand

from d15n.worker import Worker


class Command(BaseCommand):
    help = "Run d15n workers: claim and execute due workflows."

    def add_arguments(self, parser):
        parser.add_argument("--pool", type=int, default=4, help="thread pool size")
        parser.add_argument("--poll", type=float, default=0.2, help="seconds between claim polls")
        parser.add_argument(
            "--lease", type=int, default=300, help="lease TTL in seconds for running workflows"
        )

    def handle(self, *args, **options):
        Worker(
            pool_size=options["pool"],
            poll=options["poll"],
            lease_seconds=options["lease"],
        ).run()
