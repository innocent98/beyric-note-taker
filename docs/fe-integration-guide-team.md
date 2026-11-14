# Frontend integration guide: Team (W3)

Everything the Team screen, the invite dialog, the invite link page, the "Share with team" switch, the teammate chips on Tasks and "Invite them to the team" in the task drawer need. **Every payload, status code and error string below is pasted from a real response** captured by `scripts/capture_team_api.py` against the running app (real HTTP, real sessions and CSRF, real database and migrations, real sign-up and email confirmation, the real mail builder). The files are in `docs/fe-captures/team-w3/`; run the script again to re-derive any of it. Long values are shortened with an explicit `<… omitted>` marker; nothing is tidied or invented. What could not be run live is marked **NOT LIVE** where it matters and listed in the verification table at the end. Names and addresses are placeholders; tokens are masked (`<invite-token>`).

Field-by-field shapes are in `docs/openapi.json` (generated into `web/lib/api/schema.d.ts`). This guide is for behaviour.

## What the feature is, in your terms

Every account already has a **team** (a team of one: just them). Teams are small: people you work with, who share a **task board** and can be shown **meetings you choose to share**. Meetings are private unless the person who recorded them shares them. A person is on **one team at a time**.

* An **owner** or **admin** invites people by email. The invitee clicks the link in the mail and joins.
* A new person (no account) signs up with the link, even while sign-up is closed or the pilot is full; they join when they confirm their email.
* Someone who already has an account signs in and accepts. **Their team of one disappears** into the one they join: their tasks move over; their meetings stay theirs and stay private.
* Roles: **owner** (the creator), **admin**, **member**. The owner can't be removed or leave while others remain (the pilot admin hands the team over by hand).

Not in this version (nothing in the API, no control): transferring ownership from the app, a second team per person, "Add email" beside a non-member participant (waits for W6), projects/sprints (W4/W9).

## Conventions (same as the rest of the API)

Session cookie; every non-GET sends `X-CSRF-Token` (from `GET /api/session`); errors are `{"error": {"code", "message"}}`, validation errors add `fields`. An id on **another team is 404, never 403**. A call the caller's role does not allow, on their own team, is **403 `team_forbidden`**. Ids are integers. **`user_id` is the id of a person everywhere** (assignee ids on tasks, member ids here): `/api/team/members/{user_id}`.

Logged out:

`GET /api/team` as **visitor** → **401**  
*capture `07-team-logged-out.json`*  
No login: 401.

Response body:
```json
{
  "error": {
    "code": "not_authenticated",
    "message": "Log in first."
  }
}
```


## Reading the team

### `GET /api/team`: the team and what the caller may do

`GET /api/team` as **dan** → **200**  
*capture `08-team-of-one.json`*  
Every account has a team from sign-up.

Response body:
```json
{
  "id": 2,
  "name": "My team",
  "share_new_meetings": false,
  "my_role": "owner",
  "member_count": 1,
  "pending_invites": 0,
  "can_manage": true,
  "can_change_roles": true
}
```

`GET /api/team` as **dan** → **200**  
*capture `29-team-three.json`*

Response body:
```json
{
  "id": 2,
  "name": "My team",
  "share_new_meetings": false,
  "my_role": "owner",
  "member_count": 3,
  "pending_invites": 0,
  "can_manage": true,
  "can_change_roles": true
}
```


Use `can_manage` (owner or admin) to show **Invite people** and the team settings; `can_change_roles` (owner) to enable the role select. **Don't derive these from `my_role`.**

### `GET /api/team/members`: members and open invitations

`GET /api/team/members` as **dan** → **200**  
*capture `09-members-of-one.json`*

Response body:
```json
{
  "members": [
    {
      "user_id": 2,
      "name": "Dan Okoro",
      "email": "dan@example.test",
      "role": "owner",
      "is_you": true,
      "aliases": [],
      "open_tasks": 0,
      "joined_at": "2026-11-05T20:13:31.142139Z",
      "can_change_role": false,
      "can_edit_aliases": true,
      "can_remove": false
    }
  ],
  "invites": []
}
```

`GET /api/team/members` as **dan** → **200**  
*capture `12-members-with-invites.json`*  
`invites` lists the open ones; no token anywhere.

Response body:
```json
{
  "members": [
    {
      "user_id": 2,
      "name": "Dan Okoro",
      "email": "dan@example.test",
      "role": "owner",
      "is_you": true,
      "aliases": [],
      "open_tasks": 0,
      "joined_at": "2026-11-05T20:13:31.142139Z",
      "can_change_role": false,
      "can_edit_aliases": true,
      "can_remove": false
    }
  ],
  "invites": [
    {
      "id": 1,
      "email": "ada@example.test",
      "name": "Ada L",
      "role": "member",
      "status": "pending",
      "invited_by": "Dan Okoro",
      "last_sent_at": "2026-11-05T20:13:31.873140Z",
      "expires_at": "2026-11-12T20:13:31.873140Z"
    },
    {
      "id": 2,
      "email": "chidi@example.test",
      "name": null,
      "role": "member",
      "status": "pending",
      "invited_by": "Dan Okoro",
      "last_sent_at": "2026-11-05T20:13:31.873140Z",
      "expires_at": "2026-11-12T20:13:31.873140Z"
    }
  ]
}
```


**Field-nesting traps**

* `members[]` and `invites[]` are two lists in one object. Invited people are **not** members: they have no `user_id`, only an invite `id`. Task assignee ids and the chips only ever use members.
* Every member row carries the caller's permissions over **that row**: `can_change_role`, `can_edit_aliases`, `can_remove`. For your own row `can_remove` means **Leave team** (false for the owner). Show only the buttons whose flag is true.
* `invites[].status` is `pending` or `expired`. An expired invite stays on the list with **Resend**. `TeamOut.pending_invites` counts only live (`pending`) ones.
* `name` is the account name, or the part of the email before the `@` when there is none. Always plain text.
* `aliases` is plain text, 0 to 20 entries.

What a **member** (not owner/admin) sees of the same list:

