from __future__ import annotations

from pathlib import Path
import time

from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.table import Table

from analysis_engine import rebuild_families, rebuild_links
from config import APP_NAME, DEFAULT_SCAN_PATH, VERSION
from database import Database
from scanners import scan_models, scan_textures
from utils import format_bytes, format_seconds

console = Console()


def header():
    console.print(Panel.fit(f"[bold cyan]{APP_NAME} v{VERSION}[/bold cyan]\nThere.com Model Analysis Suite", border_style="cyan"))


def pause():
    console.input("\nPress Enter to return...")


def quick_status():
    db = Database()
    rel = db.relationship_stats()
    console.print(f"[bold]Database:[/bold] {db.count_models():,} models | {db.count_textures():,} textures | {rel['links']:,} links | {rel['families']:,} families")
    db.close()


def main_menu():
    while True:
        console.clear(); header(); quick_status()
        console.print(f"\n[bold]Default Resources:[/bold] {DEFAULT_SCAN_PATH}\n")
        for n, label in [
            (1, "Scan Manager"), (2, "Research / Analysis"), (3, "Search Models"),
            (4, "Search Textures"), (5, "Model Explorer"), (6, "Texture Explorer"),
            (7, "Duplicates"), (8, "Families"), (9, "Statistics"), (10, "Exit")
        ]:
            console.print(f"[bold]{n}.[/bold] {label}")
        c = console.input("\nChoice: ").strip()
        if c == "1": scan_manager()
        elif c == "2": research_menu()
        elif c == "3": search_models()
        elif c == "4": search_textures()
        elif c == "5": model_explorer()
        elif c == "6": texture_explorer()
        elif c == "7": duplicates_menu()
        elif c == "8": families_menu()
        elif c == "9": stats()
        elif c == "10": return


def progress_runner(title, func, *args, **kwargs):
    console.clear(); header(); console.print(f"[green]{title}[/green]\n")
    with Progress(
        SpinnerColumn(), TextColumn("[bold]{task.description}[/bold]"), BarColumn(),
        TextColumn("{task.completed:,}/{task.total:,}"), TextColumn("[cyan]{task.percentage:>5.1f}%[/cyan]"),
        TextColumn("[green]U:{task.fields[updated]}[/green]"), TextColumn("[yellow]S:{task.fields[skipped]}[/yellow]"),
        TextColumn("[red]E:{task.fields[errors]}[/red]"), TextColumn("[magenta]{task.fields[speed]} /s[/magenta]"),
        TimeElapsedColumn(), TimeRemainingColumn(), console=console
    ) as progress:
        task = progress.add_task("Starting...", total=1, updated="0", skipped="0", errors="0", speed="0.0")
        def cb(info):
            total = max(info.get("total", 1), 1)
            desc = info.get("relative_path") or info.get("method") or "Working..."
            if len(desc) > 60: desc = "..." + desc[-57:]
            progress.update(task, total=total, completed=info.get("index", 0), description=desc,
                            updated=f"{info.get('scanned', info.get('links', info.get('families', 0))):,}",
                            skipped=f"{info.get('skipped', 0):,}", errors=f"{info.get('errors', 0):,}",
                            speed=f"{info.get('speed', 0):.1f}")
        return func(*args, callback=cb, **kwargs)


def scan_manager():
    while True:
        console.clear(); header()
        console.print("[bold cyan]Scan Manager[/bold cyan]\n")
        opts = ["Incremental Model Scan", "Full Model Rescan", "Incremental Texture Scan", "Full Texture Rescan", "Back"]
        for i, o in enumerate(opts, 1): console.print(f"[bold]{i}.[/bold] {o}")
        c = console.input("\nChoice: ").strip()
        if c == "1": show_scan_result(progress_runner("Incremental Model Scan", scan_models, DEFAULT_SCAN_PATH, False))
        elif c == "2": show_scan_result(progress_runner("Full Model Rescan", scan_models, DEFAULT_SCAN_PATH, True))
        elif c == "3": show_scan_result(progress_runner("Incremental Texture Scan", scan_textures, DEFAULT_SCAN_PATH, False))
        elif c == "4": show_scan_result(progress_runner("Full Texture Rescan", scan_textures, DEFAULT_SCAN_PATH, True))
        elif c == "5": return


def show_scan_result(r):
    t = Table(title=r["scan_type"], header_style="bold cyan")
    t.add_column("Metric"); t.add_column("Value", justify="right")
    for k in ("found", "scanned", "skipped", "errors"):
        t.add_row(k.title(), f"{r[k]:,}")
    t.add_row("Elapsed", format_seconds(r["elapsed"]))
    console.print(t); pause()


def research_menu():
    while True:
        console.clear(); header(); quick_status()
        console.print("\n[bold]1.[/bold] Rebuild Model ↔ Texture Links")
        console.print("[bold]2.[/bold] Rebuild Model Families")
        console.print("[bold]3.[/bold] Back")
        c = console.input("\nChoice: ").strip()
        if c == "1":
            r = progress_runner("Rebuilding model-texture links", rebuild_links)
            show_dict("Relationship Build Complete", r)
        elif c == "2":
            r = progress_runner("Rebuilding model families", rebuild_families)
            show_dict("Family Build Complete", r)
        elif c == "3": return


def show_dict(title, d):
    t = Table(title=title, header_style="bold cyan")
    t.add_column("Metric"); t.add_column("Value", justify="right")
    for k, v in d.items():
        if isinstance(v, dict):
            for kk, vv in v.items(): t.add_row(str(kk), f"{vv:,}" if isinstance(vv, int) else str(vv))
        else:
            t.add_row(k, format_seconds(v) if k == "elapsed" else (f"{v:,}" if isinstance(v, int) else str(v)))
    console.print(t); pause()


