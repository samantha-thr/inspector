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
from scanners import scan_models, scan_textures
from utils import format_bytes, format_seconds
console=Console()

def header(): console.print(Panel.fit(f"[bold cyan]{APP_NAME} v{VERSION}[/bold cyan]\nThere.com Model Analysis Suite",border_style="cyan"))
def pause(): console.input("\nPress Enter to return...")
def evidence_dir():
    p=REPORTS_PATH/"evidence"; p.mkdir(parents=True,exist_ok=True); return p
def analysis_dir():
    p=REPORTS_PATH/"analysis"; p.mkdir(parents=True,exist_ok=True); return p
def timestamped_report_path(prefix:str)->Path:
    return evidence_dir()/f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

def quick_status():
    db=Database(); rel=db.relationship_stats()
    console.print(f"[bold]Database:[/bold] {db.count_models():,} models | {db.count_textures():,} textures | {rel['links']:,} links | {rel['families']:,} model families | {rel['texture_families']:,} texture families | {rel['evidence_pairs']:,} model evidence | {rel['texture_evidence_pairs']:,} texture evidence")
    db.close()

def main_menu():
    while True:
        console.clear(); header(); quick_status(); console.print(f"\n[bold]Default Resources:[/bold] {DEFAULT_SCAN_PATH}\n")
        items=["Scan Manager","Research / Analysis","Search Models","Search Textures","Model Explorer","Texture Explorer","Duplicates","Families","Model Evidence Browser","Texture Evidence Browser","Statistics","Exit"]
        for i,x in enumerate(items,1): console.print(f"[bold]{i}.[/bold] {x}")
        c=console.input("\nChoice: ").strip()
        if c=="1": scan_manager()
        elif c=="2": research_menu()
        elif c=="3": search_models()
        elif c=="4": search_textures()
        elif c=="5": model_explorer()
        elif c=="6": texture_explorer()
        elif c=="7": duplicates_menu()
        elif c=="8": families_menu()
        elif c=="9": evidence_browser()
        elif c=="10": texture_evidence_browser()
        elif c=="11": stats()
        elif c=="12": return

def make_progress(title):
    return Progress(SpinnerColumn(),TextColumn("[bold]{task.description}[/bold]"),BarColumn(),TextColumn("{task.completed:,}/{task.total:,}"),TextColumn("[cyan]{task.percentage:>5.1f}%[/cyan]"),TextColumn("[green]U:{task.fields[updated]}[/green]"),TextColumn("[yellow]S:{task.fields[skipped]}[/yellow]"),TextColumn("[red]E:{task.fields[errors]}[/red]"),TextColumn("[magenta]{task.fields[speed]} /s[/magenta]"),TimeElapsedColumn(),TimeRemainingColumn(),console=console,transient=False)

def progress_runner(title,func,*args,**kwargs):
    console.clear(); header(); console.print(f"[green]{title}[/green]\n")
    with make_progress(title) as progress:
        task=progress.add_task("Starting...",total=1,updated="0",skipped="0",errors="0",speed="0.0")
        def cb(info):
            total=max(info.get('total',1),1); desc=info.get('relative_path') or info.get('method') or title
            if len(desc)>60: desc='...'+desc[-57:]
            progress.update(task,total=total,completed=info.get('index',0),description=desc,updated=f"{info.get('scanned',info.get('links',info.get('families',0))):,}",skipped=f"{info.get('skipped',0):,}",errors=f"{info.get('errors',0):,}",speed=f"{info.get('speed',0):.1f}")
        return func(*args,callback=cb,**kwargs)

def show_dict(title,d):
    t=Table(title=title,header_style="bold cyan"); t.add_column("Metric"); t.add_column("Value",justify="right")
    for k,v in d.items():
        if isinstance(v,dict):
            for kk,vv in v.items(): t.add_row(str(kk),f"{vv:,}" if isinstance(vv,int) else str(vv))
        else: t.add_row(k,format_seconds(v) if k=='elapsed' else (f"{v:,}" if isinstance(v,int) else str(v)))
    console.print(t); pause()

