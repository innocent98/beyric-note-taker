SOURCE: https://docs.recall.ai/docs/meeting-urls.md
Fetched 2026-11-07 with curl -sL -A 'Mozilla/5.0'

---
updatedAt: 2026-10-23T19:01:28.000Z
agentTools:
  projectIndex: https://docs.recall.ai/llms.txt
---

# Meeting URLs

Use platform-specific regex patterns to validate meeting URLs before creating a bot so you can catch invalid links early.

# Meeting URL Regex

Most times, developers will validate the meeting URL by calling the [Create Bot](https://docs.recall.ai/reference/bot_create) endpoint, seeing a 400 response that includes an error message saying the meeting URL is invalid. We generally recommend this flow to verify whether meeting URLs are valid.

That said, there are rare cases where you want to check the Meeting URL before you attempt to schedule/create a bot. In these cases, you can use the following regex snippets to verify the meeting URL before creating a bot.

> 📘 Changes to meeting URL regex
>
> Meeting URL regex is rarely updated as the meeting platforms don't often update their meeting URL formats.
>
> That said, if there are changes to meeting URLs, the scope of the meeting URL will strictly increase to include new formats.
>
> If you notice we support a new meeting URL format that differs from your regex, feel free to reach out for the latest updates and we can share the updated list of meeting URL regex.

## Zoom

```python
import re

# Bare Zoom meeting/start/web-client link with optional password/tk/zak
re.compile(
  r"""
    zoom(\.us|\.com|-x\.de)\/(?:j|s|wc(?:\/join)?)\/(?P<id>[0-9]+)(?:\/(?:start|join))?
    (
      \?
      ((pwd=(?P<password>[^&#,<>"\s]+)) |
       (tk=(?P<tk>[^&#,<>"\s]+)) |
       (zak=(?P<zak>[^&#,<>"\s]+)))?
      ([?&=a-zA-Z0-9_+\-%]{0,512}\&pwd=(?P<password_later_in_query_string>[^&#,<>"\s]+))?
      ([?&=a-zA-Z0-9_+\-%]{0,512}\&tk=(?P<tk_later_in_query_string>[^&#,<>"\s]+))?
      ([?&=a-zA-Z0-9_+\-%]{0,512}\&zak=(?P<zak_later_in_query_string>[^&#,<>"\s]+))?
    )?
    ((\ \(Password:\ (?P<password_legacy>.+?)\)) |
     (\ \(Passcode:\ (?P<password_legacy_2>.+?)\)))?
  """,
  re.VERBOSE,
)

# Zoom Invitation links
re.compile(
  r"https://(?:[A-Za-z0-9_-]+\.)?zoom\.(?:us|com)/meetings/[0-9]+/invitations(?:\?[^\s]+)?"
)

# Zoom personal link (/my/...)
re.compile(
  r"([A-Za-z0-9_-]+\.)?zoom(\.us|\.com|-x\.de)\/my\/(?P<id>[A-Za-z0-9.%+=_-]+)(\?[A-Za-z0-9.+_=&-]+)?"
)

# Zoom webinar link (/w/<id>) with optional tk/pwd
re.compile(
  r"""
    zoom(\.us|\.com|-x\.de)\/w\/(?P<id>\w+)
    (\?tk=(?P<tk>[^&#,<>"\s]+))?
    ((.*)pwd=(?P<password>[^&#,<>"\s]+))?
    (&tk=(?P<tk2>[^&#,<>"\s]+))?
  """,
  re.VERBOSE,
)
```

## Google Meet

```python
import re

re.compile(
  r"(http.+)?meet\.google\.com\/(?:meet\/)?(?P<id>\w{3}-\w{4}-\w{3})"
)
```

## Microsoft Teams

```python
import re
from urllib.parse import unquote

# Normalize /_#/..., /v2#/..., /v2/#/..., and /v2/?...#/... routes.
TEAMS_FRAGMENT_URL_REGEX = re.compile(
    (
        r"^(?P<base>https?://(?:[a-z0-9-]+\.)?teams\."
        r"(?:microsoft\.com|cloud\.microsoft|microsoft\.us|live\.com))"
        r"/+(?:_|v2/?(?:\?[^#]*)?)#(?P<path>/.*)$"
    ),
    re.IGNORECASE,
)

def normalize_teams_url(url):
    url = unquote(url)
    match = TEAMS_FRAGMENT_URL_REGEX.match(url)
    if match:
        return f"{match.group('base')}{match.group('path')}"
    return url

# Apply the following extraction patterns to the normalized URL.

# Teams meetup-join deep link with JSON context; captures thread_id, message_id, context
re.compile(
  r"""
    http.+meetup-join\/
    (?P<thread_id>.+)\/
    (?P<message_id>[0-9]+)
      \?context=(?P<context>\{.+\})&?
  """,
  re.VERBOSE,
)

# Teams message deep link with query string; captures thread_id, message_id, query
re.compile(
  r"""
  http.+message\/(?P<thread_id>.+)\/(?P<message_id>[0-9]+)\?(?P<query>[^<\s.]+)
  """,
  re.VERBOSE,
)

# Teams meetup-join deep link with URL-encoded context; captures thread_id, message_id, context
re.compile(
  r"""
  http.+meetup-join\/(?P<thread_id>.+)\/(?P<message_id>[0-9]+)\?context=(?P<context>[^& ]+)
  """,
  re.VERBOSE,
)

# Teams light meeting link; coords contains URL-safe Base64 JSON
re.compile(
  r"""
  https://teams\.(?:microsoft\.com|cloud\.microsoft)/+light-meetings/launch\?(?P<query>[^<\s]+)
  """,
  re.VERBOSE,
)

# Teams personal meeting invite (teams.live.com) with optional password parameter "p"
re.compile(
  r"""
  https://teams\.live\.com/+meet/(?P<meeting_id>[0-9]+)((.*)p=(?P<meeting_password>[^;&#,<"\n >]+))?
  """,
  re.VERBOSE,
)

# Teams short business meeting invite (commercial, GCC High, and DoD) with optional password parameter "p"
re.compile(
  r"""
  https://(?:teams\.(?:microsoft\.com|cloud\.microsoft)|(?:gov|dod)\.teams\.microsoft\.us)/+meet/(?P<meeting_id>[0-9]+)((.*)p=(?P<meeting_password>[^;&#,<"\n >]+))?
  """,
  re.VERBOSE,
)

# Teams launcher wrapper URL; unwrap and validate its url parameter before matching
re.compile(
    r"^https://(?P<host>teams\.(?:microsoft\.com|cloud\.microsoft|live\.com))"
    r"/dl/launcher/launcher\.html\?(?P<query>[^<\s\"]+)"
)

```

## Webex

```python
import re

# Webex join-service URL (preserved as a URL rather than split into meeting IDs)
re.compile(
    r"""
    (?P<join_service_url>
        (?:https?:\/\/)?
        (?:www\.)?
        [A-Za-z0-9.-]+\.webex\.com\/wbxmjs\/joinservice\/[^\s<>\"]+
    )
    """,
    re.VERBOSE,
)

# General Webex meeting
re.compile(
  r"""
  \.webex\.com\/
  (?P<path>.+?)\/j\.php\?(?:.+?)?
  MTID=(?P<mtid>[A-Za-z0-9]+)
  """,
  re.VERBOSE,
)

# Personal Webex 
re.compile(
  r"""
  \.webex\.com\/(?:meet|join)\/
  (?P<personal_room_id>.[^\#\?\/ \s<>"]+)
  """,
  re.VERBOSE,
)
```

## GoTo Meeting

```python
import re

re.compile(
  r"(http.+)?(app\.goto\.com\/meeting|meet\.goto\.com|gotomeet\.me|global\.gotomeeting\.com\/join)\/(?P<id>[\w/-]*\w)"
)
```

# Meeting URL API Response

## Zoom

```
{
  // ...
  "meeting_url": {
    "meeting_id": string,
    "meeting_password": string | null,
    "platform": "zoom"
  }
}
```

## Google Meet

```
{
  // ...
  "meeting_url": {
    "meeting_id": string,
    "platform": "google_meet"
  }
}
```

## Microsoft Teams

```
{
  // ...
  "meeting_url": {
    "meeting_id": string | null,
    "meeting_password": string | null,
    "organizer_id": string | null,
    "tenant_id": string | null,
    "message_id": string | null,
    "thread_id": string | null,
    "business_meeting_id": string | null,
    "business_meeting_password": string | null,
    "platform": "microsoft_teams" | "microsoft_teams_live"
  }
}
```

> 📘 Microsoft Teams URL differences
>
> Microsoft Teams meeting URL use different parameters depending on the Teams version being used.
>
> The table below shows what you can expect for each Teams version meeting URL.
>
> | Version                | Base URL                 | Non-null Parameters                                                                              |
> | ---------------------- | ------------------------ | ------------------------------------------------------------------------------------------------ |
> | Teams for Business     | `teams.microsoft.com`    | `organizer_id`<br />`tenant_id`<br />`message_id`<br />`thread_id`                               |
> | Teams for Personal use | `teams.live.com`         | `meeting_id`<br />`meeting_password`                                                             |
> | Teams GCC High         | `gov.teams.microsoft.us` | `teams_cloud` (`"gcch"`)<br />`organizer_id`<br />`tenant_id`<br />`message_id`<br />`thread_id` |
> | Teams DoD              | `dod.teams.microsoft.us` | `teams_cloud` (`"dod"`)<br />`organizer_id`<br />`tenant_id`<br />`message_id`<br />`thread_id`  |
>
> The government-cloud rows show deep-link parameters. For short `/meet/` URLs, `business_meeting_id` and optional `business_meeting_password` replace the organizer, tenant, message, and thread fields. Both formats include `teams_cloud` and use `platform: "microsoft_teams"`.

## Webex

```
{
  // ...
  "meeting_url": {
    "meeting_subdomain": string,
    "meeting_mtid": string,
    "meeting_path": string,  
    "platform": "webex"
  }
}
```

or

```
{
  // ...
  "meeting_url": {
    "meeting_subdomain": string,
    "meeting_personal_room_id": string,
    "platform": "webex"
  }
}
```

## GoTo

```
{
  // ...
  "meeting_url": {
    "meeting_id": "680504437",
    "platform": "goto_meeting"
  }
}
```

<br />

# Wrapped Meeting URL's

Some of your users may have "wrapped" meeting links on their calendars, which are not from the meeting platform directly but instead redirect to a meeting URL.

Recall handles the following wrapped URL's automatically, so no additional unwrapping is needed before scheduling bots for these URL's:

* Mimecast
* Proofpoint URL Defense links
* Calendly redirect links

<br />
