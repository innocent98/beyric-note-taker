import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { REPO } from "./helpers";

// The file Microsoft reads to tie our domain to the app registration before publisher verification
// (Outlook spec 8.3). It must be valid JSON of exactly this shape, and its id is the application's
// client id: a placeholder until switch-on, when the lead replaces it (the SOP says how).
describe("/.well-known/microsoft-identity-association.json", () => {
  const raw = readFileSync(join(REPO, "web/public/.well-known/microsoft-identity-association.json"), "utf8");
  const body = JSON.parse(raw) as { associatedApplications: { applicationId: string }[] };

  it("names exactly one application", () => {
    expect(Object.keys(body)).toEqual(["associatedApplications"]);
    expect(body.associatedApplications).toHaveLength(1);
    expect(Object.keys(body.associatedApplications[0])).toEqual(["applicationId"]);
  });

  it("holds a client id (a GUID) or the loud placeholder, never anything else", () => {
    const id = body.associatedApplications[0].applicationId;
    expect(id).toMatch(
      /^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|REPLACE-WITH-THE-APPLICATION-CLIENT-ID-AT-SWITCH-ON)$/,
    );
  });
});
