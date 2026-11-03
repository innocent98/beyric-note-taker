import { Badge, Button } from "@/components/ui";

// The landing page from the designer's Landing.dc.html, with the words of the live pilot page
// (app/templates/landing.html): only what is true today, so no Teams, no sign-up, no sharing.
const icon = { width: 20, height: 20, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.9, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true } as const;

function Check() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="m5 12 5 5 9-10" />
    </svg>
  );
}

export function Landing() {
  return (
    <div className="land">
      <section className="land-hero">
        <p className="land-pill">Works with Google Meet and Zoom</p>
        <h1>Clear notes from every meeting, without taking them yourself.</h1>
        <p className="lede">
          Paste a meeting link. Beyric Note Taker joins the call, records the conversation and gives you a summary,
          decisions and action items when it ends.
        </p>
        <div className="land-actions">
          <Button href="/login" size="lg">Sign in</Button>
        </div>
        <p className="muted small">Beyric Note Taker is in a private pilot with a small group of testers.</p>
      </section>

      <section aria-label="Example meeting notes">
        <div className="land-preview">
          <div className="land-notes">
            <div className="land-notes-head">
              <div>
                <div className="land-notes-title">Weekly product sync</div>
                <div className="meta">Example &middot; Zoom &middot; 32 min</div>
              </div>
              <Badge tone="success">Ready</Badge>
            </div>
            <div className="land-notes-grid">
              <div>
                <h2>Summary</h2>
                <p>
                  The team agreed to ship the new onboarding flow next Tuesday. Billing changes move to the following
                  sprint while the pricing page is reviewed.
                </p>
              </div>
              <div>
                <h2>Decisions</h2>
                <ul className="land-list">
                  <li className="land-check"><Check />Onboarding ships Tuesday</li>
                  <li className="land-check"><Check />Billing moves to next sprint</li>
                </ul>
              </div>
              <div>
                <h2>Action items</h2>
                <ul className="land-list">
                  <li><span>Final copy review</span><Badge tone="brand">Amara</Badge></li>
                  <li><span>Update pricing page</span><Badge tone="info">Tunde</Badge></li>
                </ul>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="land-features">
        <div className="land-feature">
          <div className="land-icon">
            <svg {...icon}><rect x="3" y="6" width="13" height="12" rx="2" /><path d="m16 10.5 5-3v9l-5-3" /></svg>
          </div>
          <h2>Joins your calls</h2>
          <p>Send the note taker to a Google Meet or Zoom link. It records quietly and leaves when the meeting ends.</p>
        </div>
        <div className="land-feature">
          <div className="land-icon">
            <svg {...icon}><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5M9 13h6M9 17h4" /></svg>
          </div>
          <h2>Notes in your format</h2>
          <p>Templates for client calls, interviews and team updates decide what the notes focus on. Write your own in plain language.</p>
        </div>
        <div className="land-feature">
          <div className="land-icon">
            <svg {...icon}><path d="M4 7h16M4 12h10M4 17h6" /></svg>
          </div>
          <h2>Names spelled right</h2>
          <p>Add the names and terms your team uses, and transcripts and notes spell them the way you do.</p>
        </div>
      </section>

      <section className="land-note">
        <h2>Coming soon: your calendar</h2>
        <p>
          Connect Google Calendar and the note taker joins the meetings you choose by itself, including Google Meet calls
          you start on the spot. It only reads what it needs to decide which meetings to join, and you can disconnect at
          any time. See the <a href="/privacy#google">Privacy Policy</a> for exactly what it reads.
        </p>
      </section>
    </div>
  );
}

export function LandingBand() {
  return (
    <div className="land-band">
      <section className="land-steps">
        <h2>How it works</h2>
        <ol>
          <li className="land-step">
            <span className="land-step-n">01</span>
            <strong>Paste a meeting link.</strong>
            <span>Choose a template and send the note taker in.</span>
          </li>
          <li className="land-step">
            <span className="land-step-n">02</span>
            <strong>Have your meeting.</strong>
            <span>Admit it from the waiting room and talk as usual. Tell everyone it is recording.</span>
          </li>
          <li className="land-step">
            <span className="land-step-n">03</span>
            <strong>Get the notes.</strong>
            <span>Read or copy them minutes after the call ends.</span>
          </li>
        </ol>
      </section>
    </div>
  );
}

export function LandingClose() {
  return (
    <section className="land-close">
      <h2>Spend the meeting in the conversation.</h2>
      <Button href="/login" size="lg">Sign in</Button>
    </section>
  );
}
