// "Set up your workspace" is offered once, right after a new account's first sign-in. The email
// confirmation page marks this tab (sessionStorage, like lib/share-pending.ts: never a cookie, a URL
// or a log), the sign-in form reads the mark and sends the person to /setup instead of the app, and
// the mark is removed as it is read. Nothing else depends on it: a person who confirmed on another
// device or closed the tab goes straight to the app, and /setup can always be left with "Skip for
// now". Storage can be blocked: every call then does nothing.
const KEY = "beyric.setup-pending";

export function markSetupPending(): void {
  try {
    window.sessionStorage.setItem(KEY, "1");
  } catch {
    // Storage is unavailable: the person simply goes to the app after signing in.
  }
}

export function takeSetupPending(): boolean {
  try {
    const pending = window.sessionStorage.getItem(KEY) === "1";
    window.sessionStorage.removeItem(KEY);
    return pending;
  } catch {
    return false;
  }
}