def full_analysis_runner(full_rescan:bool=False):
    console.clear(); header(); mode="Full Rescan + Full Analysis" if full_rescan else "Incremental Scan + Full Analysis"; console.print(f"[bold green]{mode}[/bold green]\n")
    steps=[("Model Scan",scan_models,(DEFAULT_SCAN_PATH,full_rescan),{}),("Texture Scan",scan_textures,(DEFAULT_SCAN_PATH,full_rescan),{}),("Model ↔ Texture Links",rebuild_links,(),{}),("Model Families",rebuild_families,(),{}),("Texture Families",rebuild_texture_families,(),{}),("Model Evidence Pairs",rebuild_evidence,(),{}),("Texture Evidence Pairs",rebuild_texture_evidence,(),{})]
    results=[]
    overall=Progress(TextColumn("[bold cyan]{task.description}[/bold cyan]"),BarColumn(),TextColumn("{task.completed}/{task.total}"),console=console,transient=False)
    with overall:
        main_task=overall.add_task("Full analysis stages",total=len(steps))
        for n,(label,func,args,kwargs) in enumerate(steps,1):
            console.print(f"\n[bold cyan]Stage {n}/{len(steps)}:[/bold cyan] {label}")
            with make_progress(label) as progress:
                task=progress.add_task(label,total=1,updated="0",skipped="0",errors="0",speed="0.0")
                def cb(info):
                    total=max(info.get('total',1),1); desc=info.get('relative_path') or info.get('method') or label
                    if len(desc)>60: desc='...'+desc[-57:]
                    progress.update(task,total=total,completed=info.get('index',0),description=desc,updated=f"{info.get('scanned',info.get('links',info.get('families',0))):,}",skipped=f"{info.get('skipped',0):,}",errors=f"{info.get('errors',0):,}",speed=f"{info.get('speed',0):.1f}")
                result=func(*args,callback=cb,**kwargs); results.append((label,result))
            overall.advance(main_task)
    model_report=export_model_evidence_csv(quiet=True); texture_report=export_texture_evidence_csv(quiet=True); summary=export_full_analysis_summary(results,model_report,texture_report)
    console.print("\n[bold green]Full Analysis Complete[/bold green]"); render_full_analysis_results(results)
    console.print(f"\n[green]Model evidence CSV:[/green] {model_report}"); console.print(f"[green]Texture evidence CSV:[/green] {texture_report}"); console.print(f"[green]Summary report:[/green] {summary}"); pause()

def export_full_analysis_summary(results,model_report:Path,texture_report:Path)->Path:
    out=analysis_dir()/f"full_analysis_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    db=Database(); rel=db.relationship_stats(); ms=db.model_stats(); ts=db.texture_stats(); db.close()
    lines=[f"{APP_NAME} v{VERSION} Full Analysis Summary","="*60,f"Generated: {datetime.now().isoformat(timespec='seconds')}","","Database Totals","-"*60,f"Models: {ms['total']:,}",f"Textures: {ts['total']:,}",f"Model-texture links: {rel['links']:,}",f"Model families: {rel['families']:,}",f"Texture families: {rel['texture_families']:,}",f"Model evidence pairs: {rel['evidence_pairs']:,}",f"Texture evidence pairs: {rel['texture_evidence_pairs']:,}","","Stage Results","-"*60]
    for label,result in results:
        lines.append(label)
        for k,v in result.items(): lines.append(f"  {k}: {v}")
        lines.append("")
    lines += ["Reports","-"*60,f"Model evidence CSV: {model_report}",f"Texture evidence CSV: {texture_report}"]
    out.write_text("\n".join(lines),encoding="utf-8"); return out

def render_full_analysis_results(results):
    t=Table(title="Full Analysis Results",header_style="bold cyan"); t.add_column("Stage"); t.add_column("Key Results"); t.add_column("Elapsed",justify="right")
    for label,result in results:
        keys=[]
        for k,v in result.items():
            if k=='elapsed' or isinstance(v,dict): continue
            keys.append(f"{k}: {v:,}" if isinstance(v,int) else f"{k}: {v}")
        t.add_row(label," | ".join(keys[:4]),format_seconds(result.get('elapsed',0)))
    console.print(t)

