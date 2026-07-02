from __future__ import annotations

import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.table import Table

from asset_analysis import rebuild_model_families, rebuild_model_texture_links
from config import (
    APP_NAME,
    DEFAULT_SCAN_PATH,
    DUPLICATE_LIMIT,
    FAMILY_LIMIT,
    FOLDER_LIMIT,
    RELATIONSHIP_LIMIT,
    SEARCH_LIMIT,
    SIMILARITY_LIMIT,
    TEXTURE_LIMIT,
    VERSION,
)
from database import Database
from reports import export_database_summary, export_search_results
from scanner import scan_folder, scan_textures

console = Console()


def format_bytes(value: int | float) -> str:
    value = float(value or 0)
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
        console.print("[bold]2.[/bold] Research / Relationship Analysis")
        console.print("[bold]3.[/bold] Search Models")
        console.print("[bold]4.[/bold] Model Explorer")
        console.print("[bold]5.[/bold] Compare Models")
        console.print("[bold]6.[/bold] Similar Models")
        console.print("[bold]7.[/bold] Duplicate Browser")
        console.print("[bold]8.[/bold] Folder Explorer")
        console.print("[bold]9.[/bold] Compare Folders")
        console.print("[bold]10.[/bold] Texture Browser")
        console.print("[bold]11.[/bold] Statistics")
        console.print("[bold]12.[/bold] Export Summary Report")
        console.print("[bold]13.[/bold] Exit")

        choice = console.input("\nChoice: ").strip()
        if choice == "1": scan_manager()
        elif choice == "2": research_menu()
        elif choice == "3": search_database()
        elif choice == "4": model_explorer()
        elif choice == "5": compare_models_prompt()
        elif choice == "6": similar_models_prompt()
        elif choice == "7": duplicate_browser()
        elif choice == "8": folder_explorer()
        elif choice == "9": compare_folders_prompt()
        elif choice == "10": texture_browser()
        elif choice == "11": show_statistics()
        elif choice == "12": export_summary()
        elif choice == "13": return


def show_database_quick_status() -> None:
    db = Database()
    total = db.count_models()
    textures = db.count_textures()
    rel = db.relationship_stats()
    latest_model = db.latest_scan("model_incremental") or db.latest_scan("model_full")
    latest_texture = db.latest_scan("texture_incremental") or db.latest_scan("texture_full")
    db.close()
    model_text = "never" if not latest_model else time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(latest_model["finished"]))
    texture_text = "never" if not latest_texture else time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(latest_texture["finished"]))
    console.print(f"[bold]Database:[/bold] {total:,} models | {textures:,} textures | {rel.get('links',0):,} model-texture links | {rel.get('families',0):,} families")
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
        if choice == "1": run_model_scan(DEFAULT_SCAN_PATH, full_rescan=False)
        elif choice == "2": run_model_scan(DEFAULT_SCAN_PATH, full_rescan=True)
        elif choice == "3": run_texture_scan(DEFAULT_SCAN_PATH, full_rescan=False)
        elif choice == "4": run_texture_scan(DEFAULT_SCAN_PATH, full_rescan=True)
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
        elif choice == "7": return


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
    if summary: show_scan_summary(summary, "Models")
    pause()


def run_texture_scan(path: str, full_rescan: bool = False) -> None:
    summary = run_progress_scan("Full Texture Rescan" if full_rescan else "Incremental Texture Scan", Path(path), scan_textures, full_rescan)
    if summary: show_texture_scan_summary(summary)
    pause()


def research_menu() -> None:
    while True:
        console.clear()
        header()
        db = Database()
        rel = db.relationship_stats()
        db.close()
        console.print("[bold cyan]Research / Relationship Analysis[/bold cyan]\n")
        console.print(f"Current links: {rel.get('links',0):,} | Linked models: {rel.get('linked_models',0):,} | Linked textures: {rel.get('linked_textures',0):,}")
        console.print(f"Families: {rel.get('families',0):,} | Family members: {rel.get('family_members',0):,}\n")
        console.print("[bold]1.[/bold] Rebuild Model ↔ Texture Candidate Links")
        console.print("[bold]2.[/bold] Rebuild Model Families")
        console.print("[bold]3.[/bold] Browse Model Families")
        console.print("[bold]4.[/bold] Back")
        choice = console.input("\nChoice: ").strip()
        if choice == "1": rebuild_links_screen()
        elif choice == "2": rebuild_families_screen()
        elif choice == "3": browse_families()
        elif choice == "4": return


