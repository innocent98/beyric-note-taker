import { Button } from "@/components/ui";

// "Open Gmail" and "Open Outlook" under a "check your email" screen, as Auth.dc.html draws them
// (plain links to the two web inboxes; they do not know which one the person uses).
export function MailLinks() {
  return (
    <div className="auth-pair">
      <Button kind="secondary" href="https://mail.google.com" target="_blank" rel="noopener noreferrer">
        Open Gmail
      </Button>
      <Button kind="secondary" href="https://outlook.office.com" target="_blank" rel="noopener noreferrer">
        Open Outlook
      </Button>
    </div>
  );
}
