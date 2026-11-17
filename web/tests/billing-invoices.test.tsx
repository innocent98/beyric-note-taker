// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AdminBillingView } from "@/components/billing/admin-billing-view";
import { PaymentHistory } from "@/components/billing/payment-history";
import { ApiError } from "@/lib/api/client";
import { invoicePdfPath, type AdminPayment, type AdminRefund, type BillingInvoice, type BillingPayment } from "@/lib/billing";
import { capInvoices } from "./billing-fixtures";

// The invoice downloads in the payment history and the administrator's refund screen (billing stage S9), on the
// bodies captured from the running app (docs/fe-captures/billing-invoices/).

const mocks = vi.hoisted(() => ({
  downloadInvoice: vi.fn(),
  adminPayments: vi.fn(),
  adminRefunds: vi.fn(),
  adminRefund: vi.fn(),
  adminResolve: vi.fn(),
  saveBlob: vi.fn(),
}));
vi.mock("@/lib/api/browser", () => ({
  api: {
    billing: { downloadInvoice: mocks.downloadInvoice },
    adminBilling: { payments: mocks.adminPayments, refunds: mocks.adminRefunds, refund: mocks.adminRefund, resolve: mocks.adminResolve },
  },
}));
vi.mock("@/lib/save-file", () => ({ saveBlob: mocks.saveBlob }));

const payments = capInvoices<{ payments: BillingPayment[] }>("16-payments-after-paid.json").payments;
const waiting = capInvoices<{ payments: BillingPayment[] }>("14-payments-before-paid.json").payments;
const adminPayments = capInvoices<{ payments: AdminPayment[] }>("37-admin-payments.json").payments;
const adminRefunds = capInvoices<{ refunds: AdminRefund[] }>("60-admin-refunds-list.json").refunds;
const invoices = capInvoices<{ invoices: BillingInvoice[] }>("17-invoices-list.json").invoices;

beforeEach(() => {
  mocks.adminPayments.mockResolvedValue(adminPayments);
  mocks.adminRefunds.mockResolvedValue(adminRefunds);
});
afterEach(() => {
  vi.clearAllMocks();
  cleanup();
});

describe("the payment history", () => {
  it("shows the invoice of a paid payment and downloads it as a file", async () => {
    const blob = new Blob(["%PDF-1.7"], { type: "application/pdf" });
    mocks.downloadInvoice.mockResolvedValue({ blob, filename: `invoice-${payments[0].invoice_number}.pdf` });
    render(<PaymentHistory payments={payments} />);
    expect(screen.getByText(`Invoice ${payments[0].invoice_number}`)).toBeTruthy();
    expect(payments[0].invoice_number).toBe(invoices[0].number); // the list and the history name the same invoice
    await userEvent.click(screen.getByRole("button", { name: `Download invoice ${payments[0].invoice_number}` }));
    await waitFor(() => expect(mocks.saveBlob).toHaveBeenCalledWith(blob, `invoice-${payments[0].invoice_number}.pdf`));
    expect(mocks.downloadInvoice).toHaveBeenCalledWith(payments[0].invoice_number);
  });

  it("says it is preparing the file, and only one file at a time", async () => {
    let finish: (value: unknown) => void = () => {};
    mocks.downloadInvoice.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    render(<PaymentHistory payments={payments} />);
    const button = screen.getByRole("button", { name: /Download invoice/ });
    await userEvent.click(button);
    expect(await screen.findByText("Preparing...")).toBeTruthy();
    expect((button as HTMLButtonElement).disabled).toBe(true);
    finish({ blob: new Blob(["x"]), filename: null });
    await waitFor(() => expect(mocks.saveBlob).toHaveBeenCalledTimes(1));
    expect(mocks.saveBlob.mock.calls[0][1]).toBe(`invoice-${payments[0].invoice_number}.pdf`); // the file is named when the server names none
  });

  it("shows the server's own sentence when the file could not be prepared, not a JSON page", async () => {
    mocks.downloadInvoice.mockRejectedValue(new ApiError(503, "invoice_busy", "Invoices are being prepared. Try again in a few seconds."));
    render(<PaymentHistory payments={payments} />);
    await userEvent.click(screen.getByRole("button", { name: /Download invoice/ }));
    expect(await screen.findByText("Invoices are being prepared. Try again in a few seconds.")).toBeTruthy();
    expect(mocks.saveBlob).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /Download invoice/ })).toBeTruthy(); // can try again
  });

  it("offers no invoice for a payment that is still waiting", () => {
    render(<PaymentHistory payments={waiting} />);
    expect(screen.queryByText(/^Invoice /)).toBeNull();
    expect(screen.queryByRole("button", { name: /Download invoice/ })).toBeNull();
  });

  it("keeps a refunded payment's invoice downloadable and says Refunded", () => {
    render(<PaymentHistory payments={[{ ...payments[0], status: "refunded" }]} />);
    expect(screen.getByText("Refunded")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Download invoice/ })).toBeTruthy();
  });

  it("names the pdf route the guide documents", () => {
    expect(invoicePdfPath("BN-2026-000001")).toBe("/api/billing/invoices/BN-2026-000001/pdf");
  });
});