def scan_manager():
    while True:
        console.clear(); header(); opts=["Incremental Model Scan","Full Model Rescan","Incremental Texture Scan","Full Texture Rescan","Full Analysis - Incremental Scans","Full Analysis - Full Rescan","Back"]
        for i,o in enumerate(opts,1): console.print(f"[bold]{i}.[/bold] {o}")
        c=console.input("\nChoice: ").strip()
        if c=="1": show_dict("Scan Complete",progress_runner("Incremental Model Scan",scan_models,DEFAULT_SCAN_PATH,False))
        elif c=="2": show_dict("Scan Complete",progress_runner("Full Model Rescan",scan_models,DEFAULT_SCAN_PATH,True))
        elif c=="3": show_dict("Scan Complete",progress_runner("Incremental Texture Scan",scan_textures,DEFAULT_SCAN_PATH,False))
        elif c=="4": show_dict("Scan Complete",progress_runner("Full Texture Rescan",scan_textures,DEFAULT_SCAN_PATH,True))
        elif c=="5": full_analysis_runner(False)
        elif c=="6":
            if console.input("Full rescan can take a while. Continue? (y/N): ").strip().lower()=="y": full_analysis_runner(True)
        elif c=="7": return

def research_menu():
    while True:
        console.clear(); header(); quick_status(); opts=["Full Analysis - Incremental Scans","Full Analysis - Full Rescan","Rebuild Model ↔ Texture Links","Rebuild Model Families","Rebuild Texture Families","Rebuild Model Evidence Pairs","Rebuild Texture Evidence Pairs","Export Model Evidence CSV","Export Texture Evidence CSV","Back"]
        for i,o in enumerate(opts,1): console.print(f"[bold]{i}.[/bold] {o}")
        c=console.input("\nChoice: ").strip()
        if c=="1": full_analysis_runner(False)
        elif c=="2":
            if console.input("Full rescan can take a while. Continue? (y/N): ").strip().lower()=="y": full_analysis_runner(True)
        elif c=="3": show_dict("Relationship Build Complete",progress_runner("Rebuilding links",rebuild_links))
        elif c=="4": show_dict("Model Family Build Complete",progress_runner("Rebuilding model families",rebuild_families))
        elif c=="5": show_dict("Texture Family Build Complete",progress_runner("Rebuilding texture families",rebuild_texture_families))
        elif c=="6": show_dict("Model Evidence Build Complete",progress_runner("Rebuilding model evidence pairs",rebuild_evidence))
        elif c=="7": show_dict("Texture Evidence Build Complete",progress_runner("Rebuilding texture evidence pairs",rebuild_texture_evidence))
        elif c=="8": export_model_evidence_csv()
        elif c=="9": export_texture_evidence_csv()
        elif c=="10": return

def render_models(rows,title):
    t=Table(title=title,header_style="bold cyan"); t.add_column("#",justify="right"); t.add_column("Path"); t.add_column("Type"); t.add_column("Size",justify="right"); t.add_column("SHA")
    for i,r in enumerate(rows,1): t.add_row(str(i),r['relative_path'],r['filename_type'],format_bytes(r['size']),(r['sha256'] or '')[:16])
    console.print(t)

def render_textures(rows,title):
    t=Table(title=title,header_style="bold cyan"); t.add_column("#",justify="right"); t.add_column("Path"); t.add_column("Dim"); t.add_column("Format"); t.add_column("Flags"); t.add_column("Size",justify="right")
    for i,r in enumerate(rows,1):
        dim=f"{r['width'] or r['dds_width']}x{r['height'] or r['dds_height']}" if (r['width'] or r['dds_width']) else '-'
        flags=[]
        if r['is_grayscale']: flags.append('gray')
        if r['is_probable_normal']: flags.append('normal?')
        if r['alpha_coverage'] and r['alpha_coverage']>0.01: flags.append(f"alpha {r['alpha_coverage']:.0%}")
        t.add_row(str(i),r['relative_path'],dim,r['dds_format'] or r['extension'],', '.join(flags),format_bytes(r['size']))
    console.print(t)

