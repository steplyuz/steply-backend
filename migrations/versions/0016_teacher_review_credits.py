"""Add separately purchasable teacher-review credits.

Revision ID: 0016_teacher_review_credits
Revises: 0015_plan_entitlements_discounts
"""
from alembic import op
import sqlalchemy as sa

revision = "0016_teacher_review_credits"
down_revision = "0015_plan_entitlements_discounts"
branch_labels = None
depends_on = None

def upgrade() -> None:
    bind = op.get_bind()
    offline = op.get_context().as_sql

    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER TYPE billingproducttype "
            "ADD VALUE IF NOT EXISTS 'TEACHER_REVIEW_CREDITS'"
        )

    # Offline SQL generation: database introspection is unavailable.
    # Emit the complete DDL and let Alembic execute it in order.
    if offline:
        op.create_table(
            "teacher_review_credit_grants",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "user_id",
                sa.Uuid(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "order_id",
                sa.String(length=36),
                sa.ForeignKey("billing_orders.id", ondelete="CASCADE"),
                nullable=False,
                unique=True,
            ),
            sa.Column("credits_total", sa.Integer(), nullable=False),
            sa.Column("credits_remaining", sa.Integer(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.CheckConstraint(
                "credits_total > 0",
                name="ck_teacher_review_credits_total_positive",
            ),
            sa.CheckConstraint(
                "credits_remaining >= 0 AND credits_remaining <= credits_total",
                name="ck_teacher_review_credits_remaining_range",
            ),
        )
        op.create_index(
            "ix_teacher_review_credit_grants_user_id",
            "teacher_review_credit_grants",
            ["user_id"],
        )
        op.create_index(
            "ix_teacher_review_credits_user_remaining",
            "teacher_review_credit_grants",
            ["user_id", "credits_remaining"],
        )
        return

    # Online mode: retain idempotent checks against the live database.
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "teacher_review_credit_grants" not in tables:
        op.create_table(
            "teacher_review_credit_grants",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "user_id",
                sa.Uuid(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "order_id",
                sa.String(length=36),
                sa.ForeignKey("billing_orders.id", ondelete="CASCADE"),
                nullable=False,
                unique=True,
            ),
            sa.Column("credits_total", sa.Integer(), nullable=False),
            sa.Column("credits_remaining", sa.Integer(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.CheckConstraint(
                "credits_total > 0",
                name="ck_teacher_review_credits_total_positive",
            ),
            sa.CheckConstraint(
                "credits_remaining >= 0 AND credits_remaining <= credits_total",
                name="ck_teacher_review_credits_remaining_range",
            ),
        )

    # Refresh inspector after possible table creation.
    indexes = {
        i["name"]
        for i in sa.inspect(bind).get_indexes("teacher_review_credit_grants")
    }

    if "ix_teacher_review_credit_grants_user_id" not in indexes:
        op.create_index(
            "ix_teacher_review_credit_grants_user_id",
            "teacher_review_credit_grants",
            ["user_id"],
        )

    if "ix_teacher_review_credits_user_remaining" not in indexes:
        op.create_index(
            "ix_teacher_review_credits_user_remaining",
            "teacher_review_credit_grants",
            ["user_id", "credits_remaining"],
        )


def downgrade() -> None:
    bind = op.get_bind()

    if op.get_context().as_sql:
        op.drop_table("teacher_review_credit_grants")
        # PostgreSQL enum values are intentionally not removed.
        return

    if "teacher_review_credit_grants" in sa.inspect(bind).get_table_names():
        op.drop_table("teacher_review_credit_grants")

    # PostgreSQL enum values are intentionally not removed; this is not portable/safe.