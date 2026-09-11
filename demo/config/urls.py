from django.http import HttpResponseRedirect
from django.urls import include, path


def home(request):
    return HttpResponseRedirect("/everystep/")


urlpatterns = [
    path("", home),
    path("everystep/", include("everystep.urls")),
]
