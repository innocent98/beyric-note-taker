SOURCE: https://docs.recall.ai/docs/regions.md
Fetched 2026-11-07 with curl -sL -A 'Mozilla/5.0'

---
updatedAt: 2026-10-07T22:30:13.000Z
agentTools:
  projectIndex: https://docs.recall.ai/llms.txt
---

# Regions and Base URLs

A list of data-isolated regions supported by Recall, their hostnames (i.e., base URLs) for making requests, and where to find API keys.

Recall.ai has several regions you can choose based on your needs: US, an EU hosted region (Frankfurt), and Asia (Tokyo).

They are completely separate deployments of Recall, therefore your login and API credentials will be different for each region. Additionally, all API resources are region-local and must be accessed from the same region in which they were created.

The base API URLs for active regions are as follows:

| Region  | Base URL                           | Pay-as-you-go plan   | Launch/Enterprise Plan |
| :------ | :--------------------------------- | :------------------- | :--------------------- |
| US West | <https://us-west-2.recall.ai>      | :white\_check\_mark: | :white\_check\_mark:   |
| US East | <https://us-east-1.recall.ai>      | :white\_check\_mark: | :white\_check\_mark:   |
| EU      | <https://eu-central-1.recall.ai>   | :white\_check\_mark: | :white\_check\_mark:   |
| Asia    | <https://ap-northeast-1.recall.ai> | :white\_check\_mark: | :white\_check\_mark:   |

For example, if your account is in the `us-west-2` region, then you must make requests to `https://us-west-2.recall.ai/api/...` accordingly.

Monthly plan customers can contact the Recall team via Slack to access new regions. If you're on the pay-as-you-go plan and would like to move to a different region, you must create a new account in that region, as each region of Recall is completely independent of each other.

<Callout icon="📘" theme="info">
  ### `api.recall.ai` is equivalent to `us-east-1.recall.ai`.

  If you are calling our API using api.recall.ai at the base URL, this will continue to work for the `US East` region.
</Callout>

<br />

# Get Started

The easiest way to get started is to sign up for a pay-as-you-go account. You can sign up for an account in our US West, EU, or APAC region according to your needs:

* <Anchor target="_blank" href="https://us-west-2.recall.ai/auth/signup">US Signup Link</Anchor>
* <Anchor target="_blank" href="https://eu-central-1.recall.ai/auth/signup">EU Signup Link</Anchor>
* <Anchor target="_blank" href="https://ap-northeast-1.recall.ai/auth/signup">APAC Signup Link</Anchor>

<Callout icon="📘" theme="info">
  ### Create/view API keys

  See [here](https://docs.recall.ai/docs/authentication#api-keys) for details on how to create and view API keys after signing up/in. You must create an API key to call the API.
</Callout>

<br />

# FAQ

## Does participant or meeting data ever get stored outside of the region it was captured in?

See [Storage and Data Retention](https://docs.recall.ai/docs/storage-and-playback#data-stored-in-subprocessors) for details.
