"""Add Instagram Ads tables

Revision ID: 20260129_ig_ads
Revises: 604cd7392dc3
Create Date: 2026-01-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20260129_ig_ads'
down_revision: Union[str, None] = '604cd7392dc3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create instagram_ad_campaigns table
    op.create_table(
        'instagram_ad_campaigns',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('meta_campaign_id', sa.String(50), nullable=False),
        sa.Column('ad_account_id', sa.String(50), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('objective', sa.String(50), nullable=False),
        sa.Column('status', sa.String(20), server_default='PAUSED', nullable=True),
        sa.Column('daily_budget_cents', sa.Integer(), server_default='0', nullable=True),
        sa.Column('lifetime_budget_cents', sa.Integer(), nullable=True),
        sa.Column('currency', sa.String(3), server_default='USD', nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_ig_campaigns_user', 'instagram_ad_campaigns', ['user_id'])
    op.create_index('ix_ig_campaigns_account', 'instagram_ad_campaigns', ['ad_account_id'])
    op.create_index('ix_instagram_ad_campaigns_meta_campaign_id', 'instagram_ad_campaigns', ['meta_campaign_id'], unique=True)

    # Create instagram_ad_sets table
    op.create_table(
        'instagram_ad_sets',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('campaign_id', sa.Integer(), nullable=False),
        sa.Column('meta_adset_id', sa.String(50), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('status', sa.String(20), server_default='PAUSED', nullable=True),
        sa.Column('optimization_goal', sa.String(50), server_default='IMPRESSIONS', nullable=True),
        sa.Column('billing_event', sa.String(50), server_default='IMPRESSIONS', nullable=True),
        sa.Column('daily_budget_cents', sa.Integer(), server_default='500', nullable=True),
        sa.Column('targeting', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(['campaign_id'], ['instagram_ad_campaigns.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_instagram_ad_sets_meta_adset_id', 'instagram_ad_sets', ['meta_adset_id'], unique=True)

    # Create instagram_ads table
    op.create_table(
        'instagram_ads',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('adset_id', sa.Integer(), nullable=False),
        sa.Column('meta_ad_id', sa.String(50), nullable=False),
        sa.Column('meta_creative_id', sa.String(50), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('status', sa.String(20), server_default='PAUSED', nullable=True),
        sa.Column('ad_type', sa.String(20), server_default='IMAGE', nullable=True),
        sa.Column('creative_data', sa.JSON(), nullable=True),
        sa.Column('impressions', sa.BigInteger(), server_default='0', nullable=True),
        sa.Column('clicks', sa.BigInteger(), server_default='0', nullable=True),
        sa.Column('spend_cents', sa.Integer(), server_default='0', nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(['adset_id'], ['instagram_ad_sets.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_instagram_ads_meta_ad_id', 'instagram_ads', ['meta_ad_id'], unique=True)

    # Create instagram_posts table
    op.create_table(
        'instagram_posts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('ig_media_id', sa.String(50), nullable=False),
        sa.Column('ig_creation_id', sa.String(50), nullable=True),
        sa.Column('media_type', sa.String(20), nullable=False),
        sa.Column('caption', sa.Text(), nullable=True),
        sa.Column('permalink', sa.Text(), nullable=True),
        sa.Column('media_url', sa.Text(), nullable=True),
        sa.Column('thumbnail_url', sa.Text(), nullable=True),
        sa.Column('like_count', sa.Integer(), server_default='0', nullable=True),
        sa.Column('comments_count', sa.Integer(), server_default='0', nullable=True),
        sa.Column('shares_count', sa.Integer(), server_default='0', nullable=True),
        sa.Column('reach', sa.Integer(), server_default='0', nullable=True),
        sa.Column('impressions', sa.Integer(), server_default='0', nullable=True),
        sa.Column('status', sa.String(20), server_default='PUBLISHED', nullable=True),
        sa.Column('posted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_ig_posts_user', 'instagram_posts', ['user_id'])
    op.create_index('ix_ig_posts_type', 'instagram_posts', ['media_type'])
    op.create_index('ix_instagram_posts_ig_media_id', 'instagram_posts', ['ig_media_id'], unique=True)


def downgrade() -> None:
    op.drop_table('instagram_posts')
    op.drop_table('instagram_ads')
    op.drop_table('instagram_ad_sets')
    op.drop_table('instagram_ad_campaigns')
