from django.contrib import admin
from django.urls import path, include
from django.http import HttpResponse

from register_withvue import views as register_views


def home(request):
    return HttpResponse("🚀 Django API ishlayapti!")


urlpatterns = [
    path("", home),
    path("admin/", admin.site.urls),
    path("api/", include("register_withvue.urls")),
    path("excellence/login/", register_views.excellence_login),
    path("excellence/", register_views.excellence_info),
]
