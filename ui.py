from __future__ import annotations
import csv
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.table import Table
from analysis_engine import rebuild_evidence, rebuild_families, rebuild_links, rebuild_texture_evidence, rebuild_texture_families
from config import APP_NAME, DEFAULT_SCAN_PATH, REPORTS_PATH, VERSION
from database import Database
from intelligence_engine import ensure_intelligence_schema, rebuild_asset_intelligence
from scanners import scan_models, scan_textures
from utils import format_bytes, format_seconds
console=Console()

def header(): console.print(Panel.fit(f"[bold cyan]{APP_NAME} v{VERSION}[/bold cyan]\nThere.com Model Analysis Suite", border_style="cyan"))
def pause(): console.input("\nPress Enter to return...")
def report_path(prefix, category='evidence'):
    folder=REPORTS_PATH/category; folder.mkdir(parents=True,exist_ok=True); return folder/f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
def intel_count(db): ensure_intelligence_schema(db); return db.db.execute('SELECT COUNT(*) FROM asset_intelligence').fetchone()[0]
def quick_status():
    db=Database(); ensure_intelligence_schema(db); rel=db.relationship_stats(); ic=intel_count(db)
    console.print(f"[bold]Database:[/bold] {db.count_models():,} models | {db.count_textures():,} textures | {rel['links']:,} links | {rel['families']:,} model families | {rel['texture_families']:,} texture families | {rel['evidence_pairs']:,} model evidence | {rel['texture_evidence_pairs']:,} texture evidence | {ic:,} intelligence")
    db.close()

def main_menu():
    while True:
        console.clear(); header(); quick_status(); console.print(f"\n[bold]Default Resources:[/bold] {DEFAULT_SCAN_PATH}\n")
        items=['Scan Manager','Research / Analysis','Asset Intelligence','Search Models','Search Textures','Model Explorer','Texture Explorer','Duplicates','Families','Model Evidence Browser','Texture Evidence Browser','Statistics','Exit']
        for i,x in enumerate(items,1): console.print(f"[bold]{i}.[/bold] {x}")
        c=console.input('\nChoice: ').strip()
        if c=='1': scan_manager()
        elif c=='2': research_menu()
        elif c=='3': intelligence_menu()
        elif c=='4': search_models()
        elif c=='5': search_textures()
        elif c=='6': model_explorer()
        elif c=='7': texture_explorer()
        elif c=='8': duplicates_menu()
        elif c=='9': families_menu()
        elif c=='10': evidence_browser()
        elif c=='11': texture_evidence_browser()
        elif c=='12': stats()
        elif c=='13': return

def progress_runner(title, func, *args, **kwargs):
    console.clear(); header(); console.print(f"[green]{title}[/green]\n")
    with Progress(SpinnerColumn(),TextColumn('[bold]{task.description}[/bold]'),BarColumn(),TextColumn('{task.completed:,}/{task.total:,}'),TextColumn('[cyan]{task.percentage:>5.1f}%[/cyan]'),TextColumn('[green]U:{task.fields[updated]}[/green]'),TextColumn('[yellow]S:{task.fields[skipped]}[/yellow]'),TextColumn('[red]E:{task.fields[errors]}[/red]'),TextColumn('[magenta]{task.fields[speed]} /s[/magenta]'),TimeElapsedColumn(),TimeRemainingColumn(),console=console) as progress:
        task=progress.add_task('Starting...',total=1,updated='0',skipped='0',errors='0',speed='0.0')
        def cb(info):
            total=max(info.get('total',1),1); desc=info.get('relative_path') or info.get('method') or 'Working...'
            if len(desc)>60: desc='...'+desc[-57:]
            progress.update(task,total=total,completed=info.get('index',0),description=desc,updated=f"{info.get('scanned',info.get('links',info.get('families',0))):,}",skipped=f"{info.get('skipped',0):,}",errors=f"{info.get('errors',0):,}",speed=f"{info.get('speed',0):.1f}")
        return func(*args, callback=cb, **kwargs)

