import { describe, expect, it } from "vitest";
import {
  COMMON_COUNTRY_CODES,
  COUNTRY_CODES,
  countryName,
  countryOptions,
  filterCountryOptions,
  regionFromLanguages,
} from "@/lib/countries";
import {
  GATE_HOURS_MARK,
  GATE_PLAN_MARK,
  cameBackCancelled,
  formatHours,
  formatMoney,
  nextPollDelay,
  referenceFrom,
  routeStatement,
} from "@/lib/billing";
import { read, REPO } from "./helpers";
import { join } from "node:path";

describe("the words", () => {
  it("states the route before payment from the route it is given", () => {
    expect(routeStatement({ currency: "NGN", provider: "paystack" })).toBe("Prices in naira, paid through Paystack.");
    expect(routeStatement({ currency: "USD", provider: "flutterwave" })).toBe(
      "Prices in US dollars, paid through Flutterwave.",
    );
    expect(routeStatement({ currency: "GHS", provider: "other" })).toBe("Prices in GHS, paid through Other.");
  });

  it("writes minor units as money and never as a decimal field", () => {
    expect(formatMoney(1800, "USD")).toBe("$18.00");
    expect(formatMoney(1599, "USD")).toBe("$15.99");
    expect(formatMoney(15_000_000, "NGN")).toContain("150,000.00");
  });

  it("writes hours as hours and minutes, and minutes under an hour (the captured 720 s is 12 minutes, not 0.2 hours)", () => {
    expect(formatHours(72000)).toBe("20 hours");
    expect(formatHours(3600)).toBe("1 hour");
    expect(formatHours(5400)).toBe("1 hour 30 minutes");
    expect(formatHours(71640)).toBe("19 hours 54 minutes");
    expect(formatHours(720)).toBe("12 minutes");
    expect(formatHours(2400)).toBe("40 minutes");
    expect(formatHours(60)).toBe("1 minute");
  });

  it("never says 0 hours while some time is left", () => {
    expect(formatHours(100)).toBe("2 minutes");
    expect(formatHours(20)).toBe("less than a minute");
    expect(formatHours(0)).toBe("0 minutes");
    expect(formatHours(-5)).toBe("0 minutes");
  });
});

describe("the return address", () => {
  it("finds our reference under the names Paystack and Flutterwave use", () => {
    expect(referenceFrom(new URLSearchParams("reference=bn-1-abc&trxref=bn-1-abc"))).toBe("bn-1-abc");
    expect(referenceFrom(new URLSearchParams("trxref=bn-2-abc"))).toBe("bn-2-abc");
    expect(referenceFrom(new URLSearchParams("tx_ref=bn-3-abc&status=successful&transaction_id=99"))).toBe("bn-3-abc");
  });

  it("ignores a missing, odd or very long value", () => {
    expect(referenceFrom(new URLSearchParams(""))).toBeNull();
    expect(referenceFrom(new URLSearchParams("reference=<script>"))).toBeNull();
    expect(referenceFrom(new URLSearchParams(`reference=${"a".repeat(101)}`))).toBeNull();
  });

  it("reads a back-out only as a hint", () => {
    expect(cameBackCancelled(new URLSearchParams("status=cancelled"))).toBe(true);
    expect(cameBackCancelled(new URLSearchParams("status=successful"))).toBe(false);
    expect(cameBackCancelled(new URLSearchParams(""))).toBe(false);
  });
});

describe("the polling cadence of the guide", () => {
  it("is 2 seconds for 30 seconds, then 10 seconds up to 5 minutes, then stops", () => {
    expect(nextPollDelay(0)).toBe(2000);
    expect(nextPollDelay(29_999)).toBe(2000);
    expect(nextPollDelay(30_000)).toBe(10_000);
    expect(nextPollDelay(299_999)).toBe(10_000);
    expect(nextPollDelay(300_000)).toBeNull();
  });
});

describe("the country list", () => {
  it("is exactly the list the server accepts", () => {
    const source = read(join(REPO, "app/billing/countries.py"));
    const server = [...source.slice(source.indexOf("frozenset(")).matchAll(/\b[A-Z]{2}\b/g)].map((m) => m[0]).sort();
    expect([...COUNTRY_CODES].sort()).toEqual(server);
  });

  it("names countries from codes and is sorted by name", () => {
    expect(countryName("NG")).toBe("Nigeria");
    expect(countryName("GB")).toBe("United Kingdom");
    const names = countryOptions().map(([, name]) => name);
    expect(names).toEqual([...names].sort((a, b) => a.localeCompare(b, "en")));
    expect(countryOptions().some(([code]) => code === "UK")).toBe(false);
  });
});

describe("the sentences the gate links hang on", () => {
  it("are in the captured refusals", () => {
    const planInactive = JSON.parse(
      read(join(REPO, "docs/fe-captures/feature-gates/03-enforce-noplan-ask.json")),
    ).response.body.error;
    expect(planInactive.code).toBe("plan_inactive");
    expect(planInactive.message).toContain(GATE_PLAN_MARK);
    const noHours = JSON.parse(
      read(join(REPO, "docs/fe-captures/hours-usage/19-start-meeting-no-hours.json")),
    ).response.body.error;
    expect(noHours.code).toBe("hours_cap_reached");
    expect(noHours.message).toContain(GATE_HOURS_MARK);
  });
});

describe("the country suggestion and the picker", () => {
  it("reads the region of the first browser language tag that has one, and nothing from a bare language", () => {
    expect(regionFromLanguages(["en-NG", "en"])).toBe("NG");
    expect(regionFromLanguages(["en", "fr-CA"])).toBe("CA");
    expect(regionFromLanguages(["en"])).toBeNull();
    expect(regionFromLanguages([])).toBeNull();
    expect(regionFromLanguages(["not a tag", "es-419"])).toBeNull(); // 419 is not a country
    expect(regionFromLanguages(["en-ZZ"])).toBeNull(); // not a country the server accepts
  });

  it("puts Nigeria first among the common countries, all of which the server accepts", () => {
    expect(COMMON_COUNTRY_CODES[0]).toBe("NG");
    for (const code of COMMON_COUNTRY_CODES) expect(COUNTRY_CODES).toContain(code);
    const { common, all } = filterCountryOptions("");
    expect(common[0]).toEqual(["NG", "Nigeria"]);
    expect(all.length).toBe(COUNTRY_CODES.length);
  });

  it("searches by name or code, in name order, without the common list", () => {
    const found = filterCountryOptions("niger");
    expect(found.common).toEqual([]);
    expect(found.all.map(([code]) => code)).toEqual(["NE", "NG"]);
    expect(filterCountryOptions("gb").all.map(([code]) => code)).toContain("GB");
    expect(filterCountryOptions("zzzz").all).toEqual([]);
  });
});
