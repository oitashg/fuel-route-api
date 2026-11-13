from django.urls import path

from . import views

urlpatterns = [
    path("health/", views.health, name="health"),
    path("assumptions/", views.assumptions, name="assumptions"),
    path("route/", views.plan, name="plan-route"),
    path("routes/", views.plan_list, name="route-list"),
    path("routes/<uuid:plan_id>/", views.plan_detail, name="route-detail"),
]
