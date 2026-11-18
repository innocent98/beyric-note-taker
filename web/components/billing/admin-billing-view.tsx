"use client";

import { useCallback, useEffect, useState } from "react";
import { Alert, Badge, Button, Card, EmptyState, PageHead } from "@/components/ui";
import { LocalTime } from "@/components/app/local-time";
import { api } from "@/lib/api/browser";
import { ApiError, errorMessage } from "@/lib/api/client";
import {
  PAYMENT_KIND_WORDS,
  PAYMENT_STATUS_WORDS,
  REFUND_REASON_WORDS,
  REFUND_STATUS_WORDS,
  formatMoney,
  providerName,
  type AdminPayment,
  type AdminRefund,
} from "@/lib/billing";

type Loaded = { payments: AdminPayment[]; refunds: AdminRefund[] };

// A refusal the server wrote for the administrator is shown as it comes (it names what is wrong).
const say = (caught: unknown) => (caught instanceof ApiError && caught.status < 500 ? caught.message : errorMessage(caught));

// The refund screen (billing stage S9): the administrator's payments, the refunds waiting for a person, and
// the one control that refunds a payment. The server decides what may be refunded (`refund_option`); this
// page only shows its answer and asks before it acts. It follows the billing page's lists (`pl-row`).
export function AdminBillingView() {
  const [data, setData] = useState<Loaded | null>(null);
  const [view, setView] = useState<"all" | "needs_refund">("all");
  const [unavailable, setUnavailable] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reads, setReads] = useState(0);
  const load = useCallback(() => setReads((n) => n + 1), []);

  useEffect(() => {
    let alive = true;
    Promise.all([api.adminBilling.payments(view), api.adminBilling.refunds()])
      .then(([payments, refunds]) => {
        if (!alive) return;
        setData({ payments, refunds });
        setLoadError(null);
      })
      .catch((caught) => {
        if (!alive) return;
        if (caught instanceof ApiError && (caught.status === 403 || caught.status === 404)) {
          setUnavailable(
            caught.status === 403
              ? "This page is for the administrator only."
              : "Billing is not switched on for this server.",
          );
        } else setLoadError(say(caught));
      });
    return () => {
      alive = false;
    };
  }, [reads, view]);

  if (unavailable) {
    return (
      <div className="stack-md measure">
        <PageHead title="Refunds" />
        <Card>
          <EmptyState title={unavailable} />
        </Card>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="stack-md measure">
        <PageHead title="Refunds" />
        {loadError ? (
          <>
            <Alert kind="error">{loadError}</Alert>
            <div>
              <Button kind="secondary" type="button" onClick={load}>
                Try again
              </Button>
            </div>
          </>
        ) : (
          <p className="set-sub" role="status">
            Loading payments...
          </p>
        )}
      </div>
    );
  }

  const waiting = data.refunds.filter((refund) => refund.needs_resolution);
  return (
    <div className="stack-md measure">
      <PageHead
        title="Refunds"
        subtitle="Refund a double charge, a first plan payment within 7 days with no hours used, or an unused pack within 14 days."
      />
      {loadError ? <Alert kind="error">{loadError} What you see may be out of date.</Alert> : null}
      {waiting.length > 0 ? <Waiting refunds={waiting} onDone={load} /> : null}
      <section className="card card-flush bill-card-flush" aria-labelledby="adm-payments-title">
        <div className="bill-flush-head adm-head">
          <h2 id="adm-payments-title" className="set-title">
            Payments
          </h2>
          <div className="adm-filter" role="group" aria-label="Which payments">
            <Button
              kind={view === "all" ? "primary" : "secondary"}
              size="sm"
              type="button"
              aria-pressed={view === "all"}
              onClick={() => setView("all")}
            >
              All
            </Button>
            <Button
              kind={view === "needs_refund" ? "primary" : "secondary"}
              size="sm"
              type="button"
              aria-pressed={view === "needs_refund"}
              onClick={() => setView("needs_refund")}
            >
              Needs a refund
            </Button>
          </div>
        </div>
        {data.payments.length === 0 ? (
          <p className="set-sub bill-empty">
            {view === "needs_refund" ? "No payment is waiting for a refund." : "There are no payments yet."}
          </p>
        ) : (
          <ul className="bill-history">
            {data.payments.map((payment) => (
              <PaymentRow key={payment.id} payment={payment} onDone={load} />
            ))}
          </ul>
        )}
      </section>
      <Refunds refunds={data.refunds} />
    </div>
  );
}