def show_dict(title,d):
    t=Table(title=title,header_style='bold cyan'); t.add_column('Metric'); t.add_column('Value',justify='right')
    for k,v in d.items(): t.add_row(k,format_seconds(v) if k=='elapsed' else (f"{v:,}" if isinstance(v,int) else str(v)))
    console.print(t); pause()

def full_analysis_runner(full_rescan=False):
    steps=[('Model Scan',scan_models,(DEFAULT_SCAN_PATH,full_rescan)),('Texture Scan',scan_textures,(DEFAULT_SCAN_PATH,full_rescan)),('Model ↔ Texture Links',rebuild_links,()),('Model Families',rebuild_families,()),('Texture Families',rebuild_texture_families,()),('Model Evidence Pairs',rebuild_evidence,()),('Texture Evidence Pairs',rebuild_texture_evidence,()),('Asset Intelligence',rebuild_asset_intelligence,())]
    results=[]
    for i,(label,func,args) in enumerate(steps,1):
        console.print(f"\n[bold cyan]Stage {i}/{len(steps)}:[/bold cyan] {label}")
        results.append((label,progress_runner(label,func,*args)))
    mr=export_model_evidence_csv(True); tr=export_texture_evidence_csv(True); ir=export_asset_intelligence_csv(True)
    console.print(f"[green]Reports:[/green]\n{mr}\n{tr}\n{ir}"); pause()

def scan_manager():
    while True:
        console.clear(); header(); opts=['Incremental Model Scan','Full Model Rescan','Incremental Texture Scan','Full Texture Rescan','Full Analysis - Incremental Scans','Full Analysis - Full Rescan','Back']
        for i,o in enumerate(opts,1): console.print(f"[bold]{i}.[/bold] {o}")
        c=console.input('\nChoice: ').strip()
        if c=='1': show_dict('Scan Complete',progress_runner('Incremental Model Scan',scan_models,DEFAULT_SCAN_PATH,False))
        elif c=='2': show_dict('Scan Complete',progress_runner('Full Model Rescan',scan_models,DEFAULT_SCAN_PATH,True))
        elif c=='3': show_dict('Scan Complete',progress_runner('Incremental Texture Scan',scan_textures,DEFAULT_SCAN_PATH,False))
        elif c=='4': show_dict('Scan Complete',progress_runner('Full Texture Rescan',scan_textures,DEFAULT_SCAN_PATH,True))
        elif c=='5': full_analysis_runner(False)
        elif c=='6':
            if console.input('Full rescan can take a while. Continue? (y/N): ').lower()=='y': full_analysis_runner(True)
        elif c=='7': return

def research_menu():
    while True:
        console.clear(); header(); quick_status(); opts=['Full Analysis - Incremental Scans','Full Analysis - Full Rescan','Rebuild Model ↔ Texture Links','Rebuild Model Families','Rebuild Texture Families','Rebuild Model Evidence Pairs','Rebuild Texture Evidence Pairs','Rebuild Asset Intelligence','Export Model Evidence CSV','Export Texture Evidence CSV','Export Asset Intelligence CSV','Back']
        for i,o in enumerate(opts,1): console.print(f"[bold]{i}.[/bold] {o}")
        c=console.input('\nChoice: ').strip()
        if c=='1': full_analysis_runner(False)
        elif c=='2':
            if console.input('Full rescan can take a while. Continue? (y/N): ').lower()=='y': full_analysis_runner(True)
        elif c=='3': show_dict('Relationship Build Complete',progress_runner('Rebuilding links',rebuild_links))
        elif c=='4': show_dict('Model Family Build Complete',progress_runner('Rebuilding model families',rebuild_families))
        elif c=='5': show_dict('Texture Family Build Complete',progress_runner('Rebuilding texture families',rebuild_texture_families))
        elif c=='6': show_dict('Model Evidence Build Complete',progress_runner('Rebuilding model evidence pairs',rebuild_evidence))
        elif c=='7': show_dict('Texture Evidence Build Complete',progress_runner('Rebuilding texture evidence pairs',rebuild_texture_evidence))
        elif c=='8': show_dict('Asset Intelligence Complete',progress_runner('Rebuilding asset intelligence',rebuild_asset_intelligence))
        elif c=='9': export_model_evidence_csv()
        elif c=='10': export_texture_evidence_csv()
        elif c=='11': export_asset_intelligence_csv()
        elif c=='12': return