`GET /api/team/members` as **ada** → **200**  
*capture `33-member-members.json`*  
Each row says what the caller may do (`can_remove`, `can_change_role`, `can_edit_aliases`).

Response body:
```json
{
  "members": [
    {
      "user_id": 2,
      "name": "Dan Okoro",
      "email": "dan@example.test",
      "role": "owner",
      "is_you": false,
      "aliases": [],
      "open_tasks": 0,
      "joined_at": "2026-11-05T20:13:31.142139Z",
      "can_change_role": false,
      "can_edit_aliases": false,
      "can_remove": false
    },
    {
      "user_id": 5,
      "name": "Ada Lovelace",
      "email": "ada@example.test",
      "role": "member",
      "is_you": true,
      "aliases": [
        "Ada L"
      ],
      "open_tasks": 0,
      "joined_at": "2026-11-05T20:13:32.903718Z",
      "can_change_role": false,
      "can_edit_aliases": true,
      "can_remove": true
    },
    {
      "user_id": 3,
      "name": "Chidi Eze",
      "email": "chidi@example.test",
      "role": "member",
      "is_you": false,
      "aliases": [],
      "open_tasks": 0,
      "joined_at": "2026-11-05T20:13:33.281663Z",
      "can_change_role": false,
      "can_edit_aliases": false,
      "can_remove": false
    }
  ],
  "invites": []
}
```


## Inviting people

### `POST /api/team/invites`

Body: `invitees` (1 to 10, each `{email, name?}`) and `role` (`member`, or `admin` when the caller is the owner). `name` is "what you call them": it becomes one of their aliases when they join, so meetings that say "Ada" match them.

`POST /api/team/invites` as **dan** → **200**  
*capture `10-invite.json`*  
One new person (needs a pilot place), one existing account, one bad address. Same `status` and shape for the first two: the answer never says who has an account.

Request body:
```json
{
  "invitees": [
    {
      "email": "Ada@Example.test",
      "name": "Ada L"
    },
    {
      "email": "chidi@example.test"
    },
    {
      "email": "not an email"
    }
  ],
  "role": "member"
}
```

Response body:
```json
{
  "results": [
    {
      "email": "ada@example.test",
      "status": "sent",
      "invite": {
        "id": 1,
        "email": "ada@example.test",
        "name": "Ada L",
        "role": "member",
        "status": "pending",
        "invited_by": "Dan Okoro",
        "last_sent_at": "2026-11-05T20:13:31.873140Z",
        "expires_at": "2026-11-12T20:13:31.873140Z"
      }
    },
    {
      "email": "chidi@example.test",
      "status": "sent",
      "invite": {
        "id": 2,
        "email": "chidi@example.test",
        "name": null,
        "role": "member",
        "status": "pending",
        "invited_by": "Dan Okoro",
        "last_sent_at": "2026-11-05T20:13:31.873140Z",
        "expires_at": "2026-11-12T20:13:31.873140Z"
      }
    },
    {
      "email": "not an email",
      "status": "invalid_email",
      "invite": null
    }
  ]
}
```


Per-address `status`:

| `status` | Meaning |
|---|---|
| `sent` | New invitation, mail queued. |
| `resent` | An open invitation existed: a new link was mailed (the old link stopped working). |
| `already_invited` | One was sent less than a minute ago: nothing sent. `invite` is the existing one. |
| `already_member` | They are on this team already. |
| `invalid_email` | Not an address we accept. The rest of the request still went through. |

`POST /api/team/invites` as **dan** → **200**  
*capture `11-invite-again.json`*  
Within a minute of the last mail: `already_invited`, no second mail.

Request body:
```json
{
  "invitees": [
    {
      "email": "ada@example.test"
    }
  ],
  "role": "member"
}
```

Response body:
```json
{
  "results": [
    {
      "email": "ada@example.test",
      "status": "already_invited",
      "invite": {
        "id": 1,
        "email": "ada@example.test",
        "name": "Ada L",
        "role": "member",
        "status": "pending",
        "invited_by": "Dan Okoro",
        "last_sent_at": "2026-11-05T20:13:31.873140Z",
        "expires_at": "2026-11-12T20:13:31.873140Z"
      }
    }
  ]
}
```


The **answer never says whether an address has an account** (the first two addresses above have the same `status` and shape). Don't build UI that could.

**Whole-request errors** (nothing from the request is kept):

`POST /api/team/invites` as **dan** → **409**  
*capture `57-invite-pilot-full.json`*  
No free place: 409 `pilot_full`, nothing sent, nobody invited.

Request body:
```json
{
  "invitees": [
    {
      "email": "newcomer@example.test"
    }
  ],
  "role": "member"
}
```

Response body:
```json
{
  "error": {
    "code": "pilot_full",
    "message": "The pilot has no free places. Ask the pilot admin."
  }
}
```


`pilot_full` (409): an invitation to someone **without an account** takes one of the pilot's places (invitees count toward the 10 accounts). The message is shown to the user as is. An address that already has an account never needs a place:

`POST /api/team/invites` as **dan** → **200**  
*capture `58-invite-existing-when-full.json`*  
An account needs no place: it still works.

Request body:
```json
{
  "invitees": [
    {
      "email": "eve@example.test"
    }
  ],
  "role": "member"
}
```

Response body:
```json
{
  "results": [
    {
      "email": "eve@example.test",
      "status": "sent",
      "invite": {
        "id": 4,
        "email": "eve@example.test",
        "name": null,
        "role": "member",
        "status": "pending",
        "invited_by": "Dan Okoro",
        "last_sent_at": "2026-11-05T20:13:33.659577Z",
        "expires_at": "2026-11-12T20:13:33.659577Z"
      }
    }
  ]
}
```


* **NOT LIVE:** `409 invite_limit` ("You have reached the limit for invitations. Revoke one, or try again tomorrow.") when a team has 10 open invitations or has created 20 in 24 hours. Message copied from `app/team_invites.py`; the behaviour is covered by `tests/test_team.py::test_a_team_holds_ten_open_invitations_and_twenty_a_day`.

Errors for a member and for an admin inviting an admin:

