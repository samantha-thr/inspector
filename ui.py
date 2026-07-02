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

from config import (
    APP_NAME,
    DEFAULT_SCAN_PATH,
    DUPLICATE_LIMIT,
    FOLDER_LIMIT,
    SEARCH_LIMIT,
    SIMILARITY_LIMIT,
    VERSION,
)
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


def pause() -> None:
    console.input("\nPress Enter to return to menu...")


def main_menu() -> None:
    while True:
        console.clear()
        header()
        show_database_quick_status()
        console.print(f"\n[bold]Default Resources:[/bold] {DEFAULT_SCAN_PATH}\n")
        console.print("[bold]1.[/bold] Scan Client Resources")
        console.print("[bold]2.[/bold] Scan Another Folder")
        console.print("[bold]3.[/bold] Search Database")
        console.print("[bold]4.[/bold] Model Explorer")
        console.print("[bold]5.[/bold] Compare Models")
        console.print("[bold]6.[/bold] Similar Models")
        console.print("[bold]7.[/bold] Duplicate Browser")
        console.print("[bold]8.[/bold] Folder Explorer")
        console.print("[bold]9.[/bold] Compare Folders")
        console.print("[bold]10.[/bold] Statistics")
        console.print("[bold]11.[/bold] Export Summary Report")
        console.print("[bold]12.[/bold] Exit")

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
            model_explorer()
        elif choice == "5":
            compare_models_prompt()
        elif choice == "6":
            similar_models_prompt()
        elif choice == "7":
            duplicate_browser()
        elif choice == "8":
            folder_explorer()
        elif choice == "9":
            compare_folders_prompt()
        elif choice == "10":
            show_statistics()
        elif choice == "11":
            export_summary()
        elif choice == "12":
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
        pause()
        return

    console.print(f"[bold green]Scanning:[/bold green] {root}\n")

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
    pause()


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


def render_model_table(rows, title: str) -> None:
    table = Table(title=title, header_style="bold cyan")
    table.add_column("#", justify="right")
    table.add_column("Relative Path")
    table.add_column("Type")
    table.add_column("SOM")
    table.add_column("Size", justify="right")
    table.add_column("SHA256")

    for i, row in enumerate(rows, 1):
        table.add_row(
            str(i),
            row["relative_path"],
            row["filename_type"],
            row["som_version"] or "-",
            format_bytes(row["size"]),
            row["sha256"][:16] + "...",
        )

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

    render_model_table(rows, f"Search Results: {term}")

    if rows:
        console.print("\nEnter a result number to open it, [bold]e[/bold] to export, or press Enter to return.")
        choice = console.input("Choice: ").strip().lower()

        if choice == "e":
            out = export_search_results(rows)
            console.print(f"[green]Exported:[/green] {out}")
            pause()
        elif choice.isdigit():
            index = int(choice)
            if 1 <= index <= len(rows):
                show_model_detail(rows[index - 1]["path"])
                pause()


def model_explorer() -> None:
    console.clear()
    header()
    query = console.input("Enter filename, relative path, or full path: ").strip().strip('"')
    if not query:
        return

    db = Database()
    row = db.get_model_by_relative_or_filename(query)
    db.close()

    if not row:
        console.print("[bold red]No model found.[/bold red]")
        pause()
        return

    show_model_detail(row["path"])
    pause()


def show_model_detail(path: str) -> None:
    db = Database()
    row = db.get_model_by_path(path)
    duplicate_count = db.duplicate_count_for_hash(row["sha256"]) if row else 0
    duplicate_rows = db.models_by_hash(row["sha256"]) if row and duplicate_count > 1 else []
    candidates = db.model_comparison_candidates(path, 10) if row else []
    db.close()

    console.clear()
    header()

    if not row:
        console.print("[bold red]Model not found.[/bold red]")
        return

    table = Table(title="Model Explorer", header_style="bold cyan")
    table.add_column("Field")
    table.add_column("Value")

    table.add_row("Filename", row["filename"])
    table.add_row("Folder", row["folder"])
    table.add_row("Relative Path", row["relative_path"])
    table.add_row("Type", row["filename_type"])
    table.add_row("Size", format_bytes(row["size"]))
    table.add_row("SOM Version", row["som_version"] or "Unknown")
    table.add_row("Header", row["header"] or "Unknown")
    table.add_row("Printable Strings", f"{row['string_count']:,}")
    table.add_row("Duplicate Copies", f"{duplicate_count:,}")
    table.add_row("SHA256", row["sha256"])
    table.add_row("MD5", row["md5"])
    table.add_row("CRC32", str(row["crc32"]))
    table.add_row("First 256 Hash", row["first_256_sha256"])
    table.add_row("Full Path", row["path"])

    console.print(table)

    if row["first_64_hex"]:
        console.print(Panel(row["first_64_hex"], title="First 64 Bytes", border_style="blue"))

    if duplicate_rows:
        render_model_table(duplicate_rows, "Exact Hash Matches")

    if candidates:
        render_similarity_table(candidates, "Nearest Internal Matches")


