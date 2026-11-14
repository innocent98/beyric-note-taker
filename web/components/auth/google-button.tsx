"use client";

import { googleStartUrl } from "@/lib/accounts";

// "Continue with Google": a full page load to /api/auth/google/start (the answer is a 302 to
// Google, so it cannot be a fetch). Pass `guard` where the visitor must have ticked the Terms box
// first (sign-up): it returns false to stop, and the checked state goes to the API as
// `accept_terms=true` and `accept_recording=true`. Without a guard it is a plain link (sign-in: an
// existing account needs neither, and a new one is turned back with /login?error=terms_required).
export function GoogleButton({
  acceptTerms = false,
  acceptRecording = false,
  guard,
}: {
  acceptTerms?: boolean;
  acceptRecording?: boolean;
  guard?: () => boolean;
}) {
  const href = googleStartUrl(acceptTerms, acceptRecording);
  const content = (
    <>
      <span className="google-mark" aria-hidden="true" />
      Continue with Google
    </>
  );
  if (!guard) {
    return (
      <a className="btn btn-google" href={href}>
        {content}
      </a>
    );
  }
  return (
    <button
      className="btn btn-google"
      type="button"
      onClick={() => {
        if (guard()) window.location.assign(href);
      }}
    >
      {content}
    </button>
  );
}
