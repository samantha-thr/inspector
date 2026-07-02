
from rich.console import Console
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.panel import Panel
from scanner import scan

console=Console()

def main_menu():
    while True:
        console.clear()
        console.print(Panel.fit("[bold cyan]There Inspector v1.1.0[/bold cyan]\nThere.com Model Analysis Suite"))
        console.print("1. Scan Folder")
        console.print("2. Exit")
        choice=console.input("\nChoice: ").strip()
        if choice=="2":
            return
        if choice=="1":
            path=console.input("Folder to scan: ").strip()
            if path:
                scan(path,console)
                console.input("\nPress Enter to return to menu...")