`POST /api/team/invites` as **ada** → **403**  
*capture `30-member-invite.json`*  
403 `team_forbidden`.

Request body:
```json
{
  "invitees": [
    {
      "email": "x@example.test"
    }
  ],
  "role": "member"
}
```

Response body:
```json
{
  "error": {
    "code": "team_forbidden",
    "message": "Only an owner or admin of the team can do that."
  }
}
```

`POST /api/team/invites` as **chidi** → **403**  
*capture `38-admin-invites-admin.json`*  
Only the owner may invite an admin: 403.

Request body:
```json
{
  "invitees": [
    {
      "email": "y@example.test"
    }
  ],
  "role": "admin"
}
```

Response body:
```json
{
  "error": {
    "code": "team_forbidden",
    "message": "Only the owner of the team can invite an admin."
  }
}
```


### The mail

The invitee gets the designer's "Team invite" mail. What the mail provider would be handed (captured through the real builder and a capturing mailer, **not Resend**):

*capture `14-mail-rendered.json`*  
What the mail provider would be handed (CAPTURING MAILER, not Resend).
```json
[
  {
    "to": "ada@example.test",
    "subject": "Dan Okoro invited you to join their team",
    "text": "Dan Okoro invited you to join their team\n\nJoin the team on Beyric Note Taker to see shared meeting notes and the tasks assigned to you after each call.\n\nAccept invite:\nhttps://notes.example.test/accept-invite?token=<invite-token-1>\n\nThis invite expires in 7 days. If you were not expecting it, you can ignore this email.\n\nBeyric Note Taker, a product of Beyrichq LTD",
    "html": "<… omitted>"
  }
]
```


The link is `/accept-invite?token=…`. **You build that page** (see next section). The link works once, for 7 days; **Resend** gives a new link and restarts the 7 days.

## The invitation link page (`/accept-invite?token=…`)

1. Call the public preview with the token. It tells the page what to say and which path to take.

`GET /api/team/invites/preview?token=<invite-token-1>` as **visitor** → **200**  
*capture `15-preview-new-person.json`*  
PUBLIC: the token is the credential. `has_account` false: sign up with the link.

Response body:
```json
{
  "email": "ada@example.test",
  "name": "Ada L",
  "role": "member",
  "team_name": "My team",
  "inviter_name": "Dan Okoro",
  "has_account": false,
  "expires_at": "2026-11-12T20:13:31.873140Z"
}
```

`GET /api/team/invites/preview?token=<invite-token-2>` as **visitor** → **200**  
*capture `16-preview-existing-account.json`*  
`has_account` true: sign in, then accept.

Response body:
```json
{
  "email": "chidi@example.test",
  "name": null,
  "role": "member",
  "team_name": "My team",
  "inviter_name": "Dan Okoro",
  "has_account": true,
  "expires_at": "2026-11-12T20:13:31.873140Z"
}
```

`GET /api/team/invites/preview?token=<invite-token-3>` as **visitor** → **400**  
*capture `17-preview-bad-token.json`*  
400 `invite_invalid`: the one answer for every dead link.

Response body:
```json
{
  "error": {
    "code": "invite_invalid",
    "message": "That invitation is not valid any more. Ask for a new one."
  }
}
```


2. Branch on `has_account`:

   * **`true`**: ask them to sign in (the signed-in account **must be the invited address**), then `POST /api/team/invites/accept`.
   * **`false`**: show **Create account** with the **email pre-filled and locked** to `email`, and pass the token as `invite_token` on `POST /api/signup`. They confirm their email as usual and are on the team when they do; there is no accept call. They can also use **Continue with Google** with the same address (**NOT LIVE** in the captures: Google is not reachable here; the same joining code is covered by `tests/test_team.py::test_a_new_person_signing_in_with_google_joins_the_team_without_a_token`).

### New person: sign up with the link (works while sign-up is closed)

`POST /api/signup` as **ada** → **403**  
*capture `19-signup-without-link.json`*  
No link: the pilot is closed, 403 `pilot_closed`.

Request body (**since 2026-11-14 it also needs `"accept_recording": true`**, the recording-consent box,
see `docs/fe-integration-guide-recording-ack.md`; this capture predates the field):
```json
{
  "name": "Ada Lovelace",
  "email": "ada@example.test",
  "password": "ada-chosen-password-5",
  "accept_terms": true
}
```

Response body:
```json
{
  "error": {
    "code": "pilot_closed",
    "message": "Sign-up is not open yet. Check back soon or join the waiting list."
  }
}
```

`POST /api/signup` as **ada** → **400**  
*capture `20-signup-with-someone-elses-link.json`*  
A link works for its own address only: 400 `invite_invalid`.

Request body:
```json
{
  "name": "Eve",
  "email": "eve2@example.test",
  "password": "ada-chosen-password-5",
  "accept_terms": true,
  "invite_token": "<invite-token>"
}
```

Response body:
```json
{
  "error": {
    "code": "invite_invalid",
    "message": "That invitation is not valid any more. Join the waiting list to get a new one."
  }
}
```

`POST /api/signup` as **ada** → **202**  
*capture `21-signup-with-link.json`*  
202, as for every sign-up. The pilot is closed and this still works.

Request body:
```json
{
  "name": "Ada Lovelace",
  "email": "ada@example.test",
  "password": "ada-chosen-password-5",
  "accept_terms": true,
  "invite_token": "<invite-token>"
}
```

Response body:
```json
{
  "status": "check_your_email"
}
```

`POST /api/verify-email` as **ada** → **200**  
*capture `22-verify-email.json`*  
Confirming the address is what joins the team.

Request body:
```json
{
  "token": "<token>",
  "password": "ada-chosen-password-5"
}
```

Response body:
```json
{
  "status": "verified"
}
```

`POST /api/session` as **ada** → **200**  
*capture `23-ada-login.json`*

Request body:
```json
{
  "email": "ada@example.test",
  "password": "ada-chosen-password-5"
}
```

