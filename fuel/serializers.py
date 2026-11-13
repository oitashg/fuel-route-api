from rest_framework import serializers

from .models import RoutePlan


class TripRequestSerializer(serializers.Serializer):
    """What a caller has to send to plan a trip.

    Both ends take anything Nominatim understands -- "Dallas, TX",
    "1600 Pennsylvania Ave, Washington DC" -- or a bare "lat,lon" pair, which
    skips the geocoder entirely.
    """

    start = serializers.CharField(max_length=255, trim_whitespace=True)
    finish = serializers.CharField(max_length=255, trim_whitespace=True)
    geometry = serializers.ChoiceField(
        choices=["simplified", "full", "none"],
        default="simplified",
        help_text="How much of the route line to return.",
    )
    stop_penalty_usd = serializers.FloatField(
        required=False,
        min_value=0,
        max_value=500,
        help_text="Dollars a stop is worth avoiding. Defaults to STOP_PENALTY_USD; "
        "0 means pure cheapest fuel, however many stops that takes.",
    )


class RoutePlanSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = RoutePlan
        fields = [
            "id",
            "start_query",
            "finish_query",
            "total_distance_mi",
            "total_cost_usd",
            "stop_count",
            "created_at",
        ]
