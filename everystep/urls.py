from django.urls import path

from everystep import views

urlpatterns = [
    path("", views.ui_view),
    path("api/runs", views.api_runs_view),
    path("api/run/<uuid:run_id>", views.api_run_view),
    path("api/stream", views.api_stream_view),
]