def rebuild_links_screen() -> None:
    console.clear(); header()
    console.print("[yellow]This infers possible model-texture links using folder/name heuristics.[/yellow]")
    console.print("It does not prove a texture is used by a model.\n")
    confirm = console.input("Rebuild model-texture candidate links? (y/N): ").strip().lower()
    if confirm != "y": return
    console.print("\n[green]Building links...[/green]")
    result = rebuild_model_texture_links()
    table = Table(title="Model ↔ Texture Link Build Complete", header_style="bold cyan")
    table.add_column("Metric"); table.add_column("Value", justify="right")
    table.add_row("Folders checked", f"{result['folders']:,}")
    table.add_row("Candidate pairs checked", f"{result['checked']:,}")
    table.add_row("Links created", f"{result['links']:,}")
    table.add_row("Elapsed", format_seconds(result["elapsed"]))
    console.print(table); pause()


def rebuild_families_screen() -> None:
    console.clear(); header()
    console.print("[yellow]This groups models using exact hashes and high-value binary fingerprints.[/yellow]\n")
    confirm = console.input("Rebuild model families? (y/N): ").strip().lower()
    if confirm != "y": return
    console.print("\n[green]Building families...[/green]")
    result = rebuild_model_families()
    table = Table(title="Model Family Build Complete", header_style="bold cyan")
    table.add_column("Metric"); table.add_column("Value", justify="right")
    table.add_row("Families created", f"{result['families']:,}")
    table.add_row("Members assigned", f"{result['members']:,}")
    table.add_row("Elapsed", format_seconds(result["elapsed"]))
    console.print(table); pause()


