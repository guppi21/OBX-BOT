import os
import json
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

import discord
from sqlalchemy.orm import Session
from packages.database.session import session_scope

logger = logging.getLogger(__name__)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _enum(val: Any) -> Optional[str]:
    if val is None:
        return None
    return val.value if hasattr(val, "value") else str(val)


class BackupService:
    """Service to create complete database snapshots, manage disk rotation,
    and post automated daily backups to Discord admin channels.
    """

    @staticmethod
    def create_database_snapshot(session: Session) -> Dict[str, Any]:
        """Extract a complete, consistent snapshot of all OBX ecosystem state."""
        from packages.database.models.user import User
        from packages.database.models.wallet import Wallet
        from packages.database.models.raider_profile import RaiderProfile
        from packages.database.models.task import Task
        from packages.database.models.submission import TaskSubmission
        from packages.database.models.auction import Auction, AuctionBid
        from packages.database.models.ledger import LedgerEntry
        from packages.database.models.channel_config import GuildConfig

        # 1. Wallets & Users
        wallets = session.query(Wallet).join(User).all()
        wallet_data = [
            {
                "discord_user_id": str(w.user.discord_user_id),
                "available_balance": int(w.available_balance or 0),
                "locked_balance": int(w.locked_balance or 0),
                "total_balance": int(w.total_balance or 0),
                "updated_at": _iso(w.updated_at),
            }
            for w in wallets
            if w.user
        ]

        users = session.query(User).all()
        user_data = [
            {
                "id": str(u.id),
                "discord_user_id": str(u.discord_user_id),
                "created_at": _iso(u.created_at),
            }
            for u in users
        ]

        # 2. Raider Profiles
        profiles = session.query(RaiderProfile).all()
        profile_data = [
            {
                "discord_user_id": str(p.discord_user_id),
                "twitter_handle": p.twitter_handle,
                "twitter_profile_url": p.twitter_profile_url,
                "twitter_avatar_url": p.twitter_avatar_url,
                "created_at": _iso(p.created_at),
                "updated_at": _iso(p.updated_at),
            }
            for p in profiles
        ]

        # 3. Tasks & Submissions
        tasks = session.query(Task).all()
        task_data = [
            {
                "id": str(t.id),
                "title": t.title,
                "description": t.description,
                "task_type": t.task_type,
                "target_url": t.target_url,
                "reward_per_user": t.reward_per_user,
                "total_reward_pool": t.total_reward_pool,
                "distributed_reward": t.distributed_reward,
                "max_approvals": t.max_approvals,
                "approved_count": t.approved_count,
                "status": _enum(t.status),
                "ends_at": _iso(t.ends_at),
                "created_by": t.created_by,
                "created_at": _iso(t.created_at),
                "updated_at": _iso(t.updated_at),
            }
            for t in tasks
        ]

        submissions = session.query(TaskSubmission).all()
        submission_data = [
            {
                "id": str(s.id),
                "task_id": str(s.task_id),
                "discord_user_id": str(s.discord_user_id),
                "x_username": s.x_username,
                "proof_url": s.proof_url,
                "proof_text": s.proof_text,
                "proof_screenshot_url": s.proof_screenshot_url,
                "status": _enum(s.status),
                "submitted_at": _iso(s.submitted_at),
                "reviewed_by": s.reviewed_by,
                "reviewed_at": _iso(s.reviewed_at),
                "rejection_reason": s.rejection_reason,
                "reward_amount": s.reward_amount,
                "proof_media_deleted": getattr(s, "proof_media_deleted", False),
            }
            for s in submissions
        ]

        # 4. Auctions & Bids
        auctions = session.query(Auction).all()
        auction_data = [
            {
                "id": str(a.id),
                "title": a.title,
                "reward_title": a.reward_title,
                "description": a.description,
                "auction_type": _enum(a.auction_type),
                "status": _enum(a.status),
                "price_or_min_bid": a.price_or_min_bid,
                "total_slots": a.total_slots,
                "allocated_slots": a.allocated_slots,
                "ends_at": _iso(a.ends_at),
                "project_x_url": a.project_x_url,
                "preview_image_url": getattr(a, "preview_image_url", None),
                "image_url": getattr(a, "image_url", None),
                "created_at": _iso(a.created_at),
            }
            for a in auctions
        ]

        bids = session.query(AuctionBid).all()
        bid_data = [
            {
                "id": str(b.id),
                "auction_id": str(b.auction_id),
                "discord_user_id": str(b.discord_user_id),
                "bid_amount": b.bid_amount,
                "is_winner": b.is_winner,
                "is_settled": b.is_settled,
                "placed_at": _iso(b.placed_at),
                "updated_at": _iso(b.updated_at),
            }
            for b in bids
        ]

        # 5. Ledger Entries (Recent 500 records for audit trail)
        ledger_entries = session.query(LedgerEntry).order_by(LedgerEntry.created_at.desc()).limit(500).all()
        ledger_data = [
            {
                "id": str(l.id),
                "user_id": str(l.user_id),
                "amount": l.amount,
                "transaction_type": _enum(l.transaction_type),
                "reference_type": l.reference_type,
                "reference_id": l.reference_id,
                "description": l.description,
                "created_at": _iso(l.created_at),
            }
            for l in ledger_entries
        ]

        # 6. Guild Configs
        configs = session.query(GuildConfig).all()
        config_data = [
            {
                "guild_id": str(c.guild_id),
                "tasks_channel_id": c.tasks_channel_id,
                "leaderboard_channel_id": c.leaderboard_channel_id,
                "auctions_channel_id": c.auctions_channel_id,
                "winners_channel_id": c.winners_channel_id,
                "admin_channel_id": c.admin_channel_id,
                "economy_channel_id": c.economy_channel_id,
                "task_alerts_role_id": c.task_alerts_role_id,
                "updated_at": _iso(c.updated_at),
            }
            for c in configs
        ]

        now = datetime.now(timezone.utc)
        total_circulation = sum(w["total_balance"] for w in wallet_data)

        return {
            "version": "2.0",
            "generated_at": now.isoformat(),
            "total_users": len(user_data),
            "total_wallets": len(wallet_data),
            "total_obx_in_circulation": total_circulation,
            "total_tasks": len(task_data),
            "total_submissions": len(submission_data),
            "total_auctions": len(auction_data),
            "total_bids": len(bid_data),
            "wallets": wallet_data,
            "users": user_data,
            "raider_profiles": profile_data,
            "tasks": task_data,
            "submissions": submission_data,
            "auctions": auction_data,
            "auction_bids": bid_data,
            "ledger_entries": ledger_data,
            "guild_configs": config_data,
        }

    @classmethod
    def save_snapshot_to_disk(
        cls,
        payload: Dict[str, Any],
        backup_dir: str | Path = "backups",
        retention_hours: int = 48,
    ) -> Tuple[str, int]:
        """Atomically saves snapshot to disk and rotates older backups beyond retention_hours.
        Returns (saved_filepath, number_of_pruned_files).
        """
        dir_path = Path(backup_dir)
        dir_path.mkdir(parents=True, exist_ok=True)

        now_utc = datetime.now(timezone.utc)
        timestamp_str = now_utc.strftime("%Y-%m-%d_%H%M%S")
        target_path = dir_path / f"obx_snapshot_{timestamp_str}.json"
        tmp_path = dir_path / f".obx_snapshot_{timestamp_str}.tmp"

        # Atomic write
        json_data = json.dumps(payload, indent=2)
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(json_data)
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp_path, target_path)
        logger.info("Saved OBX snapshot to %s (%d bytes)", target_path, len(json_data))

        # Rotate old backups
        pruned_count = cls.rotate_old_backups(dir_path, retention_hours=retention_hours)
        return str(target_path), pruned_count

    @staticmethod
    def rotate_old_backups(backup_dir: str | Path, retention_hours: int = 48) -> int:
        """Scan backup_dir for obx_snapshot_*.json files and delete ones older than
        retention_hours, ensuring at least one backup is always retained.
        """
        dir_path = Path(backup_dir)
        if not dir_path.exists():
            return 0

        existing_snapshots = sorted(
            [f for f in dir_path.glob("obx_snapshot_*.json") if f.is_file()],
            key=lambda p: p.stat().st_mtime,
        )

        # Safety: If only 1 backup exists, never delete it regardless of age
        if len(existing_snapshots) <= 1:
            return 0

        cutoff_timestamp = time.time() - (retention_hours * 3600)
        pruned = 0

        # Preserve the most recent backup always (existing_snapshots[-1])
        for old_file in existing_snapshots[:-1]:
            try:
                if old_file.stat().st_mtime < cutoff_timestamp:
                    old_file.unlink()
                    pruned += 1
                    logger.info("Pruned old snapshot: %s", old_file.name)
            except Exception as exc:
                logger.warning("Could not prune snapshot %s: %s", old_file.name, exc)

        return pruned

    @classmethod
    async def post_snapshot_to_admin_channel(
        cls,
        guild: discord.Guild,
        filepath: str,
        payload: Dict[str, Any],
        bot: discord.Client,
    ) -> bool:
        """Upload the snapshot JSON file to the configured admin channel or #admin-logs."""
        try:
            from apps.obx_tasks.services.channel_service import ChannelService

            target_channel = None
            with session_scope() as session:
                ch_service = ChannelService(session)
                config = ch_service.get_or_create_guild_config(str(guild.id))
                if config and config.admin_channel_id:
                    ch = guild.get_channel(int(config.admin_channel_id))
                    if isinstance(ch, discord.TextChannel):
                        target_channel = ch

            # Fallback to searching channel by name
            if not target_channel:
                for name in ("admin-logs", "admin-log", "admin-backups", "admin"):
                    ch = discord.utils.get(guild.text_channels, name=name)
                    if ch:
                        target_channel = ch
                        break

            if not target_channel:
                logger.warning("No admin channel found in guild %s for backup upload.", guild.name)
                return False

            me = guild.me or guild.get_member(bot.user.id)
            perms = target_channel.permissions_for(me)
            if not perms.send_messages or not perms.attach_files:
                logger.warning("Bot missing send/attach permissions in %s for backup.", target_channel.name)
                return False

            filename = os.path.basename(filepath)
            file = discord.File(filepath, filename=filename)
            embed = discord.Embed(
                title="🛡️ Daily OBX System Snapshot",
                description=(
                    "**Automated Daily Backup Complete!**\n\n"
                    f"• 👥 **Total Members:** `{payload['total_users']}`\n"
                    f"• 💎 **Circulating OBX:** `{payload['total_obx_in_circulation']:,} OBX`\n"
                    f"• 📋 **Tasks / Submissions:** `{payload['total_tasks']}` tasks • `{payload['total_submissions']}` proofs\n"
                    f"• 🎯 **Auctions / Bids:** `{payload['total_auctions']}` auctions • `{payload['total_bids']}` bids\n"
                    f"• 🗄️ **Disk Snapshot:** `{filename}`\n"
                    "• 🧹 **Rotation Policy:** 48-hour rolling retention active.\n\n"
                    "This file is permanently stored on Discord CDN. Restore anytime using `/admin-restore-backup`."
                ),
                color=discord.Color.blue(),
                timestamp=datetime.now(timezone.utc),
            )
            embed.set_footer(text="OBX Autonomous Backup Engine")
            await target_channel.send(embed=embed, file=file)
            logger.info("Successfully posted daily snapshot %s to %s", filename, target_channel.name)
            return True
        except Exception as exc:
            logger.error("Error posting snapshot to admin channel in guild %s: %s", guild.id, exc)
            return False