def resolve_model(query: str):
    db = Database()
    row = db.get_model_by_relative_or_filename(query)
    db.close()
    return row


def compare_models_prompt() -> None:
    console.clear()
    header()
    a_query = console.input("First model filename/path: ").strip().strip('"')
    b_query = console.input("Second model filename/path: ").strip().strip('"')
    if not a_query or not b_query:
        return

    a = resolve_model(a_query)
    b = resolve_model(b_query)

    if not a or not b:
        console.print("[bold red]One or both models were not found.[/bold red]")
        pause()
        return

    show_model_compare(a["path"], b["path"])
    pause()


def show_model_compare(path_a: str, path_b: str) -> None:
    db = Database()
    result = db.compare_two_models(path_a, path_b)
    db.close()

    a = result["a"]
    b = result["b"]

    console.clear()
    header()
    console.print(f"[bold]Comparison Score:[/bold] {result['score']} / 100\n")
    console.print(f"[cyan]A:[/cyan] {a['relative_path']}")
    console.print(f"[cyan]B:[/cyan] {b['relative_path']}\n")

    table = Table(title="Model Comparison", header_style="bold cyan")
    table.add_column("Field")
    table.add_column("Match", justify="center")
    table.add_column("Model A")
    table.add_column("Model B")

    for field in result["fields"]:
        match = "[green]YES[/green]" if field["same"] else "[red]NO[/red]"
        av = str(field["a"])
        bv = str(field["b"])
        if len(av) > 40:
            av = av[:37] + "..."
        if len(bv) > 40:
            bv = bv[:37] + "..."
        table.add_row(field["label"], match, av, bv)

    console.print(table)


def similar_models_prompt() -> None:
    console.clear()
    header()
    query = console.input("Model filename/path to compare internally: ").strip().strip('"')
    if not query:
        return

    row = resolve_model(query)
    if not row:
        console.print("[bold red]Model not found.[/bold red]")
        pause()
        return

    db = Database()
    candidates = db.model_comparison_candidates(row["path"], SIMILARITY_LIMIT)
    db.close()

    console.clear()
    header()
    console.print(f"[bold]Base Model:[/bold] {row['relative_path']}\n")
    render_similarity_table(candidates, "Internal Similarity Candidates")

    if candidates:
        choice = console.input("\nEnter a result number to compare directly, or press Enter to return: ").strip()
        if choice.isdigit():
            index = int(choice)
            if 1 <= index <= len(candidates):
                show_model_compare(row["path"], candidates[index - 1]["path"])
                pause()
                return

    pause()


def render_similarity_table(rows, title: str) -> None:
    table = Table(title=title, header_style="bold cyan")
    table.add_column("#", justify="right")
    table.add_column("Score", justify="right")
    table.add_column("Relative Path")
    table.add_column("Type")
    table.add_column("Size", justify="right")
    table.add_column("SHA256")

    for i, row in enumerate(rows, 1):
        table.add_row(
            str(i),
            str(row["score"]),
            row["relative_path"],
            row["filename_type"],
            format_bytes(row["size"]),
            row["sha256"][:16] + "...",
        )

    console.print(table)


def duplicate_browser() -> None:
    console.clear()
    header()

    db = Database()
    rows = db.duplicate_hashes(DUPLICATE_LIMIT)
    db.close()

    table = Table(title="Duplicate Hash Groups", header_style="bold cyan")
    table.add_column("#", justify="right")
    table.add_column("Copies", justify="right")
    table.add_column("Total Size", justify="right")
    table.add_column("SHA256")

    for i, row in enumerate(rows, 1):
        table.add_row(str(i), f"{row['count']:,}", format_bytes(row["total_size"]), row["sha256"][:32] + "...")

    console.print(table)

    if rows:
        choice = console.input("\nEnter a group number to view copies, or press Enter to return: ").strip()
        if choice.isdigit():
            index = int(choice)
            if 1 <= index <= len(rows):
                show_duplicate_group(rows[index - 1]["sha256"])
                pause()


