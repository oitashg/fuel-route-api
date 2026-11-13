import uuid

from django.db import models


class FuelStation(models.Model):
    """One truck stop from the OPIS price file.

    The supplied CSV lists a truck stop once per price sample, so rows are
    folded onto the OPIS id here and ``price`` holds the mean of the samples
    (``price_samples`` says how many there were).

    Latitude and longitude are not in the file; they are filled in by the
    ``geocode_stations`` command and stored so that planning a route never has
    to call a geocoder.
    """

    GAZETTEER = "gazetteer"
    NOMINATIM = "nominatim"
    GEOCODE_SOURCES = [
        (GAZETTEER, "Bundled city gazetteer"),
        (NOMINATIM, "Nominatim lookup"),
    ]

    opis_id = models.PositiveIntegerField(unique=True, db_index=True)
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=120)
    state = models.CharField(max_length=2)
    rack_id = models.PositiveIntegerField(null=True, blank=True)

    price = models.DecimalField(max_digits=7, decimal_places=4)
    price_samples = models.PositiveSmallIntegerField(default=1)

    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    geocode_source = models.CharField(
        max_length=20, choices=GEOCODE_SOURCES, blank=True
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["state", "city", "name"]
        indexes = [
            models.Index(fields=["state", "city"]),
            models.Index(fields=["latitude", "longitude"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.city}, {self.state})"

    @property
    def located(self) -> bool:
        return self.latitude is not None and self.longitude is not None


class RoutePlan(models.Model):
    """A planned trip, kept so the result has a permanent URL.

    The whole response is stored on the row, which is what lets the map page
    render without replanning (and without another call upstream).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    start_query = models.CharField(max_length=255)
    finish_query = models.CharField(max_length=255)

    result = models.JSONField(default=dict, blank=True)
    total_distance_mi = models.FloatField(default=0)
    total_cost_usd = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    stop_count = models.PositiveSmallIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["-created_at"])]

    def __str__(self) -> str:
        return f"{self.start_query} to {self.finish_query} (${self.total_cost_usd})"
