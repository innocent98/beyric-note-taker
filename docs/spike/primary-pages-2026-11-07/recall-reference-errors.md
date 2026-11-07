SOURCE: https://docs.recall.ai/reference/errors.md
Fetched 2026-11-07 with curl -sL -A 'Mozilla/5.0'

---
updatedAt: 2026-05-31T22:52:19.000Z
agentTools:
  projectIndex: https://docs.recall.ai/llms.txt
---

# API Errors

## API Error Codes

Recall's API uses the following HTTP codes:

| Error Code | Meaning                                                                                                                                                                                                                 |
| :--------- | :---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 200        | OK -- Everything worked as expected.                                                                                                                                                                                    |
| 201        | Bot created successfully.                                                                                                                                                                                               |
| 400        | There was something wrong with your request. Check the response body for a detailed error message. For the error message reference, go [here](https://recallai.readme.io/reference/errors#400-error-message-reference). |
| 401        | Unauthorized -- No valid API key provided.                                                                                                                                                                              |
| 402        | (Self-serve customers) Insufficient credit balance -- Top up balance in the [dashboard](https://us-west-2.recall.ai/dashboard/billing/payment/setup).                                                                   |
| 403        | Request Blocked -- Our WAF blocked your request. Usually due to an [issue](#403-request-blocked) with your payload or your server's IP reputation.                                                                      |
| 405        | Method is not allowed for the endpoint -- if calling DELETE, the bot has already been dispatched.                                                                                                                       |
| 409        | Conflict -- please retry with exponential backoff (see [Scheduling Guide](https://docs.recall.ai/docs/scheduling-guide#handling-409s) and [Idempotency](https://docs.recall.ai/reference/idempotency))                                                                       |
| 429        | Too many requests: Retry after the duration specified in the returned [`Retry-After`](https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Retry-After) header (`Retry-After: <delay-seconds>`).                   |
| 502        | Our servers have dropped the request due to high load -- please retry.                                                                                                                                                  |
| 503        | Our servers have dropped the request due to high load -- please retry.                                                                                                                                                  |
| 504        | Our servers have dropped the request due to high load -- please retry.                                                                                                                                                  |
| 507        | Out of adhoc bots -- Please retry in 30 seconds. Retry after the duration specified in the returned Retry-After header (`Retry-After: <delay-seconds>`)                                                                 |

<br />

## 400 Error Message Reference

| Code                           | Response Body                                                                              | Description                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| :----------------------------- | :----------------------------------------------------------------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `teams_blacklisted_tenant`     |                                                                                            | Microsoft Teams has certain tenants that do not allow bots to join their calls. This error occurs when you attempt to create a bot for a meeting hosted by one of these tenants. There is no workaround for this error.                                                                                                                                                                                                                      |
| `update_bot_failed`            | Only non-dispatched bots can be updated                                                    | This typically happens when [Update Scheduled Bot](https://docs.recall.ai/reference/bot_partial_update) is called on a bot that was already sent to a call. You can verify a scheduled bot was already sent to a call when you receive the `joining_call` [webhook event](https://docs.recall.ai/reference/bot-status-change-events).                                                                                                                                                                  |
| `update_bot_failed`            | Not enough time to launch new bot                                                          | This typically happens when [Update Scheduled Bot](https://docs.recall.ai/reference/bot_partial_update) is called with an updated `join_at` too close to the present (`<10` minutes). There is a minimum amount of time it takes for us to launch a scheduled bot, and we cannot update a scheduled bot's `join_at` too close to the present for that reason. To handle this, you should delete the scheduled bot and create a new adhoc bot to join the meeting instead. |
| `cannot_command_completed_bot` | Cannot send a command to a bot which has completed(is shutting/has shut down/has errored). | This error occurs when a bot is shutting down, has shut down, or has errored, and you call an endpoint that requires the bot to be in the call - for instance, [Remove Bot From Call](https://docs.recall.ai/reference/bot_leave_call_create).                                                                                                                                                                                                                            |
| `cannot_command_unstarted_bot` | Cannot send a command to a bot which has not been started.                                 | This occurs when you call an endpoint that requires the bot to be in a call while the bot has already been terminated - for instance, [Remove Bot From Call](https://docs.recall.ai/reference/bot_leave_call_create).                                                                                                                                                                                                                                                     |

<br />

## Fatal Errors

When you get a [Bot Status Change Webhook Event](https://docs.recall.ai/docs/bot-status-change-events) from a bot with status `fatal`, you can check the `data.status.sub_code` field to get a description of the error. The complete list for possible `sub_code` can be found here [Bot Error Codes](https://docs.recall.ai/docs/sub-codes#fatal-sub-codes)

<br />

## 403 Request Blocked

Our Web Application Firewall (WAF) may block your request and return a 403 status code if your payload is flagged as potentially malicious or malformed.

In this case, response body will have a `request_blocked` code:

```json
{
    "code": "request_blocked",
    "detail": "Request was blocked due to security rules. This is likely due to providing a localhost URL in your payload. More details: https://docs.recall.ai/reference/errors#/403-request-blocked"
}
```

Common scenarios include:

* Providing a suspicious-looking payload, usually due to including a `localhost` URL, HTML tags, or CSS properties
* Sending a request body (including a null body) in a GET request
* Your server's IP address being flagged as having a bad reputation, particularly common if using a VPN, proxy, or corporate NAT

> 📘
>
> Tip: If you're trying to test locally, instead of providing a localhost URL, you should use an ngrok tunnel to expose your local server through a public endpoint.
>
> See [Testing Webhooks Locally](https://docs.recall.ai/docs/testing-webhooks-locally) for a full setup guide.

<br />

## 429 Rate Limit Exceeded

When you exceed your rate limit for a given endpoint, you will receive a response with a 429 status code.

The response will have a `Retry-After` header in the following form: `Retry-After: <delay-seconds>`

You should use this value in your retry strategy to ensure your integration can robustly handle throttling.

If you are already handling 429 errors accordingly, and are hitting persistent warnings about nearing a limit, please reach out to the team at <support@recall.ai> or your shared slack channel to request a limit increase.

> 📘
>
> Rate limits are enforced using a sliding-window strategy.

## 507 Insufficient Storage

The 507 error only occurs for ad-hoc bots and indicates there are no more ad-hoc bots available in our "pool" of ad-hoc bots. Read more about scheduled bots, ad-hoc bots, and the ad-hoc bot pool below.

### Scheduled bots

> ✅ Scheduled bots always join on-time and will never encounter 507 errors

A scheduled bot is a bot created via the [Create Bot](https://docs.recall.ai/reference/bot_create) or [Schedule Bot For Calendar Event](https://docs.recall.ai/reference/calendar_events_bot_destroy) endpoints with a `join_at` set more than 10 minutes from the time you make the request.

You should try using scheduled bots wherever possible because:

* Scheduled bots guarantee your bots join the meeting on time every time
* Scheduled bots will never run into a 507 error (no more ad-hoc bots in the ad-hoc bot pool claim)
* Scheduled bots come at no extra charge and reserves a dedicated instance, separate from the ad-hoc bot pool (described below)

> ⚙️
>
> You can use <Anchor target="_blank" href="https://github.com/recallai/sample-apps/tree/main/bot_delete_scheduled_bots">this sample app</Anchor> to see how to delete bot's recording media within a given time range and/or for a specific customer (using custom metadata)

### Ad-hoc bots

> 📘 When to use ad-hoc bots
>
> Ad-hoc bots are meant to fill in the gaps where you can't schedule bots more than 10 minutes in advance (e.g. ad-hoc/last minute meetings).
>
> You should use ad-hoc bots sparingly, opting for scheduled bots whenever possible

An ad-hoc bot is a bot created via the [Create Bot](https://docs.recall.ai/reference/bot_create) or [Schedule Bot For Calendar Event](https://docs.recall.ai/reference/calendar_events_bot_destroy) endpoints with a `join_at` set less than 10 minutes from the time you make the request.

#### Finding your concurrent ad-hoc bot limit

You can find a given workspace's concurrent ad-hoc bot limit in the dashboard under Developers > Rate Limits

![](https://files.readme.io/f056d11fc09b0c0f43f9ca5a2531769dd749debd6cd435ba1a3366936bc4ee08-CleanShot_2025-12-14_at_18.55.512x.png)

### Ad-hoc bot pool

Starting up a bot takes some time (10 minutes) before it is ready to join a meeting since each bot runs on its own dedicated machine. Because of this, we maintain a pool of "warm" bot instances that are already running in cases where you need to claim a bot last minute (or ad-hoc)

Each region has its own dedicated ad-hoc bot pool and this ad-hoc bot pool is shared among all developers in a region. The ad-hoc bot pool is calibrated and adaptive, constantly being replenished and grows with active usage. It is designed to maintain sufficient warm bot instances to support peak usage at any given time.

That said, the bot pool can still be drained during short periods with very high ad-hoc bot activity in a specific region. In these cases, there are no warm bot instances available and you will need to wait until more bot instances have warmed up before you can claim an ad-hoc bot

> ❗️ 507 error: the limitation of ad-hoc bots
>
> If you rely on ad-hoc bots, you will eventually encounter 507 errors indicating there are no more ad-hoc bots left to claim.
>
> When you encounter a 507 error, you can:
>
> * When a `Retry-After` header is passed, retry your request in `Retry-After` seconds.
> * Retry the request every 30s until you're able to claim an ad-hoc (usually within a few retries).
> * Audit your workflows to see if you can use scheduled bots which never encounter 507 errors

### What to do when you encounter a 507

If you must use ad-hoc bots and still encounter a 507 error, you can try your request again in `Retry-After` seconds.

<br />

## FAQs

### Is there a health-check endpoint?

There isn't a health-check endpoint available.