def show_duplicate_group(sha256: str) -> None:
    db = Database()
    rows = db.models_by_hash(sha256)
    db.close()

    console.clear()
    header()
    console.print(f"[bold]SHA256:[/bold] {sha256}\n")
    render_model_table(rows, "Duplicate Copies")


def folder_explorer() -> None:
    console.clear()
    header()

    db = Database()
    folders = db.folder_counts(FOLDER_LIMIT)
    db.close()

    table = Table(title="Folders", header_style="bold cyan")
    table.add_column("#", justify="right")
    table.add_column("Folder")
    table.add_column("Models", justify="right")

    for i, row in enumerate(folders, 1):
        table.add_row(str(i), row["folder"], f"{row['count']:,}")

    console.print(table)

    choice = console.input("\nEnter a folder number or folder name, or press Enter to return: ").strip()
    if not choice:
        return

    folder = ""
    if choice.isdigit():
        index = int(choice)
        if 1 <= index <= len(folders):
            folder = folders[index - 1]["folder"]
    else:
        folder = choice

    if folder:
        show_folder_detail(folder)
        pause()


def show_folder_detail(folder: str) -> None:
    db = Database()
    detail = db.folder_details(folder)
    models = db.models_in_folder(folder, 100)
    db.close()

    console.clear()
    header()

    if not detail:
        console.print("[bold red]Folder not found.[/bold red]")
        return

    table = Table(title=f"Folder Explorer: {folder}", header_style="bold cyan")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Models", f"{detail['count']:,}")
    table.add_row("Unique Hashes", f"{detail['unique_hashes']:,}")
    table.add_row("Duplicate Rate", f"{100.0 * (1 - detail['unique_hashes'] / max(detail['count'], 1)):.1f}%")
    table.add_row("Total Size", format_bytes(detail["total_size"]))
    table.add_row("Average Size", format_bytes(detail["avg_size"]))
    table.add_row("Smallest", format_bytes(detail["min_size"]))
    table.add_row("Largest", format_bytes(detail["max_size"]))
    table.add_row("Numeric Product IDs", f"{detail['numeric_count']:,}")
    table.add_row("Named Assets", f"{detail['named_count']:,}")
    console.print(table)

    render_model_table(models, f"First {len(models)} Models in {folder}")


def compare_folders_prompt() -> None:
    console.clear()
    header()
    folder_a = console.input("First folder code: ").strip()
    folder_b = console.input("Second folder code: ").strip()
    if not folder_a or not folder_b:
        return

    db = Database()
    result = db.folder_comparison(folder_a, folder_b)
    db.close()

    console.clear()
    header()

    if not result["a"] or not result["b"]:
        console.print("[bold red]One or both folders were not found.[/bold red]")
        pause()
        return

    table = Table(title=f"Folder Compare: {folder_a} vs {folder_b}", header_style="bold cyan")
    table.add_column("Metric")
    table.add_column(folder_a, justify="right")
    table.add_column(folder_b, justify="right")

    a = result["a"]
    b = result["b"]
    table.add_row("Models", f"{a['count']:,}", f"{b['count']:,}")
    table.add_row("Unique Hashes", f"{a['unique_hashes']:,}", f"{b['unique_hashes']:,}")
    table.add_row("Duplicate Rate", f"{100.0 * (1 - a['unique_hashes'] / max(a['count'], 1)):.1f}%", f"{100.0 * (1 - b['unique_hashes'] / max(b['count'], 1)):.1f}%")
    table.add_row("Average Size", format_bytes(a["avg_size"]), format_bytes(b["avg_size"]))
    table.add_row("Largest", format_bytes(a["max_size"]), format_bytes(b["max_size"]))
    table.add_row("Numeric IDs", f"{a['numeric_count']:,}", f"{b['numeric_count']:,}")
    table.add_row("Named Assets", f"{a['named_count']:,}", f"{b['named_count']:,}")
    table.add_row("Shared Exact Hashes", f"{result['shared_hashes']:,}", f"{result['shared_hashes']:,}")

    console.print(table)
    pause()


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
    pause()


def export_summary() -> None:
    console.clear()
    header()
    out = export_database_summary()
    console.print(f"[green]Exported summary:[/green] {out}")
    pause()
