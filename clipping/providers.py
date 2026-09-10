"""Which RapidAPI endpoints this project needs, and where to get access to them.

Paths here are declarations, not proven facts. RapidAPI rejects an unsubscribed request
before it routes, so a path cannot be confirmed until a subscription exists. That is what
`clipping doctor` is for: it reports exactly which of these answer and which do not.
"""

from dataclasses import dataclass, field

# The four capabilities the pipeline needs, regardless of who provides them.
CAPABILITIES = ("profile", "tagged", "posts", "post")


@dataclass(frozen=True)
class EndpointSpec:
    capability: str
    path: str
    params: dict[str, str] = field(default_factory=dict)

    def query(self, handle: str, shortcode: str) -> dict[str, str]:
        return {
            k: v.format(handle=handle, shortcode=shortcode) for k, v in self.params.items()
        }


@dataclass(frozen=True)
class ProviderSpec:
    key: str
    label: str
    host: str
    signup_url: str
    endpoints: tuple[EndpointSpec, ...]
    note: str = ""

    def endpoint(self, capability: str) -> EndpointSpec | None:
        return next((e for e in self.endpoints if e.capability == capability), None)


# Primary. Paths are documented by the provider and corroborated by the scrapify-ig
# wrapper, which exposes a tagged-medias call against this same host.
SOCIAL_API = ProviderSpec(
    key="social_api",
    label="Instagram Scraper API",
    host="instagram-scraper-api2.p.rapidapi.com",
    signup_url="https://rapidapi.com/social-api1-instagram/api/instagram-scraper-api2",
    endpoints=(
        EndpointSpec("profile", "/v1/info", {"username_or_id_or_url": "{handle}"}),
        EndpointSpec("tagged", "/v1/tagged", {"username_or_id_or_url": "{handle}"}),
        EndpointSpec("posts", "/v1/posts", {"username_or_id_or_url": "{handle}"}),
        EndpointSpec("post", "/v1/post_info", {"code_or_id_or_url": "{shortcode}"}),
    ),
)

# Fallback, chosen for a different path family so it fails independently of the primary.
# These paths are the least certain in this file; doctor will report which ones exist.
SCRAPER_2025 = ProviderSpec(
    key="scraper_2025",
    label="Instagram Scraper 2025",
    host="instagram-scraper-20251.p.rapidapi.com",
    signup_url="https://rapidapi.com/DavidGelling/api/instagram-scraper-20251",
    endpoints=(
        EndpointSpec("profile", "/userinfo", {"username_or_id": "{handle}"}),
        EndpointSpec("tagged", "/usertaggedposts", {"username_or_id": "{handle}"}),
        EndpointSpec("posts", "/userposts", {"username_or_id": "{handle}"}),
        EndpointSpec("post", "/postinfo", {"shortcode": "{shortcode}"}),
    ),
    note="Paths unconfirmed. Check the RapidAPI playground against doctor's output.",
)

PROVIDERS: tuple[ProviderSpec, ...] = (SOCIAL_API, SCRAPER_2025)


def by_key(key: str) -> ProviderSpec | None:
    return next((p for p in PROVIDERS if p.key == key), None)
