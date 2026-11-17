# Paystack documented samples (not captures)

Every JSON file here is a **documented sample**: the response or event body Paystack prints on its own
documentation pages (read 2026-11-17), trimmed of fields nothing reads (`log`, `fees_split`, ...). None
is a capture from a real or test-mode Paystack call; there are no keys yet. The values (ids, emails,
references, the `AUTH_...` code) are Paystack's own sample values, not real data.

| File | Page it comes from |
|---|---|
| `charge_success_event.json` | https://paystack.com/docs/payments/webhooks/ ("Transaction Successful" sample) |
| `refund_processed_event.json` | the same page ("Refund Processed" sample) |
| `dispute_create_event.json` | the same page ("Dispute Created" sample) |
| `initialize_response.json` | https://paystack.com/docs/api/transaction/ ("Initialize Transaction") |
| `verify_response.json` | the same page ("Verify Transaction") |
| `charge_authorization_response.json` | the same page ("Charge Authorization") |
| `charge_authorization_paused_response.json` | https://paystack.com/docs/payments/recurring-charges/ ("Two factor authentication") |
| `refund_response.json` | https://paystack.com/docs/api/refund/ ("Create Refund") |
| `error_validation_response.json` | https://paystack.com/docs/api/errors/ ("Sample Validation Error") |

`signature_vectors.json` is different: the secrets are invented test values (they are not keys), the
timestamp is the one in Paystack's sample header (`t=1760270400`), and each expected signature was
computed with `openssl dgst -sha512 -hmac` over `charge_success_event.json`, independently of the
adapter's code. Paystack's page prints no complete vector (its sample header is elided with `...`),
so these are computed from the algorithm it documents, not copied from it.
