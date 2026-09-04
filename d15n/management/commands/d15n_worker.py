from django.core.management.base import BaseCommand

from d15n.worker import Worker


class Command(BaseCommand):
    help = "Run a d15n runner: claim and execute due workflows."

    def add_arguments(self, parser):
        parser.add_argument("--pool", type=int, default=4, help="thread pool size")
        parser.add_argument("--poll", type=float, default=0.2, help="seconds between claim polls")
        parser.add_argument(
            "--drain",
            type=float,
            default=30,
            help=(
                "seconds to wait for in-flight workflows after SIGTERM/SIGINT, at step "
                "boundaries, before orphaning them and exiting (0: wait indefinitely)"
            ),
        )
        parser.add_argument(
            "--name",
            default=None,
            help=(
                "stable runner name (default: hostname). Must be identical across "
                "restarts so the runner re-claims its in-flight workflows; it must "
                "also be unique among concurrently running runners."
            ),
        )

    def handle(self, *args, **options):
        Worker(
            pool_size=options["pool"],
            poll=options["poll"],
            drain=options["drain"],
            name=options["name"],
        ).run()
