// @vitest-environment jsdom
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ForgotPasswordFlow } from "@/components/auth/forgot-password-flow";
import { ResetPasswordForm } from "@/components/auth/reset-password-form";
import { SignupFlow } from "@/components/auth/signup-flow";
import { VerifyEmailForm } from "@/components/auth/verify-email-form";
import { WaitlistForm } from "@/components/auth/waitlist-form";
import { LoginForm } from "@/components/app/login-form";
import { ApiError } from "@/lib/api/client";
import { accountBody, capturedAccount } from "./helpers";

// Every error here is built from a response captured against the running app
// (docs/fe-captures/pilot-accounts/), so the codes and messages are the real ones.

const mocks = vi.hoisted(() => ({
  replace: vi.fn(),
  refresh: vi.fn(),
  login: vi.fn(),
  signup: vi.fn(),
  resendVerification: vi.fn(),
  verifyEmail: vi.fn(),
  forgotPassword: vi.fn(),
  resetPassword: vi.fn(),
  joinWaitlist: vi.fn(),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: mocks.replace, refresh: mocks.refresh }) }));
vi.mock("@/lib/api/browser", () => ({
  api: {
    login: mocks.login,
    signup: mocks.signup,
    resendVerification: mocks.resendVerification,
    verifyEmail: mocks.verifyEmail,
    forgotPassword: mocks.forgotPassword,
    resetPassword: mocks.resetPassword,
    joinWaitlist: mocks.joinWaitlist,
  },
}));

function failure(file: string): ApiError {
  const { status, body } = capturedAccount(file).response;
  const { code, message, fields } = (body as { error: { code: string; message: string; fields?: { field: string; message: string }[] } }).error;
  return new ApiError(status, code, message, fields ?? []);
}

beforeEach(() => {
  Object.values(mocks).forEach((fn) => fn.mockReset());
  vi.useRealTimers();
});

async function fillSignup(user: ReturnType<typeof userEvent.setup>, { terms = true } = {}) {
  await user.type(screen.getByLabelText("Full name"), "Ada Example");
  await user.type(screen.getByLabelText("Work email"), "ada@example.test");
  await user.type(screen.getByLabelText("Password"), "correct horse battery");
  if (terms) await user.click(screen.getByRole("checkbox"));
}

describe("sign-up screen states", () => {
  it("draws the closed screen, with the waiting list and no sign-up form, when the pilot is closed", () => {
    render(<SignupFlow pilot="closed" google invite={null} />);
    expect(screen.getByRole("heading", { name: "Sign-up is not open yet" })).toBeTruthy();
    expect(screen.getByRole("form", { name: "Join the waiting list" })).toBeTruthy();
    expect(screen.queryByLabelText("Password")).toBeNull();
    expect(screen.queryByRole("button", { name: "Continue with Google" })).toBeNull();
  });

  it("draws the pilot-full screen when the pilot is full", () => {
    render(<SignupFlow pilot="full" google={false} invite={null} prefillEmail="latecomer@example.test" />);
    expect(screen.getByRole("heading", { name: "The pilot is full" })).toBeTruthy();
    expect((screen.getByLabelText("Email") as HTMLInputElement).value).toBe("latecomer@example.test");
  });

  it("draws the form, with the invitation notice, for an invited address while the pilot is closed", () => {
    render(<SignupFlow pilot="closed" google invite="tok" />);
    expect(screen.getByRole("heading", { name: "Create your account" })).toBeTruthy();
    expect(screen.getByText(/You have an invitation to the pilot/)).toBeTruthy();
  });

  it("shows Continue with Google only when the server has Google sign-in", () => {
    const { unmount } = render(<SignupFlow pilot="open" google invite={null} />);
    expect(screen.getByRole("button", { name: "Continue with Google" })).toBeTruthy();
    unmount();
    render(<SignupFlow pilot="open" google={false} invite={null} />);
    expect(screen.queryByRole("button", { name: "Continue with Google" })).toBeNull();
  });

  it("falls back to the plain form when the pilot's state could not be read", () => {
    render(<SignupFlow pilot={null} google={false} invite={null} />);
    expect(screen.getByRole("heading", { name: "Create your account" })).toBeTruthy();
  });
});