Response body:
```json
{
  "authenticated": true,
  "csrf_token": "<csrf-H>",
  "user": {
    "email": "ada@example.test",
    "name": "Ada Lovelace",
    "is_admin": false,
    "email_verified": true,
    "time_zone": null
  },
  "usage": {
    "used_seconds": 0,
    "cap_seconds": 18000
  },
  "unread_notifications": 0,
  "badges": {
    "review_open": 0,
    "tasks_new": 0
  }
}
```

`GET /api/team` as **ada** → **200**  
*capture `24-ada-team.json`*  
`my_role` member, `can_manage` false.

Response body:
```json
{
  "id": 2,
  "name": "My team",
  "share_new_meetings": false,
  "my_role": "member",
  "member_count": 2,
  "pending_invites": 1,
  "can_manage": false,
  "can_change_roles": false
}
```

`GET /api/team/members` as **dan** → **200**  
*capture `25-members-after-ada.json`*  
Ada is on the team, with 'Ada L' as an alias; her invite is gone from `invites`.

Response body:
```json
{
  "members": [
    {
      "user_id": 2,
      "name": "Dan Okoro",
      "email": "dan@example.test",
      "role": "owner",
      "is_you": true,
      "aliases": [],
      "open_tasks": 0,
      "joined_at": "2026-11-05T20:13:31.142139Z",
      "can_change_role": false,
      "can_edit_aliases": true,
      "can_remove": false
    },
    {
      "user_id": 5,
      "name": "Ada Lovelace",
      "email": "ada@example.test",
      "role": "member",
      "is_you": false,
      "aliases": [
        "Ada L"
      ],
      "open_tasks": 0,
      "joined_at": "2026-11-05T20:13:32.903718Z",
      "can_change_role": true,
      "can_edit_aliases": true,
      "can_remove": true
    }
  ],
  "invites": [
    {
      "id": 2,
      "email": "chidi@example.test",
      "name": null,
      "role": "member",
      "status": "pending",
      "invited_by": "Dan Okoro",
      "last_sent_at": "2026-11-05T20:13:31.873140Z",
      "expires_at": "2026-11-12T20:13:31.873140Z"
    }
  ]
}
```


**Traps:**

* The `invite_invalid` message on sign-up still says "Join the waiting list to get a new one." (it is the shared wording for every kind of invitation). **Don't show it for a team link**: show "This invitation is not valid any more. Ask the person who invited you for a new one."
* Signing up never signs the person in; they confirm their email first (same as any sign-up). Show the usual "check your email" step and then the sign-in form.
* Joining happens at **email confirmation**, not at sign-up. Until then they are not on the team.

### Existing account: accept

`POST /api/team/invites/accept` as **chidi** → **400**  
*capture `26-accept-wrong-token.json`*  
400 `invite_invalid`: one answer for every bad link.

Request body:
```json
{
  "token": "<token>"
}
```

Response body:
```json
{
  "error": {
    "code": "invite_invalid",
    "message": "That invitation is not valid any more. Ask for a new one."
  }
}
```

`POST /api/team/invites/accept` as **chidi** → **200**  
*capture `27-accept.json`*  
Chidi's team of one dissolves into Dan's.

Request body:
```json
{
  "token": "<token>"
}
```

Response body:
```json
{
  "id": 2,
  "name": "My team",
  "share_new_meetings": false,
  "my_role": "member",
  "member_count": 3,
  "pending_invites": 0,
  "can_manage": false,
  "can_change_roles": false
}
```

`POST /api/team/invites/accept` as **chidi** → **400**  
*capture `28-accept-again.json`*  
Single use: 400 `invite_invalid`.

Request body:
```json
{
  "token": "<token>"
}
```

Response body:
```json
{
  "error": {
    "code": "invite_invalid",
    "message": "That invitation is not valid any more. Ask for a new one."
  }
}
```


The response is the **new** team (`TeamOut`). After it, **reload everything team-shaped** (tasks, members): their team of one is gone, their tasks now belong to the new team, and their own meetings are no longer shared with anyone.

* `400 invite_invalid` is the **one answer** for a wrong, used, expired, revoked link, and for a link that was sent to a different address than the signed-in account's.
* **NOT LIVE (covered by tests only):** `409 already_on_team` ("You are on another team. Leave it first, then accept this invitation.") when the person is on a team with other people. The link is **not** used up in that case. Source: `app/teams.py`; test `tests/test_team.py::test_someone_on_a_team_with_other_people_must_leave_it_first`. Show the message and a **Leave team** route.

**UX consequences to surface (explicit instructions):**

* Before an **existing** account accepts, tell them: "Your own team will be replaced. Your tasks move to the new team. Your meetings stay yours and private." Don't accept silently.
* A person who joins is not shown any meeting until the owner shares one with the team.

## Resend and revoke

`POST /api/team/invites/3/resend` as **dan** → **429**  
*capture `49-resend-too-soon.json`*  
Within a minute of the last mail: 429 `too_soon`.

Response body:
```json
{
  "error": {
    "code": "too_soon",
    "message": "That invitation was sent a moment ago. Try again shortly."
  }
}
```

`GET /api/team/members` as **dan** → **200**  
*capture `50-members-expired-invite.json`*  
TIME FAKED in the database: the invite reads `expired`.

Response body:
```json
{
  "members": [
    {
      "user_id": 2,
      "name": "Dan Okoro",
      "email": "dan@example.test",
      "role": "owner",
      "is_you": true,
      "aliases": [],
      "open_tasks": 0,
      "joined_at": "2026-11-05T20:13:31.142139Z",
      "can_change_role": false,
      "can_edit_aliases": true,
      "can_remove": false
    },
    {
      "user_id": 5,
      "name": "Ada Lovelace",
      "email": "ada@example.test",
      "role": "member",
      "is_you": false,
      "aliases": [
        "Ada"
      ],
      "open_tasks": 0,
      "joined_at": "2026-11-05T20:13:32.903718Z",
      "can_change_role": true,
      "can_edit_aliases": true,
      "can_remove": true
    },
    {
      "user_id": 3,
      "name": "Chidi Eze",
      "email": "chidi@example.test",
      "role": "admin",
      "is_you": false,
      "aliases": [],
      "open_tasks": 0,
      "joined_at": "2026-11-05T20:13:33.281663Z",
      "can_change_role": true,
      "can_edit_aliases": true,
      "can_remove": true
    }
  ],
  "invites": [
    {
      "id": 3,
      "email": "later@example.test",
      "name": null,
      "role": "member",
      "status": "expired",
      "invited_by": "Dan Okoro",
      "last_sent_at": "2026-10-28T20:13:33.547631Z",
      "expires_at": "2026-11-04T20:13:33.547638Z"
    }
  ]
}
```