def search_models():
    console.clear(); header(); q=console.input("Search models: ").strip()
    if q:
        db=Database(); rows=db.search_models(q); db.close(); render_models(rows,f"Models: {q}"); pause()

def search_textures():
    console.clear(); header(); q=console.input("Search textures: ").strip()
    if q:
        db=Database(); rows=db.search_textures(q); db.close(); render_textures(rows,f"Textures: {q}"); pause()

def model_explorer():
    console.clear(); header(); q=console.input("Model filename/path: ").strip().strip('"'); db=Database(); m=db.model_by_query(q)
    if not m: db.close(); console.print("[red]Not found[/red]"); pause(); return
    links=db.links_for_model(m['path']); evidence=db.evidence_for_model(m['path'],50); db.close(); t=Table(title="Model Explorer",header_style="bold cyan"); t.add_column("Field"); t.add_column("Value")
    for k in ["relative_path","filename_type","size","sha256","prefix_4k_sha256","suffix_4k_sha256","entropy","string_count"]:
        v=m[k]; t.add_row(k,format_bytes(v) if k=='size' else str(v))
    console.print(t)
    if links:
        lt=Table(title="Candidate Textures",header_style="bold cyan"); lt.add_column("Score",justify="right"); lt.add_column("Texture"); lt.add_column("Format"); lt.add_column("Reason")
        for l in links: lt.add_row(str(l['score']),l['texture_relative_path'],l['dds_format'] or '-',l['reason'][:60])
        console.print(lt)
    if evidence: render_evidence(evidence,"Model evidence involving this model")
    pause()

def texture_explorer():
    console.clear(); header(); q=console.input("Texture filename/path: ").strip().strip('"'); db=Database(); tex=db.texture_by_query(q)
    if not tex: db.close(); console.print("[red]Not found[/red]"); pause(); return
    links=db.links_for_texture(tex['path']); evidence=db.texture_evidence_for_texture(tex['path'],50); db.close(); t=Table(title="Texture Explorer",header_style="bold cyan"); t.add_column("Field"); t.add_column("Value")
    for k in ["relative_path","size","sha256","width","height","dds_format","dds_mipmaps","alpha_coverage","edge_density","brightness","saturation","is_grayscale","is_probable_normal","analysis_status"]:
        v=tex[k]; t.add_row(k,format_bytes(v) if k=='size' else str(v))
    console.print(t)
    if links:
        lt=Table(title="Candidate Models",header_style="bold cyan"); lt.add_column("Score",justify="right"); lt.add_column("Model"); lt.add_column("Reason")
        for l in links: lt.add_row(str(l['score']),l['model_relative_path'],l['reason'][:60])
        console.print(lt)
    if evidence: render_texture_evidence(evidence,"Texture evidence involving this texture")
    pause()

def duplicates_menu():
    console.clear(); header(); db=Database(); rows=db.duplicates("models",50); db.close(); t=Table(title="Duplicate Model Hashes",header_style="bold cyan"); t.add_column("#",justify="right"); t.add_column("Copies",justify="right"); t.add_column("Size",justify="right"); t.add_column("SHA")
    for i,r in enumerate(rows,1): t.add_row(str(i),f"{r['count']:,}",format_bytes(r['total_size']),r['sha256'][:24])
    console.print(t); pause()

def families_menu():
    console.clear(); header(); db=Database(); rows=db.families(100); tex=db.texture_families(100); db.close()
    t=Table(title="Top Model Families",header_style="bold cyan"); t.add_column("#",justify="right"); t.add_column("Name"); t.add_column("Method"); t.add_column("Members",justify="right")
    for i,r in enumerate(rows,1): t.add_row(str(i),r['name'],r['method'],f"{r['member_count']:,}")
    console.print(t); tt=Table(title="Top Texture Families",header_style="bold cyan"); tt.add_column("#",justify="right"); tt.add_column("Name"); tt.add_column("Method"); tt.add_column("Members",justify="right")
    for i,r in enumerate(tex[:30],1): tt.add_row(str(i),r['name'],r['method'],f"{r['member_count']:,}")
    console.print(tt); pause()

