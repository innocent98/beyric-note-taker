// The countries a person can choose as the country they are billed from: the ISO 3166-1 alpha-2 codes
// the server accepts (`ISO_COUNTRIES` in app/billing/countries.py; web/tests/billing-countries.test.ts
// checks this copy against that file). The names come from the browser's own region names, so no
// name is typed here.
// A person always picks from this list and the code is what is sent; a typed name or `UK` is refused
// by the server (422 `billing_country_invalid`).
export const COUNTRY_CODES: readonly string[] = [
  "AD", "AE", "AF", "AG", "AI", "AL", "AM", "AO", "AQ", "AR", "AS", "AT", "AU", "AW", "AX", "AZ", "BA", "BB",
  "BD", "BE", "BF", "BG", "BH", "BI", "BJ", "BL", "BM", "BN", "BO", "BQ", "BR", "BS", "BT", "BV", "BW", "BY",
  "BZ", "CA", "CC", "CD", "CF", "CG", "CH", "CI", "CK", "CL", "CM", "CN", "CO", "CR", "CU", "CV", "CW", "CX",
  "CY", "CZ", "DE", "DJ", "DK", "DM", "DO", "DZ", "EC", "EE", "EG", "EH", "ER", "ES", "ET", "FI", "FJ", "FK",
  "FM", "FO", "FR", "GA", "GB", "GD", "GE", "GF", "GG", "GH", "GI", "GL", "GM", "GN", "GP", "GQ", "GR", "GS",
  "GT", "GU", "GW", "GY", "HK", "HM", "HN", "HR", "HT", "HU", "ID", "IE", "IL", "IM", "IN", "IO", "IQ", "IR",
  "IS", "IT", "JE", "JM", "JO", "JP", "KE", "KG", "KH", "KI", "KM", "KN", "KP", "KR", "KW", "KY", "KZ", "LA",
  "LB", "LC", "LI", "LK", "LR", "LS", "LT", "LU", "LV", "LY", "MA", "MC", "MD", "ME", "MF", "MG", "MH", "MK",
  "ML", "MM", "MN", "MO", "MP", "MQ", "MR", "MS", "MT", "MU", "MV", "MW", "MX", "MY", "MZ", "NA", "NC", "NE",
  "NF", "NG", "NI", "NL", "NO", "NP", "NR", "NU", "NZ", "OM", "PA", "PE", "PF", "PG", "PH", "PK", "PL", "PM",
  "PN", "PR", "PS", "PT", "PW", "PY", "QA", "RE", "RO", "RS", "RU", "RW", "SA", "SB", "SC", "SD", "SE", "SG",
  "SH", "SI", "SJ", "SK", "SL", "SM", "SN", "SO", "SR", "SS", "ST", "SV", "SX", "SY", "SZ", "TC", "TD", "TF",
  "TG", "TH", "TJ", "TK", "TL", "TM", "TN", "TO", "TR", "TT", "TV", "TW", "TZ", "UA", "UG", "UM", "US", "UY",
  "UZ", "VA", "VC", "VE", "VG", "VI", "VN", "VU", "WF", "WS", "YE", "YT", "ZA", "ZM", "ZW",
];

export function countryName(code: string, locale = "en"): string {
  try {
    return new Intl.DisplayNames([locale], { type: "region" }).of(code) ?? code;
  } catch {
    return code;
  }
}

// [code, name] pairs for a select, sorted by name.
export function countryOptions(locale = "en"): Array<readonly [string, string]> {
  return COUNTRY_CODES.map((code) => [code, countryName(code, locale)] as const).sort((a, b) =>
    a[1].localeCompare(b[1], locale),
  );
}

// Shown first in the picker, Nigeria on top (the one country that pays in naira), then the countries most
// people who sign up are established in. A person can still pick any country in the list below them.
export const COMMON_COUNTRY_CODES: readonly string[] = ["NG", "US", "GB", "GH", "KE", "ZA", "CA", "IN", "AE", "DE", "FR", "AU"];

// The region a browser reports in its language tags ("en-NG" gives "NG"), or null when none of them has one
// ("en" says nothing about where a person is, so nothing is made up from it) or the region is not a country
// the server accepts. Used only to SUGGEST a country; the person confirms it.
export function regionFromLanguages(languages: readonly string[]): string | null {
  for (const tag of languages) {
    let region: string | undefined;
    try {
      region = new Intl.Locale(tag).region;
    } catch {
      continue;
    }
    if (region && COUNTRY_CODES.includes(region)) return region;
  }
  return null;
}

// What the picker lists: with nothing typed, the common countries and then all of them; with a search, the
// countries whose name (or code) contains it, in name order.
export function filterCountryOptions(
  query: string,
  locale = "en",
): { common: Array<readonly [string, string]>; all: Array<readonly [string, string]> } {
  const all = countryOptions(locale);
  const needle = query.trim().toLowerCase();
  if (needle) {
    const found = all.filter(([code, name]) => name.toLowerCase().includes(needle) || code.toLowerCase() === needle);
    return { common: [], all: found };
  }
  const common = COMMON_COUNTRY_CODES.map((code) => [code, countryName(code, locale)] as const);
  return { common, all };
}
