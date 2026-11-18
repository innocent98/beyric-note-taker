"use client";

import { useRef, useState } from "react";
import { Alert, Badge, Button } from "@/components/ui";
import { LocalTime } from "@/components/app/local-time";
import { api } from "@/lib/api/browser";
import { PAYMENT_KIND_WORDS, PAYMENT_STATUS_WORDS, formatMoney, type BillingPayment } from "@/lib/billing";
import { saveBlob } from "@/lib/save-file";
import { billingErrorMessage } from "./common";

// The person's own payments, newest first (GET /payments, at most 50). A waiting payment with its own
// page (`pay_url`) offers to continue there: it is that payment's page, never a second checkout.
// A payment that was used has an invoice (`invoice_number`): its PDF is built when asked (stage S9), so the
// button says it is preparing it, and a refusal ("try again in a few seconds") is a sentence under the row,
// never a JSON page in a new tab.
export function PaymentHistory({ payments }: { payments: BillingPayment[] }) {
  const [working, setWorking] = useState<string | null>(null);
  const [failed, setFailed] = useState<{ number: string; message: string } | null>(null);
  const running = useRef(false);

  async function download(number: string) {
    if (running.current) return;
    running.current = true;
    setWorking(number);
    setFailed(null);
    try {
      const file = await api.billing.downloadInvoice(number);
      saveBlob(file.blob, file.filename ?? `invoice-${number}.pdf`);
    } catch (caught) {
      setFailed({ number, message: billingErrorMessage(caught) });
    }
    setWorking(null);
    running.current = false;
  }

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
            const number = payment.invoice_number ?? null;
            return (
              <li className="pl-row pl-row-center" key={payment.reference}>
                <div className="pl-grow">
                  <div className="pl-row-title">{PAYMENT_KIND_WORDS[payment.kind]}</div>
                  <div className="pl-row-sub">
                    <LocalTime iso={payment.paid_at ?? payment.created_at} kind="day" /> · {payment.reference}
                  </div>
                  {number ? <div className="pl-row-sub bill-invoice">Invoice {number}</div> : null}
                  {failed && failed.number === number ? (
                    <div className="bill-invoice-error">
                      <Alert kind="error">{failed.message}</Alert>
                    </div>
                  ) : null}
                </div>
                <div className="pl-controls">
                  <strong>{formatMoney(payment.amount_minor, payment.currency)}</strong>
                  <Badge tone={words.tone}>{words.label}</Badge>
                  {number ? (
                    <Button
                      kind="secondary"
                      size="sm"
                      type="button"
                      disabled={working !== null}
                      aria-label={`Download invoice ${number}`}
                      onClick={() => void download(number)}
                    >
                      {working === number ? "Preparing..." : "Download invoice"}
                    </Button>
                  ) : null}
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