def render_models(rows, title):
    t = Table(title=title, header_style="bold cyan")
    t.add_column("#", justify="right"); t.add_column("Path"); t.add_column("Type"); t.add_column("Size", justify="right"); t.add_column("SHA")
    for i, r in enumerate(rows, 1):
        t.add_row(str(i), r["relative_path"], r["filename_type"], format_bytes(r["size"]), (r["sha256"] or "")[:16])
    console.print(t)


def render_textures(rows, title):
    t = Table(title=title, header_style="bold cyan")
    t.add_column("#", justify="right"); t.add_column("Path"); t.add_column("Dim"); t.add_column("Format"); t.add_column("Size", justify="right")
    for i, r in enumerate(rows, 1):
        dim = f"{r['width'] or r['dds_width']}x{r['height'] or r['dds_height']}" if (r["width"] or r["dds_width"]) else "-"
        t.add_row(str(i), r["relative_path"], dim, r["dds_format"] or r["extension"], format_bytes(r["size"]))
    console.print(t)


def search_models():
    console.clear(); header()
    q = console.input("Search models: ").strip()
    if not q: return
    db = Database(); rows = db.search_models(q); db.close()
    render_models(rows, f"Models: {q}"); pause()


def search_textures():
    console.clear(); header()
    q = console.input("Search textures: ").strip()
    if not q: return
    db = Database(); rows = db.search_textures(q); db.close()
    render_textures(rows, f"Textures: {q}"); pause()


def model_explorer():
    console.clear(); header()
    q = console.input("Model filename/path: ").strip().strip('"')
    db = Database(); m = db.model_by_query(q)
    if not m:
        db.close(); console.print("[red]Not found[/red]"); pause(); return
    links = db.links_for_model(m["path"])
    db.close()
    t = Table(title="Model Explorer", header_style="bold cyan")
    t.add_column("Field"); t.add_column("Value")
    for k in ["relative_path", "filename_type", "size", "sha256", "prefix_4k_sha256", "suffix_4k_sha256", "entropy", "string_count"]:
        v = m[k]
        if k == "size": v = format_bytes(v)
        t.add_row(k, str(v))
    console.print(t)
    if links:
        lt = Table(title="Candidate Textures", header_style="bold cyan")
        lt.add_column("Score", justify="right"); lt.add_column("Texture"); lt.add_column("Format"); lt.add_column("Reason")
        for l in links:
            lt.add_row(str(l["score"]), l["texture_relative_path"], l["dds_format"] or "-", l["reason"][:60])
        console.print(lt)
    pause()


def texture_explorer():
    console.clear(); header()
    q = console.input("Texture filename/path: ").strip().strip('"')
    db = Database(); tex = db.texture_by_query(q)
    if not tex:
        db.close(); console.print("[red]Not found[/red]"); pause(); return
    links = db.links_for_texture(tex["path"])
    db.close()
    t = Table(title="Texture Explorer", header_style="bold cyan")
    t.add_column("Field"); t.add_column("Value")
    for k in ["relative_path", "size", "sha256", "width", "height", "dds_format", "dds_mipmaps", "analysis_status"]:
        v = tex[k]
        if k == "size": v = format_bytes(v)
        t.add_row(k, str(v))
    console.print(t)
    if links:
        lt = Table(title="Candidate Models", header_style="bold cyan")
        lt.add_column("Score", justify="right"); lt.add_column("Model"); lt.add_column("Reason")
        for l in links:
            lt.add_row(str(l["score"]), l["model_relative_path"], l["reason"][:60])
        console.print(lt)
    pause()


def duplicates_menu():
    console.clear(); header()
    db = Database()
    rows = db.duplicates("models", 50)
    db.close()
    t = Table(title="Duplicate Model Hashes", header_style="bold cyan")
    t.add_column("#", justify="right"); t.add_column("Copies", justify="right"); t.add_column("Size", justify="right"); t.add_column("SHA")
    for i, r in enumerate(rows, 1):
        t.add_row(str(i), f"{r['count']:,}", format_bytes(r["total_size"]), r["sha256"][:24])
    console.print(t); pause()


def families_menu():
    console.clear(); header()
    db = Database(); rows = db.families(100); db.close()
    t = Table(title="Model Families", header_style="bold cyan")
    t.add_column("#", justify="right"); t.add_column("Name"); t.add_column("Method"); t.add_column("Members", justify="right")
    for i, r in enumerate(rows, 1):
        t.add_row(str(i), r["name"], r["method"], f"{r['member_count']:,}")
    console.print(t); pause()


def stats():
    console.clear(); header()
    db = Database()
    ms, ts, rel = db.model_stats(), db.texture_stats(), db.relationship_stats()
    fmts = db.texture_format_counts()
    db.close()
    t = Table(title="Statistics", header_style="bold cyan")
    t.add_column("Metric"); t.add_column("Value", justify="right")
    t.add_row("Models", f"{ms['total']:,}")
    t.add_row("Textures", f"{ts['total']:,}")
    t.add_row("DDS Textures", f"{ts['dds_count'] or 0:,}")
    t.add_row("Model-Texture Links", f"{rel['links']:,}")
    t.add_row("Families", f"{rel['families']:,}")
    t.add_row("Model Data", format_bytes(ms["total_size"]))
    t.add_row("Texture Data", format_bytes(ts["total_size"]))
    console.print(t)
    ft = Table(title="Texture Formats", header_style="bold cyan")
    ft.add_column("Format"); ft.add_column("Count", justify="right")
    for r in fmts[:20]:
        ft.add_row(r["format"] or "Unknown", f"{r['count']:,}")
    console.print(ft); pause()
