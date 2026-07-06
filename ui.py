from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.table import Table

from analysis_engine import rebuild_evidence, rebuild_families, rebuild_links, rebuild_texture_evidence, rebuild_texture_families
from config import APP_NAME, DEFAULT_SCAN_PATH, REPORTS_PATH, VERSION
from database import Database
from intelligence_engine import ensure_intelligence_schema, rebuild_asset_intelligence
from knowledge import KnowledgeBase
from pipeline import full_analysis_steps, run_step
from scanners import scan_models, scan_textures
from utils import format_bytes, format_seconds


console = Console()


def header():
    console.print(Panel.fit(f"[bold cyan]{APP_NAME} v{VERSION}[/bold cyan]\nThere.com Asset Intelligence Suite", border_style="cyan"))


def pause():
    console.input("\nPress Enter to return...")


def report_path(prefix, category="evidence"):
    folder = REPORTS_PATH / category
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"


def intel_count(db):
    ensure_intelligence_schema(db)
    return db.db.execute("SELECT COUNT(*) FROM asset_intelligence").fetchone()[0]


def quick_status():
    db = Database()
    ensure_intelligence_schema(db)
    rel = db.relationship_stats()
    ic = intel_count(db)
    console.print(
        f"[bold]Database:[/bold] {db.count_models():,} models | {db.count_textures():,} textures | "
        f"{rel['links']:,} links | {rel['families']:,} model families | "
        f"{rel['texture_families']:,} texture families | {rel['evidence_pairs']:,} model evidence | "
        f"{rel['texture_evidence_pairs']:,} texture evidence | {ic:,} intelligence"
    )
    db.close()


def main_menu():
    while True:
        console.clear()
        header()
        quick_status()
        console.print(f"\n[bold]Default Resources:[/bold] {DEFAULT_SCAN_PATH}\n")
        items = [
            "Analyze Resources",
            "Investigate Assets",
            "Browse Library",
            "Knowledge Base",
            "Reports",
            "Settings",
            "Legacy Tools",
            "Exit",
        ]
        for i, label in enumerate(items, 1):
            console.print(f"[bold]{i}.[/bold] {label}")
        c = console.input("\nChoice: ").strip()
        if c == "1": analyze_resources_menu()
        elif c == "2": investigate_assets_menu()
        elif c == "3": browse_library_menu()
        elif c == "4": knowledge_base_menu()
        elif c == "5": reports_menu()
        elif c == "6": settings_menu()
        elif c == "7": legacy_tools_menu()
        elif c == "8": return


# ------------------------------------------------------------------
# Progress / pipelines
# ------------------------------------------------------------------

