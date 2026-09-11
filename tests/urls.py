from django.urls import include, path

urlpatterns = [
    path("everystep/", include("everystep.urls")),
]