def intelligence_menu():
    while True:
        console.clear(); header(); quick_status(); opts=['Rebuild Asset Intelligence','Top Suspicious Assets','Top Reused Assets','Top Model Intelligence','Top Texture Intelligence','Folder Intelligence','Search Intelligence Record','Export Asset Intelligence CSV','Back']
        for i,o in enumerate(opts,1): console.print(f"[bold]{i}.[/bold] {o}")
        c=console.input('\nChoice: ').strip()
        if c=='1': show_dict('Asset Intelligence Complete',progress_runner('Rebuilding asset intelligence',rebuild_asset_intelligence))
        elif c=='2': show_asset_intelligence('suspicion')
        elif c=='3': show_asset_intelligence('reuse')
        elif c=='4': show_asset_intelligence('suspicion','model')
        elif c=='5': show_asset_intelligence('suspicion','texture')
        elif c=='6': show_folder_intelligence()
        elif c=='7': search_intelligence()
        elif c=='8': export_asset_intelligence_csv()
        elif c=='9': return

def show_asset_intelligence(sort='suspicion', asset_type=None):
    db=Database(); ensure_intelligence_schema(db); order='suspicion_score DESC,reuse_score DESC' if sort=='suspicion' else 'reuse_score DESC,suspicion_score DESC'
    if asset_type: rows=db.db.execute(f'SELECT * FROM asset_intelligence WHERE asset_type=? ORDER BY {order} LIMIT 100',(asset_type,)).fetchall()
    else: rows=db.db.execute(f'SELECT * FROM asset_intelligence ORDER BY {order} LIMIT 100').fetchall()
    db.close(); render_asset_intelligence(rows,f'Asset Intelligence by {sort}'); pause()

def render_asset_intelligence(rows,title):
    t=Table(title=title,header_style='bold cyan'); t.add_column('#',justify='right'); t.add_column('Type'); t.add_column('Susp',justify='right'); t.add_column('Reuse',justify='right'); t.add_column('Evidence',justify='right'); t.add_column('Asset'); t.add_column('Flags')
    for i,r in enumerate(rows,1): t.add_row(str(i),r['asset_type'],str(r['suspicion_score']),str(r['reuse_score']),f"{r['evidence_count']} / {r['max_evidence_score']}",r['relative_path'],(r['flags'] or '')[:45])
    console.print(t)

def show_folder_intelligence():
    db=Database(); ensure_intelligence_schema(db); rows=db.db.execute('SELECT * FROM folder_intelligence ORDER BY avg_suspicion_score DESC, model_count+texture_count DESC LIMIT 100').fetchall(); db.close()
    t=Table(title='Folder Intelligence',header_style='bold cyan'); t.add_column('#',justify='right'); t.add_column('Folder'); t.add_column('Models',justify='right'); t.add_column('Textures',justify='right'); t.add_column('Avg Susp',justify='right'); t.add_column('Flags')
    for i,r in enumerate(rows,1): t.add_row(str(i),r['folder'],f"{r['model_count']:,}",f"{r['texture_count']:,}",f"{r['avg_suspicion_score']:.1f}",(r['top_flags'] or '')[:60])
    console.print(t); pause()

def search_intelligence():
    q=console.input('Filename/path: ').strip().strip('"'); db=Database(); ensure_intelligence_schema(db); r=db.db.execute('SELECT * FROM asset_intelligence WHERE asset_path=? OR relative_path=? OR filename=? LIMIT 1',(q,q,q)).fetchone(); db.close()
    if not r: console.print('[red]No intelligence record found[/red]'); pause(); return
    t=Table(title='Asset Intelligence Record',header_style='bold cyan'); t.add_column('Field'); t.add_column('Value')
    for k in r.keys(): t.add_row(k,str(r[k]))
    console.print(t); pause()

def search_models():
    q=console.input('Search models: ').strip(); db=Database(); rows=db.search_models(q) if q else []; db.close(); render_models(rows,f'Models: {q}'); pause()
