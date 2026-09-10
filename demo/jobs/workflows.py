"""Sample workflows for the d15n demo.

Every workflow body is a straight sequence of step calls and `parallel`
forks, so the UI can render the static step graph for each run. The steps
themselves may do anything; they are opaque units of work.
"""

import time

from d15n import parallel, step, workflow


# --- order_processing: a named sequential pipeline --------------------------


@step
def validate_order(order):
    return {"order": order["id"], "items": len(order["items"])}


@step
def charge_order(order):
    total = round(sum(item["price"] * item["qty"] for item in order["items"]), 2)
    return {"order": order["id"], "total": total}


@step
def ship_order(order):
    time.sleep(0.4)
    return {"order": order["id"], "carrier": "exoscale-courier", "eta": "3d"}


@step
def notify_order(order):
    return {"order": order["id"], "message": "your order is on its way"}


@workflow
def order_processing(args):
    order = args["order"]
    validate_order(order, d15n_id="validate")
    receipt = charge_order(order, d15n_id="charge")
    ship_order(order, d15n_id="ship")
    notify_order(order, d15n_id="notify")
    return receipt


# --- fan_out: a fork with single-step and sequence branches -----------------


@step
def enrich_order(order):
    return {"order": order["id"], "enriched": True}


@step
def rank_order(order):
    time.sleep(0.3)
    value = sum(item["price"] for item in order["items"])
    return {"order": order["id"], "rank": round(value % 97, 1)}


@step
def tag_order(order):
    return {"order": order["id"], "tags": ["demo"]}


@step
def score_order(order):
    return {"order": order["id"], "score": len(order["items"]) * 10}


@workflow
def fan_out(args):
    order = args["order"]
    enriched, ranked, (tags, score) = parallel(
        lambda: enrich_order(order),
        lambda: rank_order(order),
        [lambda: tag_order(order), lambda: score_order(order)],
        d15n_id="fan",
    )
    return {"enriched": enriched, "ranked": ranked, "tags": tags, "score": score}


# --- nested_fan_out: a fork inside a fork ------------------------------------


@step
def fetch_region(region):
    return {"region": region, "ok": True}


@step
def price_region(region):
    return {"region": region, "price": 0.09}


@step
def merge_results(left, right):
    return {"merged": [left, right]}


@workflow
def nested_fan_out(args):
    regions = args["regions"]
    first, second = parallel(
        lambda: parallel(
            lambda: fetch_region(regions[0]),
            lambda: price_region(regions[0]),
            d15n_id="inner",
        ),
        lambda: merge_results(regions[0], regions[1]),
        d15n_id="outer",
    )
    return {"first": first, "second": second}


# --- release_pipeline: sequential stages, each with parallel branches --------


@step
def run_lint(app):
    return {"app": app, "issues": 0}


@step
def run_tests(app):
    time.sleep(0.5)
    return {"app": app, "passed": 128}


@step
def build_docs(app):
    time.sleep(0.3)
    return {"app": app, "pages": 42}


@step
def build_image(app, arch):
    time.sleep(0.6)
    return {"app": app, "arch": arch, "digest": f"sha256:demo-{arch}"}


@step
def publish_release(app):
    return {"app": app, "published": True}


@workflow
def release_pipeline(args):
    app = args["app"]
    parallel(
        lambda: run_lint(app, d15n_id="lint"),
        lambda: run_tests(app, d15n_id="tests"),
        lambda: build_docs(app, d15n_id="docs"),
        d15n_id="checks",
    )
    parallel(
        lambda: build_image(app, "amd64", d15n_id="amd64"),
        lambda: build_image(app, "arm64", d15n_id="arm64"),
        d15n_id="builds",
    )
    return publish_release(app, d15n_id="publish")


# --- slow_pipeline: long steps, clearly in flight ----------------------------


@step
def extract_batch(order):
    time.sleep(2.0)
    return {"order": order["id"], "rows": len(order["items"])}


@step
def transform_batch(rows):
    time.sleep(2.0)
    return {"rows": rows, "transformed": True}


@step
def load_batch(rows):
    time.sleep(2.0)
    return {"rows": rows, "loaded": True}


@workflow
def slow_pipeline(args):
    order = args["order"]
    extracted = extract_batch(order)
    transformed = transform_batch(extracted["rows"])
    return load_batch(transformed["rows"])


# --- slow_fan: parallel branches long enough to watch them run at once -------


@step
def index_products(order):
    time.sleep(3.0)
    return {"order": order["id"], "indexed": len(order["items"])}


@step
def index_customers(order):
    time.sleep(3.0)
    return {"order": order["id"], "customers": 2}


@step
def index_orders(order):
    time.sleep(3.0)
    return {"order": order["id"], "orders": 1}


@step
def build_search_index(shards):
    time.sleep(3.0)
    return {"shards": shards, "built": True}


@workflow
def slow_fan(args):
    order = args["order"]
    return parallel(
        lambda: index_products(order, d15n_id="products"),
        lambda: index_customers(order, d15n_id="customers"),
        [
            lambda: index_orders(order, d15n_id="orders"),
            lambda: build_search_index(3, d15n_id="search"),
        ],
        d15n_id="index",
    )


# --- big_report: ten sequential steps ----------------------------------------


@step
def report_section(topic):
    time.sleep(0.1)
    return {"topic": topic, "words": 120}


@workflow
def big_report(args):
    return (
        report_section("revenue"),
        report_section("churn"),
        report_section("capacity"),
        report_section("incidents"),
        report_section("backlog"),
        report_section("hiring"),
        report_section("roadmap"),
        report_section("budget"),
        report_section("risks"),
        report_section("next-quarter"),
    )


# --- risky_transfer: fails half the time --------------------------------------


@step
def prepare_transfer(order):
    return {"order": order["id"], "prepared": True}


@step
def transfer_funds(order, should_fail):
    if should_fail:
        raise ValueError("insufficient funds at the other end")
    return {"order": order["id"], "transferred": True}


@step
def confirm_transfer(order):
    return {"order": order["id"], "confirmed": True}


@workflow
def risky_transfer(args):
    order = args["order"]
    prepare_transfer(order, d15n_id="prepare")
    result = transfer_funds(order, args["should_fail"], d15n_id="transfer")
    confirm_transfer(order, d15n_id="confirm")
    return result


# --- flaky_parallel: one branch of a fork fails about half the time ----------


@step
def sync_inventory(order):
    return {"order": order["id"], "synced": len(order["items"])}


@step
def sync_partner(order, should_fail):
    if should_fail:
        raise ConnectionError("partner gateway timeout")
    return {"order": order["id"], "partner": "synced"}


@step
def sync_audit(order):
    return {"order": order["id"], "audited": True}


@workflow
def flaky_parallel(args):
    order = args["order"]
    return parallel(
        lambda: sync_inventory(order, d15n_id="inventory"),
        lambda: sync_partner(order, args["should_fail"], d15n_id="partner"),
        lambda: sync_audit(order, d15n_id="audit"),
        d15n_id="sync",
    )