`POST /api/team/invites/3/resend` as **dan** → **200**  
*capture `51-resend.json`*  
A new link, 7 more days; the old link stops working.

Response body:
```json
{
  "id": 3,
  "email": "later@example.test",
  "name": null,
  "role": "member",
  "status": "pending",
  "invited_by": "Dan Okoro",
  "last_sent_at": "2026-11-05T20:13:33.565654Z",
  "expires_at": "2026-11-12T20:13:33.565654Z"
}
```

`DELETE /api/team/invites/3` as **dan** → **204**  
*capture `53-revoke.json`*

No response body.

`DELETE /api/team/invites/3` as **dan** → **404**  
*capture `54-revoke-again.json`*  
404: no open invitation by that id.

Response body:
```json
{
  "error": {
    "code": "not_found",
    "message": "No such invitation."
  }
}
```


* `POST /api/team/invites/{id}/resend` → the invite (`TeamInviteOut`); **429 `too_soon`** inside a minute of the last mail (disable the button for a minute after a send, but still handle it). Resending an expired invitation to a new person needs a free pilot place again: **NOT LIVE** `409 pilot_full` (covered by `tests/test_team.py::test_resending_an_expired_invitation_to_a_new_person_needs_a_place_again`).
* `DELETE /api/team/invites/{id}` → **204**; the link stops working and the pilot place is freed. A closed (accepted or revoked) or foreign invitation is 404.
* The raw link is **never returned by the API**: only the mail has it.

An invitation also stops working when its sender is removed from (or leaves) the team.

## Roles

`PATCH /api/team/members/3` as **dan** → **200**  
*capture `37-role-to-admin.json`*  
Owner only. The answer is the member list.

Request body:
```json
{
  "role": "admin"
}
```

Response body:
```json
{
  "members": [
    {
      "user_id": 2,
      "name": "Dan Okoro",
      "email": "dan@example.test",
      "role": "owner",
      "is_you": true,
      "aliases": [],
      "open_tasks": 0,
      "joined_at": "2026-11-05T20:13:31.142139Z",
      "can_change_role": false,
      "can_edit_aliases": true,
      "can_remove": false
    },
    {
      "user_id": 5,
      "name": "Ada Lovelace",
      "email": "ada@example.test",
      "role": "member",
      "is_you": false,
      "aliases": [
        "Ada L"
      ],
      "open_tasks": 0,
      "joined_at": "2026-11-05T20:13:32.903718Z",
      "can_change_role": true,
      "can_edit_aliases": true,
      "can_remove": true
    },
    {
      "user_id": 3,
      "name": "Chidi Eze",
      "email": "chidi@example.test",
      "role": "admin",
      "is_you": false,
      "aliases": [],
      "open_tasks": 0,
      "joined_at": "2026-11-05T20:13:33.281663Z",
      "can_change_role": true,
      "can_edit_aliases": true,
      "can_remove": true
    }
  ],
  "invites": []
}
```

`PATCH /api/team/members/5` as **chidi** → **403**  
*capture `39-admin-changes-role.json`*  
Admins cannot change roles: 403.

Request body:
```json
{
  "role": "admin"
}
```

Response body:
```json
{
  "error": {
    "code": "team_forbidden",
    "message": "Only the owner of the team can change roles."
  }
}
```

`PATCH /api/team/members/2` as **dan** → **403**  
*capture `40-owner-role.json`*  
The owner's role is fixed: 403.

Request body:
```json
{
  "role": "member"
}
```

Response body:
```json
{
  "error": {
    "code": "team_forbidden",
    "message": "Only the owner of the team can change roles."
  }
}
```

`PATCH /api/team/members/5` as **dan** → **422**  
*capture `41-role-owner-invalid.json`*  
`owner` is not a role you can give: 422.

Request body:
```json
{
  "role": "owner"
}
```

Response body:
```json
{
  "error": {
    "code": "validation_error",
    "message": "Some fields are missing or not valid.",
    "fields": [
      {
        "field": "role",
        "message": "Input should be 'admin' or 'member'"
      }
    ]
  }
}
```


| | owner | admin | member |
|---|---|---|---|
| Read team, members | yes | yes | yes |
| Rename team, "Share new meetings" default | yes | yes | no |
| Invite a member | yes | yes | no |
| Invite an admin | yes | no | no |
| Change roles | yes | no | no |
| Remove a member | yes | yes | no |
| Remove an admin | yes | no | no |
| Remove the owner | never | never | never |
| Edit anyone's aliases | yes | yes | only their own |
| Leave | no (hand the team over first) | yes | yes |

Role changes return the member list (`TeamMembers`). `role` accepts only `admin` or `member`.

Roles on the **tasks** side (who may reassign or delete tasks) are in the Tasks guide and follow the same `owner/admin` vs `member` split.

## Aliases ("Matched in meetings as")

`PATCH /api/team/members/{user_id}` with `aliases` **replaces the whole list**. Entries are trimmed, one line, at most 60 characters, duplicates (ignoring case and accents) are dropped, empty entries are dropped, at most 20. The answer is the member list: show the **stored** values, not what the user typed.

`PATCH /api/team/members/5` as **chidi** → **200**  
*capture `42-admin-edits-aliases.json`*  
Aliases replace the list; cleaned and de-duplicated.

Request body:
```json
{
  "aliases": [
    "Ada L",
    "Countess",
    " countess ",
    ""
  ]
}
```

