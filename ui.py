from __future__ import annotations

import time
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

from config import APP_NAME, DEFAULT_SCAN_PATH, SEARCH_LIMIT, VERSION
from database import Database
from reports import export_database_summary, export_search_results
from scanner import scan_folder

console = Console()


def format_bytes(value: int | float) -> str:
    value = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def format_seconds(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


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
        show_database_quick_status()
        console.print(f"\n[bold]Default Resources:[/bold] {DEFAULT_SCAN_PATH}\n")
        console.print("[bold]1.[/bold] Scan Client Resources")
        console.print("[bold]2.[/bold] Scan Another Folder")
        console.print("[bold]3.[/bold] Search Database")
        console.print("[bold]4.[/bold] Statistics")
        console.print("[bold]5.[/bold] Export Summary Report")
        console.print("[bold]6.[/bold] Exit")

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
            export_summary()
        elif choice == "6":
            return


def show_database_quick_status() -> None:
    db = Database()
    total = db.count_models()
    latest = db.latest_scan()
    db.close()

    if latest:
        last = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(latest["finished"]))
        console.print(f"[bold]Database:[/bold] {total:,} models indexed | Last scan: {last}")
    else:
        console.print(f"[bold]Database:[/bold] {total:,} models indexed | No scan history yet")


def run_scan(path: str) -> None:
    root = Path(path)
    console.clear()
    header()

    if not root.exists():
        console.print(f"[bold red]Path not found:[/bold red] {root}")
        console.input("\nPress Enter to return to menu...")
        return

    console.print(f"[bold green]Scanning:[/bold green] {root}\n")

    live_stats = {
        "scanned": 0,
        "skipped": 0,
        "errors": 0,
        "speed": 0.0,
        "status": "Starting",
    }

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}[/bold]"),
        BarColumn(bar_width=None),
        TextColumn("{task.completed:,}/{task.total:,}"),
        TextColumn("[cyan]{task.percentage:>5.1f}%[/cyan]"),
        TextColumn("[green]H:{task.fields[hashed]}[/green]"),
        TextColumn("[yellow]S:{task.fields[skipped]}[/yellow]"),
        TextColumn("[red]E:{task.fields[errors]}[/red]"),
        TextColumn("[magenta]{task.fields[speed]:>7} models/s[/magenta]"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    ) as progress:
        task = progress.add_task(
            "Discovering models...",
            total=1,
            hashed="0",
            skipped="0",
            errors="0",
            speed="0.0",
        )

        def update(info: dict) -> None:
            if progress.tasks[0].total != info["total"]:
                progress.update(task, total=info["total"])

            description = info["relative_path"]
            if len(description) > 58:
                description = "..." + description[-55:]

            live_stats.update({
                "scanned": info["scanned"],
                "skipped": info["skipped"],
                "errors": info["errors"],
                "speed": info["speed"],
                "status": info["status"],
            })

            progress.update(
                task,
                completed=info["index"],
                description=description,
                hashed=f"{info['scanned']:,}",
                skipped=f"{info['skipped']:,}",
                errors=f"{info['errors']:,}",
                speed=f"{info['speed']:.1f}",
            )

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
    table.add_row("Elapsed", format_seconds(summary["elapsed"]))
    table.add_row("Average Speed", f"{summary['found'] / max(summary['elapsed'], 0.001):,.1f} models/s")
    table.add_row("Models in Database", f"{summary['database_models']:,}")
    table.add_row("Duplicate Hash Groups", f"{summary['duplicate_hash_groups']:,}")

    for key, value in summary.get("filename_types", {}).items():
        table.add_row(key, f"{value:,}")

    for key, value in summary.get("som_versions", {}).items():
        table.add_row(f"SOM {key}", f"{value:,}")

    console.print()
    console.print(table)


def search_database() -> None:
    console.clear()
    header()
    term = console.input("Search filename, folder, path, or hash: ").strip()

    if not term:
        return

    db = Database()
    rows = db.search(term, SEARCH_LIMIT)
    db.close()

    table = Table(title=f"Search Results: {term}", header_style="bold cyan")
    table.add_column("Folder")
    table.add_column("Filename")
    table.add_column("Type")
    table.add_column("SOM")
    table.add_column("Size", justify="right")
    table.add_column("SHA256")

    for row in rows:
        table.add_row(
            row["folder"],
            row["filename"],
            row["filename_type"],
            row["som_version"] or "-",
            format_bytes(row["size"]),
            row["sha256"][:16] + "...",
        )

    console.print(table)

    if rows:
        save = console.input("\nExport these results to CSV? (y/N): ").strip().lower()
        if save == "y":
            out = export_search_results(rows)
            console.print(f"[green]Exported:[/green] {out}")

    console.input("\nPress Enter to return to menu...")


def show_statistics() -> None:
    console.clear()
    header()

    db = Database()
    total = db.count_models()
    duplicate_groups = db.duplicate_hash_count()
    type_counts = db.filename_type_counts()
    som_counts = db.som_version_counts()
    folders = db.folder_counts(25)
    size_stats = db.size_stats()
    latest = db.latest_scan()
    db.close()

    table = Table(title="Database Statistics", header_style="bold cyan")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Models Indexed", f"{total:,}")
    table.add_row("Duplicate Hash Groups", f"{duplicate_groups:,}")
    table.add_row("Total Model Data", format_bytes(size_stats["total_size"]))
    table.add_row("Average Model Size", format_bytes(size_stats["avg_size"]))
    table.add_row("Smallest Model", format_bytes(size_stats["min_size"]))
    table.add_row("Largest Model", format_bytes(size_stats["max_size"]))

    if latest:
        table.add_row("Last Scan Found", f"{latest['found']:,}")
        table.add_row("Last Scan Hashed", f"{latest['scanned']:,}")
        table.add_row("Last Scan Skipped", f"{latest['skipped']:,}")
        table.add_row("Last Scan Errors", f"{latest['errors']:,}")
        table.add_row("Last Scan Elapsed", format_seconds(latest["elapsed"]))

    for key, value in type_counts.items():
        table.add_row(key, f"{value:,}")

    for key, value in som_counts.items():
        table.add_row(f"SOM {key}", f"{value:,}")

    console.print(table)

    folder_table = Table(title="Top Folders", header_style="bold cyan")
    folder_table.add_column("Folder")
    folder_table.add_column("Models", justify="right")

    for row in folders:
        folder_table.add_row(row["folder"], f"{row['count']:,}")

    console.print(folder_table)

    largest = size_stats["largest"]
    smallest = size_stats["smallest"]

    size_table = Table(title="Size Extremes", header_style="bold cyan")
    size_table.add_column("Type")
    size_table.add_column("Folder")
    size_table.add_column("Filename")
    size_table.add_column("Size", justify="right")

    if largest:
        size_table.add_row("Largest", largest["folder"], largest["filename"], format_bytes(largest["size"]))
    if smallest:
        size_table.add_row("Smallest", smallest["folder"], smallest["filename"], format_bytes(smallest["size"]))

    console.print(size_table)
    console.input("\nPress Enter to return to menu...")


def export_summary() -> None:
    console.clear()
    header()
    out = export_database_summary()
    console.print(f"[green]Exported summary:[/green] {out}")
    console.input("\nPress Enter to return to menu...")
