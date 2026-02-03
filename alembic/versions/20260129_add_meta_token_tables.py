"""Add Meta token management tables

Revision ID: 20260129_meta_tokens
Revises: 20260129_ig_ads
Create Date: 2026-01-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20260129_meta_tokens'
down_revision: Union[str, None] = '20260129_ig_ads'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create clients table
    op.create_table(
        'clients',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('client_id', sa.String(36), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_clients_client_id', 'clients', ['client_id'], unique=True)

    # Create meta_users table
    op.create_table(
        'meta_users',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('client_id', sa.String(36), nullable=False),
        sa.Column('meta_user_id', sa.String(50), nullable=False),
        sa.Column('name', sa.String(255), nullable=True),
        sa.Column('email', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(['client_id'], ['clients.client_id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_meta_users_meta_user_id', 'meta_users', ['meta_user_id'])
    op.create_index('ix_meta_users_client_user', 'meta_users', ['client_id', 'meta_user_id'], unique=True)

    # Create meta_pages table
    op.create_table(
        'meta_pages',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('client_id', sa.String(36), nullable=False),
        sa.Column('page_id', sa.String(50), nullable=False),
        sa.Column('connected_meta_user_id', sa.String(50), nullable=False),
        sa.Column('name', sa.String(255), nullable=True),
        sa.Column('category', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(['client_id'], ['clients.client_id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_meta_pages_page_id', 'meta_pages', ['page_id'])
    op.create_index('ix_meta_pages_client_page', 'meta_pages', ['client_id', 'page_id'], unique=True)

    # Create meta_tokens table
    op.create_table(
        'meta_tokens',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('client_id', sa.String(36), nullable=False),
        sa.Column('owner_type', sa.String(20), nullable=False),
        sa.Column('owner_id', sa.String(50), nullable=False),
        sa.Column('access_token_ciphertext', sa.Text(), nullable=False),
        sa.Column('token_fingerprint', sa.String(64), nullable=False),
        sa.Column('scopes', sa.JSON(), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.String(20), server_default='active', nullable=True),
        sa.Column('last_validated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(['client_id'], ['clients.client_id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_meta_tokens_client_owner', 'meta_tokens', ['client_id', 'owner_type', 'owner_id'])
    op.create_index('ix_meta_tokens_active', 'meta_tokens', ['client_id', 'owner_type', 'owner_id', 'status'])

    # Create instagram_accounts table
    op.create_table(
        'instagram_accounts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('client_id', sa.String(36), nullable=False),
        sa.Column('ig_user_id', sa.String(50), nullable=False),
        sa.Column('username', sa.String(100), nullable=True),
        sa.Column('page_id', sa.String(50), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(['client_id'], ['clients.client_id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_instagram_accounts_ig_user_id', 'instagram_accounts', ['ig_user_id'])
    op.create_index('ix_ig_accounts_client_user', 'instagram_accounts', ['client_id', 'ig_user_id'], unique=True)


def downgrade() -> None:
    op.drop_table('instagram_accounts')
    op.drop_table('meta_tokens')
    op.drop_table('meta_pages')
    op.drop_table('meta_users')
    op.drop_table('clients')