describe("the administrator's refund screen", () => {
  it("lists payments with what the server says about refunding each, and refuses nothing by itself", async () => {
    render(<AdminBillingView />);
    expect(await screen.findByRole("heading", { name: "Payments" })).toBeTruthy();
    const refundable = adminPayments.filter((p) => p.refund_option?.allowed && !p.refund);
    expect(refundable.length).toBeGreaterThan(0);
    for (const p of refundable) expect(screen.getByRole("button", { name: `Refund payment ${p.id}` })).toBeTruthy();
    const over = adminPayments.find((p) => p.refund_option && !p.refund_option.allowed)!;
    expect(screen.getByText(`Cannot be refunded: ${over.refund_option!.message}`)).toBeTruthy();
    expect(screen.queryByRole("button", { name: `Refund payment ${over.id}` })).toBeNull();
  });

  it("asks before refunding, says what it does, then refunds the whole payment once", async () => {
    mocks.adminRefund.mockResolvedValue(adminRefunds[0]);
    render(<AdminBillingView />);
    const target = adminPayments.find((p) => p.refund_option?.allowed && p.refund_option.reason === "first_plan_7d")!;
    await userEvent.click(await screen.findByRole("button", { name: `Refund payment ${target.id}` }));
    const panel = screen.getByRole("group", { name: /^Refund / });
    expect(within(panel).getByText(/The plan ends now, its hours are removed and the saved card is deleted/)).toBeTruthy();
    expect(within(panel).getByText(/It cannot be undone/)).toBeTruthy();
    expect(mocks.adminRefund).not.toHaveBeenCalled(); // asking is not refunding
    await userEvent.type(within(panel).getByRole("textbox"), "asked by email");
    await userEvent.click(within(panel).getByRole("button", { name: /^Refund USD|^Refund \$/ }));
    await waitFor(() => expect(mocks.adminRefund).toHaveBeenCalledTimes(1));
    expect(mocks.adminRefund).toHaveBeenCalledWith(target.id, "first_plan_7d", "asked by email");
  });

  it("can be backed out of without a call", async () => {
    render(<AdminBillingView />);
    const target = adminPayments.find((p) => p.refund_option?.allowed)!;
    await userEvent.click(await screen.findByRole("button", { name: `Refund payment ${target.id}` }));
    await userEvent.click(screen.getByRole("button", { name: "Not now" }));
    expect(mocks.adminRefund).not.toHaveBeenCalled();
    expect(screen.queryByRole("group", { name: /^Refund / })).toBeNull();
  });

  it("shows the server's refusal and reads again (another administrator may have been first)", async () => {
    mocks.adminRefund.mockRejectedValue(new ApiError(409, "refund_in_progress", "A refund of this payment is already under way."));
    render(<AdminBillingView />);
    const target = adminPayments.find((p) => p.refund_option?.allowed)!;
    await userEvent.click(await screen.findByRole("button", { name: `Refund payment ${target.id}` }));
    const calls = mocks.adminPayments.mock.calls.length;
    await userEvent.click(within(screen.getByRole("group", { name: /^Refund / })).getByRole("button", { name: /^Refund / }));
    expect(await screen.findByText("A refund of this payment is already under way.")).toBeTruthy();
    await waitFor(() => expect(mocks.adminPayments.mock.calls.length).toBeGreaterThan(calls));
  });

  it("puts a refund nobody can be sure of in front of the administrator and never offers to ask again", async () => {
    mocks.adminResolve.mockResolvedValue({ ...adminRefunds[0], status: "failed" });
    render(<AdminBillingView />);
    const waitingRefund = adminRefunds.find((r) => r.needs_resolution)!;
    const section = (await screen.findByRole("heading", { name: "Waiting for you" })).closest("section") as HTMLElement;
    expect(within(section).getByText(waitingRefund.reference)).toBeTruthy();
    expect(within(section).queryByRole("button", { name: /Refund/ })).toBeNull(); // nothing asks again from here
    await userEvent.click(within(section).getByRole("button", { name: "No refund exists" }));
    await waitFor(() => expect(mocks.adminResolve).toHaveBeenCalledWith(waitingRefund.id, "not_made"));
  });

  it("says plainly that the page is for the administrator, and that billing may be off", async () => {
    mocks.adminPayments.mockRejectedValue(new ApiError(403, "forbidden", "This is for the administrator only."));
    render(<AdminBillingView />);
    expect(await screen.findByText("This page is for the administrator only.")).toBeTruthy();
    cleanup();
    mocks.adminPayments.mockRejectedValue(new ApiError(404, "not_found", "Not found."));
    render(<AdminBillingView />);
    expect(await screen.findByText("Billing is not switched on for this server.")).toBeTruthy();
  });
});