def browse_families() -> None:
    console.clear(); header()
    db = Database(); rows = db.model_families(FAMILY_LIMIT); db.close()
    table = Table(title="Model Families", header_style="bold cyan")
    table.add_column("#", justify="right"); table.add_column("Name"); table.add_column("Method")
    table.add_column("Confidence", justify="right"); table.add_column("Members", justify="right")
    for i, row in enumerate(rows, 1):
        table.add_row(str(i), row["name"], row["method"], str(row["confidence"]), f"{row['member_count']:,}")
    console.print(table)
    if rows:
        choice = console.input("\nEnter family number to view members, or press Enter to return: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(rows):
            show_family_members(rows[int(choice)-1]["id"])
            pause()


def show_family_members(family_id: int) -> None:
    db = Database(); rows = db.model_family_members(family_id, 500); db.close()
    console.clear(); header()
    render_model_table(rows, f"Family Members ({len(rows)})")


def show_scan_summary(summary: dict, label: str = "Models") -> None:
    table = Table(title=f"{summary.get('scan_mode', 'Scan')} Complete", show_header=True, header_style="bold cyan")
    table.add_column("Metric"); table.add_column("Value", justify="right")
    table.add_row("Root", summary["root"])
    table.add_row(f"{label} Found", f"{summary['found']:,}")
    table.add_row("Hashed / Updated", f"{summary['scanned']:,}")
    table.add_row("Skipped Unchanged", f"{summary['skipped']:,}")
    table.add_row("Errors", f"{summary['errors']:,}")
    table.add_row("Elapsed", format_seconds(summary["elapsed"]))
    table.add_row("Average Speed", f"{summary['found'] / max(summary['elapsed'], 0.001):,.1f} files/s")
    table.add_row("Models in Database", f"{summary['database_models']:,}")
    table.add_row("Duplicate Hash Groups", f"{summary['duplicate_hash_groups']:,}")
    for key, value in summary.get("filename_types", {}).items(): table.add_row(key, f"{value:,}")
    for key, value in summary.get("som_versions", {}).items(): table.add_row(f"SOM {key}", f"{value:,}")
    console.print(); console.print(table)


def show_texture_scan_summary(summary: dict) -> None:
    stats = summary.get("texture_stats", {})
    table = Table(title=f"{summary.get('scan_mode', 'Texture Scan')} Complete", show_header=True, header_style="bold cyan")
    table.add_column("Metric"); table.add_column("Value", justify="right")
    table.add_row("Root", summary["root"])
    table.add_row("Textures Found", f"{summary['found']:,}")
    table.add_row("Analyzed / Updated", f"{summary['scanned']:,}")
    table.add_row("Skipped Unchanged", f"{summary['skipped']:,}")
    table.add_row("Errors", f"{summary['errors']:,}")
    table.add_row("Elapsed", format_seconds(summary["elapsed"]))
    table.add_row("Average Speed", f"{summary['found'] / max(summary['elapsed'], 0.001):,.1f} files/s")
    table.add_row("Textures in Database", f"{summary['database_textures']:,}")
    table.add_row("DDS Textures", f"{stats.get('dds_count', 0):,}")
    table.add_row("Unique Texture Hashes", f"{stats.get('unique_hashes', 0):,}")
    table.add_row("Total Texture Data", format_bytes(stats.get("total_size", 0)))
    console.print(); console.print(table)


def render_model_table(rows, title: str) -> None:
    table = Table(title=title, header_style="bold cyan")
    table.add_column("#", justify="right"); table.add_column("Relative Path"); table.add_column("Type")
    table.add_column("SOM"); table.add_column("Size", justify="right"); table.add_column("SHA256")
    for i, row in enumerate(rows, 1):
        table.add_row(str(i), row["relative_path"], row["filename_type"], row["som_version"] or "-", format_bytes(row["size"]), row["sha256"][:16] + "...")
    console.print(table)


def render_texture_table(rows, title: str) -> None:
    table = Table(title=title, header_style="bold cyan")
    table.add_column("#", justify="right"); table.add_column("Relative Path")
    table.add_column("Dimensions"); table.add_column("Format"); table.add_column("Mip")
    table.add_column("Alpha"); table.add_column("Avg RGB"); table.add_column("Size", justify="right")
    for i, row in enumerate(rows, 1):
        dims = f"{row['width']}x{row['height']}" if row["width"] else (f"{row['dds_width']}x{row['dds_height']}" if row["dds_width"] else "-")
        fmt = row["dds_format"] or row["extension"]
        avg = f"{row['avg_r']:.0f},{row['avg_g']:.0f},{row['avg_b']:.0f}" if row["ahash"] else "-"
        table.add_row(str(i), row["relative_path"], dims, fmt, str(row["dds_mipmaps"] or "-"), "yes" if row["has_alpha"] or row["dds_has_alpha"] else "no", avg, format_bytes(row["size"]))
    console.print(table)


# Models
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
    texture_links = db.model_texture_links_for_model(path, RELATIONSHIP_LIMIT) if row else []
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
        ("Candidate Texture Links", f"{len(texture_links):,}"), ("Duplicate Copies", f"{duplicate_count:,}"),
        ("SHA256", row["sha256"]), ("MD5", row["md5"]), ("CRC32", str(row["crc32"])),
        ("Prefix 4K Hash", row["prefix_4k_sha256"]), ("Middle 4K Hash", row["middle_4k_sha256"]),
        ("Suffix 4K Hash", row["suffix_4k_sha256"]), ("Full Path", row["path"]),
    ]
    for k, v in fields: table.add_row(k, str(v))
    console.print(table)
    if row["first_64_hex"]: console.print(Panel(row["first_64_hex"], title="First 64 Bytes", border_style="blue"))
    if row["sample_strings"]: console.print(Panel(row["sample_strings"], title="Sample Strings", border_style="green"))
    if texture_links: render_model_texture_links(texture_links, "Candidate Textures")
    if duplicate_rows: render_model_table(duplicate_rows, "Exact Hash Matches")
    if candidates: render_similarity_table(candidates, "Nearest Internal Matches")


def render_model_texture_links(rows, title: str) -> None:
    table = Table(title=title, header_style="bold cyan")
    table.add_column("#", justify="right"); table.add_column("Score", justify="right")
    table.add_column("Texture"); table.add_column("Format"); table.add_column("Reason")
    for i, row in enumerate(rows, 1):
        table.add_row(str(i), str(row["score"]), row["texture_relative_path"], row["dds_format"] or "-", row["reason"][:60])
    console.print(table)


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
    for i, row in enumerate(folders, 1): table.add_row(str(i), row["folder"], f"{row['count']:,}")
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


