from __future__ import annotations

import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.table import Table

from config import APP_NAME, DEFAULT_SCAN_PATH, DUPLICATE_LIMIT, FOLDER_LIMIT, SEARCH_LIMIT, SIMILARITY_LIMIT, TEXTURE_LIMIT, VERSION
from database import Database
from reports import export_database_summary, export_search_results
from scanner import scan_folder, scan_textures

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
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def header() -> None:
    console.print(Panel.fit(f"[bold cyan]{APP_NAME} v{VERSION}[/bold cyan]\nThere.com Model Analysis Suite", border_style="cyan"))


def pause() -> None:
    console.input("\nPress Enter to return to menu...")


def main_menu() -> None:
    while True:
        console.clear()
        header()
        show_database_quick_status()
        console.print(f"\n[bold]Default Resources:[/bold] {DEFAULT_SCAN_PATH}\n")
        console.print("[bold]1.[/bold] Scan Manager")
        console.print("[bold]2.[/bold] Search Models")
        console.print("[bold]3.[/bold] Model Explorer")
        console.print("[bold]4.[/bold] Compare Models")
        console.print("[bold]5.[/bold] Similar Models")
        console.print("[bold]6.[/bold] Duplicate Browser")
        console.print("[bold]7.[/bold] Folder Explorer")
        console.print("[bold]8.[/bold] Compare Folders")
        console.print("[bold]9.[/bold] Texture Browser")
        console.print("[bold]10.[/bold] Statistics")
        console.print("[bold]11.[/bold] Export Summary Report")
        console.print("[bold]12.[/bold] Exit")

        choice = console.input("\nChoice: ").strip()
        if choice == "1": scan_manager()
        elif choice == "2": search_database()
        elif choice == "3": model_explorer()
        elif choice == "4": compare_models_prompt()
        elif choice == "5": similar_models_prompt()
        elif choice == "6": duplicate_browser()
        elif choice == "7": folder_explorer()
        elif choice == "8": compare_folders_prompt()
        elif choice == "9": texture_browser()
        elif choice == "10": show_statistics()
        elif choice == "11": export_summary()
        elif choice == "12": return


def show_database_quick_status() -> None:
    db = Database()
    total = db.count_models()
    textures = db.count_textures()
    latest_model = db.latest_scan("model_incremental") or db.latest_scan("model_full")
    latest_texture = db.latest_scan("texture_incremental") or db.latest_scan("texture_full")
    db.close()

    model_text = "never" if not latest_model else time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(latest_model["finished"]))
    texture_text = "never" if not latest_texture else time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(latest_texture["finished"]))
    console.print(f"[bold]Database:[/bold] {total:,} models | {textures:,} textures")
    console.print(f"[bold]Last Model Scan:[/bold] {model_text} | [bold]Last Texture Scan:[/bold] {texture_text}")


def scan_manager() -> None:
    while True:
        console.clear()
        header()
        console.print("[bold cyan]Scan Manager[/bold cyan]\n")
        console.print("[bold]1.[/bold] Incremental Model Scan")
        console.print("[bold]2.[/bold] Full Model Rescan")
        console.print("[bold]3.[/bold] Incremental Texture Scan")
        console.print("[bold]4.[/bold] Full Texture Rescan")
        console.print("[bold]5.[/bold] Scan Another Folder - Models")
        console.print("[bold]6.[/bold] Scan Another Folder - Textures")
        console.print("[bold]7.[/bold] Back")
        choice = console.input("\nChoice: ").strip()

        if choice == "1":
            run_model_scan(DEFAULT_SCAN_PATH, full_rescan=False)
        elif choice == "2":
            run_model_scan(DEFAULT_SCAN_PATH, full_rescan=True)
        elif choice == "3":
            run_texture_scan(DEFAULT_SCAN_PATH, full_rescan=False)
        elif choice == "4":
            run_texture_scan(DEFAULT_SCAN_PATH, full_rescan=True)
        elif choice == "5":
            path = console.input("Folder to scan for models: ").strip().strip('"')
            if path:
                full = console.input("Full rescan? (y/N): ").strip().lower() == "y"
                run_model_scan(path, full_rescan=full)
        elif choice == "6":
            path = console.input("Folder to scan for textures: ").strip().strip('"')
            if path:
                full = console.input("Full rescan? (y/N): ").strip().lower() == "y"
                run_texture_scan(path, full_rescan=full)
        elif choice == "7":
            return


