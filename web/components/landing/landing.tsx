import type { ReactNode } from "react";
import { AppLink, Button, Icon, Logo } from "@/components/ui";
import { LEGAL_NAME } from "@/lib/site";
import {
  CLOSING,
  DEEP_DIVES,
  DEEP_DIVES_NOTE,
  FAQS,
  FEATURES,
  HERO,
  PLATFORMS,
  SECURITY,
  SECURITY_TITLE,
  SIGNUP_NOTE,
  TEAMS,
  TOOLS,
  TOOLS_LABEL,
  WHY,
} from "./content";

// The landing page from the designer's second Landing file (2026-11-14), section by section, with
// the words cut to what the app does (content.ts). The sign-up buttons ("Get started", "Create your
// account") go to /signup, which draws the form, or the closed or full screen with the waiting list
// when the pilot is not open. "See the app" is not drawn: it links to the prototype file, which is not
// a page. Every mock-up is decorative (aria-hidden, no focusable control) and shows a made-up team.

function Glyph({ d, size, stroke = 1.9 }: { d: string; size: number; stroke?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={stroke} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

const CHECK = "m5 12 5 5 9-10";

// One line telling a visitor that sign-up is by invitation, with the way in for everyone else.
export function SignupNote({ className = "" }: { className?: string }) {
  return (
    <p className={`ld-signup-note ${className}`.trim()}>
      {SIGNUP_NOTE.before}
      <AppLink href="/signup">{SIGNUP_NOTE.link}</AppLink>
      {SIGNUP_NOTE.after}
    </p>
  );
}

// `signedIn` is true on /welcome and /pricing for the owner: the logo then leads to /welcome (the page
// that shows the landing to the owner, since `/` is their Meetings page), and the two sign-up buttons give
// way to one way back into the app. A visitor's header is the designer's. `page` says which links the
// header draws: the landing page has the section anchors, the pricing page the designer's two links.
export function LandingHeader({ signedIn = false, page = "landing" }: { signedIn?: boolean; page?: "landing" | "pricing" }) {
  const home = signedIn ? "/welcome" : "/";
  return (
    <header className="ld-header">
      <div className="ld-header-in">
        <Logo size="lg" href={home} />
        <nav className="ld-nav" aria-label="Main">
          {page === "landing" ? (
            <>
              <a href="#product">Product</a>
              <a href="#teams">Who it&apos;s for</a>
              <AppLink href="/pricing">Pricing</AppLink>
              <a href="#security">Security</a>
            </>
          ) : (
            <>
              <AppLink href={home}>Product</AppLink>
              <AppLink href="/pricing" aria-current="page">
                Pricing
              </AppLink>
            </>
          )}
        </nav>
        <div className="ld-header-actions">
          {signedIn ? (
            <AppLink className="ld-get-started" href="/">
              Open the app
            </AppLink>
          ) : (
            <>
              <AppLink className="ld-signin" href="/login">
                Sign in
              </AppLink>
              <AppLink className="ld-get-started" href="/signup">
                Get started
              </AppLink>
            </>
          )}
        </div>
      </div>
    </header>
  );
}

function Pill({ tone, children }: { tone: "ok" | "warn" | "danger" | "muted" | "teal"; children: ReactNode }) {
  return <span className={`ld-status ld-status-${tone}`}>{children}</span>;
}

// Initials in a coloured circle: the hero's action items (22 px) and the task cards' owners.
function Avatar({ n, children }: { n: 1 | 2 | 3 | 4 | 5; children: ReactNode }) {
  return <span className={`avatar avatar-22 avatar-${["", "a", "b", "c", "d", "e"][n]}`}>{children}</span>;
}

function Hero() {
  return (
    <section className="ld-hero">
      <div className="ld-hero-copy">
        <div className="ld-pill">{HERO.pill}</div>
        <h1 className="ld-h1">{HERO.title}</h1>
        <p className="ld-lede">{HERO.lede}</p>
        <div className="ld-cta">
          <Button href="/signup" size="xl">
            Get started
            <Icon name="arrow" size={16} stroke={2.2} />
          </Button>
        </div>
        <SignupNote />
        <div className="ld-platforms">
          <span>Joins calls on</span>
          <span className="ld-chips">
            {PLATFORMS.map((name) => (
              <span key={name} className="ld-chip">
                {name}
              </span>
            ))}
          </span>
        </div>
      </div>

      <div className="ld-shot">
        <p className="ld-sample">{HERO.sample}</p>
        <div className="ld-window" aria-hidden="true">
          <div className="ld-window-head">
            <div>
              <div className="ld-mini-title">Beyric Monday planning</div>
              <div className="ld-mini-meta">Mon 2 Nov &middot; Google Meet &middot; 46 min</div>
            </div>
            <Pill tone="ok">Notes ready</Pill>
          </div>
          <div className="ld-window-body">
            <div>
              <div className="ld-mini-label">Summary</div>
              <div className="ld-mini-text">
                The team reviewed Client Portal progress ahead of the December 8 launch, including onboarding testing,
                payment checkout and the pricing model.
              </div>
            </div>
            <div>
              <div className="ld-mini-label ld-mini-label-lg">Action items, already assigned</div>
              <div className="ld-mini-items">
                <div>
                  <span>Finish payment checkout</span>
                  <span className="ld-mini-owner">
                    Fri 6 Nov<Avatar n={1}>AT</Avatar>
                  </span>
                </div>
                <div>
                  <span>Test web and mobile data sync</span>
                  <span className="ld-mini-owner">
                    Today<Avatar n={3}>DO</Avatar>
                  </span>
                </div>
                <div>
                  <span>Log design refinements on the tracker</span>
                  <span className="ld-mini-owner">
                    Thu 5 Nov<Avatar n={4}>AN</Avatar>
                  </span>
                </div>
              </div>
            </div>
            <div className="ld-mini-chips">
              <span>4 decisions logged</span>
              <span>2 open questions carried over</span>
            </div>
          </div>
        </div>
        <div className="ld-toast" aria-hidden="true">
          <span className="ld-toast-icon">
            <Glyph d={CHECK} size={15} stroke={2.4} />
          </span>
          <span className="ld-toast-text">
            <strong>8 tasks created</strong>
            <span>Assigned to 3 people in Sprint 12</span>
          </span>
        </div>
      </div>
    </section>
  );
}

function Tools() {
  return (
    <section className="ld-tools">
      <div className="ld-tools-in">
        <span className="ld-tools-label">{TOOLS_LABEL}</span>
        {TOOLS.map((name) => (
          <span key={name}>{name}</span>
        ))}
      </div>
    </section>
  );
}

function Why() {
  return (
    <section className="ld-section ld-why">
      <div className="ld-heading ld-heading-wide">
        <span className="ld-eyebrow">{WHY.eyebrow}</span>
        <h2 className="ld-h2">{WHY.title}</h2>
        <p className="ld-sub">{WHY.body}</p>
      </div>
      <div className="ld-compare">
        <div className="ld-compare-card">
          <div className="ld-compare-label">{WHY.withoutTitle}</div>
          <ul className="ld-compare-list">
            {WHY.without.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
        <div className="ld-compare-card ld-compare-yes">
          <div className="ld-compare-label">{WHY.withTitle}</div>
          <ul className="ld-compare-list">
            {WHY.with.map((line) => (
              <li key={line}>
                <Glyph d={CHECK} size={18} stroke={2.4} />
                {line}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}

// ---- The mock-ups beside the five deep dives (all made-up data, all decorative) ----

function AskMock() {
  return (
    <div className="ld-panel" aria-hidden="true">
      <div className="ld-ask-q">What did we decide about pricing?</div>
      <div className="ld-ask-a">
        <span>No pricing model has been chosen yet. It has come up in 3 meetings and is planned for Monday planning on Mon 9 Nov.</span>
        <ul>
          <li>
            <span>Kemi listed three options: per seat, per workspace, or usage based.</span>
            <span className="ld-src">Transcript line</span>
          </li>
          <li>
            <span>Kemi proposed deciding it next week.</span>
            <span className="ld-src">Transcript line</span>
          </li>
        </ul>
        <span className="ld-ask-source">Source: Beyric Monday planning, Mon 2 Nov</span>
      </div>
    </div>
  );
}

function HeardMock() {
  return (
    <div className="ld-panel" aria-hidden="true">
      <div className="ld-caps">SUGGESTED TASKS · WAITING FOR YOU</div>
      <div className="ld-card ld-card-gap-10">
        <div className="ld-card-row">
          <span className="ld-card-title">Look into the Metrika analytics setup</span>
          <Pill tone="danger">Low confidence</Pill>
        </div>
        <div className="ld-card-note">No one took this on. It was said as &quot;someone should&quot;.</div>
        <div className="ld-quote">Daniel: Someone should look at the Metrika setup.</div>
        <div className="ld-mini-buttons">
          <span className="ld-mini-btn ld-mini-btn-primary">Confirm task</span>
          <span className="ld-mini-btn">Dismiss</span>
        </div>
      </div>
      <div className="ld-card ld-card-gap-8">
        <div className="ld-card-row">
          <span className="ld-card-title">Organise the Figma file</span>
          <Pill tone="warn">Medium confidence</Pill>
        </div>
        <div className="ld-card-note">Raised as something to do &quot;at some point&quot;, with no commitment or date.</div>
      </div>
    </div>
  );
}

function DecisionsMock() {
  return (
    <div className="ld-panel" aria-hidden="true">
      <div className="ld-list">
        <div>
          <span>
            <span className="ld-list-title">Launch the Client Portal on Tue 8 Dec</span>
            <span className="ld-list-meta">Kemi Adeyemi · Mon 2 Nov · replaces the 30 Nov date</span>
          </span>
          <Pill tone="ok">Approved</Pill>
        </div>
        <div>
          <span>
            <span className="ld-list-title ld-list-struck">Launch the Client Portal on Mon 30 Nov</span>
            <span className="ld-list-meta">Kemi Adeyemi · Mon 26 Oct</span>
          </span>
          <Pill tone="muted">Replaced</Pill>
        </div>
        <div>
          <span>
            <span className="ld-list-title">Decide the pricing model next week</span>
            <span className="ld-list-meta">Kemi Adeyemi · revisit Mon 9 Nov</span>
          </span>
          <Pill tone="warn">Proposed</Pill>
        </div>
        <div>
          <span>
            <span className="ld-list-title">Offer a lifetime deal at launch</span>
            <span className="ld-list-meta">Kemi Adeyemi · Fri 30 Oct</span>
          </span>
          <Pill tone="danger">Rejected</Pill>
        </div>
      </div>
    </div>
  );
}

function RecurringMock() {
  return (
    <div className="ld-panel" aria-hidden="true">
      <div className="ld-card ld-card-gap-6">
        <div className="ld-card-row">
          <span className="ld-card-title ld-card-title-lg">Beyric Monday planning</span>
          <span className="ld-next">Next: Mon 9 Nov, 14:00</span>
        </div>
        <span className="ld-card-meta">Weekly on Mondays · 4 unfinished tasks · 2 open questions</span>
      </div>
      <div className="ld-stuck">
        <span>Pricing model</span>
        Discussed in 3 meetings without a decision
      </div>
      <div className="ld-card ld-card-gap-8 ld-agenda">
        <span className="ld-caps">SUGGESTED AGENDA</span>
        <span>1. Decide the pricing model</span>
        <span>2. Review the overdue task</span>
        <span>3. Settle 2 open questions carried over</span>
      </div>
    </div>
  );
}

function TasksMock() {
  return (
    <div className="ld-panel" aria-hidden="true">
      <div className="ld-card ld-card-gap-10">
        <div className="ld-card-row">
          <span className="ld-card-title ld-card-title-lg">Client Portal launch</span>
          <span className="ld-card-meta">Tue 8 Dec · 36 days</span>
        </div>
        <div className="ld-progress">
          <div className="ld-progress-fill" />
        </div>
        <span className="ld-card-note">2 of 8 launch tasks done</span>
      </div>
      <div className="ld-board">
        <div>
          <span className="ld-board-title">To do · Sprint 12</span>
          <div className="ld-board-task">
            Finish payment checkout<span>Fri 6 Nov · AT</span>
          </div>
          <div className="ld-board-task">
            Post this week&apos;s tasks in Slack<span className="ld-carried">Carried over · DO</span>
          </div>
        </div>
        <div>
          <span className="ld-board-title">In progress</span>
          <div className="ld-board-task">
            Test web and mobile data sync<span>Today · DO</span>
          </div>
        </div>
      </div>
    </div>
  );
}

const MOCKS = { ask: AskMock, heard: HeardMock, decisions: DecisionsMock, recurring: RecurringMock, tasks: TasksMock } as const;

function Product() {
  return (
    <section id="product" className="ld-product">
      {DEEP_DIVES.map((dive, index) => {
        const Mock = MOCKS[dive.id];
        return (
          <div key={dive.id} className={`ld-dive${index % 2 === 1 ? " ld-dive-flip" : ""}`}>
            <div className="ld-dive-text">
              <span className="ld-eyebrow">{dive.eyebrow}</span>
              <h3 className="ld-h3">{dive.title}</h3>
              <p className="ld-sub ld-sub-md">{dive.body}</p>
              <ul className="ld-points">
                {dive.points.map((point) => (
                  <li key={point}>{point}</li>
                ))}
              </ul>
            </div>
            <Mock />
          </div>
        );
      })}
      <p className="ld-product-note">{DEEP_DIVES_NOTE}</p>
    </section>
  );
}

function Features() {
  return (
    <section id="features" className="ld-night">
      <div className="ld-section ld-features">
        <div className="ld-heading">
          <span className="ld-eyebrow ld-eyebrow-mint">AND THE BASICS, DONE PROPERLY</span>
          <h2 className="ld-h2">Everything you expect from a note taker.</h2>
        </div>
        <div className="ld-feature-grid">
          {FEATURES.map((f) => (
            <div key={f.title} className="ld-feature">
              <div className="ld-feature-title">{f.title}</div>
              <p>{f.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function Teams() {
  return (
    <section id="teams" className="ld-section ld-teams">
      <div className="ld-heading">
        <span className="ld-eyebrow">WHO IT&apos;S FOR</span>
        <h2 className="ld-h2">Built for teams that run on meetings and ship on deadlines.</h2>
      </div>
      <div className="ld-team-grid">
        {TEAMS.map((t) => (
          <div key={t.title} className="ld-team">
            <div className="ld-team-title">{t.title}</div>
            <p>{t.body}</p>
            <span className="ld-team-uses">{t.uses}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

function Security() {
  return (
    <section id="security" className="ld-section ld-security">
      <div className="ld-security-intro">
        <span className="ld-eyebrow">PRIVACY AND SECURITY</span>
        <h2 className="ld-h2 ld-h2-sm">{SECURITY_TITLE}</h2>
        <p>
          Built for teams who discuss customers, hiring and plans in their calls. Read the full details in our{" "}
          <AppLink href="/privacy">Privacy Policy</AppLink>.
        </p>
      </div>
      <div className="ld-security-grid">
        {SECURITY.map((item) => (
          <div key={item.title} className="ld-security-item">
            <div className="ld-security-title">{item.title}</div>
            <p>{item.body}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

// The questions, as native <details> (one open at a time, no JavaScript). Shared by the landing page
// and the pricing page (its own questions, its own container class).
export function FaqList({ items, className = "" }: { items: ReadonlyArray<{ q: string; a: string }>; className?: string }) {
  return (
    <div className={`ld-faq-list ${className}`.trim()}>
      {items.map((item, index) => (
        <details key={item.q} name="faq" open={index === 0}>
          <summary>
            {item.q}
            <span className="ld-faq-plus">
              <Glyph d="M12 5v14M5 12h14" size={14} stroke={2.4} />
            </span>
          </summary>
          <p>{item.a}</p>
        </details>
      ))}
    </div>
  );
}

function Faq() {
  return (
    <section id="faq" className="ld-faq">
      <div className="ld-faq-in">
        <h2 className="ld-h2 ld-h2-center">Questions, answered</h2>
        <FaqList items={FAQS} />
      </div>
    </section>
  );
}

function Closing() {
  return (
    <section className="ld-closing">
      <div className="ld-closing-in">
        <div className="ld-closing-copy">
          <h2 className="ld-h2 ld-h2-md">{CLOSING.title}</h2>
          <p>{CLOSING.body}</p>
          <SignupNote className="ld-signup-note-on-teal" />
        </div>
        <div className="ld-closing-actions">
          <AppLink className="btn btn-xl ld-create" href="/signup">
            Create your account
          </AppLink>
          <Button kind="outline-light" href="/pricing" size="xl">
            See pricing
          </Button>
        </div>
      </div>
    </section>
  );
}

export function LandingFooter({ signedIn = false }: { signedIn?: boolean }) {
  return (
    <footer className="ld-footer">
      <div className="ld-footer-in">
        <div className="ld-footer-cols">
          <div className="ld-footer-brand">
            <Logo size="sm" href={signedIn ? "/welcome" : "/"} />
            <p>Meeting notes, decisions and tasks for teams, from {LEGAL_NAME}.</p>
          </div>
          <div className="ld-footer-col">
            <span>Product</span>
            <a href="#product">Product</a>
            <a href="#features">Features</a>
            <AppLink href="/pricing">Pricing</AppLink>
            <a href="#security">Security</a>
          </div>
          <div className="ld-footer-col">
            <span>Company</span>
            <AppLink href="/privacy">Privacy</AppLink>
            <AppLink href="/terms">Terms</AppLink>
            <AppLink href="mailto:privacy@beyrictech.com">Contact</AppLink>
          </div>
        </div>
        <div className="ld-footer-base">
          <span>&copy; 2026 {LEGAL_NAME}</span>
          <span>notes.beyrictech.com</span>
        </div>
      </div>
    </footer>
  );
}

// The designer's pricing footer: one line, the entity and four links.
export function PricingFooter({ signedIn = false }: { signedIn?: boolean }) {
  return (
    <footer className="ld-pfooter">
      <div className="ld-pfooter-in">
        <span>&copy; 2026 {LEGAL_NAME}</span>
        <span className="ld-pfooter-links">
          <AppLink href={signedIn ? "/welcome" : "/"}>Product</AppLink>
          <AppLink href="/privacy">Privacy</AppLink>
          <AppLink href="/terms">Terms</AppLink>
          <AppLink href="mailto:privacy@beyrictech.com">Contact</AppLink>
        </span>
      </div>
    </footer>
  );
}

// A visitor who is not logged in sees this page at `/` (app/page.tsx shows the owner Meetings there);
// everyone, the owner included, sees it at `/welcome` (app/welcome/page.tsx).
export function Landing() {
  return (
    <>
      <Hero />
      <Tools />
      <Why />
      <Product />
      <Features />
      <Teams />
      <Security />
      <Faq />
      <Closing />
    </>
  );
}
