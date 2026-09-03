import sys
from pathlib import Path

# Add project root to sys.path
root_dir = Path(__file__).resolve().parents[2]
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

import uuid
import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from packages.database.session import session_scope
from apps.obx_core.services.wallet_service import WalletService
from apps.obx_core.services.reconciliation import ReconciliationService
from packages.shared.exceptions import OBXError

app = typer.Typer(help="OBX Economy Core - Admin & Developer CLI")
console = Console(width=200)


@app.command("create-user")
def create_user(
    discord_user_id: str = typer.Argument(..., help="Discord User Snowflake ID"),
):
    """Create a new test user and initialize their wallet."""
    try:
        with session_scope() as session:
            service = WalletService(session)
            user, wallet, created = service.get_or_create_user(discord_user_id)
            if created:
                console.print(f"[green]✓ Successfully created user and wallet for Discord ID: [bold]{discord_user_id}[/bold] (User ID: {user.id})[/green]")
            else:
                console.print(f"[yellow]! User already exists: [bold]{discord_user_id}[/bold] (User ID: {user.id})[/yellow]")
    except Exception as exc:
        console.print(f"[red]✗ Error creating user: {exc}[/red]")
        raise typer.Exit(code=1)


@app.command("credit")
def credit(
    discord_user_id: str = typer.Argument(..., help="Discord User Snowflake ID"),
    amount: int = typer.Argument(..., min=1, help="Amount of OBX tokens to credit"),
    ref_type: str = typer.Option("admin_credit", "--ref-type", "-r", help="Reference type"),
    idempotency_key: str = typer.Option(None, "--idempotency-key", "-k", help="Custom idempotency key"),
    desc: str = typer.Option("Admin CLI credit", "--desc", "-d", help="Transaction description"),
):
    """Credit OBX tokens to a user's wallet."""
    key = idempotency_key or f"cli-credit-{discord_user_id}-{uuid.uuid4()}"
    try:
        with session_scope() as session:
            service = WalletService(session)
            entry = service.credit(
                discord_user_id=discord_user_id,
                amount=amount,
                reference_type=ref_type,
                idempotency_key=key,
                description=desc,
            )
            console.print(
                f"[green]✓ Credited [bold]{amount} OBX[/bold] to [bold]{discord_user_id}[/bold] "
                f"(Tx ID: {entry.id}, Key: {key})[/green]"
            )
    except OBXError as exc:
        console.print(f"[red]✗ OBX Error: {exc.message} ({exc.code})[/red]")
        raise typer.Exit(code=1)
    except Exception as exc:
        console.print(f"[red]✗ Error: {exc}[/red]")
        raise typer.Exit(code=1)


@app.command("debit")
def debit(
    discord_user_id: str = typer.Argument(..., help="Discord User Snowflake ID"),
    amount: int = typer.Argument(..., min=1, help="Amount of OBX tokens to debit"),
    ref_type: str = typer.Option("admin_debit", "--ref-type", "-r", help="Reference type"),
    idempotency_key: str = typer.Option(None, "--idempotency-key", "-k", help="Custom idempotency key"),
    desc: str = typer.Option("Admin CLI debit", "--desc", "-d", help="Transaction description"),
):
    """Debit OBX tokens from a user's wallet."""
    key = idempotency_key or f"cli-debit-{discord_user_id}-{uuid.uuid4()}"
    try:
        with session_scope() as session:
            service = WalletService(session)
            entry = service.debit(
                discord_user_id=discord_user_id,
                amount=amount,
                reference_type=ref_type,
                idempotency_key=key,
                description=desc,
            )
            console.print(
                f"[green]✓ Debited [bold]{amount} OBX[/bold] from [bold]{discord_user_id}[/bold] "
                f"(Tx ID: {entry.id})[/green]"
            )
    except OBXError as exc:
        console.print(f"[red]✗ OBX Error: {exc.message} ({exc.code})[/red]")
        raise typer.Exit(code=1)
    except Exception as exc:
        console.print(f"[red]✗ Error: {exc}[/red]")
        raise typer.Exit(code=1)


