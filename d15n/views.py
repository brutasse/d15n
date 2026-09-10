"""Django views for host applications."""

from django.http import HttpResponse

from d15n import metrics


def metrics_view(request):
    """Serve d15n's Prometheus metrics, refreshing the queue gauges from the
    database on each scrape."""
    return HttpResponse(metrics.render_latest(), content_type=metrics.content_type)