def run_progress_scan(title: str, root: Path, scan_func, full_rescan: bool) -> dict:
    console.clear()
    header()
    if not root.exists():
        console.print(f"[bold red]Path not found:[/bold red] {root}")
        pause()
        return {}

    console.print(f"[bold green]{title}:[/bold green] {root}\n")
    with Progress(
        SpinnerColumn(), TextColumn("[bold]{task.description}[/bold]"), BarColumn(bar_width=None),
        TextColumn("{task.completed:,}/{task.total:,}"), TextColumn("[cyan]{task.percentage:>5.1f}%[/cyan]"),
        TextColumn("[green]H:{task.fields[hashed]}[/green]"), TextColumn("[yellow]S:{task.fields[skipped]}[/yellow]"),
        TextColumn("[red]E:{task.fields[errors]}[/red]"), TextColumn("[magenta]{task.fields[speed]:>7} files/s[/magenta]"),
        TimeElapsedColumn(), TimeRemainingColumn(), console=console, transient=False,
    ) as progress:
        task = progress.add_task("Discovering files...", total=1, hashed="0", skipped="0", errors="0", speed="0.0")

        def update(info: dict) -> None:
            if progress.tasks[0].total != info["total"]:
                progress.update(task, total=info["total"])
            description = info["relative_path"]
            if len(description) > 58:
                description = "..." + description[-55:]
            progress.update(
                task, completed=info["index"], description=description,
                hashed=f"{info['scanned']:,}", skipped=f"{info['skipped']:,}",
                errors=f"{info['errors']:,}", speed=f"{info['speed']:.1f}",
            )

        return scan_func(root, update, full_rescan=full_rescan)


def run_model_scan(path: str, full_rescan: bool = False) -> None:
    summary = run_progress_scan("Full Model Rescan" if full_rescan else "Incremental Model Scan", Path(path), scan_folder, full_rescan)
    if summary:
        show_scan_summary(summary, "Models")
    pause()


def run_texture_scan(path: str, full_rescan: bool = False) -> None:
    summary = run_progress_scan("Full Texture Rescan" if full_rescan else "Incremental Texture Scan", Path(path), scan_textures, full_rescan)
    if summary:
        show_texture_scan_summary(summary)
    pause()


def show_scan_summary(summary: dict, label: str = "Models") -> None:
    table = Table(title=f"{summary.get('scan_mode', 'Scan')} Complete", show_header=True, header_style="bold cyan")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Root", summary["root"])
    table.add_row(f"{label} Found", f"{summary['found']:,}")
    table.add_row("Hashed / Updated", f"{summary['scanned']:,}")
    table.add_row("Skipped Unchanged", f"{summary['skipped']:,}")
    table.add_row("Errors", f"{summary['errors']:,}")
    table.add_row("Elapsed", format_seconds(summary["elapsed"]))
    table.add_row("Average Speed", f"{summary['found'] / max(summary['elapsed'], 0.001):,.1f} files/s")
    table.add_row("Models in Database", f"{summary['database_models']:,}")
    table.add_row("Duplicate Hash Groups", f"{summary['duplicate_hash_groups']:,}")
    for key, value in summary.get("filename_types", {}).items():
        table.add_row(key, f"{value:,}")
    for key, value in summary.get("som_versions", {}).items():
        table.add_row(f"SOM {key}", f"{value:,}")
    console.print()
    console.print(table)


def show_texture_scan_summary(summary: dict) -> None:
    stats = summary.get("texture_stats", {})
    table = Table(title=f"{summary.get('scan_mode', 'Texture Scan')} Complete", show_header=True, header_style="bold cyan")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Root", summary["root"])
    table.add_row("Textures Found", f"{summary['found']:,}")
    table.add_row("Analyzed / Updated", f"{summary['scanned']:,}")
    table.add_row("Skipped Unchanged", f"{summary['skipped']:,}")
    table.add_row("Errors", f"{summary['errors']:,}")
    table.add_row("Elapsed", format_seconds(summary["elapsed"]))
    table.add_row("Average Speed", f"{summary['found'] / max(summary['elapsed'], 0.001):,.1f} files/s")
    table.add_row("Textures in Database", f"{summary['database_textures']:,}")
    table.add_row("Unique Texture Hashes", f"{stats.get('unique_hashes', 0):,}")
    table.add_row("Total Texture Data", format_bytes(stats.get("total_size", 0)))
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
        table.add_row(str(i), row["relative_path"], row["filename_type"], row["som_version"] or "-", format_bytes(row["size"]), row["sha256"][:16] + "...")
    console.print(table)


