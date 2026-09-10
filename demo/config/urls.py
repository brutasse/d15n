from django.http import HttpResponseRedirect
from django.urls import include, path


def home(request):
    return HttpResponseRedirect("/d15n/")


urlpatterns = [
    path("", home),
    path("d15n/", include("d15n.urls")),
]