def search_textures():
    q=console.input('Search textures: ').strip(); db=Database(); rows=db.search_textures(q) if q else []; db.close(); render_textures(rows,f'Textures: {q}'); pause()

def render_models(rows,title):
    t=Table(title=title,header_style='bold cyan'); t.add_column('#',justify='right'); t.add_column('Path'); t.add_column('Type'); t.add_column('Size',justify='right')
    for i,r in enumerate(rows,1): t.add_row(str(i),r['relative_path'],r['filename_type'],format_bytes(r['size']))
    console.print(t)
def render_textures(rows,title):
    t=Table(title=title,header_style='bold cyan'); t.add_column('#',justify='right'); t.add_column('Path'); t.add_column('Format'); t.add_column('Size',justify='right')
    for i,r in enumerate(rows,1): t.add_row(str(i),r['relative_path'],r['dds_format'] or r['extension'],format_bytes(r['size']))
    console.print(t)

def model_explorer():
    q=console.input('Model filename/path: ').strip().strip('"'); db=Database(); m=db.model_by_query(q); ensure_intelligence_schema(db)
    if not m: db.close(); console.print('[red]Not found[/red]'); pause(); return
    intel=db.db.execute('SELECT * FROM asset_intelligence WHERE asset_path=? OR relative_path=? OR filename=? LIMIT 1',(m['path'],m['relative_path'],m['filename'])).fetchone(); links=db.links_for_model(m['path']); ev=db.evidence_for_model(m['path'],50); db.close()
    render_models([m],'Model Explorer')
    if intel: render_asset_intelligence([intel],'Asset Intelligence')
    if links: render_texture_links(links)
    if ev: render_evidence(ev,'Model Evidence')
    pause()

def texture_explorer():
    q=console.input('Texture filename/path: ').strip().strip('"'); db=Database(); t=db.texture_by_query(q); ensure_intelligence_schema(db)
    if not t: db.close(); console.print('[red]Not found[/red]'); pause(); return
    intel=db.db.execute('SELECT * FROM asset_intelligence WHERE asset_path=? OR relative_path=? OR filename=? LIMIT 1',(t['path'],t['relative_path'],t['filename'])).fetchone(); links=db.links_for_texture(t['path']); ev=db.texture_evidence_for_texture(t['path'],50); db.close()
    render_textures([t],'Texture Explorer')
    if intel: render_asset_intelligence([intel],'Asset Intelligence')
    if links: render_model_links(links)
    if ev: render_texture_evidence(ev,'Texture Evidence')
    pause()

def render_texture_links(rows):
    tab=Table(title='Candidate Textures',header_style='bold cyan'); tab.add_column('Score'); tab.add_column('Texture'); tab.add_column('Reason')
    for r in rows: tab.add_row(str(r['score']),r['texture_relative_path'],r['reason'][:60])
    console.print(tab)
def render_model_links(rows):
    tab=Table(title='Candidate Models',header_style='bold cyan'); tab.add_column('Score'); tab.add_column('Model'); tab.add_column('Reason')
    for r in rows: tab.add_row(str(r['score']),r['model_relative_path'],r['reason'][:60])
    console.print(tab)

def duplicates_menu():
    db=Database(); rows=db.duplicates('models',50); db.close(); t=Table(title='Duplicate Model Hashes',header_style='bold cyan'); t.add_column('#'); t.add_column('Copies'); t.add_column('Size'); t.add_column('SHA')
    for i,r in enumerate(rows,1): t.add_row(str(i),f"{r['count']:,}",format_bytes(r['total_size']),r['sha256'][:24])
    console.print(t); pause()
def families_menu():
    db=Database(); mf=db.families(50); tf=db.texture_families(50); db.close(); t=Table(title='Model Families',header_style='bold cyan'); t.add_column('Name'); t.add_column('Method'); t.add_column('Members')
    for r in mf: t.add_row(r['name'],r['method'],f"{r['member_count']:,}")
    console.print(t); tt=Table(title='Texture Families',header_style='bold cyan'); tt.add_column('Name'); tt.add_column('Method'); tt.add_column('Members')
    for r in tf: tt.add_row(r['name'],r['method'],f"{r['member_count']:,}")
    console.print(tt); pause()