Response body:
```json
{
  "members": [
    {
      "user_id": 2,
      "name": "Dan Okoro",
      "email": "dan@example.test",
      "role": "owner",
      "is_you": false,
      "aliases": [],
      "open_tasks": 0,
      "joined_at": "2026-11-05T20:13:31.142139Z",
      "can_change_role": false,
      "can_edit_aliases": true,
      "can_remove": false
    },
    {
      "user_id": 5,
      "name": "Ada Lovelace",
      "email": "ada@example.test",
      "role": "member",
      "is_you": false,
      "aliases": [
        "Ada L",
        "Countess"
      ],
      "open_tasks": 0,
      "joined_at": "2026-11-05T20:13:32.903718Z",
      "can_change_role": false,
      "can_edit_aliases": true,
      "can_remove": true
    },
    {
      "user_id": 3,
      "name": "Chidi Eze",
      "email": "chidi@example.test",
      "role": "admin",
      "is_you": true,
      "aliases": [],
      "open_tasks": 0,
      "joined_at": "2026-11-05T20:13:33.281663Z",
      "can_change_role": false,
      "can_edit_aliases": true,
      "can_remove": true
    }
  ],
  "invites": []
}
```

`PATCH /api/team/members/5` as **ada** → **200**  
*capture `43-member-edits-own-aliases.json`*

Request body:
```json
{
  "aliases": [
    "Ada"
  ]
}
```

Response body:
```json
{
  "members": [
    {
      "user_id": 2,
      "name": "Dan Okoro",
      "email": "dan@example.test",
      "role": "owner",
      "is_you": false,
      "aliases": [],
      "open_tasks": 0,
      "joined_at": "2026-11-05T20:13:31.142139Z",
      "can_change_role": false,
      "can_edit_aliases": false,
      "can_remove": false
    },
    {
      "user_id": 5,
      "name": "Ada Lovelace",
      "email": "ada@example.test",
      "role": "member",
      "is_you": true,
      "aliases": [
        "Ada"
      ],
      "open_tasks": 0,
      "joined_at": "2026-11-05T20:13:32.903718Z",
      "can_change_role": false,
      "can_edit_aliases": true,
      "can_remove": true
    },
    {
      "user_id": 3,
      "name": "Chidi Eze",
      "email": "chidi@example.test",
      "role": "admin",
      "is_you": false,
      "aliases": [],
      "open_tasks": 0,
      "joined_at": "2026-11-05T20:13:33.281663Z",
      "can_change_role": false,
      "can_edit_aliases": false,
      "can_remove": false
    }
  ],
  "invites": []
}
```

`PATCH /api/team/members/2` as **ada** → **403**  
*capture `44-member-edits-other-aliases.json`*  
403.

Request body:
```json
{
  "aliases": [
    "Boss"
  ]
}
```

Response body:
```json
{
  "error": {
    "code": "team_forbidden",
    "message": "You can only change your own names."
  }
}
```

`PATCH /api/team/members/5` as **ada** → **422**  
*capture `45-aliases-too-many.json`*  
At most 20: 422.

Request body:
```json
{
  "aliases": [
    "a",
    "a",
    "a",
    "a",
    "a",
    "a",
    "a",
    "a",
    "a",
    "a",
    "a",
    "a",
    "a",
    "a",
    "a",
    "a",
    "a",
    "a",
    "a",
    "a",
    "a"
  ]
}
```

Response body:
```json
{
  "error": {
    "code": "validation_error",
    "message": "Some fields are missing or not valid.",
    "fields": [
      {
        "field": "aliases",
        "message": "List should have at most 20 items after validation, not 21"
      }
    ]
  }
}
```


Aliases are what the app uses to match a name said in a meeting to a person. Only **active members** are matched (invited people, suspended accounts: never). That is why a task whose owner is named but not on the team shows **Needs owner** and the drawer's **Invite them to the team** (below).

## Team settings

`PATCH /api/team` as **dan** → **200**  
*capture `46-team-settings.json`*  
`share_new_meetings` applies to the owner's and admins' meetings only, at first notes.

Request body:
```json
{
  "name": "Product team",
  "share_new_meetings": true
}
```

Response body:
```json
{
  "id": 2,
  "name": "Product team",
  "share_new_meetings": true,
  "my_role": "owner",
  "member_count": 3,
  "pending_invites": 0,
  "can_manage": true,
  "can_change_roles": true
}
```

`PATCH /api/team` as **dan** → **422**  
*capture `47-team-settings-empty-name.json`*

Request body:
```json
{
  "name": "   "
}
```

Response body:
```json
{
  "error": {
    "code": "invalid_name",
    "message": "Give the team a name."
  }
}
```

`PATCH /api/team` as **ada** → **403**  
*capture `31-member-patch-team.json`*

Request body:
```json
{
  "name": "Mine"
}
```

Response body:
```json
{
  "error": {
    "code": "team_forbidden",
    "message": "Only an owner or admin of the team can do that."
  }
}
```


`share_new_meetings` is the team default for sharing. **Read this carefully before wording the switch:**

* It is **off** by default.
* It applies **when a meeting's notes are first written** (never when the bot joins, never again when notes are rewritten, so an owner's "unshare" sticks).
* It covers only meetings recorded by the team's **owner and admins**. A member's meetings are **never** shared by it: a member shares their own with the switch on the meeting page.
* Label it so, e.g. "Share new meetings from owners and admins with the team".

## Share a meeting with the team ("Share with team")

`POST /api/meetings/{id}/share` and `DELETE /api/meetings/{id}/share`. **Owner of the meeting only**: anyone else gets 404 (including a teammate who can read it). Both are idempotent and return the meeting (`MeetingDetail`, trimmed here).

A teammate **cannot read** a meeting before it is shared:

`GET /api/meetings/1` as **ada** → **404**  
*capture `59-shared-before.json`*  
Private: 404 for a teammate.

Response body:
```json
{
  "error": {
    "code": "not_found",
    "message": "No such meeting"
  }
}
```

`POST /api/meetings/1/share` as **dan** → **200**  
*capture `60-share.json`*  
The owner shares; the answer is the meeting.