def render_evidence(rows,title):
    t=Table(title=title,header_style="bold cyan"); t.add_column("#",justify="right"); t.add_column("Score",justify="right"); t.add_column("Type"); t.add_column("A"); t.add_column("B"); t.add_column("Reasons")
    for i,r in enumerate(rows,1): t.add_row(str(i),str(r['overall_score']),r['evidence_type'],r['path_a'],r['path_b'],(r['reasons'] or '')[:70])
    console.print(t)

def render_texture_evidence(rows,title):
    t=Table(title=title,header_style="bold cyan"); t.add_column("#",justify="right"); t.add_column("Score",justify="right"); t.add_column("Type"); t.add_column("Texture A"); t.add_column("Texture B"); t.add_column("Reasons")
    for i,r in enumerate(rows,1): t.add_row(str(i),str(r['overall_score']),r['evidence_type'],r['path_a'],r['path_b'],(r['reasons'] or '')[:70])
    console.print(t)

def evidence_browser():
    console.clear(); header(); db=Database(); rows=db.top_evidence(100); db.close(); render_evidence(rows,"Top Model Evidence Pairs"); pause()

def texture_evidence_browser():
    console.clear(); header(); db=Database(); rows=db.top_texture_evidence(100); db.close(); render_texture_evidence(rows,"Top Texture Evidence Pairs"); pause()

def export_model_evidence_csv(quiet:bool=False):
    out=timestamped_report_path("model_evidence_pairs"); db=Database(); rows=db.top_evidence(100000); db.close()
    with out.open('w',newline='',encoding='utf-8') as f:
        w=csv.writer(f); w.writerow(["overall_score","binary_score","texture_score","string_score","evidence_type","model_a","model_b","reasons"])
        for r in rows: w.writerow([r['overall_score'],r['binary_score'],r['texture_score'],r['string_score'],r['evidence_type'],r['path_a'],r['path_b'],r['reasons']])
    if not quiet: console.print(f"[green]Exported:[/green] {out}"); pause()
    return out

def export_texture_evidence_csv(quiet:bool=False):
    out=timestamped_report_path("texture_evidence_pairs"); db=Database(); rows=db.top_texture_evidence(100000); db.close()
    with out.open('w',newline='',encoding='utf-8') as f:
        w=csv.writer(f); w.writerow(["overall_score","exact_score","perceptual_score","histogram_score","color_score","alpha_score","format_score","size_score","evidence_type","texture_a","texture_b","format_a","format_b","reasons"])
        for r in rows: w.writerow([r['overall_score'],r['exact_score'],r['perceptual_score'],r['histogram_score'],r['color_score'],r['alpha_score'],r['format_score'],r['size_score'],r['evidence_type'],r['path_a'],r['path_b'],r['format_a'],r['format_b'],r['reasons']])
    if not quiet: console.print(f"[green]Exported:[/green] {out}"); pause()
    return out

def stats():
    console.clear(); header(); db=Database(); ms=db.model_stats(); ts=db.texture_stats(); rel=db.relationship_stats(); fmts=db.texture_format_counts(); db.close(); t=Table(title="Statistics",header_style="bold cyan"); t.add_column("Metric"); t.add_column("Value",justify="right")
    for k,v in [("Models",ms['total']),("Textures",ts['total']),("DDS Textures",ts['dds_count'] or 0),("Model-Texture Links",rel['links']),("Model Families",rel['families']),("Texture Families",rel['texture_families']),("Model Evidence Pairs",rel['evidence_pairs']),("Texture Evidence Pairs",rel['texture_evidence_pairs'])]: t.add_row(k,f"{v:,}")
    t.add_row("Model Data",format_bytes(ms['total_size'])); t.add_row("Texture Data",format_bytes(ts['total_size'])); console.print(t); ft=Table(title="Texture Formats",header_style="bold cyan"); ft.add_column("Format"); ft.add_column("Count",justify="right")
    for r in fmts[:20]: ft.add_row(r['format'] or 'Unknown',f"{r['count']:,}")
    console.print(ft); pause()
