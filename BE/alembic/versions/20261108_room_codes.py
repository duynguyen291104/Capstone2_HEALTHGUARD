"""Add optional household room codes.

Revision ID: 20261108_room_codes
Revises: e6f162c4c514
"""

from alembic import op
import sqlalchemy as sa

revision = "20261108_room_codes"
down_revision = "e6f162c4c514"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("care_groups", sa.Column("join_code_hash", sa.String(64)))
    op.add_column("care_groups", sa.Column("join_password_hash", sa.String(255)))
    op.add_column("care_groups", sa.Column("join_expires_at", sa.DateTime(timezone=True)))
    op.add_column("care_groups", sa.Column("join_failed_attempts", sa.Integer()))
    op.create_index("ix_care_groups_join_code_hash", "care_groups", ["join_code_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_care_groups_join_code_hash", table_name="care_groups")
    for column in ("join_failed_attempts", "join_expires_at", "join_password_hash", "join_code_hash"):
        op.drop_column("care_groups", column)