describe("sign-up form", () => {
  it("checks the fields before calling the API, and shows the terms message under the box", async () => {
    const user = userEvent.setup();
    render(<SignupFlow pilot="open" google={false} invite={null} />);
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(screen.getByText("Enter your name.")).toBeTruthy();
    expect(screen.getByText("Enter your work email.")).toBeTruthy();
    expect(screen.getByText("Use at least 8 characters.")).toBeTruthy();
    expect(screen.getByText("Accept the Terms and the Privacy Policy to create an account.")).toBeTruthy();
    expect(mocks.signup).not.toHaveBeenCalled();
  });

  it("posts the captured body shape, then shows 'check your email' without saying whether the address exists", async () => {
    mocks.signup.mockResolvedValue(accountBody("18-signup-ok.json"));
    const user = userEvent.setup();
    render(<SignupFlow pilot="open" google={false} invite={null} />);
    await fillSignup(user);
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(mocks.signup).toHaveBeenCalledWith({
      name: "Ada Example",
      email: "ada@example.test",
      password: "correct horse battery",
      accept_terms: true,
    });
    expect(await screen.findByRole("heading", { name: "Check your email" })).toBeTruthy();
    expect(screen.getByText("ada@example.test")).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/already|exists|registered/i);
    expect(document.body.textContent).toContain("resend in 1:00");
  });

  it("sends the invitation token as invite_token", async () => {
    mocks.signup.mockResolvedValue({ status: "check_your_email" });
    const user = userEvent.setup();
    render(<SignupFlow pilot="closed" google={false} invite="invite-token-placeholder" />);
    await fillSignup(user);
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(mocks.signup).toHaveBeenCalledWith(expect.objectContaining({ invite_token: "invite-token-placeholder" }));
  });

  it.each([
    ["05-signup-while-closed.json", "Sign-up is not open yet"],
    ["59-signup-pilot-full.json", "The pilot is full"],
  ])("switches to the pilot screen when the API answers %s", async (file, heading) => {
    mocks.signup.mockRejectedValue(failure(file));
    const user = userEvent.setup();
    render(<SignupFlow pilot="open" google={false} invite={null} />);
    await fillSignup(user);
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByRole("heading", { name: heading })).toBeTruthy();
    // The address already typed starts the waiting-list form.
    expect((screen.getByLabelText("Email") as HTMLInputElement).value).toBe("ada@example.test");
  });

  it("shows the captured weak-password message under the password field, and keeps the form", async () => {
    mocks.signup.mockRejectedValue(failure("12-signup-weak-password.json"));
    const user = userEvent.setup();
    render(<SignupFlow pilot="open" google={false} invite={null} />);
    await fillSignup(user);
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByText("That password is too common. Choose one that is harder to guess.")).toBeTruthy();
    expect((screen.getByLabelText("Full name") as HTMLInputElement).value).toBe("Ada Example");
  });

  it("shows the invalid-invitation message and the rate limit as they come", async () => {
    mocks.signup.mockRejectedValueOnce(failure("82-signup-with-invite-wrong-address.json"));
    const user = userEvent.setup();
    render(<SignupFlow pilot="closed" google={false} invite="tok" />);
    await fillSignup(user);
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "That invitation is not valid any more. Join the waiting list to get a new one.",
    );
    mocks.signup.mockRejectedValueOnce(failure("124-signup-rate-limited.json"));
    await user.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("Too many attempts. Try again later."));
  });

  it("asks for the terms before Google, and sends accept_terms=true once they are ticked", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    const user = userEvent.setup();
    render(<SignupFlow pilot="open" google invite={null} />);
    await user.click(screen.getByRole("button", { name: "Continue with Google" }));
    expect(assign).not.toHaveBeenCalled();
    expect(screen.getByText("Accept the Terms and the Privacy Policy to create an account.")).toBeTruthy();
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Continue with Google" }));
    expect(assign).toHaveBeenCalledWith("/api/auth/google/start?return_to=%2F&accept_terms=true");
    vi.unstubAllGlobals();
  });

  it("makes the resend button wait 60 seconds, then asks for a new link", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mocks.signup.mockResolvedValue({ status: "check_your_email" });
    mocks.resendVerification.mockResolvedValue(accountBody("35-resend-unverified.json"));
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<SignupFlow pilot="open" google={false} invite={null} />);
    await fillSignup(user);
    await user.click(screen.getByRole("button", { name: "Create account" }));
    await screen.findByRole("heading", { name: "Check your email" });
    expect(screen.queryByRole("button", { name: "resend the link" })).toBeNull();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    await user.click(screen.getByRole("button", { name: "resend the link" }));
    expect(mocks.resendVerification).toHaveBeenCalledWith("ada@example.test");
    expect((await screen.findByRole("status")).textContent).toMatch(/If that address is waiting to be confirmed/);
    expect(document.body.textContent).toContain("resend in 1:00");
  });

  it("goes back to the form from 'Change it', with the name and email kept and the password cleared", async () => {
    mocks.signup.mockResolvedValue({ status: "check_your_email" });
    const user = userEvent.setup();
    render(<SignupFlow pilot="open" google={false} invite={null} />);
    await fillSignup(user);
    await user.click(screen.getByRole("button", { name: "Create account" }));
    await user.click(await screen.findByRole("button", { name: "Change it" }));
    expect((screen.getByLabelText("Work email") as HTMLInputElement).value).toBe("ada@example.test");
    expect((screen.getByLabelText("Password") as HTMLInputElement).value).toBe("");
  });
});

