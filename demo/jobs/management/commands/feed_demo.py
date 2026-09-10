import random
import time

from django.core.management.base import BaseCommand

from d15n import schedule

from jobs import workflows

# (workflow, weight): the seeder picks from this pool.
POOL = [
    (workflows.order_processing, 3),
    (workflows.fan_out, 2),
    (workflows.nested_fan_out, 1),
    (workflows.release_pipeline, 3),
    (workflows.slow_pipeline, 2),
    (workflows.slow_fan, 1),
    (workflows.big_report, 1),
    (workflows.risky_transfer, 2),
    (workflows.flaky_parallel, 2),
]


def _order(n):
    return {
        "id": f"ord-{n:04d}",
        "items": [
            {"sku": f"sku-{i}", "price": 9.99 + i, "qty": (n + i) % 3 + 1}
            for i in range((n % 4) + 1)
        ],
    }


def _args_for(func, n):
    if func is workflows.nested_fan_out:
        return {"regions": [f"ch-gva-{n}", f"ch-dk-{n}", f"nl-ams-{n}"]}
    if func is workflows.release_pipeline:
        return {"app": f"app-{n % 5}"}
    if func in (workflows.risky_transfer, workflows.flaky_parallel):
        return {"order": _order(n), "should_fail": random.random() < 0.5}
    return {"order": _order(n)}


def _pick():
    funcs = [f for f, _ in POOL]
    weights = [w for _, w in POOL]
    return random.choices(funcs, weights=weights)[0]


class Command(BaseCommand):
    help = "Schedule a burst of demo workflows, then keep feeding new ones."

    def add_arguments(self, parser):
        parser.add_argument(
            "--burst", type=int, default=30, help="initial number of runs to schedule"
        )
        parser.add_argument(
            "--interval", type=float, default=5.0, help="seconds between drip batches"
        )
        parser.add_argument(
            "--drip", type=int, default=2, help="runs scheduled per drip batch"
        )
        parser.add_argument(
            "--once", action="store_true", help="schedule the burst and exit (no loop)"
        )

    def handle(self, *args, **options):
        # The initial burst cycles through the pool so every workflow type is
        # on the board right away.
        for i in range(options["burst"]):
            func = POOL[i % len(POOL)][0]
            schedule(func, _args_for(func, i + 1))
        self.stdout.write(self.style.SUCCESS(f"scheduled {options['burst']} demo workflows"))
        if options["once"]:
            return
        n = options["burst"]
        while True:
            time.sleep(options["interval"])
            for _ in range(options["drip"]):
                n += 1
                func = _pick()
                schedule(func, _args_for(func, n))
            self.stdout.write(f"total fed: {n}")
