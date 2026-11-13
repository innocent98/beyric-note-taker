import { describe, expect, it } from "vitest";
import { describeLink, findMeetingLink } from "@/lib/share";

const MEET = "https://meet.google.com/abc-defg-hij";
// Teams links, in the forms app/meeting_url.py accepts (the same placeholder values as
// tests/test_meeting_url.py: no real tenant, user or thread).
const THREAD = "19%3ameeting_PLACEHOLDERPLACEHOLDERPLACEHOLDERPLACEH%40thread.v2";
const CONTEXT =
  "context=%7b%22Tid%22%3a%2200000000-0000-0000-0000-000000000000%22%2c" +
  "%22Oid%22%3a%2211111111-1111-1111-1111-111111111111%22%7d";
const TEAMS_JOIN = `https://teams.microsoft.com/l/meetup-join/${THREAD}/0?${CONTEXT}`;
const TEAMS_SHORT = "https://teams.microsoft.com/meet/1234567890123?p=PLACEHOLDERpasscode";
const TEAMS_LIVE = "https://teams.live.com/meet/9876543210?p=PLACEHOLDERpasscode";

describe("findMeetingLink", () => {
  it("finds a Meet link inside a long sentence", () => {
    const text = `Hi team, please join our weekly sync with Google Meet: ${MEET}. See you there, thanks!`;
    expect(findMeetingLink({ text })).toEqual({ url: MEET, platform: "google_meet", others: 0 });
  });

  it("finds the link when the sharing app puts it in the url field, or in the title", () => {
    expect(findMeetingLink({ url: MEET })?.url).toBe(MEET);
    expect(findMeetingLink({ title: `Join ${MEET}` })?.url).toBe(MEET);
  });

  it("keeps a Zoom passcode and drops every other query parameter", () => {
    const text =
      "Topic: Standup\nJoin Zoom Meeting\nhttps://us02web.zoom.us/j/81234567890?pwd=AbC123xyz&utm_source=x\n\nMeeting ID: 812 3456 7890\nPasscode: 123456";
    expect(findMeetingLink({ text })).toEqual({
      url: "https://us02web.zoom.us/j/81234567890?pwd=AbC123xyz",
      platform: "zoom",
      others: 0,
    });
  });

  it("accepts a Zoom link without a passcode and the web-client form", () => {
    expect(findMeetingLink({ text: "https://zoom.us/j/81234567890" })?.url).toBe("https://zoom.us/j/81234567890");
    expect(findMeetingLink({ text: "https://zoom.us/wc/join/81234567890" })?.url).toBe(
      "https://zoom.us/wc/join/81234567890",
    );
  });

  it("builds a link from a bare Meet code only when the code is all that was shared", () => {
    expect(findMeetingLink({ text: "abc-defg-hij" })).toEqual({ url: MEET, platform: "google_meet", others: 0 });
    expect(findMeetingLink({ text: "  abc-defg-hij \n" })?.url).toBe(MEET);
    expect(findMeetingLink({ text: "my code is abc-defg-hij ok" })).toBeNull();
  });

  it("accepts a Meet link without the scheme, and turns http into https", () => {
    expect(findMeetingLink({ text: "meet.google.com/abc-defg-hij" })?.url).toBe(MEET);
    expect(findMeetingLink({ text: "http://meet.google.com/abc-defg-hij" })?.url).toBe(MEET);
  });

  it("drops punctuation that follows the link in a sentence", () => {
    for (const tail of [".", ",", ")", "!", ">", '"']) {
      expect(findMeetingLink({ text: `(${MEET}${tail} later` })?.url).toBe(MEET);
    }
  });

  it("returns null when there is no meeting link", () => {
    expect(findMeetingLink({ text: "Lunch at noon? https://example.com/menu" })).toBeNull();
    expect(findMeetingLink({ text: "" })).toBeNull();
    expect(findMeetingLink({})).toBeNull();
    expect(findMeetingLink({ text: "https://meet.google.com/landing" })).toBeNull();
    expect(findMeetingLink({ text: "https://zoom.us/signin" })).toBeNull();
  });

  it("uses the first of two links and says how many others there were", () => {
    const zoom = "https://zoom.us/j/81234567890";
    expect(findMeetingLink({ text: `First ${MEET} or second ${zoom}` })).toEqual({
      url: MEET,
      platform: "google_meet",
      others: 1,
    });
    expect(findMeetingLink({ text: `First ${zoom} then ${MEET}` })?.url).toBe(zoom);
  });

  it("counts the same link shared twice as one", () => {
    expect(findMeetingLink({ url: MEET, text: `Join ${MEET}` })).toEqual({
      url: MEET,
      platform: "google_meet",
      others: 0,
    });
  });

  describe("hostile text", () => {
    it.each([
      ["lookalike host", "https://meet.google.com.evil.example/abc-defg-hij"],
      ["suffix lookalike", "https://notmeet.google.com/abc-defg-hij"],
      ["lookalike without scheme", "notmeet.google.com/abc-defg-hij"],
      ["userinfo trick", "https://meet.google.com@evil.example/abc-defg-hij"],
      ["userinfo with a real host after it", "https://evil.example@meet.google.com/abc-defg-hij"],
      ["a port", "https://meet.google.com:8443/abc-defg-hij"],
      ["javascript scheme", "javascript:alert(1)//meet.google.com/abc-defg-hij"],
      ["backslash", "https://meet.google.com\\@evil.example/abc-defg-hij"],
      ["extra path", "https://meet.google.com/abc-defg-hij/../../x"],
      ["zoom lookalike", "https://zoom.us.evil.example/j/81234567890"],
      ["zoom with a letter in the id", "https://zoom.us/j/8123abc"],
    ])("refuses a %s", (_name, text) => {
      expect(findMeetingLink({ text })).toBeNull();
    });

    it("never returns markup or script as a link", () => {
      const found = findMeetingLink({
        text: `<script>alert(1)</script><img src=x onerror=alert(1)> ${MEET}">`,
      });
      expect(found?.url).toBe(MEET);
    });

    it("skips a bad link and still finds a good one after it", () => {
      expect(findMeetingLink({ text: `https://evil.example/x ${MEET}` })?.url).toBe(MEET);
    });

    it("stays fast and bounded on a huge shared text", () => {
      const filler = "a".repeat(200_000);
      const started = Date.now();
      expect(findMeetingLink({ text: `${filler} ${MEET}` })).toBeNull(); // past the length cap
      expect(findMeetingLink({ text: `${MEET} ${filler}` })?.url).toBe(MEET);
      expect(findMeetingLink({ text: "https://".repeat(50_000) })).toBeNull();
      expect(Date.now() - started).toBeLessThan(1000);
    });

    it("ignores values that are not text", () => {
      expect(findMeetingLink({ text: 5 as unknown as string })).toBeNull();
    });
  });
});

