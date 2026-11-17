import { Badge, Button } from "@/components/ui";
import { LocalTime } from "@/components/app/local-time";
import { PAYMENT_KIND_WORDS, PAYMENT_STATUS_WORDS, formatMoney, type BillingPayment } from "@/lib/billing";

// The person's own payments, newest first (GET /payments, at most 50). A waiting payment with its own
// page (`pay_url`) offers to continue there: it is that payment's page, never a second checkout.
export function PaymentHistory({ payments }: { payments: BillingPayment[] }) {
  return (
    <section className="card card-flush bill-card-flush" aria-labelledby="bill-history-title">
      <div className="bill-flush-head">
        <h2 id="bill-history-title" className="set-title">
          Payments
        </h2>
      </div>
      {payments.length === 0 ? (
        <p className="set-sub bill-empty">You have not made a payment yet.</p>
      ) : (
        <ul className="bill-history">
          {payments.map((payment) => {
            const words = PAYMENT_STATUS_WORDS[payment.status];
            return (
              <li className="pl-row pl-row-center" key={payment.reference}>
                <div className="pl-grow">
                  <div className="pl-row-title">{PAYMENT_KIND_WORDS[payment.kind]}</div>
                  <div className="pl-row-sub">
                    <LocalTime iso={payment.paid_at ?? payment.created_at} kind="day" /> · {payment.reference}
                  </div>
                </div>
                <div className="pl-controls">
                  <strong>{formatMoney(payment.amount_minor, payment.currency)}</strong>
                  <Badge tone={words.tone}>{words.label}</Badge>
                  {payment.status === "pending" && payment.pay_url ? (
                    <Button href={payment.pay_url} kind="secondary" size="sm">
                      Continue to pay
                    </Button>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
