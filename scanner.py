
from pathlib import Path
from hashlib import sha256, md5
import zlib, time
from database import Database
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn

def scan(path, console):
    db=Database()
    files=list(Path(path).rglob("*.model"))
    total=len(files)
    console.clear()
    console.print(f"[bold green]Scanning[/bold green]: {path}")
    console.print(f"Found {total:,} model(s)\n")
    start=time.time()
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task=progress.add_task("Scanning", total=total)
        for f in files:
            data=f.read_bytes()
            db.add_model(str(f),f.stat().st_size,sha256(data).hexdigest(),md5(data).hexdigest(),zlib.crc32(data)&0xffffffff)
            progress.update(task,advance=1,description=f.name[:40])
    elapsed=time.time()-start
    db.close()
    console.print("\n[bold cyan]Scan Complete[/bold cyan]")
    console.print(f"Models : {total:,}")
    console.print(f"Elapsed: {elapsed:.2f}s")