describe("confirm email", () => {
  it("shows a form and calls nothing when the page opens (a mail scanner's GET must not confirm)", () => {
    render(<VerifyEmailForm token="tok" />);
    expect(screen.getByRole("heading", { name: "Confirm your email" })).toBeTruthy();
    expect(mocks.verifyEmail).not.toHaveBeenCalled();
  });

  it("posts the token and the password, and sends the person to sign in (it does not sign them in)", async () => {
    mocks.verifyEmail.mockResolvedValue(accountBody("24-verify-ok.json"));
    const user = userEvent.setup();
    render(<VerifyEmailForm token="tok" />);
    await user.type(screen.getByLabelText("Your password"), "pw-placeholder");
    await user.click(screen.getByRole("button", { name: "Confirm email" }));
    expect(mocks.verifyEmail).toHaveBeenCalledWith("tok", "pw-placeholder");
    expect(await screen.findByRole("heading", { name: "Email confirmed" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Go to sign in" }).getAttribute("href")).toBe("/login");
    expect(mocks.replace).not.toHaveBeenCalled();
  });

  it("asks for the password before calling the API", async () => {
    const user = userEvent.setup();
    render(<VerifyEmailForm token="tok" />);
    await user.click(screen.getByRole("button", { name: "Confirm email" }));
    expect(screen.getByText("Enter the password you chose.")).toBeTruthy();
    expect(mocks.verifyEmail).not.toHaveBeenCalled();
  });

  it("keeps the form and shows the captured message after a wrong password", async () => {
    mocks.verifyEmail.mockRejectedValue(failure("22-verify-wrong-password.json"));
    const user = userEvent.setup();
    render(<VerifyEmailForm token="tok" />);
    await user.type(screen.getByLabelText("Your password"), "nope");
    await user.click(screen.getByRole("button", { name: "Confirm email" }));
    expect(
      await screen.findByText(
        "That is not the password you chose when you signed up. If you do not remember it, create the account again.",
      ),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Confirm email" })).toBeTruthy();
  });

  it("offers a new link when the link is dead, and resends to the address typed", async () => {
    mocks.verifyEmail.mockRejectedValue(failure("23-verify-unknown-token.json"));
    mocks.resendVerification.mockResolvedValue(accountBody("35-resend-unverified.json"));
    const user = userEvent.setup();
    render(<VerifyEmailForm token="old" />);
    await user.type(screen.getByLabelText("Your password"), "pw-placeholder");
    await user.click(screen.getByRole("button", { name: "Confirm email" }));
    expect(await screen.findByRole("heading", { name: "This link does not work" })).toBeTruthy();
    expect(screen.getByText("This link has expired or was already used. Ask for a new one.")).toBeTruthy();
    await user.type(screen.getByLabelText("Email you signed up with"), "ben@example.test");
    await user.click(screen.getByRole("button", { name: "Send a new link" }));
    expect(mocks.resendVerification).toHaveBeenCalledWith("ben@example.test");
    expect((await screen.findByRole("status")).textContent).toMatch(/same password as before/);
  });

  it("treats a link with no token as a dead link", () => {
    render(<VerifyEmailForm token={null} />);
    expect(screen.getByRole("heading", { name: "This link does not work" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Confirm email" })).toBeNull();
  });

  it("shows the pilot-full screen with the waiting list when the pilot filled up, and says the link stays good", async () => {
    mocks.verifyEmail.mockRejectedValue(failure("58-verify-pilot-filled-up.json"));
    const user = userEvent.setup();
    render(<VerifyEmailForm token="tok" />);
    await user.type(screen.getByLabelText("Your password"), "pw-placeholder");
    await user.click(screen.getByRole("button", { name: "Confirm email" }));
    expect(await screen.findByRole("heading", { name: "The pilot is full" })).toBeTruthy();
    expect(screen.getByText(/confirmation link was not used up/)).toBeTruthy();
    expect(screen.getByRole("form", { name: "Join the waiting list" })).toBeTruthy();
  });
});

describe("forgot password", () => {
  it("checks the address, then shows 'check your email' with the captured 202", async () => {
    mocks.forgotPassword.mockResolvedValue(accountBody("39-forgot-known.json"));
    const user = userEvent.setup();
    render(<ForgotPasswordFlow />);
    await user.type(screen.getByLabelText("Work email"), "nope");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));
    expect(screen.getByText("Enter a valid email, like you@company.com.")).toBeTruthy();
    expect(mocks.forgotPassword).not.toHaveBeenCalled();
    await user.clear(screen.getByLabelText("Work email"));
    await user.type(screen.getByLabelText("Work email"), "ada@example.test");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));
    expect(mocks.forgotPassword).toHaveBeenCalledWith("ada@example.test");
    expect(await screen.findByRole("heading", { name: "Check your email" })).toBeTruthy();
    expect(document.body.textContent).toContain("If an account exists for ada@example.test, a reset link is on its way. It expires in 30 minutes.");
    expect(document.body.textContent).toContain("resend in 1:00");
  });

  it("shows a rate limit message from the server", async () => {
    mocks.forgotPassword.mockRejectedValue(failure("124-signup-rate-limited.json"));
    const user = userEvent.setup();
    render(<ForgotPasswordFlow />);
    await user.type(screen.getByLabelText("Work email"), "ada@example.test");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Too many attempts. Try again later.");
  });
});

describe("reset password", () => {
  it("checks the two fields match, then posts the token and the new password", async () => {
    mocks.resetPassword.mockResolvedValue(accountBody("44-reset-ok.json"));
    const user = userEvent.setup();
    render(<ResetPasswordForm token="tok" />);
    await user.type(screen.getByLabelText("New password"), "a brand new password");
    await user.type(screen.getByLabelText("Confirm password"), "a different one");
    await user.click(screen.getByRole("button", { name: "Update password" }));
    expect(screen.getByText("Passwords do not match.")).toBeTruthy();
    expect(mocks.resetPassword).not.toHaveBeenCalled();
    await user.clear(screen.getByLabelText("Confirm password"));
    await user.type(screen.getByLabelText("Confirm password"), "a brand new password");
    await user.click(screen.getByRole("button", { name: "Update password" }));
    expect(mocks.resetPassword).toHaveBeenCalledWith("tok", "a brand new password");
    expect(await screen.findByRole("heading", { name: "Password updated" })).toBeTruthy();
    expect(screen.getByText(/signed out on every device/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Back to sign in" }).getAttribute("href")).toBe("/login");
  });

  it("keeps the form after a weak password (the link is not used up)", async () => {
    mocks.resetPassword.mockRejectedValue(failure("42-reset-weak-password.json"));
    const user = userEvent.setup();
    render(<ResetPasswordForm token="tok" />);
    await user.type(screen.getByLabelText("New password"), "password123");
    await user.type(screen.getByLabelText("Confirm password"), "password123");
    await user.click(screen.getByRole("button", { name: "Update password" }));
    expect(await screen.findByText("That password is too common. Choose one that is harder to guess.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Update password" })).toBeTruthy();
  });

  it("sends the visitor to ask for a new link when the link is dead", async () => {
    mocks.resetPassword.mockRejectedValue(failure("43-reset-bad-token.json"));
    const user = userEvent.setup();
    render(<ResetPasswordForm token="old" />);
    await user.type(screen.getByLabelText("New password"), "a brand new password");
    await user.type(screen.getByLabelText("Confirm password"), "a brand new password");
    await user.click(screen.getByRole("button", { name: "Update password" }));
    expect(await screen.findByText("This link has expired or was already used. Ask for a new one.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Ask for a new link" }).getAttribute("href")).toBe("/forgot-password");
  });

  it("treats a link with no token as a dead link", () => {
    render(<ResetPasswordForm token={null} />);
    expect(screen.getByRole("link", { name: "Ask for a new link" })).toBeTruthy();
  });
});

describe("waiting list", () => {
  it("is filled in from prefill_email, posts the captured shape and says nothing about the address's status", async () => {
    mocks.joinWaitlist.mockResolvedValue(accountBody("49-waitlist-join.json"));
    const user = userEvent.setup();
    render(<WaitlistForm initialEmail="latecomer@example.test" />);
    expect((screen.getByLabelText("Email") as HTMLInputElement).value).toBe("latecomer@example.test");
    await user.type(screen.getByLabelText("Name (optional)"), "Wendy Example");
    await user.click(screen.getByRole("button", { name: "Join the waiting list" }));
    expect(mocks.joinWaitlist).toHaveBeenCalledWith("latecomer@example.test", "Wendy Example");
    expect((await screen.findByRole("status")).textContent).toContain("You are on the list.");
  });

  it("sends no name when none was typed, and shows the captured bad-email message from the API", async () => {
    mocks.joinWaitlist.mockRejectedValue(failure("51-waitlist-bad-email.json"));
    const user = userEvent.setup();
    render(<WaitlistForm />);
    await user.type(screen.getByLabelText("Email"), "x@y.z");
    await user.click(screen.getByRole("button", { name: "Join the waiting list" }));
    expect(mocks.joinWaitlist).toHaveBeenCalledWith("x@y.z", undefined);
    expect(await screen.findByText("Enter a valid email address.")).toBeTruthy();
  });
});

describe("sign-in additions", () => {
  it("shows Continue with Google only when google_signin is true, as a link that needs no JavaScript", () => {
    const { unmount } = render(<LoginForm google />);
    const link = screen.getByRole("link", { name: "Continue with Google" });
    expect(link.getAttribute("href")).toBe("/api/auth/google/start?return_to=%2F");
    unmount();
    render(<LoginForm />);
    expect(screen.queryByRole("link", { name: "Continue with Google" })).toBeNull();
  });

  it("links Forgot password? beside the password label", () => {
    render(<LoginForm />);
    expect(screen.getByRole("link", { name: "Forgot password?" }).getAttribute("href")).toBe("/forgot-password");
  });

  it("shows the message for ?error=<code> and a link to sign-up when the code needs one", () => {
    render(<LoginForm redirectError="Google sign-in was cancelled." redirectSignup />);
    expect(screen.getByRole("alert").textContent).toContain("Google sign-in was cancelled.");
    expect(screen.getByRole("link", { name: "Go to sign-up" }).getAttribute("href")).toBe("/signup");
  });

  it("adds the 'confirm your email' line to a wrong-password answer, since the API says nothing else", async () => {
    mocks.login.mockRejectedValue(new ApiError(401, "invalid_credentials", "That email or password is not right."));
    const user = userEvent.setup();
    render(<LoginForm />);
    await user.type(screen.getByLabelText("Work email"), "ada@example.test");
    await user.type(screen.getByLabelText("Password"), "nope");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("That email or password is not right.");
    expect(alert.textContent).toContain("confirm your email first");
  });

  it("shows the too-many-attempts answer of nginx's own page as the same message", async () => {
    mocks.login.mockRejectedValue(new ApiError(429, "too_many_requests", "Too many attempts. Try again later."));
    const user = userEvent.setup();
    render(<LoginForm />);
    await user.type(screen.getByLabelText("Work email"), "ada@example.test");
    await user.type(screen.getByLabelText("Password"), "x");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Too many attempts. Try again later.");
  });
});
