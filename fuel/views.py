"""API endpoints and the two HTML pages that make the API easy to look at."""

from __future__ import annotations

import logging

from django.conf import settings
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import FuelStation, RoutePlan
from .serializers import RoutePlanSummarySerializer, TripRequestSerializer
from .services.geo import GeoError
from .services.planner import PlanningError
from .services.trip import assumptions as trip_assumptions
from .services.trip import plan_trip

log = logging.getLogger(__name__)


@api_view(["GET"])
def health(request):
    """Liveness probe. Also says whether the price table has been loaded."""
    located = FuelStation.objects.filter(latitude__isnull=False).count()
    return Response(
        {
            "status": "ok" if located else "no fuel data loaded",
            "service": "fuel-route-api",
            "truck_stops": FuelStation.objects.count(),
            "truck_stops_located": located,
        }
    )


@api_view(["GET"])
def assumptions(request):
    return Response(
        {
            "vehicle": {
                "mpg": settings.VEHICLE_MPG,
                "range_mi": settings.VEHICLE_RANGE_MI,
                "tank_gallons": round(settings.VEHICLE_RANGE_MI / settings.VEHICLE_MPG, 2),
            },
            "corridor_mi": settings.CORRIDOR_MI,
            "stop_penalty_usd": settings.STOP_PENALTY_USD,
            "assumptions": trip_assumptions(),
        }
    )


@api_view(["POST"])
def plan(request):
    """Plan a trip: route it, pick the fuel stops, price the fuel."""
    form = TripRequestSerializer(data=request.data)
    form.is_valid(raise_exception=True)
    data = form.validated_data

    try:
        result = plan_trip(
            data["start"],
            data["finish"],
            geometry=data["geometry"],
            stop_penalty_usd=data.get("stop_penalty_usd"),
        )
    except GeoError as exc:
        # A bad address or an upstream outage: the caller can act on this.
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except PlanningError as exc:
        # The route is fine, there is just no way to fuel it from this price
        # list. Worth its own status code so a client can tell them apart.
        return Response(
            {"detail": str(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY
        )
    except Exception:  # pragma: no cover
        log.exception("planning failed for %r -> %r", data["start"], data["finish"])
        return Response(
            {"detail": "Something went wrong while planning this trip."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    saved = RoutePlan.objects.create(
        start_query=data["start"],
        finish_query=data["finish"],
        result=result,
        total_distance_mi=result["route"]["distance_mi"],
        total_cost_usd=result["fuel"]["total_cost_usd"],
        stop_count=result["fuel"]["stop_count"],
    )

    body = {"id": str(saved.id), **result}
    body["map_url"] = request.build_absolute_uri(
        reverse("route-map", args=[saved.id])
    )
    return Response(body, status=status.HTTP_201_CREATED)


@api_view(["GET"])
def plan_detail(request, plan_id):
    """Re-serve a stored plan, so a result has a permanent URL."""
    saved = get_object_or_404(RoutePlan, pk=plan_id)
    body = {"id": str(saved.id), **saved.result}
    body["map_url"] = request.build_absolute_uri(reverse("route-map", args=[saved.id]))
    return Response(body)


@api_view(["GET"])
def plan_list(request):
    """The ten most recent plans."""
    plans = RoutePlan.objects.all()[:10]
    return Response(RoutePlanSummarySerializer(plans, many=True).data)


def index(request):
    """A small form so the API can be tried without a REST client."""
    return render(
        request,
        "fuel/index.html",
        {"recent": RoutePlan.objects.all()[:8]},
    )


def route_map(request, plan_id):
    """Draw a stored plan: the line, and a pin on every fuel stop."""
    saved = get_object_or_404(RoutePlan, pk=plan_id)
    return render(
        request,
        "fuel/map.html",
        {
            "plan": saved,
            "result": saved.result,
            "tile_url": settings.MAP_TILE_URL,
            "tile_attribution": settings.MAP_TILE_ATTRIBUTION,
        },
    )