Response body:
```json
{
  "id": 1,
  "title": "Planning call",
  "platform": "google_meet",
  "native_id": "nat-aaaa-bbb",
  "status": "ready",
  "step": "done",
  "status_text": "Ready",
  "status_tone": "success",
  "template_id": 5,
  "template_name": "General",
  "created_at": "2026-11-03T10:00:00Z",
  "tasks_count": 0,
  "failure_reason": null,
  "retry_block": null,
  "retry_block_message": null,
  "can_cancel": false,
  "can_end": false,
  "can_retry": false,
  "can_regenerate": true,
  "can_delete": true,
  "shared_with_team": true,
  "can_share": true,
  "shared_by": null,
  "join_attempts": 1,
  "max_join_attempts": 3,
  "bot_left_early": false,
  "speakers_incomplete": false,
  "refresh_after_seconds": null,
  "worker_stale": false,
  "join_retry_at": null,
  "extraction_status": null,
  "notes": "<… omitted>",
  "notes_html": "<… omitted>",
  "transcript": "<… omitted>",
  "translations": [],
  "duration_seconds": null,
  "participants": [
    "Dan"
  ],
  "decisions_count": 0,
  "review_open_count": 0,
  "tasks": []
}
```

`GET /api/meetings/1` as **ada** → **200**  
*capture `61-shared-read.json`*  
A teammate reads it: `can_share` false, `shared_by` names the owner, every `can_*` false.

Response body:
```json
{
  "id": 1,
  "title": "Planning call",
  "platform": "google_meet",
  "native_id": "nat-aaaa-bbb",
  "status": "ready",
  "step": "done",
  "status_text": "Ready",
  "status_tone": "success",
  "template_id": 5,
  "template_name": "General",
  "created_at": "2026-11-03T10:00:00Z",
  "tasks_count": 0,
  "failure_reason": null,
  "retry_block": null,
  "retry_block_message": null,
  "can_cancel": false,
  "can_end": false,
  "can_retry": false,
  "can_regenerate": false,
  "can_delete": false,
  "shared_with_team": true,
  "can_share": false,
  "shared_by": "Dan Okoro",
  "join_attempts": 1,
  "max_join_attempts": 3,
  "bot_left_early": false,
  "speakers_incomplete": false,
  "refresh_after_seconds": null,
  "worker_stale": false,
  "join_retry_at": null,
  "extraction_status": null,
  "notes": "<… omitted>",
  "notes_html": "<… omitted>",
  "transcript": "<… omitted>",
  "translations": [],
  "duration_seconds": null,
  "participants": [
    "Dan"
  ],
  "decisions_count": 0,
  "review_open_count": 0,
  "tasks": []
}
```