def progress_runner(title, func, *args, **kwargs):
    console.clear()
    header()
    console.print(f"[green]{title}[/green]\n")
    with Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}[/bold]"),
        BarColumn(),
        TextColumn("{task.completed:,}/{task.total:,}"),
        TextColumn("[cyan]{task.percentage:>5.1f}%[/cyan]"),
        TextColumn("[green]U:{task.fields[updated]}[/green]"),
        TextColumn("[yellow]S:{task.fields[skipped]}[/yellow]"),
        TextColumn("[red]E:{task.fields[errors]}[/red]"),
        TextColumn("[magenta]{task.fields[speed]} /s[/magenta]"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Starting...", total=1, updated="0", skipped="0", errors="0", speed="0.0")

        def cb(info):
            total = max(info.get("total", 1), 1)
            desc = info.get("relative_path") or info.get("method") or "Working..."
            if len(desc) > 60:
                desc = "..." + desc[-57:]
            progress.update(
                task,
                total=total,
                completed=info.get("index", 0),
                description=desc,
                updated=f"{info.get('scanned', info.get('links', info.get('families', 0))):,}",
                skipped=f"{info.get('skipped', 0):,}",
                errors=f"{info.get('errors', 0):,}",
                speed=f"{info.get('speed', 0):.1f}",
            )

        return func(*args, callback=cb, **kwargs)


def show_dict(title, d):
    t = Table(title=title, header_style="bold cyan")
    t.add_column("Metric")
    t.add_column("Value", justify="right")
    for k, v in d.items():
        if k.startswith("_"):
            continue
        t.add_row(k, format_seconds(v) if k == "elapsed" else (f"{v:,}" if isinstance(v, int) else str(v)))
    console.print(t)
    pause()


def run_full_analysis(full_rescan=False):
    console.clear()
    header()
    label = "Full Analysis - Full Rescan" if full_rescan else "Full Analysis - Incremental Scans"
    console.print(f"[bold green]{label}[/bold green]\n")

    results = []
    db = Database()
    run_id = db.begin_analysis_run(label)
    db.close()

    steps = full_analysis_steps(full_rescan)
    try:
        with Progress(
            TextColumn("[bold cyan]{task.description}[/bold cyan]"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            console=console,
            transient=False,
        ) as overall:
            main_task = overall.add_task("Analysis stages", total=len(steps))
            for i, step in enumerate(steps, 1):
                console.print(f"\n[bold cyan]Stage {i}/{len(steps)}:[/bold cyan] {step.label}")
                result = progress_runner(step.label, step.function, *step.args, **(step.kwargs or {}))
                results.append((step.label, result))
                overall.advance(main_task)

        mr = export_model_evidence_csv(quiet=True)
        tr = export_texture_evidence_csv(quiet=True)
        ir = export_asset_intelligence_csv(quiet=True)
        summary = export_analysis_summary(results, mr, tr, ir)

        db = Database()
        db.finish_analysis_run(run_id, "complete", f"summary={summary}")
        db.close()

        render_full_analysis_results(results)
        console.print(f"\n[green]Model evidence:[/green] {mr}")
        console.print(f"[green]Texture evidence:[/green] {tr}")
        console.print(f"[green]Asset intelligence:[/green] {ir}")
        console.print(f"[green]Summary:[/green] {summary}")
    except Exception as exc:
        db = Database()
        db.finish_analysis_run(run_id, "error", f"{type(exc).__name__}: {exc}")
        db.close()
        raise
    pause()


def render_full_analysis_results(results):
    t = Table(title="Full Analysis Results", header_style="bold cyan")
    t.add_column("Stage")
    t.add_column("Key Results")
    t.add_column("Elapsed", justify="right")
    for label, result in results:
        keys = []
        for k, v in result.items():
            if k.startswith("_") or k == "elapsed":
                continue
            keys.append(f"{k}: {v:,}" if isinstance(v, int) else f"{k}: {v}")
        t.add_row(label, " | ".join(keys[:4]), format_seconds(result.get("elapsed", 0)))
    console.print(t)


def export_analysis_summary(results, model_report, texture_report, intelligence_report):
    folder = REPORTS_PATH / "analysis"
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"analysis_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

    db = Database()
    rel = db.relationship_stats()
    ms = db.model_stats()
    ts = db.texture_stats()
    db.close()

    lines = [
        f"{APP_NAME} v{VERSION} Analysis Summary",
        "=" * 60,
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        f"Models: {ms['total']:,}",
        f"Textures: {ts['total']:,}",
        f"Links: {rel['links']:,}",
        f"Model families: {rel['families']:,}",
        f"Texture families: {rel['texture_families']:,}",
        f"Model evidence: {rel['evidence_pairs']:,}",
        f"Texture evidence: {rel['texture_evidence_pairs']:,}",
        "",
        "Stages",
        "-" * 60,
    ]
    for label, result in results:
        lines.append(label)
        for k, v in result.items():
            if not k.startswith("_"):
                lines.append(f"  {k}: {v}")
        lines.append("")
    lines += [
        "Reports",
        "-" * 60,
        f"Model evidence CSV: {model_report}",
        f"Texture evidence CSV: {texture_report}",
        f"Asset intelligence CSV: {intelligence_report}",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


# ------------------------------------------------------------------
# Workflow menus
# ------------------------------------------------------------------

def analyze_resources_menu():
    while True:
        console.clear()
        header()
        quick_status()
        opts = [
            "Quick Analyze / Incremental Full Analysis",
            "Full Analyze / Full Rescan",
            "Incremental Model Scan",
            "Full Model Rescan",
            "Incremental Texture Scan",
            "Full Texture Rescan",
            "Analysis Run History",
            "Back",
        ]
        for i, o in enumerate(opts, 1):
            console.print(f"[bold]{i}.[/bold] {o}")
        c = console.input("\nChoice: ").strip()
        if c == "1": run_full_analysis(False)
        elif c == "2":
            if console.input("Full rescan can take a while. Continue? (y/N): ").strip().lower() == "y":
                run_full_analysis(True)
        elif c == "3": show_dict("Scan Complete", progress_runner("Incremental Model Scan", scan_models, DEFAULT_SCAN_PATH, False))
        elif c == "4": show_dict("Scan Complete", progress_runner("Full Model Rescan", scan_models, DEFAULT_SCAN_PATH, True))
        elif c == "5": show_dict("Scan Complete", progress_runner("Incremental Texture Scan", scan_textures, DEFAULT_SCAN_PATH, False))
        elif c == "6": show_dict("Scan Complete", progress_runner("Full Texture Rescan", scan_textures, DEFAULT_SCAN_PATH, True))
        elif c == "7": analysis_history()
        elif c == "8": return


def investigate_assets_menu():
    while True:
        console.clear()
        header()
        opts = [
            "Search / Open Asset Profile",
            "Top Suspicious Assets",
            "Top Reused Assets",
            "Top Model Evidence",
            "Top Texture Evidence",
            "Unreviewed / New Assets",
            "Back",
        ]
        for i, o in enumerate(opts, 1):
            console.print(f"[bold]{i}.[/bold] {o}")
        c = console.input("\nChoice: ").strip()
        if c == "1": open_asset_profile()
        elif c == "2": show_asset_intelligence(sort="suspicion")
        elif c == "3": show_asset_intelligence(sort="reuse")
        elif c == "4": evidence_browser()
        elif c == "5": texture_evidence_browser()
        elif c == "6": show_review_queue()
        elif c == "7": return


def browse_library_menu():
    while True:
        console.clear()
        header()
        opts = [
            "Browse Folders",
            "Search Models",
            "Search Textures",
            "Model Explorer",
            "Texture Explorer",
            "Families",
            "Back",
        ]
        for i, o in enumerate(opts, 1):
            console.print(f"[bold]{i}.[/bold] {o}")
        c = console.input("\nChoice: ").strip()
        if c == "1": browse_folders()
        elif c == "2": search_models()
        elif c == "3": search_textures()
        elif c == "4": model_explorer()
        elif c == "5": texture_explorer()
        elif c == "6": families_menu()
        elif c == "7": return


def knowledge_base_menu():
    while True:
        console.clear()
        header()
        kb = KnowledgeBase()
        opts = [
            "View Folder Knowledge",
            "View Texture Role Rules",
            "View Assumptions",
            "Sync Knowledge Pack to Database",
            "Back",
        ]
        for i, o in enumerate(opts, 1):
            console.print(f"[bold]{i}.[/bold] {o}")
        c = console.input("\nChoice: ").strip()
        if c == "1": show_knowledge_folders(kb)
        elif c == "2": show_texture_role_rules(kb)
        elif c == "3": show_assumptions(kb)
        elif c == "4": sync_knowledge(kb)
        elif c == "5": return


def reports_menu():
    while True:
        console.clear()
        header()
        opts = [
            "Export Model Evidence CSV",
            "Export Texture Evidence CSV",
            "Export Asset Intelligence CSV",
            "Export All Core Reports",
            "Statistics",
            "Back",
        ]
        for i, o in enumerate(opts, 1):
            console.print(f"[bold]{i}.[/bold] {o}")
        c = console.input("\nChoice: ").strip()
        if c == "1": export_model_evidence_csv()
        elif c == "2": export_texture_evidence_csv()
        elif c == "3": export_asset_intelligence_csv()
        elif c == "4": export_all_reports()
        elif c == "5": stats()
        elif c == "6": return


def settings_menu():
    console.clear()
    header()
    t = Table(title="Settings", header_style="bold cyan")
    t.add_column("Setting")
    t.add_column("Value")
    t.add_row("Resource Folder", DEFAULT_SCAN_PATH)
    t.add_row("Reports Folder", str(REPORTS_PATH))
    t.add_row("Knowledge Pack", str(KnowledgeBase().path))
    t.add_row("Database", "database/inspector_v2.db")
    console.print(t)
    pause()


def legacy_tools_menu():
    while True:
        console.clear()
        header()
        opts = [
            "Rebuild Model ↔ Texture Links",
            "Rebuild Model Families",
            "Rebuild Texture Families",
            "Rebuild Model Evidence Pairs",
            "Rebuild Texture Evidence Pairs",
            "Rebuild Asset Intelligence",
            "Duplicates",
            "Back",
        ]
        for i, o in enumerate(opts, 1):
            console.print(f"[bold]{i}.[/bold] {o}")
        c = console.input("\nChoice: ").strip()
        if c == "1": show_dict("Relationship Build Complete", progress_runner("Rebuilding links", rebuild_links))
        elif c == "2": show_dict("Model Family Build Complete", progress_runner("Rebuilding model families", rebuild_families))
        elif c == "3": show_dict("Texture Family Build Complete", progress_runner("Rebuilding texture families", rebuild_texture_families))
        elif c == "4": show_dict("Model Evidence Build Complete", progress_runner("Rebuilding model evidence pairs", rebuild_evidence))
        elif c == "5": show_dict("Texture Evidence Build Complete", progress_runner("Rebuilding texture evidence pairs", rebuild_texture_evidence))
        elif c == "6": show_dict("Asset Intelligence Complete", progress_runner("Rebuilding asset intelligence", rebuild_asset_intelligence))
        elif c == "7": duplicates_menu()
        elif c == "8": return


# ------------------------------------------------------------------
# Knowledge
# ------------------------------------------------------------------

def show_knowledge_folders(kb):
    t = Table(title="Folder Knowledge", header_style="bold cyan")
    t.add_column("Folder")
    t.add_column("Label")
    t.add_column("Category")
    t.add_column("Notes")
    for folder, label, category, notes in kb.summary_rows():
        t.add_row(folder, label, category, notes[:70])
    console.print(t)
    pause()


def show_texture_role_rules(kb):
    t = Table(title="Texture Role Rules", header_style="bold cyan")
    t.add_column("Folder")
    t.add_column("Suffixes")
    t.add_column("Role")
    t.add_column("Weight")
    t.add_column("Notes")
    for r in kb.data.get("texture_roles", []):
        t.add_row(r.get("folder", "*"), ", ".join(r.get("suffixes", [])), r.get("role", ""), str(r.get("weight", "")), r.get("notes", "")[:60])
    console.print(t)
    pause()


def show_assumptions(kb):
    t = Table(title="Knowledge Assumptions", header_style="bold cyan")
    t.add_column("Key")
    t.add_column("Confidence")
    t.add_column("Value")
    for a in kb.assumptions():
        t.add_row(a.get("key", ""), str(a.get("confidence", "")), a.get("value", "")[:90])
    console.print(t)
    pause()


def sync_knowledge(kb):
    db = Database()
    count = kb.sync_to_database(db)
    db.close()
    console.print(f"[green]Synced {count} knowledge rules to the database.[/green]")
    pause()


# ------------------------------------------------------------------
# Investigation / browsing
# ------------------------------------------------------------------

def show_asset_intelligence(sort="suspicion", asset_type=None):
    db = Database()
    ensure_intelligence_schema(db)
    order = "suspicion_score DESC,reuse_score DESC" if sort == "suspicion" else "reuse_score DESC,suspicion_score DESC"
    if asset_type:
        rows = db.db.execute(f"SELECT * FROM asset_intelligence WHERE asset_type=? ORDER BY {order} LIMIT 100", (asset_type,)).fetchall()
    else:
        rows = db.db.execute(f"SELECT * FROM asset_intelligence ORDER BY {order} LIMIT 100").fetchall()
    db.close()
    render_asset_intelligence(rows, f"Asset Intelligence by {sort}")
    pause()


def render_asset_intelligence(rows, title):
    t = Table(title=title, header_style="bold cyan")
    t.add_column("#", justify="right")
    t.add_column("Type")
    t.add_column("Susp", justify="right")
    t.add_column("Reuse", justify="right")
    t.add_column("Evidence", justify="right")
    t.add_column("Asset")
    t.add_column("Flags")
    for i, r in enumerate(rows, 1):
        t.add_row(
            str(i), r["asset_type"], str(r["suspicion_score"]), str(r["reuse_score"]),
            f"{r['evidence_count']} / {r['max_evidence_score']}", r["relative_path"], (r["flags"] or "")[:45]
        )
    console.print(t)


def open_asset_profile():
    q = console.input("Filename/path/search term: ").strip().strip('"')
    if not q:
        return
    db = Database()
    m = db.model_by_query(q)
    t = db.texture_by_query(q)
    if m:
        db.close()
        model_explorer_prefilled(q)
        return
    if t:
        db.close()
        texture_explorer_prefilled(q)
        return
    models = db.search_models(q, 10)
    textures = db.search_textures(q, 10)
    db.close()
    if not models and not textures:
        console.print("[red]No matching assets found.[/red]")
        pause()
        return
    if models:
        render_models(models, "Model Matches")
    if textures:
        render_textures(textures, "Texture Matches")
    pause()


def model_explorer_prefilled(q):
    _model_explorer(q)


def texture_explorer_prefilled(q):
    _texture_explorer(q)


def browse_folders():
    db = Database()
    rows = db.db.execute("""
        SELECT folder, SUM(model_count) model_count, SUM(texture_count) texture_count
        FROM (
            SELECT folder, COUNT(*) model_count, 0 texture_count FROM models GROUP BY folder
            UNION ALL
            SELECT folder, 0 model_count, COUNT(*) texture_count FROM textures GROUP BY folder
        )
        GROUP BY folder
        ORDER BY model_count + texture_count DESC
        LIMIT 200
    """).fetchall()
    db.close()
    kb = KnowledgeBase()
    t = Table(title="Folders", header_style="bold cyan")
    t.add_column("#", justify="right")
    t.add_column("Folder")
    t.add_column("Label")
    t.add_column("Category")
    t.add_column("Models", justify="right")
    t.add_column("Textures", justify="right")
    for i, r in enumerate(rows, 1):
        t.add_row(str(i), r["folder"], kb.folder_label(r["folder"]), kb.folder_category(r["folder"]), f"{r['model_count']:,}", f"{r['texture_count']:,}")
    console.print(t)
    pause()


def show_review_queue():
    db = Database()
    ensure_intelligence_schema(db)
    rows = db.db.execute("""
        SELECT ai.*
        FROM asset_intelligence ai
        LEFT JOIN asset_reviews ar ON ar.asset_path=ai.asset_path
        WHERE ar.status IS NULL OR ar.status='new'
        ORDER BY ai.suspicion_score DESC, ai.reuse_score DESC
        LIMIT 100
    """).fetchall()
    db.close()
    render_asset_intelligence(rows, "Unreviewed / New Assets")
    pause()


def analysis_history():
    db = Database()
    rows = db.recent_analysis_runs(50)
    db.close()
    t = Table(title="Analysis Run History", header_style="bold cyan")
    t.add_column("ID", justify="right")
    t.add_column("Type")
    t.add_column("Status")
    t.add_column("Started")
    t.add_column("Summary")
    for r in rows:
        t.add_row(str(r["id"]), r["run_type"] or "", r["status"] or "", str(r["started"] or ""), (r["summary"] or "")[:70])
    console.print(t)
    pause()


# ------------------------------------------------------------------
# Existing browser/explorer/report helpers
# ------------------------------------------------------------------

def search_models():
    q = console.input("Search models: ").strip()
    db = Database()
    rows = db.search_models(q) if q else []
    db.close()
    render_models(rows, f"Models: {q}")
    pause()


def search_textures():
    q = console.input("Search textures: ").strip()
    db = Database()
    rows = db.search_textures(q) if q else []
    db.close()
    render_textures(rows, f"Textures: {q}")
    pause()


def render_models(rows, title):
    t = Table(title=title, header_style="bold cyan")
    t.add_column("#", justify="right")
    t.add_column("Path")
    t.add_column("Type")
    t.add_column("Size", justify="right")
    for i, r in enumerate(rows, 1):
        t.add_row(str(i), r["relative_path"], r["filename_type"], format_bytes(r["size"]))
    console.print(t)


def render_textures(rows, title):
    t = Table(title=title, header_style="bold cyan")
    t.add_column("#", justify="right")
    t.add_column("Path")
    t.add_column("Format")
    t.add_column("Size", justify="right")
    for i, r in enumerate(rows, 1):
        t.add_row(str(i), r["relative_path"], r["dds_format"] or r["extension"], format_bytes(r["size"]))
    console.print(t)


def model_explorer():
    q = console.input("Model filename/path: ").strip().strip('"')
    _model_explorer(q)


def _model_explorer(q):
    db = Database()
    m = db.model_by_query(q)
    ensure_intelligence_schema(db)
    if not m:
        db.close()
        console.print("[red]Not found[/red]")
        pause()
        return
    intel = db.db.execute("SELECT * FROM asset_intelligence WHERE asset_path=? OR relative_path=? OR filename=? LIMIT 1", (m["path"], m["relative_path"], m["filename"])).fetchone()
    review = db.get_asset_review(m["path"])
    tags = db.tags_for_asset(m["path"])
    notes = db.notes_for_asset(m["path"], 5)
    links = db.links_for_model(m["path"])
    ev = db.evidence_for_model(m["path"], 50)
    db.close()
    render_models([m], "Model Profile")
    render_review_block(review, tags, notes)
    if intel:
        render_asset_intelligence([intel], "Asset Intelligence")
    if links:
        render_texture_links(links)
    if ev:
        render_evidence(ev, "Model Evidence")
    pause()


def texture_explorer():
    q = console.input("Texture filename/path: ").strip().strip('"')
    _texture_explorer(q)


def _texture_explorer(q):
    db = Database()
    tex = db.texture_by_query(q)
    ensure_intelligence_schema(db)
    if not tex:
        db.close()
        console.print("[red]Not found[/red]")
        pause()
        return
    intel = db.db.execute("SELECT * FROM asset_intelligence WHERE asset_path=? OR relative_path=? OR filename=? LIMIT 1", (tex["path"], tex["relative_path"], tex["filename"])).fetchone()
    review = db.get_asset_review(tex["path"])
    tags = db.tags_for_asset(tex["path"])
    notes = db.notes_for_asset(tex["path"], 5)
    links = db.links_for_texture(tex["path"])
    ev = db.texture_evidence_for_texture(tex["path"], 50)
    db.close()
    render_textures([tex], "Texture Profile")
    render_review_block(review, tags, notes)
    if intel:
        render_asset_intelligence([intel], "Asset Intelligence")
    if links:
        render_model_links(links)
    if ev:
        render_texture_evidence(ev, "Texture Evidence")
    pause()


def render_review_block(review, tags, notes):
    t = Table(title="Review / Notes", header_style="bold cyan")
    t.add_column("Field")
    t.add_column("Value")
    t.add_row("Status", review["status"] if review else "new")
    t.add_row("Priority", review["priority"] if review else "normal")
    t.add_row("Tags", ", ".join(tags) if tags else "")
    if review and review["notes"]:
        t.add_row("Review Notes", review["notes"][:100])
    for n in notes:
        t.add_row("Note", n["note"][:100])
    console.print(t)


def render_texture_links(rows):
    tab = Table(title="Candidate Textures", header_style="bold cyan")
    tab.add_column("Score")
    tab.add_column("Texture")
    tab.add_column("Reason")
    for r in rows:
        tab.add_row(str(r["score"]), r["texture_relative_path"], r["reason"][:60])
    console.print(tab)


def render_model_links(rows):
    tab = Table(title="Candidate Models", header_style="bold cyan")
    tab.add_column("Score")
    tab.add_column("Model")
    tab.add_column("Reason")
    for r in rows:
        tab.add_row(str(r["score"]), r["model_relative_path"], r["reason"][:60])
    console.print(tab)


def duplicates_menu():
    db = Database()
    rows = db.duplicates("models", 50)
    db.close()
    t = Table(title="Duplicate Model Hashes", header_style="bold cyan")
    t.add_column("#")
    t.add_column("Copies")
    t.add_column("Size")
    t.add_column("SHA")
    for i, r in enumerate(rows, 1):
        t.add_row(str(i), f"{r['count']:,}", format_bytes(r["total_size"]), r["sha256"][:24])
    console.print(t)
    pause()


def families_menu():
    db = Database()
    mf = db.families(50)
    tf = db.texture_families(50)
    db.close()
    t = Table(title="Model Families", header_style="bold cyan")
    t.add_column("Name")
    t.add_column("Method")
    t.add_column("Members")
    for r in mf:
        t.add_row(r["name"], r["method"], f"{r['member_count']:,}")
    console.print(t)
    tt = Table(title="Texture Families", header_style="bold cyan")
    tt.add_column("Name")
    tt.add_column("Method")
    tt.add_column("Members")
    for r in tf:
        tt.add_row(r["name"], r["method"], f"{r['member_count']:,}")
    console.print(tt)
    pause()


def render_evidence(rows, title):
    t = Table(title=title, header_style="bold cyan")
    t.add_column("Score")
    t.add_column("Type")
    t.add_column("A")
    t.add_column("B")
    t.add_column("Reasons")
    for r in rows:
        t.add_row(str(r["overall_score"]), r["evidence_type"], r["path_a"], r["path_b"], (r["reasons"] or "")[:70])
    console.print(t)


def render_texture_evidence(rows, title):
    t = Table(title=title, header_style="bold cyan")
    t.add_column("Score")
    t.add_column("Type")
    t.add_column("A")
    t.add_column("B")
    t.add_column("Reasons")
    for r in rows:
        t.add_row(str(r["overall_score"]), r["evidence_type"], r["path_a"], r["path_b"], (r["reasons"] or "")[:70])
    console.print(t)


def evidence_browser():
    db = Database()
    rows = db.top_evidence(100)
    db.close()
    render_evidence(rows, "Top Model Evidence Pairs")
    pause()


def texture_evidence_browser():
    db = Database()
    rows = db.top_texture_evidence(100)
    db.close()
    render_texture_evidence(rows, "Top Texture Evidence Pairs")
    pause()


def export_model_evidence_csv(quiet=False):
    out = report_path("model_evidence_pairs")
    db = Database()
    rows = db.top_evidence(100000)
    db.close()
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["overall_score", "binary_score", "texture_score", "string_score", "evidence_type", "model_a", "model_b", "reasons"])
        for r in rows:
            w.writerow([r["overall_score"], r["binary_score"], r["texture_score"], r["string_score"], r["evidence_type"], r["path_a"], r["path_b"], r["reasons"]])
    if not quiet:
        console.print(f"[green]Exported:[/green] {out}")
        pause()
    return out


def export_texture_evidence_csv(quiet=False):
    out = report_path("texture_evidence_pairs")
    db = Database()
    rows = db.top_texture_evidence(100000)
    db.close()
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["overall_score", "evidence_type", "texture_a", "texture_b", "reasons"])
        for r in rows:
            w.writerow([r["overall_score"], r["evidence_type"], r["path_a"], r["path_b"], r["reasons"]])
    if not quiet:
        console.print(f"[green]Exported:[/green] {out}")
        pause()
    return out


def export_asset_intelligence_csv(quiet=False):
    out = report_path("asset_intelligence", "intelligence")
    db = Database()
    ensure_intelligence_schema(db)
    rows = db.db.execute("SELECT * FROM asset_intelligence ORDER BY suspicion_score DESC,reuse_score DESC LIMIT 100000").fetchall()
    db.close()
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["asset_type", "suspicion_score", "reuse_score", "fingerprint_score", "evidence_count", "max_evidence_score", "duplicate_count", "family_count", "linked_asset_count", "classification", "relative_path", "flags", "summary"])
        for r in rows:
            w.writerow([r["asset_type"], r["suspicion_score"], r["reuse_score"], r["fingerprint_score"], r["evidence_count"], r["max_evidence_score"], r["duplicate_count"], r["family_count"], r["linked_asset_count"], r["classification"], r["relative_path"], r["flags"], r["summary"]])
    if not quiet:
        console.print(f"[green]Exported:[/green] {out}")
        pause()
    return out


def export_all_reports():
    mr = export_model_evidence_csv(True)
    tr = export_texture_evidence_csv(True)
    ir = export_asset_intelligence_csv(True)
    console.print(f"[green]Model evidence:[/green] {mr}")
    console.print(f"[green]Texture evidence:[/green] {tr}")
    console.print(f"[green]Asset intelligence:[/green] {ir}")
    pause()


def stats():
    db = Database()
    ensure_intelligence_schema(db)
    ms = db.model_stats()
    ts = db.texture_stats()
    rel = db.relationship_stats()
    ic = intel_count(db)
    db.close()
    t = Table(title="Statistics", header_style="bold cyan")
    t.add_column("Metric")
    t.add_column("Value")
    for k, v in [
        ("Models", ms["total"]),
        ("Textures", ts["total"]),
        ("Links", rel["links"]),
        ("Model Families", rel["families"]),
        ("Texture Families", rel["texture_families"]),
        ("Model Evidence", rel["evidence_pairs"]),
        ("Texture Evidence", rel["texture_evidence_pairs"]),
        ("Asset Intelligence", ic),
    ]:
        t.add_row(k, f"{v:,}")
    console.print(t)
    pause()