@app.command("lock")
def lock_funds(
    discord_user_id: str = typer.Argument(..., help="Discord User Snowflake ID"),
    amount: int = typer.Argument(..., min=1, help="Amount of OBX tokens to lock"),
    ref_type: str = typer.Option("admin_lock", "--ref-type", "-r", help="Reference type"),
    idempotency_key: str = typer.Option(None, "--idempotency-key", "-k", help="Custom idempotency key"),
    desc: str = typer.Option("Admin CLI lock funds", "--desc", "-d", help="Transaction description"),
):
    """Lock OBX tokens (move from available to locked balance)."""
    key = idempotency_key or f"cli-lock-{discord_user_id}-{uuid.uuid4()}"
    try:
        with session_scope() as session:
            service = WalletService(session)
            entry = service.lock_funds(
                discord_user_id=discord_user_id,
                amount=amount,
                reference_type=ref_type,
                idempotency_key=key,
                description=desc,
            )
            console.print(
                f"[green]✓ Locked [bold]{amount} OBX[/bold] for [bold]{discord_user_id}[/bold] "
                f"(Tx ID: {entry.id})[/green]"
            )
    except OBXError as exc:
        console.print(f"[red]✗ OBX Error: {exc.message} ({exc.code})[/red]")
        raise typer.Exit(code=1)
    except Exception as exc:
        console.print(f"[red]✗ Error: {exc}[/red]")
        raise typer.Exit(code=1)


@app.command("release")
def release_funds(
    discord_user_id: str = typer.Argument(..., help="Discord User Snowflake ID"),
    amount: int = typer.Argument(..., min=1, help="Amount of OBX tokens to release"),
    ref_type: str = typer.Option("admin_release", "--ref-type", "-r", help="Reference type"),
    idempotency_key: str = typer.Option(None, "--idempotency-key", "-k", help="Custom idempotency key"),
    desc: str = typer.Option("Admin CLI release funds", "--desc", "-d", help="Transaction description"),
):
    """Release locked OBX tokens (move from locked to available balance)."""
    key = idempotency_key or f"cli-release-{discord_user_id}-{uuid.uuid4()}"
    try:
        with session_scope() as session:
            service = WalletService(session)
            entry = service.release_funds(
                discord_user_id=discord_user_id,
                amount=amount,
                reference_type=ref_type,
                idempotency_key=key,
                description=desc,
            )
            console.print(
                f"[green]✓ Released [bold]{amount} OBX[/bold] for [bold]{discord_user_id}[/bold] "
                f"(Tx ID: {entry.id})[/green]"
            )
    except OBXError as exc:
        console.print(f"[red]✗ OBX Error: {exc.message} ({exc.code})[/red]")
        raise typer.Exit(code=1)
    except Exception as exc:
        console.print(f"[red]✗ Error: {exc}[/red]")
        raise typer.Exit(code=1)


@app.command("balance")
def balance(
    discord_user_id: str = typer.Argument(..., help="Discord User Snowflake ID"),
):
    """Check the wallet balance of a user."""
    try:
        with session_scope() as session:
            service = WalletService(session)
            bal = service.get_balance(discord_user_id)

            table = Table(title=f"Wallet Balance: {discord_user_id}", border_style="cyan")
            table.add_column("Property", style="bold")
            table.add_column("Value", style="green")

            table.add_row("Discord User ID", bal["discord_user_id"])
            table.add_row("Available Balance", f"{bal['available_balance']:,} OBX")
            table.add_row("Locked Balance", f"{bal['locked_balance']:,} OBX")
            table.add_row("Total Balance", f"{bal['total_balance']:,} OBX")
            table.add_row("Last Updated", str(bal["updated_at"]))

            console.print(table)
    except OBXError as exc:
        console.print(f"[red]✗ OBX Error: {exc.message} ({exc.code})[/red]")
        raise typer.Exit(code=1)
    except Exception as exc:
        console.print(f"[red]✗ Error: {exc}[/red]")
        raise typer.Exit(code=1)


