import type { ReactNode } from "react";
import { AppLink, Badge, Icon, Logo } from "@/components/ui";
import { LEGAL_NAME } from "@/lib/site";

// The sign-in layout from Auth.dc.html: the form on the left, a teal panel with a product preview
// on the right (the panel drops below the form on a narrow screen, as the prototype's grid does).
// Only what the app can do today is drawn on the left: see docs/ui/parity-report.md. Every account
// screen (sign-in, sign-up, confirm email, forgot and reset password, pilot full) uses this frame.
// `action` is the prototype's link at the top right ("New to Beyric? Create account").
export function AuthShell({
  children,
  action,
}: {
  children: ReactNode;
  action?: { text?: string; label: string; href: string };
}) {
  return (
    <div className="auth-split">
      <div className="auth-main">
        <div className="auth-bar">
          <Logo size="lg" />
          {action ? (
            <div className="auth-bar-link">
              {action.text ? <span className="auth-bar-text">{action.text}</span> : null}
              <AppLink href={action.href}>{action.label}</AppLink>
            </div>
          ) : null}
        </div>
        <main className="auth-center">
          <div className="auth-box">{children}</div>
        </main>
        <div className="auth-foot">
          <span>&copy; 2026 {LEGAL_NAME}</span>
          <span className="auth-foot-links">
            <AppLink href="/privacy">Privacy</AppLink>
            <AppLink href="/terms">Terms</AppLink>
            <AppLink href="mailto:privacy@beyrictech.com">Help</AppLink>
          </span>
        </div>
      </div>
      <AuthPanel />
    </div>
  );
}

function AuthPanel() {
  return (
    <aside className="auth-panel" aria-hidden="true">
      <div className="auth-panel-copy">
        <h2>Every meeting, written up and assigned.</h2>
        <p>Notes, decisions and tasks for each owner, ready minutes after the call ends.</p>
      </div>
      <div className="auth-preview">
        <div className="preview-card">
          <div className="preview-head">
            <div>
              <div className="preview-title">Beyric Monday planning</div>
              <div className="preview-meta">Mon 2 Nov &middot; Google Meet &middot; 46 min</div>
            </div>
            <Badge tone="success" dot small>
              Ready
            </Badge>
          </div>
          <div className="preview-body">
            <div>
              <div className="preview-label">Summary</div>
              <div className="preview-text">
                Client Portal progress, onboarding testing and the move to Slack. The team is preparing for a
                December 8 launch.
              </div>
            </div>
            <div>
              <div className="preview-label">Decisions</div>
              <div className="preview-checks">
                <div>
                  <Icon name="check" size={14} stroke={2.4} />
                  Use Slack for official work updates
                </div>
                <div>
                  <Icon name="check" size={14} stroke={2.4} />
                  Prepare for the December 8 launch
                </div>
              </div>
            </div>
          </div>
        </div>
        <div className="preview-task">
          <div className="preview-task-head">
            <span className="preview-eyebrow">NEW TASK</span>
            <span className="preview-sprint">Sprint 12</span>
          </div>
          <div className="preview-task-title">Finish payment checkout</div>
          <div className="preview-task-foot">
            <span className="preview-due">Due Fri 6 Nov</span>
            <span className="preview-owner">
              <span className="avatar avatar-22 avatar-a">AT</span>Adebayo Tosin
            </span>
          </div>
        </div>
      </div>
      <div className="auth-panel-note">Works with Google Meet, Microsoft Teams and Zoom</div>
    </aside>
  );
}
