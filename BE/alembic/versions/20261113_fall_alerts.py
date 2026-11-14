"""Store scoped FallGuard alerts and delivery outcomes."""

import sqlalchemy as sa
from alembic import op

revision = "20261113_fall_alerts"
down_revision = "20261108_room_codes"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "fall_alerts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("elder_id", sa.Uuid(), sa.ForeignKey("elder_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("telegram_status", sa.String(24), nullable=False),
        sa.Column("sent", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_fall_alerts_elder_id", "fall_alerts", ["elder_id"])


def downgrade():
    op.drop_index("ix_fall_alerts_elder_id", table_name="fall_alerts")
    op.drop_table("fall_alerts")