# Textures
def texture_browser() -> None:
    while True:
        console.clear(); header()
        console.print("[bold cyan]Texture Browser[/bold cyan]\n")
        console.print("[bold]1.[/bold] Search Textures")
        console.print("[bold]2.[/bold] Duplicate Texture Hashes")
        console.print("[bold]3.[/bold] Similar Textures")
        console.print("[bold]4.[/bold] Texture Format Summary")
        console.print("[bold]5.[/bold] Back")
        choice = console.input("\nChoice: ").strip()
        if choice == "1": search_textures()
        elif choice == "2": duplicate_textures()
        elif choice == "3": similar_textures_prompt()
        elif choice == "4": texture_format_summary()
        elif choice == "5": return


def search_textures() -> None:
    console.clear(); header()
    term = console.input("Search texture filename, folder, path, hash, or DDS format: ").strip()
    if not term: return
    db = Database(); rows = db.search_textures(term, RELATIONSHIP_LIMIT); db.close()
    render_texture_table(rows, f"Texture Search: {term}")
    pause()


def duplicate_textures() -> None:
    console.clear(); header()
    db = Database(); rows = db.duplicate_texture_hashes(DUPLICATE_LIMIT); db.close()
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
    model_links = db.texture_links_for_texture(row["path"], RELATIONSHIP_LIMIT)
    db.close()
    console.print(f"[bold]Base Texture:[/bold] {row['relative_path']}\n")
    table = Table(title="Similar Texture Candidates", header_style="bold cyan")
    table.add_column("#", justify="right"); table.add_column("Score", justify="right"); table.add_column("Relative Path")
    table.add_column("Dimensions"); table.add_column("Format"); table.add_column("Avg RGB"); table.add_column("SHA256")
    for i, tex in enumerate(rows, 1):
        dims = f"{tex['width']}x{tex['height']}" if tex["width"] else (f"{tex['dds_width']}x{tex['dds_height']}" if tex["dds_width"] else "-")
        avg = f"{tex['avg_r']:.0f},{tex['avg_g']:.0f},{tex['avg_b']:.0f}" if tex["ahash"] else "-"
        table.add_row(str(i), str(tex["score"]), tex["relative_path"], dims, tex["dds_format"] or tex["extension"], avg, tex["sha256"][:16] + "...")
    console.print(table)
    if model_links:
        model_table = Table(title="Candidate Linked Models", header_style="bold cyan")
        model_table.add_column("#", justify="right"); model_table.add_column("Score", justify="right")
        model_table.add_column("Model"); model_table.add_column("Reason")
        for i, link in enumerate(model_links, 1):
            model_table.add_row(str(i), str(link["score"]), link["model_relative_path"], link["reason"][:60])
        console.print(model_table)
    pause()


def texture_format_summary() -> None:
    console.clear(); header()
    db = Database(); rows = db.texture_format_counts(); db.close()
    table = Table(title="Texture Format Summary", header_style="bold cyan")
    table.add_column("Format"); table.add_column("Count", justify="right")
    for row in rows: table.add_row(row["format"] or "Unknown", f"{row['count']:,}")
    console.print(table); pause()


def show_statistics() -> None:
    console.clear(); header()
    db = Database()
    total = db.count_models(); textures = db.count_textures(); texture_stats = db.texture_stats()
    rel = db.relationship_stats()
    duplicate_groups = db.duplicate_hash_count(); type_counts = db.filename_type_counts()
    som_counts = db.som_version_counts(); folders = db.folder_counts(25)
    size_stats = db.size_stats(); latest = db.latest_scan()
    db.close()
    table = Table(title="Database Statistics", header_style="bold cyan")
    table.add_column("Metric"); table.add_column("Value", justify="right")
    table.add_row("Models Indexed", f"{total:,}")
    table.add_row("Textures Indexed", f"{textures:,}")
    table.add_row("DDS Textures", f"{texture_stats.get('dds_count', 0):,}")
    table.add_row("Model-Texture Links", f"{rel.get('links', 0):,}")
    table.add_row("Model Families", f"{rel.get('families', 0):,}")
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
