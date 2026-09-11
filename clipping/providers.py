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

# Second provider, on a different path family so it fails independently of the primary.
# Host confirmed from the publisher's own playground snippet, which uses kebab-case paths
# with a trailing slash, for example /post-highlights-info/. The four paths below follow
# that convention but are guesses: this API rejects on subscription before it routes, so a
# wrong path and a right one both return 403 until a subscription exists.
SCRAPER_2025 = ProviderSpec(
    key="scraper_2025",
    label="Instagram Scraper 2025 (Cloe social)",
    host="instagram-scraper-20253.p.rapidapi.com",
    signup_url="https://rapidapi.com/search/instagram-scraper-20253",
    endpoints=(
        EndpointSpec("profile", "/user-info/", {"username_or_id_or_url": "{handle}"}),
        EndpointSpec("tagged", "/user-tag/", {"username_or_id_or_url": "{handle}"}),
        # No general "all posts" endpoint exists on this listing. Reels is the closest
        # available, so a roster crawl through it will miss photo and carousel posts.
        EndpointSpec("posts", "/user-reels/", {"username_or_id_or_url": "{handle}"}),
        EndpointSpec("post", "/post-info/", {"code_or_id_or_url": "{shortcode}"}),
    ),
    note="Paths confirmed live. 'posts' maps to reels only; photo posts are not reachable.",
)

# Current primary. Host, paths and parameter name all confirmed against a live
# subscription. Unlike the Cloe social listing it exposes a general /userposts endpoint,
# which is what the roster crawl needs.
#
# Its response shape is identical to SCRAPER_2025 ({"data": {"items": [...]},
# "pagination_token": "..."}), so one normalizer handles both without branching.
#
# This API ignores count and limit parameters: asking for 20 still returns 21. The batch
# size is therefore enforced after parsing, in pipeline.ingest.
SCRAPER_20251 = ProviderSpec(
    key="scraper_20251",
    label="Instagram Scraper 2025",
    host="instagram-scraper-20251.p.rapidapi.com",
    signup_url="https://rapidapi.com/search/instagram-scraper-20251",
    endpoints=(
        EndpointSpec("profile", "/userinfo", {"username_or_id": "{handle}"}),
        EndpointSpec("tagged", "/usertaggedposts", {"username_or_id": "{handle}"}),
        EndpointSpec("posts", "/userposts", {"username_or_id": "{handle}"}),
        EndpointSpec("post", "/postinfo", {"shortcode": "{shortcode}"}),
    ),
    note="Ignores count/limit; the 20-post batch size is applied after parsing.",
)

# How many tagged posts one execution processes. The API will not page for us.
BATCH_SIZE = 20

PROVIDERS: tuple[ProviderSpec, ...] = (SCRAPER_20251, SCRAPER_2025, SOCIAL_API)


def by_key(key: str) -> ProviderSpec | None:
    return next((p for p in PROVIDERS if p.key == key), None)