@app.command("transactions")
def transactions(
    discord_user_id: str = typer.Argument(..., help="Discord User Snowflake ID"),
    limit: int = typer.Option(10, "--limit", "-l", help="Number of transactions to display"),
    offset: int = typer.Option(0, "--offset", "-o", help="Offset for pagination"),
):
    """View recent ledger entries for a user."""
    try:
        with session_scope() as session:
            service = WalletService(session)
            entries, total = service.get_transactions(discord_user_id, limit=limit, offset=offset)

            table = Table(title=f"Ledger Entries for {discord_user_id} (Total: {total})", border_style="magenta")
            table.add_column("Date (UTC)", style="dim")
            table.add_column("Type", style="bold")
            table.add_column("Amount", justify="right")
            table.add_column("Ref Type")
            table.add_column("Description")
            table.add_column("Idempotency Key", style="dim")

            for e in entries:
                type_val = e.transaction_type.value if hasattr(e.transaction_type, "value") else str(e.transaction_type)
                type_color = "green" if type_val in ["CREDIT", "REFUND", "RELEASE"] else "red"
                table.add_row(
                    str(e.created_at.strftime("%Y-%m-%d %H:%M:%S")),
                    f"[{type_color}]{type_val}[/{type_color}]",
                    f"{e.amount:,} OBX",
                    e.reference_type,
                    e.description or "-",
                    e.idempotency_key[:16] + "...",
                )

            console.print(table)
    except OBXError as exc:
        console.print(f"[red]✗ OBX Error: {exc.message} ({exc.code})[/red]")
        raise typer.Exit(code=1)
    except Exception as exc:
        console.print(f"[red]✗ Error: {exc}[/red]")
        raise typer.Exit(code=1)


@app.command("reconcile")
def reconcile(
    discord_user_id: str = typer.Option(None, "--user", "-u", help="Optional specific user to reconcile"),
):
    """Run a wallet reconciliation check against the immutable ledger."""
    try:
        with session_scope() as session:
            recon = ReconciliationService(session)
            if discord_user_id:
                disc = recon.reconcile_user(discord_user_id)
                if not disc:
                    console.print(Panel(f"[green]✓ User {discord_user_id} wallet matches ledger perfectly.[/green]", title="Reconciliation Success"))
                else:
                    console.print(Panel(
                        f"[red]✗ Discrepancy for user {discord_user_id}:[/red]\n"
                        f"Actual Available: {disc.actual_available} | Expected: {disc.expected_available} (Diff: {disc.available_diff})\n"
                        f"Actual Locked: {disc.actual_locked} | Expected: {disc.expected_locked} (Diff: {disc.locked_diff})\n"
                        f"Total Ledger Entries: {disc.ledger_entry_count}",
                        title="Reconciliation Inconsistency Detected",
                    ))
                    raise typer.Exit(code=1)
            else:
                report = recon.reconcile_all()
                if report.is_consistent:
                    console.print(Panel(f"[green]✓ System-wide reconciliation PASSED. All {report.total_users_checked} user wallets are 100% consistent.[/green]", title="Reconciliation Complete"))
                else:
                    console.print(Panel(
                        f"[red]✗ System-wide reconciliation FAILED: {report.mismatched_users_count} discrepancies out of {report.total_users_checked} users.[/red]",
                        title="Reconciliation Error",
                    ))
                    table = Table(title="Discrepancies Detail", border_style="red")
                    table.add_column("Discord ID")
                    table.add_column("Actual / Expected Avail")
                    table.add_column("Actual / Expected Lock")
                    table.add_column("Avail Diff")
                    table.add_column("Lock Diff")

                    for d in report.discrepancies:
                        table.add_row(
                            d.discord_user_id,
                            f"{d.actual_available} / {d.expected_available}",
                            f"{d.actual_locked} / {d.expected_locked}",
                            str(d.available_diff),
                            str(d.locked_diff),
                        )
                    console.print(table)
                    raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as exc:
        console.print(f"[red]✗ Error during reconciliation: {exc}[/red]")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