**New fields on the meeting** (also on the owner's own view):

* `shared_with_team` (bool): drives the switch.
* `can_share` (bool): true only for the one who recorded it. **Show the switch only when true.** (In a team of one, sharing does nothing visible: hide the switch when `member_count` is 1.)
* `shared_by` (string or null): for a teammate reading a shared meeting ("Shared by Dan Okoro"); null on your own meetings.

For a teammate every `can_*` action flag is false (teammates read; they never change a meeting).

`POST /api/meetings/1/share` as **ada** → **404**  
*capture `62-teammate-share.json`*  
Only the owner: 404.

Response body:
```json
{
  "error": {
    "code": "not_found",
    "message": "No such meeting"
  }
}
```

`POST /api/meetings/1/share` as **eve** → **404**  
*capture `64-outsider-share.json`*

Response body:
```json
{
  "error": {
    "code": "not_found",
    "message": "No such meeting"
  }
}
```

`DELETE /api/meetings/1/share` as **dan** → **200**  
*capture `65-unshare.json`*

Response body:
```json
{
  "id": 1,
  "title": "Planning call",
  "platform": "google_meet",
  "native_id": "nat-aaaa-bbb",
  "status": "ready",
  "step": "done",
  "status_text": "Ready",
  "status_tone": "success",
  "template_id": 5,
  "template_name": "General",
  "created_at": "2026-11-03T10:00:00Z",
  "tasks_count": 0,
  "failure_reason": null,
  "retry_block": null,
  "retry_block_message": null,
  "can_cancel": false,
  "can_end": false,
  "can_retry": false,
  "can_regenerate": true,
  "can_delete": true,
  "shared_with_team": false,
  "can_share": true,
  "shared_by": null,
  "join_attempts": 1,
  "max_join_attempts": 3,
  "bot_left_early": false,
  "speakers_incomplete": false,
  "refresh_after_seconds": null,
  "worker_stale": false,
  "join_retry_at": null,
  "extraction_status": null,
  "notes": "<… omitted>",
  "notes_html": "<… omitted>",
  "transcript": "<… omitted>",
  "translations": [],
  "duration_seconds": null,
  "participants": [
    "Dan"
  ],
  "decisions_count": 0,
  "review_open_count": 0,
  "tasks": []
}
```

`GET /api/meetings/1` as **ada** → **404**  
*capture `66-shared-after-unshare.json`*

Response body:
```json
{
  "error": {
    "code": "not_found",
    "message": "No such meeting"
  }
}
```


**UX consequences to surface:**

* Sharing shows teammates the **notes, transcript and tasks** of that meeting. Say so next to the switch: "Everyone on your team can read the notes and transcript."
* Unsharing is immediate: teammates lose the meeting at once (404 for them), including tasks assigned to them from it, **except** the title and short quote of tasks assigned to them, which they keep seeing while they are on the team.

## Teammate chips on Tasks

`GET /api/tasks` already returns `people`: the **active members of the caller's team**. Build the chips from it (All tasks, My tasks, one per person, Needs owner). Nothing new to call.

`GET /api/tasks` as **dan** → **200**  
*capture `67-tasks-people.json`*  
`people` is the active members of the team: what the chips are built from.

Response body:
```json
{
  "tasks": [],
  "people": [
    {
      "user_id": 2,
      "name": "Dan Okoro",
      "is_you": true
    },
    {
      "user_id": 5,
      "name": "Ada Lovelace",
      "is_you": false
    },
    {
      "user_id": 3,
      "name": "Chidi Eze",
      "is_you": false
    }
  ],
  "attention": {
    "needs_owner": 0,
    "unverified": 0
  },
  "has_more": false,
  "next_before_id": null
}
```


A task whose owner was said in the meeting but did not match a member has `spoken_owner` set and `assignee` null. For those, the drawer shows **Invite them to the team**: open the invite dialog with `name` = `spoken_owner`, so the name becomes an alias when they join. Show it only when `can_manage` is true (otherwise say "Ask an owner or admin to invite them").

## Leaving and removing

`DELETE /api/team/members/{user_id}` removes someone (or, with your own id, leaves). **204**, no body. What happens (show it in the confirm dialog):

* They get a **fresh team of one** (they stay signed in).
* **Open tasks assigned to them become unassigned.** Done tasks keep their name for history, but they can no longer open them.
* Tasks read from **their own meetings** go with them (unassigned unless they hold them): the old team can no longer read those meetings.
* **Every meeting of theirs shared with the team becomes private.** Meetings never move.
* Invitations they sent stop working.

`DELETE /api/team/members/2` as **dan** → **409**  
*capture `68-owner-leave.json`*  
The owner cannot leave while others remain: 409.

Response body:
```json
{
  "error": {
    "code": "owner_cannot_leave",
    "message": "You own this team. Ask the pilot admin to hand it to someone else before you leave."
  }
}
```

`DELETE /api/team/members/5` as **ada** → **204**  
*capture `69-leave.json`*  
A member leaves: 204. They get a fresh team of one.

No response body.

`GET /api/team` as **ada** → **200**  
*capture `70-after-leave-team.json`*

Response body:
```json
{
  "id": 6,
  "name": "My team",
  "share_new_meetings": false,
  "my_role": "owner",
  "member_count": 1,
  "pending_invites": 0,
  "can_manage": true,
  "can_change_roles": true
}
```

`DELETE /api/team/members/2` as **chidi** → **403**  
*capture `71-admin-removes-owner.json`*  
403.

Response body:
```json
{
  "error": {
    "code": "team_forbidden",
    "message": "Only an owner or admin of the team can do that."
  }
}
```

`DELETE /api/team/members/3` as **dan** → **204**  
*capture `72-remove-admin.json`*  
The owner removes an admin: 204.

No response body.

`DELETE /api/team/members/3` as **ada** → **403**  
*capture `32-member-remove.json`*

Response body:
```json
{
  "error": {
    "code": "team_forbidden",
    "message": "Only an owner or admin of the team can do that."
  }
}
```

`PATCH /api/team/members/3` as **eve** → **404**  
*capture `34-outsider-patch-member.json`*  
Someone on another team: 404, never 403.

Request body:
```json
{
  "aliases": [
    "x"
  ]
}
```

Response body:
```json
{
  "error": {
    "code": "not_found",
    "message": "No such team member."
  }
}
```

`DELETE /api/team/members/5` as **eve** → **404**  
*capture `35-outsider-remove-member.json`*

Response body:
```json
{
  "error": {
    "code": "not_found",
    "message": "No such team member."
  }
}
```

`GET /api/team/members` as **eve** → **200**  
*capture `36-outsider-team.json`*  
Eve sees only her own team of one.

Response body:
```json
{
  "members": [
    {
      "user_id": 4,
      "name": "Eve Outsider",
      "email": "eve@example.test",
      "role": "owner",
      "is_you": true,
      "aliases": [],
      "open_tasks": 0,
      "joined_at": "2026-11-05T20:13:31.142139Z",
      "can_change_role": false,
      "can_edit_aliases": true,
      "can_remove": false
    }
  ],
  "invites": []
}
```


## Account deletion (pilot admin, not the Team screen)

The pilot admin's `DELETE /api/admin/users/{id}` answers **409 `team_owner_has_members`** for someone who owns a team that still has other members, until the admin runs `scripts/transfer_team_owner.py <team id> <new owner email>`. **NOT LIVE** in the captures (admin route, covered by `tests/test_team.py::test_an_owner_with_other_members_cannot_be_deleted_until_the_team_is_handed_over`). Nothing for the app UI to do.

## Verification: what was run live, what was not

| Behaviour | Status |
|---|---|
| Team read, members read with per-row permissions, `invites` list | verified live |
| Invite (new, existing, invalid), already_invited, pilot_full, existing account when full | verified live |
| Member 403 and admin-invites-admin 403 | verified live |
| Preview (new, existing, bad token) | verified live |
| Sign-up with a link while the pilot is closed; someone else's link; confirm email joins the team | verified live |
| Accept, wrong token, accept again | verified live |
| Resend (too soon, expired shown as `expired`, resend), revoke, revoke again, foreign/outsider 404s | verified live (the expiry was **made by editing the row's timestamps**, labelled in the capture) |
| Roles (change, admin cannot, owner fixed, `owner` invalid), aliases (admin, own, other's, too many), team settings, empty name | verified live |
| Share, teammate read, teammate/outsider cannot share, unshare, 404 after unshare | verified live |
| Tasks `people` | verified live |
| Owner cannot leave, member leaves, fresh team, admin cannot remove owner, owner removes admin | verified live |
| Mail rendering | verified live through the real builder; **not sent through Resend** |
| `invite_limit` (409) | NOT LIVE: unit test only |
| `already_on_team` (409) on accept | NOT LIVE: unit test only |
| Google sign-in joining a team | NOT LIVE (needs Google): unit test only |
| `pilot_full` on resending an expired invitation | NOT LIVE: unit test only |
| Share default applied at first notes (`share_new_meetings`) | NOT LIVE through HTTP (the pipeline needs the bot): `tests/test_pipeline.py` |
| Task re-homing on leave/removal and on joining (tasks move; open tasks unassigned) | NOT LIVE through HTTP in the captures (no tasks were created): `tests/test_team.py` |
| Pilot admin 409 `team_owner_has_members` | NOT LIVE: unit test only |