function PaymentRow({ payment, onDone }: { payment: AdminPayment; onDone: () => void }) {
  const [asking, setAsking] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const words = PAYMENT_STATUS_WORDS[payment.status];
  const option = payment.refund_option;
  const money = formatMoney(payment.amount_minor, payment.currency);

  async function refund() {
    if (!option || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.adminBilling.refund(payment.id, option.reason, note.trim() || null);
      setAsking(false);
      onDone();
    } catch (caught) {
      setError(say(caught));
      onDone(); // a refused refund may have changed the facts (another admin was first): read again
    }
    setBusy(false);
  }

  return (
    <li className="pl-row adm-row" key={payment.id}>
      <div className="adm-line">
        <div className="pl-grow">
          <div className="pl-row-title">
            {PAYMENT_KIND_WORDS[payment.kind]} · {payment.payer_email ?? "account deleted"}
          </div>
          <div className="pl-row-sub">
            <LocalTime iso={payment.paid_at ?? payment.created_at} kind="day" /> · {providerName(payment.provider)} ·{" "}
            {payment.reference}
            {payment.invoice_number ? <> · Invoice {payment.invoice_number}</> : null}
          </div>
          {payment.needs_refund_reason ? (
            <div className="pl-row-sub">Nothing could use this payment: {payment.needs_refund_reason}.</div>
          ) : null}
        </div>
        <div className="pl-controls">
          <strong>{money}</strong>
          <Badge tone={words.tone}>{words.label}</Badge>
          {payment.refund ? (
            <Badge tone={REFUND_STATUS_WORDS[payment.refund.status].tone}>
              {REFUND_STATUS_WORDS[payment.refund.status].label}
            </Badge>
          ) : null}
          {option?.allowed && !asking ? (
            <Button
              kind="secondary"
              size="sm"
              type="button"
              aria-label={`Refund payment ${payment.id}`}
              onClick={() => setAsking(true)}
            >
              Refund...
            </Button>
          ) : null}
        </div>
      </div>
      {option && !option.allowed && !payment.refund ? (
        <p className="pl-row-sub adm-why">Cannot be refunded: {option.message}</p>
      ) : null}
      {asking && option ? (
        <div className="alert adm-confirm" role="group" aria-label={`Refund ${money}`}>
          <p className="alert-text">
            <strong>
              Refund {money} to {payment.payer_email}?
            </strong>{" "}
            {REFUND_REASON_WORDS[option.reason].label}. {REFUND_REASON_WORDS[option.reason].effect} The whole payment
            goes back through {providerName(payment.provider)}. It cannot be undone.
          </p>
          <label className="adm-note">
            <span className="set-sub">A note for yourself (optional)</span>
            <input
              className="input"
              value={note}
              maxLength={500}
              onChange={(event) => setNote(event.target.value)}
            />
          </label>
          {error ? <Alert kind="error">{error}</Alert> : null}
          <div className="alert-actions">
            <Button kind="primary" size="sm" type="button" disabled={busy} onClick={() => void refund()}>
              {busy ? "Refunding..." : `Refund ${money}`}
            </Button>
            <Button kind="secondary" size="sm" type="button" disabled={busy} onClick={() => setAsking(false)}>
              Not now
            </Button>
          </div>
        </div>
      ) : null}
    </li>
  );
}

// A refund the provider never answered (or a process died on): nothing asks for it again. Someone looks at the
// provider's dashboard for the reference and says what is there. Only "no refund exists" lets a new one start.
function Waiting({ refunds, onDone }: { refunds: AdminRefund[]; onDone: () => void }) {
  return (
    <section className="card bill-card" aria-labelledby="adm-waiting-title">
      <div>
        <h2 id="adm-waiting-title" className="set-title">
          Waiting for you
        </h2>
        <p className="set-sub">
          These refunds got no answer from the provider, so nobody knows if they happened. Do not ask for them again:
          look in the provider&apos;s dashboard for the reference, then say what you found.
        </p>
      </div>
      <ul className="bill-history">
        {refunds.map((refund) => (
          <WaitingRow key={refund.id} refund={refund} onDone={onDone} />
        ))}
      </ul>
    </section>
  );
}

function WaitingRow({ refund, onDone }: { refund: AdminRefund; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function resolve(outcome: "made" | "not_made") {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.adminBilling.resolve(refund.id, outcome);
      onDone();
    } catch (caught) {
      setError(say(caught));
    }
    setBusy(false);
  }

  return (
    <li className="pl-row adm-row">
      <div className="adm-line">
        <div className="pl-grow">
          <div className="pl-row-title">
            {formatMoney(refund.amount_minor, refund.currency)} to {refund.payer_email ?? "a deleted account"}
          </div>
          <div className="pl-row-sub">
            Look for this reference: <code>{refund.reference}</code>
          </div>
        </div>
        <div className="pl-controls">
          <Button kind="secondary" size="sm" type="button" disabled={busy} onClick={() => void resolve("made")}>
            The refund exists
          </Button>
          <Button kind="secondary" size="sm" type="button" disabled={busy} onClick={() => void resolve("not_made")}>
            No refund exists
          </Button>
        </div>
      </div>
      {error ? <Alert kind="error">{error}</Alert> : null}
    </li>
  );
}

function Refunds({ refunds }: { refunds: AdminRefund[] }) {
  return (
    <section className="card card-flush bill-card-flush" aria-labelledby="adm-refunds-title">
      <div className="bill-flush-head">
        <h2 id="adm-refunds-title" className="set-title">
          Refunds
        </h2>
      </div>
      {refunds.length === 0 ? (
        <p className="set-sub bill-empty">No refund has been made.</p>
      ) : (
        <ul className="bill-history">
          {refunds.map((refund) => {
            const words = REFUND_STATUS_WORDS[refund.status];
            return (
              <li className="pl-row pl-row-center" key={refund.id}>
                <div className="pl-grow">
                  <div className="pl-row-title">
                    {REFUND_REASON_WORDS[refund.reason].label} · {refund.payer_email ?? "account deleted"}
                  </div>
                  <div className="pl-row-sub">
                    <LocalTime iso={refund.created_at} kind="day" /> · {refund.reference}
                    {refund.note ? <> · {refund.note}</> : null}
                  </div>
                </div>
                <div className="pl-controls">
                  <strong>{formatMoney(refund.amount_minor, refund.currency)}</strong>
                  <Badge tone={words.tone}>{words.label}</Badge>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
