// Leaves for another address with a full page load: the provider's hosted payment page, or the bank's.
// One function so a test can stand in for it (jsdom cannot navigate).
export function goTo(url: string): void {
  window.location.assign(url);
}
