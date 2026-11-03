import type { ReactNode } from "react";
import { AppLink, Badge, Button, Icon, Logo } from "@/components/ui";
import { LEGAL_NAME } from "@/lib/site";
import { FAQS, FEATURES, PLATFORMS, SECURITY, TOOLS } from "./content";

// The landing page from the designer's Landing.dc.html, section by section. The sign-up buttons
// ("Get started", "Create your account") and "See the app" are not drawn: accounts do not exist yet
// and the prototype link is not a page (docs/ui/parity-report.md).

function Glyph({ d, size, stroke = 1.9 }: { d: string; size: number; stroke?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={stroke} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

export function LandingHeader() {
  return (
    <header className="ld-header">
      <div className="ld-header-in">
        <Logo size="lg" />
        <nav className="ld-nav" aria-label="Sections">
          <a href="#workflow">How it works</a>
          <a href="#features">Features</a>
          <a href="#security">Security</a>
          <a href="#faq">FAQ</a>
        </nav>
        <div className="ld-header-actions">
          <AppLink className="ld-signin" href="/login">
            Sign in
          </AppLink>
        </div>
      </div>
    </header>
  );
}

// Initials in a coloured circle, in the sizes the landing draws (26, 22 and 18 px).
function Avatar({ n, children, size }: { n: 1 | 2 | 3 | 4 | 5; children: ReactNode; size: 26 | 22 | 18 }) {
  return <span className={`avatar avatar-${size} avatar-${["", "a", "b", "c", "d", "e"][n]}`}>{children}</span>;
}

function Hero() {
  return (
    <section className="ld-hero">
      <div className="ld-hero-copy">
        <div className="ld-pill">
          <span className="ld-pill-tag">New</span>Tasks, sprints and backlogs from every meeting
        </div>
        <h1 className="ld-h1">Your meetings, turned into finished work.</h1>
        <p className="ld-lede">
          Beyric Note Taker joins your calls, writes clear notes, and turns every action item into a task assigned to the
          right person on your team.
        </p>
        <div className="ld-cta">
          <Button href="/login" size="xl">
            Sign in
            <Icon name="arrow" size={16} stroke={2.2} />
          </Button>
        </div>
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

      <div className="ld-shot" aria-hidden="true">
        <div className="ld-window">
          <div className="ld-window-bar">
            <span /> <span /> <span />
          </div>
          <div className="ld-window-body">
            <div className="ld-mini-nav">
              <span className="is-current">Meetings</span>
              <span className="ld-mini-tasks">
                Tasks<span className="ld-mini-count">8</span>
              </span>
              <span>Projects</span>
              <span>Decisions</span>
              <span>Team</span>
            </div>
            <div className="ld-mini-main">
              <div className="ld-mini-head">
                <div>
                  <div className="ld-mini-title">Beyric Monday planning</div>
                  <div className="ld-mini-meta">Mon 2 Nov &middot; Google Meet &middot; 46 min</div>
                </div>
                <Badge tone="success" dot small>
                  Ready
                </Badge>
              </div>
              <div>
                <div className="ld-mini-label">Summary</div>
                <div className="ld-mini-text">
                  The team reviewed Client Portal progress ahead of the December 8 launch, including onboarding
                  testing, payment checkout and the pricing model.
                </div>
              </div>
              <div>
                <div className="ld-mini-label ld-mini-label-lg">Action items</div>
                <div className="ld-mini-items">
                  <div>
                    <span>Finish payment checkout</span>
                    <Avatar n={1} size={22}>AT</Avatar>
                  </div>
                  <div>
                    <span>Test web and mobile data sync</span>
                    <Avatar n={3} size={22}>DO</Avatar>
                  </div>
                  <div>
                    <span>Log design refinements on the tracker</span>
                    <Avatar n={4} size={22}>AN</Avatar>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
        <div className="ld-toast">
          <span className="ld-toast-icon">
            <Glyph d="m5 12 5 5 9-10" size={15} stroke={2.4} />
          </span>
          <span className="ld-toast-text">
            <strong>8 tasks created</strong>
            <span>Assigned to 3 people in Sprint 12</span>
          </span>
        </div>
        <div className="ld-rec">
          <span className="ld-rec-dot" />
          Recording &middot; 12:48
        </div>
      </div>
    </section>
  );
}

function Tools() {
  return (
    <section className="ld-tools">
      <div className="ld-tools-in">
        <span className="ld-tools-label">Works with the tools you use</span>
        {TOOLS.map((name) => (
          <span key={name}>{name}</span>
        ))}
      </div>
    </section>
  );
}

function Step({ n, title, body, children, className = "" }: { n: string; title: string; body: string; children: ReactNode; className?: string }) {
  return (
    <div className="ld-step">
      <div className={`ld-step-art ${className}`}>{children}</div>
      <div className="ld-step-text">
        <span className="ld-step-n">{n}</span>
        <div className="ld-step-title">{title}</div>
        <p>{body}</p>
      </div>
    </div>
  );
}

function Workflow() {
  return (
    <section id="workflow" className="ld-section ld-workflow">
      <div className="ld-heading">
        <span className="ld-eyebrow">HOW IT WORKS</span>
        <h2 className="ld-h2">From conversation to assigned work, with no one taking notes.</h2>
      </div>
      <div className="ld-steps" aria-hidden="false">
        <Step n="01" title="It joins and listens" body="Paste a link or connect your calendar. The note taker joins, records audio and writes a transcript with speaker names." className="ld-art-lines">
          <div className="ld-line">
            <Avatar n={5} size={26}>KA</Avatar>
            <div className="ld-bubble">
              <span className="ld-time">00:12</span>We have three pricing options on the table.
            </div>
          </div>
          <div className="ld-line">
            <Avatar n={1} size={26}>AT</Avatar>
            <div className="ld-bubble">
              <span className="ld-time">04:38</span>I&apos;ll take payment checkout this week.
            </div>
          </div>
        </Step>
        <Step n="02" title="It writes the notes" body="A summary, key points, decisions and open questions, in the format your template asks for. Unclear words are flagged for review." className="ld-art-center">
          <div className="ld-notes-card">
            <div className="ld-caps">DECISIONS</div>
            <div className="ld-check-line">
              <Glyph d="m5 12 5 5 9-10" size={14} stroke={2.4} />
              Use Slack for official work updates
            </div>
            <div className="ld-check-line">
              <Glyph d="m5 12 5 5 9-10" size={14} stroke={2.4} />
              Prepare for the December 8 launch
            </div>
            <div className="ld-caps ld-caps-gap">OPEN QUESTIONS</div>
            <div className="ld-question">Which pricing model will the team choose?</div>
          </div>
        </Step>
        <Step n="03" title="It assigns the work" body="Each action item becomes a task for the teammate who owns it, filed into the right project and sprint, with a due date." className="ld-art-board">
          <div className="ld-col">
            <span className="ld-col-title">
              <i className="ld-dot-grey" />To do
            </span>
            <div className="ld-task">
              Finish payment checkout
              <span className="ld-task-foot ld-due-soon">
                Fri 6 Nov<Avatar n={1} size={18}>AT</Avatar>
              </span>
            </div>
            <div className="ld-task">
              Test data sync
              <span className="ld-task-foot">
                Today<Avatar n={3} size={18}>DO</Avatar>
              </span>
            </div>
          </div>
          <div className="ld-col">
            <span className="ld-col-title">
              <i className="ld-dot-amber" />In progress
            </span>
            <div className="ld-task">
              Refine onboarding copy
              <span className="ld-task-foot">
                Thu 5 Nov<Avatar n={4} size={18}>AN</Avatar>
              </span>
            </div>
          </div>
        </Step>
      </div>
    </section>
  );
}

function Features() {
  return (
    <section id="features" className="ld-night">
      <div className="ld-section ld-features">
        <div className="ld-features-head">
          <div className="ld-heading">
            <span className="ld-eyebrow ld-eyebrow-mint">BEYOND THE NOTES</span>
            <h2 className="ld-h2">One place for what was said, decided and promised.</h2>
          </div>
          <p>Every meeting adds to a record your whole team can search, share and act on.</p>
        </div>
        <div className="ld-feature-grid">
          {FEATURES.map((f) => (
            <div key={f.title} className="ld-feature">
              <div className="ld-feature-icon">
                <Glyph d={f.icon} size={20} />
              </div>
              <div className="ld-feature-title">{f.title}</div>
              <p>{f.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function Security() {
  return (
    <section id="security" className="ld-section ld-security">
      <div className="ld-security-intro">
        <span className="ld-eyebrow">PRIVACY AND SECURITY</span>
        <h2 className="ld-h2 ld-h2-sm">Your meetings stay yours.</h2>
        <p>
          Built for teams who discuss customers, hiring and plans in their calls. Read the full details in our{" "}
          <AppLink href="/privacy">Privacy Policy</AppLink>.
        </p>
      </div>
      <div className="ld-security-grid">
        {SECURITY.map((item) => (
          <div key={item.title} className="ld-security-item">
            <Glyph d={item.icon} size={22} />
            <div className="ld-security-title">{item.title}</div>
            <p>{item.body}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

function Faq() {
  return (
    <section id="faq" className="ld-faq">
      <div className="ld-faq-in">
        <h2 className="ld-h2 ld-h2-center">Questions, answered</h2>
        <div className="ld-faq-list">
          {FAQS.map((item, index) => (
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
      </div>
    </section>
  );
}

function Closing() {
  return (
    <section className="ld-closing">
      <div className="ld-closing-in">
        <div className="ld-closing-copy">
          <h2 className="ld-h2 ld-h2-md">Spend the meeting in the conversation.</h2>
          <p>Send the note taker to your next call and see the notes and tasks it leaves behind.</p>
        </div>
        <div className="ld-closing-actions">
          <Button kind="outline-light" href="/login" size="xl">
            Sign in
          </Button>
        </div>
      </div>
    </section>
  );
}

export function LandingFooter() {
  return (
    <footer className="ld-footer">
      <div className="ld-footer-in">
        <div className="ld-footer-cols">
          <div className="ld-footer-brand">
            <Logo size="sm" />
            <p>Meeting notes, decisions and tasks for teams, from {LEGAL_NAME}.</p>
          </div>
          <div className="ld-footer-col">
            <span>Product</span>
            <a href="#workflow">How it works</a>
            <a href="#features">Features</a>
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

// Only a visitor who is not logged in sees this page (app/page.tsx shows the owner Meetings).
export function Landing() {
  return (
    <>
      <Hero />
      <Tools />
      <Workflow />
      <Features />
      <Security />
      <Faq />
      <Closing />
    </>
  );
}
