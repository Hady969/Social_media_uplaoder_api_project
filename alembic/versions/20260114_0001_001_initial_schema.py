"""Initial schema

Revision ID: 001
Revises:
Create Date: 2026-01-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Define enums once
campaign_status_enum = postgresql.ENUM(
    'DRAFT', 'ACTIVE', 'PAUSED', 'COMPLETED', 'DELETED',
    name='campaignstatusenum',
    create_type=False
)

campaign_objective_enum = postgresql.ENUM(
    'AWARENESS', 'REACH', 'ENGAGEMENT', 'VIDEO_VIEWS',
    'WEBSITE_TRAFFIC', 'WEBSITE_CONVERSIONS', 'APP_INSTALLS',
    'APP_ENGAGEMENTS', 'FOLLOWERS',
    name='campaignobjectiveenum',
    create_type=False
)

ad_status_enum = postgresql.ENUM(
    'DRAFT', 'PENDING_REVIEW', 'APPROVED', 'REJECTED',
    'ACTIVE', 'PAUSED', 'DELETED',
    name='adstatusenum',
    create_type=False
)


def upgrade() -> None:
    # Create enum types first
    op.execute("CREATE TYPE campaignstatusenum AS ENUM ('DRAFT', 'ACTIVE', 'PAUSED', 'COMPLETED', 'DELETED')")
    op.execute("CREATE TYPE campaignobjectiveenum AS ENUM ('AWARENESS', 'REACH', 'ENGAGEMENT', 'VIDEO_VIEWS', 'WEBSITE_TRAFFIC', 'WEBSITE_CONVERSIONS', 'APP_INSTALLS', 'APP_ENGAGEMENTS', 'FOLLOWERS')")
    op.execute("CREATE TYPE adstatusenum AS ENUM ('DRAFT', 'PENDING_REVIEW', 'APPROVED', 'REJECTED', 'ACTIVE', 'PAUSED', 'DELETED')")

    # Create users table
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('x_user_id', sa.String(50), nullable=False),
        sa.Column('username', sa.String(100), nullable=False),
        sa.Column('display_name', sa.String(200), nullable=True),
        sa.Column('profile_image_url', sa.Text(), nullable=True),
        sa.Column('bio', sa.Text(), nullable=True),
        sa.Column('location', sa.String(200), nullable=True),
        sa.Column('website_url', sa.Text(), nullable=True),
        sa.Column('followers_count', sa.Integer(), server_default='0'),
        sa.Column('following_count', sa.Integer(), server_default='0'),
        sa.Column('tweet_count', sa.Integer(), server_default='0'),
        sa.Column('listed_count', sa.Integer(), server_default='0'),
        sa.Column('verified', sa.Boolean(), server_default='false'),
        sa.Column('protected', sa.Boolean(), server_default='false'),
        sa.Column('x_created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('last_login', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_users_x_user_id', 'users', ['x_user_id'], unique=True)
    op.create_index('ix_users_username', 'users', ['username'], unique=True)

    # Create user_sessions table
    op.create_table(
        'user_sessions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('session_id', sa.String(100), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('access_token', sa.Text(), nullable=False),
        sa.Column('refresh_token', sa.Text(), nullable=True),
        sa.Column('token_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ip_address', sa.String(50), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_activity', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_user_sessions_session_id', 'user_sessions', ['session_id'], unique=True)
    op.create_index('ix_user_sessions_user_active', 'user_sessions', ['user_id', 'is_active'])

    # Create oauth_states table
    op.create_table(
        'oauth_states',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('state', sa.String(100), nullable=False),
        sa.Column('code_verifier', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used', sa.Boolean(), server_default='false'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_oauth_states_state', 'oauth_states', ['state'], unique=True)

    # Create campaigns table
    op.create_table(
        'campaigns',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('x_campaign_id', sa.String(50), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('objective', campaign_objective_enum, nullable=False),
        sa.Column('status', campaign_status_enum, server_default='DRAFT'),
        sa.Column('daily_budget_micros', sa.BigInteger(), nullable=False),
        sa.Column('total_budget_micros', sa.BigInteger(), nullable=True),
        sa.Column('currency', sa.String(3), server_default='USD'),
        sa.Column('start_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column('end_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column('timezone', sa.String(50), server_default='UTC'),
        sa.Column('targeting', sa.JSON(), nullable=True),
        sa.Column('impressions', sa.BigInteger(), server_default='0'),
        sa.Column('clicks', sa.BigInteger(), server_default='0'),
        sa.Column('engagements', sa.BigInteger(), server_default='0'),
        sa.Column('spend_micros', sa.BigInteger(), server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_campaigns_x_campaign_id', 'campaigns', ['x_campaign_id'], unique=True)
    op.create_index('ix_campaigns_user_status', 'campaigns', ['user_id', 'status'])

    # Create ad_groups table
    op.create_table(
        'ad_groups',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('x_ad_group_id', sa.String(50), nullable=True),
        sa.Column('campaign_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('status', ad_status_enum, server_default='DRAFT'),
        sa.Column('bid_type', sa.String(20), server_default='AUTO'),
        sa.Column('bid_amount_micros', sa.BigInteger(), nullable=True),
        sa.Column('targeting', sa.JSON(), nullable=True),
        sa.Column('impressions', sa.BigInteger(), server_default='0'),
        sa.Column('clicks', sa.BigInteger(), server_default='0'),
        sa.Column('engagements', sa.BigInteger(), server_default='0'),
        sa.Column('spend_micros', sa.BigInteger(), server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_ad_groups_x_ad_group_id', 'ad_groups', ['x_ad_group_id'], unique=True)

    # Create ads table
    op.create_table(
        'ads',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('x_ad_id', sa.String(50), nullable=True),
        sa.Column('ad_group_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('status', ad_status_enum, server_default='DRAFT'),
        sa.Column('review_status', sa.String(50), nullable=True),
        sa.Column('rejection_reason', sa.Text(), nullable=True),
        sa.Column('creative', sa.JSON(), nullable=False),
        sa.Column('impressions', sa.BigInteger(), server_default='0'),
        sa.Column('clicks', sa.BigInteger(), server_default='0'),
        sa.Column('engagements', sa.BigInteger(), server_default='0'),
        sa.Column('spend_micros', sa.BigInteger(), server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['ad_group_id'], ['ad_groups.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_ads_x_ad_id', 'ads', ['x_ad_id'], unique=True)

    # Create analytics_cache table
    op.create_table(
        'analytics_cache',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('metric_type', sa.String(50), nullable=False),
        sa.Column('metric_date', sa.DateTime(timezone=True), nullable=False),
        sa.Column('impressions', sa.BigInteger(), server_default='0'),
        sa.Column('engagements', sa.BigInteger(), server_default='0'),
        sa.Column('likes', sa.BigInteger(), server_default='0'),
        sa.Column('retweets', sa.BigInteger(), server_default='0'),
        sa.Column('replies', sa.BigInteger(), server_default='0'),
        sa.Column('profile_visits', sa.BigInteger(), server_default='0'),
        sa.Column('followers_gained', sa.Integer(), server_default='0'),
        sa.Column('followers_lost', sa.Integer(), server_default='0'),
        sa.Column('raw_data', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_analytics_user_date', 'analytics_cache', ['user_id', 'metric_date'])
    op.create_index('ix_analytics_user_type', 'analytics_cache', ['user_id', 'metric_type'])


def downgrade() -> None:
    op.drop_table('analytics_cache')
    op.drop_table('ads')
    op.drop_table('ad_groups')
    op.drop_table('campaigns')
    op.drop_table('oauth_states')
    op.drop_table('user_sessions')
    op.drop_table('users')

    # Drop enums
    op.execute('DROP TYPE IF EXISTS adstatusenum')
    op.execute('DROP TYPE IF EXISTS campaignobjectiveenum')
    op.execute('DROP TYPE IF EXISTS campaignstatusenum')
