from django.contrib import admin
from django.urls import include, path

from fuel import views

urlpatterns = [
    path("", views.index, name="index"),
    path("map/<uuid:plan_id>/", views.route_map, name="route-map"),
    path("api/v1/", include("fuel.urls")),
    path("admin/", admin.site.urls),
]
