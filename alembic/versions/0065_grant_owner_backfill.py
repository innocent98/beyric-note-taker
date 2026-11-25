"""Paid hour grants belong to the account that paid for them (billing review S2)

Hours a person paid for were found by `user_id` OR by the fingerprint of the email address the
grant was made under, so after an address change, or a deletion followed by someone taking the
same address, a stranger could spend them. The application now spends a plan period, a pack or an
admin gift by `user_id` alone (`entitlements.my_grants`). Every such grant is made with its
`user_id`, so this migration only repairs the exceptions: a `plan_period` or `pack` grant whose
`user_id` is empty although the payment (or the plan) it came from names a live account.

It binds a grant only when the answer is clear, and never overwrites one:
- the owner is the payer of the grant's source payment, or the owner of its subscription; when both
  exist and differ the grant is left alone;
- the owner is a live account (not `deleted`, not purged): a grant whose account was deleted stays
  without an owner on purpose (its hours go with the account);
- a trial, a pilot and an admin gift are left alone (a trial and a pilot of a deleted account are
  found again by the fingerprint, as designed; a gift has no payer to read).

The upgrade is a plain data repair, safe to run again. The downgrade does nothing: the values it
set are correct, and nothing can tell them from the ones that were always there.

Revision ID: 0065
Revises: 0064
Create Date: 2026-11-25
"""

from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        update hour_grants g
        set user_id = o.owner_id
        from (
            select g2.id as grant_id, coalesce(p.payer_user_id, s.user_id) as owner_id
            from hour_grants g2
            left join payments p on p.id = g2.source_payment_id
            left join subscriptions s on s.id = g2.subscription_id
            where g2.user_id is null
              and g2.kind in ('plan_period', 'pack')
              and (p.payer_user_id is null or s.user_id is null or p.payer_user_id = s.user_id)
        ) o
        join users u on u.id = o.owner_id
        where g.id = o.grant_id
          and g.user_id is null
          and u.status <> 'deleted'
          and u.purged_at is null
        """
    )


def downgrade() -> None:
    pass  # a data repair of correct values: nothing to undo (see the module note)