describe("findMeetingLink: Microsoft Teams", () => {
  it.each([
    ["a meetup-join link", TEAMS_JOIN],
    ["a short /meet/ link", TEAMS_SHORT],
    ["a teams.live.com link", TEAMS_LIVE],
  ])("finds %s on its own, kept exactly as written", (_name, link) => {
    expect(findMeetingLink({ text: link })).toEqual({ url: link, platform: "teams", others: 0 });
    expect(findMeetingLink({ url: link })?.url).toBe(link);
  });

  it("finds the link inside an invitation, in angle brackets or at the end of a sentence", () => {
    const text = [
      "Microsoft Teams meeting",
      "Join: <" + TEAMS_JOIN + ">",
      "Meeting ID: 123 456 789 012",
      `Or join here, please: ${TEAMS_SHORT}.`,
    ].join("\n");
    expect(findMeetingLink({ text })).toEqual({ url: TEAMS_JOIN, platform: "teams", others: 1 });
    expect(findMeetingLink({ text: `Join ${TEAMS_SHORT}.` })?.url).toBe(TEAMS_SHORT);
  });

  it("tidies the scheme, the host case and the fragment, and nothing else", () => {
    const messy = TEAMS_JOIN.replace("https://teams.microsoft.com", "HTTP://Teams.Microsoft.COM") + "#frag";
    expect(findMeetingLink({ text: messy })?.url).toBe(TEAMS_JOIN);
  });

  it("accepts a link with no scheme that stands on its own", () => {
    expect(findMeetingLink({ text: TEAMS_SHORT.replace("https://", "") })?.url).toBe(TEAMS_SHORT);
    expect(findMeetingLink({ text: `see (${TEAMS_LIVE.replace("https://", "")}) soon` })?.url).toBe(TEAMS_LIVE);
  });

  it("takes the first link when a Meet link comes before the Teams one, and the other way round", () => {
    expect(findMeetingLink({ text: `Primary ${MEET}, backup ${TEAMS_SHORT}` })).toEqual({
      url: MEET,
      platform: "google_meet",
      others: 1,
    });
    expect(findMeetingLink({ text: `Primary ${TEAMS_SHORT}, backup ${MEET}` })).toEqual({
      url: TEAMS_SHORT,
      platform: "teams",
      others: 1,
    });
  });

  it("counts the same Teams link shared twice as one", () => {
    expect(findMeetingLink({ url: TEAMS_JOIN, text: `Join ${TEAMS_JOIN}` })?.others).toBe(0);
  });

  it("finds a Teams link when it is all that was shared in the title", () => {
    expect(findMeetingLink({ title: `Weekly sync ${TEAMS_SHORT}` })?.url).toBe(TEAMS_SHORT);
  });

  it("reads Outlook's Safe Links wrapper and keeps the link inside", () => {
    const wrapped =
      "https://eur01.safelinks.protection.outlook.com/?url=" +
      `${encodeURIComponent(TEAMS_JOIN)}&data=05%7c02%7cplaceholder&reserved=0`;
    expect(findMeetingLink({ text: `Join <${wrapped}>` })).toEqual({
      url: TEAMS_JOIN,
      platform: "teams",
      others: 0,
    });
    // The wrapped and the plain link of one meeting are one link.
    expect(findMeetingLink({ text: `${wrapped} and ${TEAMS_JOIN}` })?.others).toBe(0);
  });

  it("finds the same link again when it is read back from the address or storage", () => {
    for (const link of [TEAMS_JOIN, TEAMS_SHORT, TEAMS_LIVE]) {
      const first = findMeetingLink({ text: link })!;
      expect(findMeetingLink({ url: first.url })).toEqual({ ...first, others: 0 });
    }
  });

  describe("hostile text", () => {
    it.each([
      // not Teams' hosts
      ["a lookalike host", "https://teams.microsoft.com.evil.example/meet/1234567890123"],
      ["a lookalike host, live", "https://teams.live.com.evil.example/meet/9876543210"],
      ["a prefix lookalike", "https://evilteams.microsoft.com/meet/1234567890123"],
      ["a subdomain of Teams", "https://evil.teams.microsoft.com/meet/1234567890123"],
      ["a lookalike without scheme", "evilteams.microsoft.com/meet/1234567890123"],
      ["userinfo trick", "https://teams.microsoft.com@evil.example/meet/1234567890123"],
      ["userinfo with the real host after it", "https://user:pw@teams.microsoft.com/meet/1234567890123"],
      ["a port", "https://teams.microsoft.com:8443/meet/1234567890123"],
      ["a backslash", "https://teams.microsoft.com\\@evil.example/meet/1234567890123"],
      ["another scheme", "ftp://teams.microsoft.com/meet/1234567890123"],
      // paths that are not a meeting
      ["the Teams home page", "https://teams.microsoft.com/"],
      ["a chat link", "https://teams.microsoft.com/l/chat/0/0"],
      ["a /meet/ id with letters", "https://teams.microsoft.com/meet/abc"],
      ["a /meet/ id that is too short", "https://teams.microsoft.com/meet/12345"],
      ["an extra path", "https://teams.microsoft.com/meet/1234567890123/extra"],
      ["a path that climbs out", "https://teams.microsoft.com/meet/1234567890123/../1234567890123"],
      ["a meetup-join with no thread", "https://teams.microsoft.com/l/meetup-join/"],
      ["a thread that is not Teams' own", "https://teams.microsoft.com/l/meetup-join/meeting_x%40thread.v2/0"],
      ["a thread on another host name", "https://teams.microsoft.com/l/meetup-join/19%3ameeting_x%40evil.example/0"],
      ["a thread that cannot be decoded", "https://teams.microsoft.com/l/meetup-join/19%3ameeting_x%ZZ%40thread.v2/0"],
      ["meetup-join on teams.live.com", "https://teams.live.com/l/meetup-join/19%3ameeting_x%40thread.v2/0"],
      // hostile characters and size
      ["a pipe in the query", "https://teams.microsoft.com/meet/1234567890123?p=a%20b|c"],
      ["a very long link", "https://teams.microsoft.com/meet/1234567890123?p=" + "a".repeat(2000)],
      // wrappers around something else, or around a wrapper, or on a lookalike host
      ["a wrapper around another site", "https://eur01.safelinks.protection.outlook.com/?url=https%3A%2F%2Fexample.com%2Fx"],
      ["a wrapper around a Meet link", "https://eur01.safelinks.protection.outlook.com/?url=https%3A%2F%2Fmeet.google.com%2Fabc-defg-hij"],
      ["a wrapper with no link in it", "https://eur01.safelinks.protection.outlook.com/?data=x"],
      [
        "a wrapper on a lookalike host",
        "https://safelinks.protection.outlook.com.evil.example/?url=" + encodeURIComponent(TEAMS_LIVE),
      ],
    ])("refuses %s", (_name, text) => {
      expect(findMeetingLink({ text })).toBeNull();
    });

    it("refuses a wrapper inside a wrapper", () => {
      const inner = "https://eur01.safelinks.protection.outlook.com/?url=" + encodeURIComponent(TEAMS_JOIN);
      const outer = "https://eur02.safelinks.protection.outlook.com/?url=" + encodeURIComponent(inner);
      expect(findMeetingLink({ text: outer })).toBeNull();
    });

    it("stops a link at markup or a quote, as for every platform, and never returns the markup", () => {
      for (const tail of ['<script>alert(1)</script>', '"onmouseover="x', "'x"]) {
        const found = findMeetingLink({ text: `${TEAMS_SHORT}${tail}` });
        expect(found?.url).toBe(TEAMS_SHORT);
      }
      expect(findMeetingLink({ text: 'https://teams.microsoft.com/meet/1234567890123?p="x"' })?.url).toBe(
        "https://teams.microsoft.com/meet/1234567890123?p=",
      );
    });

    it("skips a look-alike and still finds the real Teams link after it", () => {
      const text = `https://teams.microsoft.com.evil.example/meet/1234567890123 ${TEAMS_SHORT}`;
      expect(findMeetingLink({ text })).toEqual({ url: TEAMS_SHORT, platform: "teams", others: 0 });
    });
  });
});

describe("describeLink", () => {
  it("names the meeting and never shows a Zoom passcode", () => {
    expect(describeLink(findMeetingLink({ text: MEET })!)).toBe("Google Meet abc-defg-hij");
    const zoom = findMeetingLink({ text: "https://zoom.us/j/81234567890?pwd=secretpass" })!;
    expect(describeLink(zoom)).toBe("Zoom meeting 81234567890 (with passcode)");
    expect(describeLink(zoom)).not.toContain("secretpass");
    expect(describeLink(findMeetingLink({ text: "https://zoom.us/j/81234567890" })!)).toBe(
      "Zoom meeting 81234567890",
    );
  });

  it("names a Teams meeting without showing any part of its link", () => {
    for (const link of [TEAMS_JOIN, TEAMS_SHORT, TEAMS_LIVE]) {
      const text = describeLink(findMeetingLink({ text: link })!);
      expect(text).toBe("Microsoft Teams meeting");
    }
  });
});
