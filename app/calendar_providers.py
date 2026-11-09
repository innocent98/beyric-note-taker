"""The provider registry: one table that says what each calendar provider is (Outlook spec 6.1).

A provider is a row here, not a code path scattered through the sync, the join runner and the
routes. The row says how to read that provider's events (`parse_event`), which features a
connection gives (`granted_features`), whether it is switched on for this server (`enabled`), how
to build its source (`make_source`) and which flow cookie its OAuth round trip uses. Google is the
only row today; Microsoft (M2 to M5) and the pasted ICS link (C23) are added here and nowhere else.

`SourceRegistry` is the per-process set of live sources, one per enabled provider, each with its
own access-token cache. The sync loop and the join runner both take one, so a connection is always
read by the source of its own provider and the sync and the runner share one token cache.
"""

import logging
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass

from app import graph_events, microsoft_oauth
from app.calendar_source import CalendarSource
from app.config import Settings
from app.gcal_events import ParsedEvent, parse_event
from app.google_oauth import CALENDAR_SCOPE, MEET_SCOPE
from app.models import CalendarConnection

logger = logging.getLogger(__name__)

# The providers a route can name. A name here that has no row below answers 404.
ROUTE_PROVIDERS = ("google", "microsoft")


@dataclass(frozen=True)
class Provider:
    name: str
    parse_event: Callable[[dict, CalendarConnection], ParsedEvent]
    granted_features: Callable[[CalendarConnection], set[str]]
    enabled: Callable[[Settings], bool]
    make_source: Callable[[Settings], CalendarSource]
    flow_cookie: str  # the cookie that ties the callback to the browser that started the flow
    flow_cookie_path: str


# ---- Google ----

# The features a Google connect can ask for, and the scope each needs.
GOOGLE_FEATURE_SCOPES = {"calendar": CALENDAR_SCOPE, "meet": MEET_SCOPE}


def _google_features(conn: CalendarConnection) -> set[str]:
    """`calendar` and/or `meet`, from the scopes Google granted. Exact scope strings only: a
    broader or look-alike scope is not ours to rely on."""
    scopes = set((conn.granted_scopes or "").split())
    return {feature for feature, scope in GOOGLE_FEATURE_SCOPES.items() if scope in scopes}


def _google_parse(raw: dict, conn: CalendarConnection) -> ParsedEvent:
    return parse_event(raw, hosted_domain=conn.account_domain)


def _google_source(settings: Settings) -> CalendarSource:
    from app.gcal_client import GoogleSource  # not at the top: keeps this module light to import

    return GoogleSource(settings)


# ---- Microsoft (Outlook) ----


def _microsoft_features(conn: CalendarConnection) -> set[str]:
    """`calendar` when `Calendars.Read` is among the granted scopes (full Graph URLs or short
    names, any case; `Calendars.ReadBasic` and look-alikes do not count)."""
    return {"calendar"} if microsoft_oauth.has_calendar_scope(conn.granted_scopes) else set()


def _microsoft_parse(raw: dict, conn: CalendarConnection) -> ParsedEvent:
    # The organisation's domains matter only for a work account, and only if they were read.
    domains = conn.tenant_domains if conn.account_kind == "work" else None
    return graph_events.parse_event(raw, tenant_domains=domains)


def _microsoft_source(settings: Settings) -> CalendarSource:
    from app.graph_client import MicrosoftSource  # not at the top: keeps this module light

    return MicrosoftSource(settings)


PROVIDERS: dict[str, Provider] = {
    "google": Provider(
        name="google",
        parse_event=_google_parse,
        granted_features=_google_features,
        enabled=lambda settings: bool(settings.calendar_enabled),
        make_source=_google_source,
        flow_cookie="calendar_flow",
        flow_cookie_path="/api/calendar/google",
    ),
    "microsoft": Provider(
        name="microsoft",
        parse_event=_microsoft_parse,
        granted_features=_microsoft_features,
        enabled=microsoft_oauth.calendar_enabled,
        make_source=_microsoft_source,
        flow_cookie="calendar_flow_ms",
        flow_cookie_path="/api/calendar/microsoft",
    ),
}


def get(name: str) -> Provider | None:
    return PROVIDERS.get(name)


def features_of(conn: CalendarConnection) -> set[str]:
    """The features this connection can use, by its own provider's rules (empty for a provider
    with no row, such as a not yet built one)."""
    provider = PROVIDERS.get(conn.provider)
    return provider.granted_features(conn) if provider else set()


def parse(raw: dict, conn: CalendarConnection) -> ParsedEvent:
    """One raw event of `conn` to the common shape, by the connection's provider."""
    provider = PROVIDERS.get(conn.provider)
    if provider is None:
        raise ValueError("no parser for this provider")
    return provider.parse_event(raw, conn)


# ---- the live sources ----


class SourceRegistry:
    """The sources of one process, by provider name."""

    def __init__(self, sources: Iterable[CalendarSource]) -> None:
        self._sources: dict[str, CalendarSource] = {}
        for source in sources:
            if source.provider in self._sources:
                raise ValueError(f"two sources for provider {source.provider}")
            self._sources[source.provider] = source

    def get(self, provider: str) -> CalendarSource | None:
        return self._sources.get(provider)

    def __iter__(self) -> Iterator[CalendarSource]:
        return iter(self._sources.values())

    def __len__(self) -> int:
        return len(self._sources)


def as_registry(
    sources: "SourceRegistry | CalendarSource | Iterable[CalendarSource] | None",
) -> SourceRegistry:
    """A registry from what a caller has: one source, several, a registry or nothing."""
    if sources is None:
        return SourceRegistry([])
    if isinstance(sources, SourceRegistry):
        return sources
    if hasattr(sources, "provider"):  # one source (a fake in a test may have only `get_event`)
        return SourceRegistry([sources])
    return SourceRegistry(sources)


def build_sources(settings: Settings) -> SourceRegistry:
    """One source per provider that is switched on for this server. A provider that is switched on
    but whose source is not built yet (`NotImplementedError`) is skipped with a log line: it must
    never take the scheduler's other jobs down."""
    sources = []
    for provider in PROVIDERS.values():
        if not provider.enabled(settings):
            continue
        try:
            sources.append(provider.make_source(settings))
        except NotImplementedError:
            logger.warning(
                "calendar provider %s is switched on but has no source yet", provider.name
            )
    return SourceRegistry(sources)
