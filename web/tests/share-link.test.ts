import { describe, expect, it } from "vitest";
import { describeLink, findMeetingLink } from "@/lib/share";

const MEET = "https://meet.google.com/abc-defg-hij";

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
});
