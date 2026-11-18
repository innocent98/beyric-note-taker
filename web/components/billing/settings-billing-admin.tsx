import { Button } from "@/components/ui";

// For the administrator only (the page decides): the way into the refund screen (billing stage S9). Drawn as
// the other Settings cards are; LEAD-DESIGNED, no admin design exists. It only links: the page behind it asks
// the server what the administrator may do.
export function SettingsBillingAdmin() {
  return (
    <section className="card bill-card" aria-labelledby="set-billing-admin">
      <div>
        <h2 id="set-billing-admin" className="set-title">
          Refunds
        </h2>
        <p className="set-sub">Payments and refunds, for the administrator.</p>
      </div>
      <div>
        <Button href="/admin/billing" kind="secondary" size="sm">
          Open refunds
        </Button>
      </div>
    </section>
  );
}
