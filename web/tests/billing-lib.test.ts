import { describe, expect, it } from "vitest";
import { COUNTRY_CODES, countryName, countryOptions } from "@/lib/countries";
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

  it("writes hours with one decimal at most", () => {
    expect(formatHours(72000)).toBe("20 hours");
    expect(formatHours(3600)).toBe("1 hour");
    expect(formatHours(71640)).toBe("19.9 hours");
    expect(formatHours(0)).toBe("0 hours");
    expect(formatHours(-5)).toBe("0 hours");
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