def render_texture_table(rows, title: str) -> None:
    table = Table(title=title, header_style="bold cyan")
    table.add_column("#", justify="right")
    table.add_column("Relative Path")
    table.add_column("Dimensions")
    table.add_column("Alpha")
    table.add_column("Avg RGB")
    table.add_column("Size", justify="right")
    table.add_column("SHA256")
    for i, row in enumerate(rows, 1):
        dims = f"{row['width']}x{row['height']}" if row["width"] else "-"
        avg = f"{row['avg_r']:.0f},{row['avg_g']:.0f},{row['avg_b']:.0f}"
        table.add_row(str(i), row["relative_path"], dims, "yes" if row["has_alpha"] else "no", avg, format_bytes(row["size"]), row["sha256"][:16] + "...")
    console.print(table)


def search_database() -> None:
    console.clear(); header()
    term = console.input("Search filename, folder, path, or hash: ").strip()
    if not term: return
    db = Database(); rows = db.search(term, SEARCH_LIMIT); db.close()
    render_model_table(rows, f"Search Results: {term}")
    if rows:
        console.print("\nEnter a result number to open it, [bold]e[/bold] to export, or press Enter to return.")
        choice = console.input("Choice: ").strip().lower()
        if choice == "e":
            out = export_search_results(rows); console.print(f"[green]Exported:[/green] {out}"); pause()
        elif choice.isdigit() and 1 <= int(choice) <= len(rows):
            show_model_detail(rows[int(choice) - 1]["path"]); pause()


def resolve_model(query: str):
    db = Database(); row = db.get_model_by_relative_or_filename(query); db.close(); return row


def model_explorer() -> None:
    console.clear(); header()
    query = console.input("Enter filename, relative path, or full path: ").strip().strip('"')
    if not query: return
    row = resolve_model(query)
    if not row:
        console.print("[bold red]No model found.[/bold red]"); pause(); return
    show_model_detail(row["path"]); pause()


def show_model_detail(path: str) -> None:
    db = Database()
    row = db.get_model_by_path(path)
    duplicate_count = db.duplicate_count_for_hash(row["sha256"]) if row else 0
    duplicate_rows = db.models_by_hash(row["sha256"]) if row and duplicate_count > 1 else []
    candidates = db.model_comparison_candidates(path, 10) if row else []
    db.close()
    console.clear(); header()
    if not row:
        console.print("[bold red]Model not found.[/bold red]"); return
    table = Table(title="Model Explorer", header_style="bold cyan")
    table.add_column("Field"); table.add_column("Value")
    fields = [
        ("Filename", row["filename"]), ("Folder", row["folder"]), ("Relative Path", row["relative_path"]),
        ("Type", row["filename_type"]), ("Size", format_bytes(row["size"])),
        ("SOM Version", row["som_version"] or "Unknown"), ("Header", row["header"] or "Unknown"),
        ("String Count", f"{row['string_count']:,}"), ("Entropy", f"{row['entropy']:.4f}"),
        ("Printable Ratio", f"{row['printable_ratio']:.4f}"), ("Zero Ratio", f"{row['zero_ratio']:.4f}"),
        ("Duplicate Copies", f"{duplicate_count:,}"), ("SHA256", row["sha256"]), ("MD5", row["md5"]),
        ("CRC32", str(row["crc32"])), ("Prefix 4K Hash", row["prefix_4k_sha256"]),
        ("Middle 4K Hash", row["middle_4k_sha256"]), ("Suffix 4K Hash", row["suffix_4k_sha256"]),
        ("Full Path", row["path"]),
    ]
    for k, v in fields:
        table.add_row(k, str(v))
    console.print(table)
    if row["first_64_hex"]:
        console.print(Panel(row["first_64_hex"], title="First 64 Bytes", border_style="blue"))
    if row["sample_strings"]:
        console.print(Panel(row["sample_strings"], title="Sample Strings", border_style="green"))
    if duplicate_rows: render_model_table(duplicate_rows, "Exact Hash Matches")
    if candidates: render_similarity_table(candidates, "Nearest Internal Matches")


