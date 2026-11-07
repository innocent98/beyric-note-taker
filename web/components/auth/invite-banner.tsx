import { Icon } from "@/components/ui";
import { initials } from "@/lib/format";

// The top of the designer's "Join your team" screen (Auth.dc.html:173-180): a bordered banner with
// the inviter's avatar and "{name} invited you to the {team} workspace". The file draws three
// sample teammates; an invitation names only who invited you, so one avatar is drawn. Without an
// inviter name the sentence reads "You are invited to the {team} workspace" and the avatar carries
// the team's first letter.
export function InviteBanner({ by, team }: { by: string | null; team: string }) {
  return (
    <div className="invite-banner">
      <div className="invite-avatars" aria-hidden="true">
        <span className="invite-avatar">{initials(by ?? team)}</span>
      </div>
      <div className="invite-banner-text">
        {by ? (
          <>
            <b>{by}</b> invited you to the <b>{team}</b> workspace
          </>
        ) : (
          <>
            You are invited to the <b>{team}</b> workspace
          </>
        )}
      </div>
    </div>
  );
}

// The invited address, locked (Auth.dc.html:186-189): a grey box with a lock at its right. It is
// text, not an input: the address is the invitation's and cannot be changed.
export function LockedEmail({ email }: { email: string }) {
  return (
    <div className="field">
      <span className="field-label" id="locked-email-label">
        Email
      </span>
      <div className="locked-field" aria-labelledby="locked-email-label" role="textbox" aria-readonly="true">
        <span>{email}</span>
        <Icon name="lock" size={15} />
      </div>
    </div>
  );
}
