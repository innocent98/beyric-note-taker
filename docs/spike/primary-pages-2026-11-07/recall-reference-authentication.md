SOURCE: https://docs.recall.ai/reference/authentication.md
Fetched 2026-11-07 with curl -sL -A 'Mozilla/5.0'

---
updatedAt: 2026-10-26T17:44:29.000Z
agentTools:
  projectIndex: https://docs.recall.ai/llms.txt
---

# Recall.ai API Authentication

Start making requests to the Recall API.

## API Keys

API keys are used to authenticate to the Recall.ai platform. API keys don't expire and must be explicitly disabled if you want to rotate an API key.

> 📘 View the API key and workspace verification secret dashboard
>
> * <Anchor label="(US) us-east-1" target="_blank" href="https://us-east-1.recall.ai/dashboard/developers/api-keys">(US) us-east-1</Anchor>
> * <Anchor label="(US) us-west-2" target="_blank" href="https://us-west-2.recall.ai/dashboard/developers/api-keys">(US) us-west-2</Anchor>
> * <Anchor label="(EU) eu-central-1" target="_blank" href="https://eu-central-1.recall.ai/dashboard/developers/api-keys">(EU) eu-central-1</Anchor>
> * <Anchor label="(JP) ap-northeast-1" target="_blank" href="https://ap-northeast-1.recall.ai/dashboard/developers/api-keys">(JP) ap-northeast-1</Anchor>

API keys belong to individual users, but access is [Workspace](https://docs.recall.ai/docs/environments)-scoped. This means that multiple accounts under the same environment share access to the same resources, but have their own API keys.

We **highly recommend** adding a service account such as <engineering@your-company.com> when creating API keys for production Workspaces. Should a team member leave in the future, this will ensure that no production workflows are interrupted when you revoke their access.

<br />

## HTTP Header: Token Authorization

All requests must be authenticated by providing your Recall API key in the HTTP `Authorization` header:

```
Authorization: $RECALLAI_API_KEY
```

Unauthenticated requests will receive a 401 error: `401: Unauthorized`.

<Callout icon="📘" theme="info">
  The `Token` prefix is optional, and can be safely omitted.
</Callout>

<br />
