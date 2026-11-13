from django.contrib import admin

from .models import FuelStation, RoutePlan


@admin.register(FuelStation)
class FuelStationAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "state", "price", "price_samples", "geocode_source")
    list_filter = ("state", "geocode_source")
    search_fields = ("name", "city", "address", "opis_id")
    ordering = ("state", "city")


@admin.register(RoutePlan)
class RoutePlanAdmin(admin.ModelAdmin):
    list_display = ("id", "start_query", "finish_query", "total_distance_mi",
                    "total_cost_usd", "stop_count", "created_at")
    search_fields = ("start_query", "finish_query")
    readonly_fields = ("id", "created_at", "result")
