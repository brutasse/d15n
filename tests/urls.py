from django.urls import include, path

urlpatterns = [
    path("d15n/", include("d15n.urls")),
]