def compare_models_prompt() -> None:
    console.clear(); header()
    a_query = console.input("First model filename/path: ").strip().strip('"')
    b_query = console.input("Second model filename/path: ").strip().strip('"')
    if not a_query or not b_query: return
    a = resolve_model(a_query); b = resolve_model(b_query)
    if not a or not b:
        console.print("[bold red]One or both models were not found.[/bold red]"); pause(); return
    show_model_compare(a["path"], b["path"]); pause()


def show_model_compare(path_a: str, path_b: str) -> None:
    db = Database(); result = db.compare_two_models(path_a, path_b); db.close()
    a, b = result["a"], result["b"]
    console.clear(); header()
    console.print(f"[bold]Comparison Score:[/bold] {result['score']} / 100\n")
    console.print(f"[cyan]A:[/cyan] {a['relative_path']}")
    console.print(f"[cyan]B:[/cyan] {b['relative_path']}\n")
    table = Table(title="Model Comparison", header_style="bold cyan")
    table.add_column("Field"); table.add_column("Match", justify="center"); table.add_column("Model A"); table.add_column("Model B")
    for field in result["fields"]:
        match = "[green]YES[/green]" if field["same"] else "[red]NO[/red]"
        av, bv = str(field["a"]), str(field["b"])
        if len(av) > 40: av = av[:37] + "..."
        if len(bv) > 40: bv = bv[:37] + "..."
        table.add_row(field["label"], match, av, bv)
    console.print(table)