def render_evidence(rows,title):
    t=Table(title=title,header_style='bold cyan'); t.add_column('Score'); t.add_column('Type'); t.add_column('A'); t.add_column('B'); t.add_column('Reasons')
    for r in rows: t.add_row(str(r['overall_score']),r['evidence_type'],r['path_a'],r['path_b'],(r['reasons'] or '')[:70])
    console.print(t)
def render_texture_evidence(rows,title):
    t=Table(title=title,header_style='bold cyan'); t.add_column('Score'); t.add_column('Type'); t.add_column('A'); t.add_column('B'); t.add_column('Reasons')
    for r in rows: t.add_row(str(r['overall_score']),r['evidence_type'],r['path_a'],r['path_b'],(r['reasons'] or '')[:70])
    console.print(t)
def evidence_browser():
    db=Database(); rows=db.top_evidence(100); db.close(); render_evidence(rows,'Top Model Evidence Pairs'); pause()
def texture_evidence_browser():
    db=Database(); rows=db.top_texture_evidence(100); db.close(); render_texture_evidence(rows,'Top Texture Evidence Pairs'); pause()

def export_model_evidence_csv(quiet=False):
    out=report_path('model_evidence_pairs'); db=Database(); rows=db.top_evidence(100000); db.close()
    with out.open('w',newline='',encoding='utf-8') as f:
        w=csv.writer(f); w.writerow(['overall_score','binary_score','texture_score','string_score','evidence_type','model_a','model_b','reasons'])
        for r in rows: w.writerow([r['overall_score'],r['binary_score'],r['texture_score'],r['string_score'],r['evidence_type'],r['path_a'],r['path_b'],r['reasons']])
    if not quiet: console.print(f'[green]Exported:[/green] {out}'); pause()
    return out
def export_texture_evidence_csv(quiet=False):
    out=report_path('texture_evidence_pairs'); db=Database(); rows=db.top_texture_evidence(100000); db.close()
    with out.open('w',newline='',encoding='utf-8') as f:
        w=csv.writer(f); w.writerow(['overall_score','evidence_type','texture_a','texture_b','reasons'])
        for r in rows: w.writerow([r['overall_score'],r['evidence_type'],r['path_a'],r['path_b'],r['reasons']])
    if not quiet: console.print(f'[green]Exported:[/green] {out}'); pause()
    return out
def export_asset_intelligence_csv(quiet=False):
    out=report_path('asset_intelligence','intelligence'); db=Database(); ensure_intelligence_schema(db); rows=db.db.execute('SELECT * FROM asset_intelligence ORDER BY suspicion_score DESC,reuse_score DESC LIMIT 100000').fetchall(); db.close()
    with out.open('w',newline='',encoding='utf-8') as f:
        w=csv.writer(f); w.writerow(['asset_type','suspicion_score','reuse_score','fingerprint_score','evidence_count','max_evidence_score','duplicate_count','family_count','linked_asset_count','classification','relative_path','flags','summary'])
        for r in rows: w.writerow([r['asset_type'],r['suspicion_score'],r['reuse_score'],r['fingerprint_score'],r['evidence_count'],r['max_evidence_score'],r['duplicate_count'],r['family_count'],r['linked_asset_count'],r['classification'],r['relative_path'],r['flags'],r['summary']])
    if not quiet: console.print(f'[green]Exported:[/green] {out}'); pause()
    return out

def stats():
    db=Database(); ensure_intelligence_schema(db); ms=db.model_stats(); ts=db.texture_stats(); rel=db.relationship_stats(); ic=intel_count(db); db.close(); t=Table(title='Statistics',header_style='bold cyan'); t.add_column('Metric'); t.add_column('Value')
    for k,v in [('Models',ms['total']),('Textures',ts['total']),('Links',rel['links']),('Model Families',rel['families']),('Texture Families',rel['texture_families']),('Model Evidence',rel['evidence_pairs']),('Texture Evidence',rel['texture_evidence_pairs']),('Asset Intelligence',ic)]: t.add_row(k,f"{v:,}")
    console.print(t); pause()
