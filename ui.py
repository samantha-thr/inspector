from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from config import APP_NAME, DEFAULT_SCAN_PATH, VERSION
from database import Database
from scanner import scan_folder

console = Console()


def header() -> None:
    console.print(
        Panel.fit(
            f"[bold cyan]{APP_NAME} v{VERSION}[/bold cyan]\n"
            "There.com Model Analysis Suite",
            border_style="cyan",
        )
    )


def main_menu() -> None:
    while True:
        console.clear()
        header()
        console.print(f"[bold]Default Resources:[/bold] {DEFAULT_SCAN_PATH}\n")
        console.print("[bold]1.[/bold] Scan Client Resources")
        console.print("[bold]2.[/bold] Scan Another Folder")
        console.print("[bold]3.[/bold] Search Database")
        console.print("[bold]4.[/bold] Statistics")
        console.print("[bold]5.[/bold] Exit")

        choice = console.input("\nChoice: ").strip()

        if choice == "1":
            run_scan(DEFAULT_SCAN_PATH)
        elif choice == "2":
            path = console.input("Folder to scan: ").strip().strip('"')
            if path:
                run_scan(path)
        elif choice == "3":
            search_database()
        elif choice == "4":
            show_statistics()
        elif choice == "5":
            return


def run_scan(path: str) -> None:
    root = Path(path)
    console.clear()
    header()

    if not root.exists():
        console.print(f"[bold red]Path not found:[/bold red] {root}")
        console.input("\nPress Enter to return to menu...")
        return

    console.print(f"[bold green]Scanning:[/bold green] {root}\n")

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}[/bold]"),
        BarColumn(),
        TextColumn("{task.completed:,}/{task.total:,}"),
        TextColumn("[cyan]{task.percentage:>5.1f}%[/cyan]"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    ) as progress:
        task = progress.add_task("Discovering models...", total=1)

        def update(info: dict) -> None:
            if progress.tasks[0].total != info["total"]:
                progress.update(task, total=info["total"])

            description = f"{info['folder']} / {info['file'][:45]}"
            progress.update(task, completed=info["index"], description=description)

        summary = scan_folder(root, update)

    show_scan_summary(summary)
    console.input("\nPress Enter to return to menu...")


def show_scan_summary(summary: dict) -> None:
    table = Table(title="Scan Complete", show_header=True, header_style="bold cyan")
    table.add_column("Metric")
    table.add_column("Value", justify="right")

    table.add_row("Root", summary["root"])
    table.add_row("Models Found", f"{summary['found']:,}")
    table.add_row("Hashed / Updated", f"{summary['scanned']:,}")
    table.add_row("Skipped Unchanged", f"{summary['skipped']:,}")
    table.add_row("Errors", f"{summary['errors']:,}")
    table.add_row("Elapsed", f"{summary['elapsed']:.2f}s")
    table.add_row("Models in Database", f"{summary['database_models']:,}")
    table.add_row("Duplicate Hash Groups", f"{summary['duplicate_hash_groups']:,}")

    for key, value in summary.get("filename_types", {}).items():
        table.add_row(key, f"{value:,}")

    console.print()
    console.print(table)


def search_database() -> None:
    console.clear()
    header()
    term = console.input("Search filename, folder, path, or hash: ").strip()

    if not term:
        return

    db = Database()
    rows = db.search(term)
    db.close()

    table = Table(title=f"Search Results: {term}", header_style="bold cyan")
    table.add_column("Folder")
    table.add_column("Filename")
    table.add_column("Type")
    table.add_column("Size", justify="right")
    table.add_column("SHA256")

    for row in rows:
        table.add_row(
            row["folder"],
            row["filename"],
            row["filename_type"],
            f"{row['size']:,}",
            row["sha256"][:16] + "...",
        )

    console.print(table)
    console.input("\nPress Enter to return to menu...")


def show_statistics() -> None:
    console.clear()
    header()

    db = Database()
    total = db.count_models()
    duplicate_groups = db.duplicate_hash_count()
    type_counts = db.filename_type_counts()
    folders = db.folder_counts(25)
    db.close()

    table = Table(title="Database Statistics", header_style="bold cyan")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Models Indexed", f"{total:,}")
    table.add_row("Duplicate Hash Groups", f"{duplicate_groups:,}")

    for key, value in type_counts.items():
        table.add_row(key, f"{value:,}")

    console.print(table)

    folder_table = Table(title="Top Folders", header_style="bold cyan")
    folder_table.add_column("Folder")
    folder_table.add_column("Models", justify="right")

    for row in folders:
        folder_table.add_row(row["folder"], f"{row['count']:,}")

    console.print(folder_table)
    console.input("\nPress Enter to return to menu...")
