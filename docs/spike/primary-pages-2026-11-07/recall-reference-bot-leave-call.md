SOURCE: https://docs.recall.ai/reference/bot_leave_call_create.md
Fetched 2026-11-07 with curl -sL -A 'Mozilla/5.0' (EXCERPT: see the marker inside)

---
updatedAt: 2026-04-21T05:14:25.000Z
agentTools:
  projectIndex: https://docs.recall.ai/llms.txt
---

# Remove Bot From Call

Removes the bot from the meeting. This is irreversible.

The base rate limit for this endpoint is:
- 300 requests per min per workspace

# OpenAPI definition

```json

--- EXCERPT of the OpenAPI JSON on that page (only the parts bakeoff.py uses; the full page is ~576 KB) ---

## POST /api/v1/bot/{id}/leave_call/  (Remove Bot From Call)
{
 "description": "Removes the bot from the meeting. This is irreversible.\n\nThe base rate limit for this endpoint is:\n- 300 requests per min per workspace",
 "parameters": [
  {
   "in": "path",
   "name": "id",
   "schema": {
    "type": "string",
    "format": "uuid"
   },
   "description": "A UUID string identifying this bot.",
   "required": true
  }
 ],
 "responses": {
  "200": {
   "content": {
    "application/json": {
     "schema": {
      "$ref": "#/components/schemas/Bot"
     }
    }
   },
   "description": ""
  },
  "400": {
   "description": "No response body"
  }
 }
}