def similar_models_prompt() -> None:
    console.clear(); header()
    query = console.input("Model filename/path to compare internally: ").strip().strip('"')
    if not query: return
    row = resolve_model(query)
    if not row:
        console.print("[bold red]Model not found.[/bold red]"); pause(); return
    db = Database(); candidates = db.model_comparison_candidates(row["path"], SIMILARITY_LIMIT); db.close()
    console.clear(); header()
    console.print(f"[bold]Base Model:[/bold] {row['relative_path']}\n")
    render_similarity_table(candidates, "Internal Similarity Candidates")
    if candidates:
        choice = console.input("\nEnter a result number to compare directly, or press Enter to return: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(candidates):
            show_model_compare(row["path"], candidates[int(choice)-1]["path"]); pause(); return
    pause()


def render_similarity_table(rows, title: str) -> None:
    table = Table(title=title, header_style="bold cyan")
    table.add_column("#", justify="right"); table.add_column("Score", justify="right")
    table.add_column("Relative Path"); table.add_column("Type"); table.add_column("Size", justify="right"); table.add_column("SHA256")
    for i, row in enumerate(rows, 1):
        table.add_row(str(i), str(row["score"]), row["relative_path"], row["filename_type"], format_bytes(row["size"]), row["sha256"][:16] + "...")
    console.print(table)


def duplicate_browser() -> None:
    console.clear(); header()
    db = Database(); rows = db.duplicate_hashes(DUPLICATE_LIMIT); db.close()
    table = Table(title="Duplicate Hash Groups", header_style="bold cyan")
    table.add_column("#", justify="right"); table.add_column("Copies", justify="right"); table.add_column("Total Size", justify="right"); table.add_column("SHA256")
    for i, row in enumerate(rows, 1):
        table.add_row(str(i), f"{row['count']:,}", format_bytes(row["total_size"]), row["sha256"][:32] + "...")
    console.print(table)
    if rows:
        choice = console.input("\nEnter a group number to view copies, or press Enter to return: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(rows):
            show_duplicate_group(rows[int(choice)-1]["sha256"]); pause()


def show_duplicate_group(sha256: str) -> None:
    db = Database(); rows = db.models_by_hash(sha256); db.close()
    console.clear(); header(); console.print(f"[bold]SHA256:[/bold] {sha256}\n")
    render_model_table(rows, "Duplicate Copies")


def folder_explorer() -> None:
    console.clear(); header()
    db = Database(); folders = db.folder_counts(FOLDER_LIMIT); db.close()
    table = Table(title="Folders", header_style="bold cyan")
    table.add_column("#", justify="right"); table.add_column("Folder"); table.add_column("Models", justify="right")
    for i, row in enumerate(folders, 1):
        table.add_row(str(i), row["folder"], f"{row['count']:,}")
    console.print(table)
    choice = console.input("\nEnter a folder number or folder name, or press Enter to return: ").strip()
    if not choice: return
    folder = folders[int(choice)-1]["folder"] if choice.isdigit() and 1 <= int(choice) <= len(folders) else choice
    show_folder_detail(folder); pause()


def show_folder_detail(folder: str) -> None:
    db = Database(); detail = db.folder_details(folder); models = db.models_in_folder(folder, 100); db.close()
    console.clear(); header()
    if not detail:
        console.print("[bold red]Folder not found.[/bold red]"); return
    table = Table(title=f"Folder Explorer: {folder}", header_style="bold cyan")
    table.add_column("Metric"); table.add_column("Value", justify="right")
    rows = [
        ("Models", f"{detail['count']:,}"), ("Unique Hashes", f"{detail['unique_hashes']:,}"),
        ("Duplicate Rate", f"{100.0 * (1 - detail['unique_hashes'] / max(detail['count'], 1)):.1f}%"),
        ("Total Size", format_bytes(detail["total_size"])), ("Average Size", format_bytes(detail["avg_size"])),
        ("Smallest", format_bytes(detail["min_size"])), ("Largest", format_bytes(detail["max_size"])),
        ("Numeric Product IDs", f"{detail['numeric_count']:,}"), ("Named Assets", f"{detail['named_count']:,}"),
        ("Avg Entropy", f"{detail['avg_entropy']:.4f}"), ("Avg Printable Ratio", f"{detail['avg_printable_ratio']:.4f}"),
        ("Avg Zero Ratio", f"{detail['avg_zero_ratio']:.4f}")
    ]
    for k, v in rows: table.add_row(k, v)
    console.print(table); render_model_table(models, f"First {len(models)} Models in {folder}")


def compare_folders_prompt() -> None:
    console.clear(); header()
    folder_a = console.input("First folder code: ").strip()
    folder_b = console.input("Second folder code: ").strip()
    if not folder_a or not folder_b: return
    db = Database(); result = db.folder_comparison(folder_a, folder_b); db.close()
    console.clear(); header()
    if not result["a"] or not result["b"]:
        console.print("[bold red]One or both folders were not found.[/bold red]"); pause(); return
    a, b = result["a"], result["b"]
    table = Table(title=f"Folder Compare: {folder_a} vs {folder_b}", header_style="bold cyan")
    table.add_column("Metric"); table.add_column(folder_a, justify="right"); table.add_column(folder_b, justify="right")
    table.add_row("Models", f"{a['count']:,}", f"{b['count']:,}")
    table.add_row("Unique Hashes", f"{a['unique_hashes']:,}", f"{b['unique_hashes']:,}")
    table.add_row("Duplicate Rate", f"{100.0*(1-a['unique_hashes']/max(a['count'],1)):.1f}%", f"{100.0*(1-b['unique_hashes']/max(b['count'],1)):.1f}%")
    table.add_row("Average Size", format_bytes(a["avg_size"]), format_bytes(b["avg_size"]))
    table.add_row("Avg Entropy", f"{a['avg_entropy']:.4f}", f"{b['avg_entropy']:.4f}")
    table.add_row("Avg Printable Ratio", f"{a['avg_printable_ratio']:.4f}", f"{b['avg_printable_ratio']:.4f}")
    table.add_row("Avg Zero Ratio", f"{a['avg_zero_ratio']:.4f}", f"{b['avg_zero_ratio']:.4f}")
    table.add_row("Shared Exact Hashes", f"{result['shared_hashes']:,}", f"{result['shared_hashes']:,}")
    console.print(table); pause()


def texture_browser() -> None:
    console.clear(); header()
    console.print("[bold cyan]Texture Browser[/bold cyan]\n")
    console.print("[bold]1.[/bold] Search Textures")
    console.print("[bold]2.[/bold] Duplicate Texture Hashes")
    console.print("[bold]3.[/bold] Similar Textures")
    console.print("[bold]4.[/bold] Back")
    choice = console.input("\nChoice: ").strip()
    if choice == "1": search_textures()
    elif choice == "2": duplicate_textures()
    elif choice == "3": similar_textures_prompt()


def search_textures() -> None:
    console.clear(); header()
    term = console.input("Search texture filename, folder, path, or hash: ").strip()
    if not term: return
    db = Database(); rows = db.search_textures(term, TEXTURE_LIMIT); db.close()
    render_texture_table(rows, f"Texture Search: {term}")
    pause()


def duplicate_textures() -> None:
    console.clear(); header()
    db = Database(); rows = db.duplicate_texture_hashes(TEXTURE_LIMIT); db.close()
    table = Table(title="Duplicate Texture Hash Groups", header_style="bold cyan")
    table.add_column("#", justify="right"); table.add_column("Copies", justify="right"); table.add_column("Total Size", justify="right"); table.add_column("SHA256")
    for i, row in enumerate(rows, 1):
        table.add_row(str(i), f"{row['count']:,}", format_bytes(row["total_size"]), row["sha256"][:32] + "...")
    console.print(table)
    if rows:
        choice = console.input("\nEnter group number to view copies, or press Enter to return: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(rows):
            db = Database(); textures = db.textures_by_hash(rows[int(choice)-1]["sha256"]); db.close()
            render_texture_table(textures, "Duplicate Texture Copies")
    pause()


def similar_textures_prompt() -> None:
    console.clear(); header()
    query = console.input("Texture filename/path to compare: ").strip().strip('"')
    if not query: return
    db = Database()
    row = db.get_texture_by_relative_or_filename(query)
    if not row:
        db.close(); console.print("[bold red]Texture not found.[/bold red]"); pause(); return
    rows = db.similar_textures(row["path"], TEXTURE_LIMIT)
    db.close()
    console.print(f"[bold]Base Texture:[/bold] {row['relative_path']}\n")
    table = Table(title="Similar Texture Candidates", header_style="bold cyan")
    table.add_column("#", justify="right"); table.add_column("Score", justify="right"); table.add_column("Relative Path")
    table.add_column("Dimensions"); table.add_column("Avg RGB"); table.add_column("SHA256")
    for i, tex in enumerate(rows, 1):
        dims = f"{tex['width']}x{tex['height']}" if tex["width"] else "-"
        avg = f"{tex['avg_r']:.0f},{tex['avg_g']:.0f},{tex['avg_b']:.0f}"
        table.add_row(str(i), str(tex["score"]), tex["relative_path"], dims, avg, tex["sha256"][:16] + "...")
    console.print(table)
    pause()


def show_statistics() -> None:
    console.clear(); header()
    db = Database()
    total = db.count_models(); textures = db.count_textures(); texture_stats = db.texture_stats()
    duplicate_groups = db.duplicate_hash_count(); type_counts = db.filename_type_counts()
    som_counts = db.som_version_counts(); folders = db.folder_counts(25)
    size_stats = db.size_stats(); latest = db.latest_scan()
    db.close()
    table = Table(title="Database Statistics", header_style="bold cyan")
    table.add_column("Metric"); table.add_column("Value", justify="right")
    table.add_row("Models Indexed", f"{total:,}")
    table.add_row("Textures Indexed", f"{textures:,}")
    table.add_row("Duplicate Model Hash Groups", f"{duplicate_groups:,}")
    table.add_row("Unique Texture Hashes", f"{texture_stats.get('unique_hashes', 0):,}")
    table.add_row("Total Model Data", format_bytes(size_stats["total_size"]))
    table.add_row("Total Texture Data", format_bytes(texture_stats.get("total_size", 0)))
    table.add_row("Average Model Size", format_bytes(size_stats["avg_size"]))
    table.add_row("Average Texture Size", format_bytes(texture_stats.get("avg_size", 0)))
    if latest:
        table.add_row("Last Scan Found", f"{latest['found']:,}")
        table.add_row("Last Scan Updated", f"{latest['scanned']:,}")
        table.add_row("Last Scan Skipped", f"{latest['skipped']:,}")
        table.add_row("Last Scan Errors", f"{latest['errors']:,}")
        table.add_row("Last Scan Type", latest["scan_type"])
        table.add_row("Last Scan Elapsed", format_seconds(latest["elapsed"]))
    for key, value in type_counts.items(): table.add_row(key, f"{value:,}")
    for key, value in som_counts.items(): table.add_row(f"SOM {key}", f"{value:,}")
    console.print(table)
    folder_table = Table(title="Top Model Folders", header_style="bold cyan")
    folder_table.add_column("Folder"); folder_table.add_column("Models", justify="right")
    for row in folders: folder_table.add_row(row["folder"], f"{row['count']:,}")
    console.print(folder_table); pause()


def export_summary() -> None:
    console.clear(); header()
    out = export_database_summary()
    console.print(f"[green]Exported summary:[/green] {out}")
    pause()
