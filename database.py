
import sqlite3
from pathlib import Path
Path("database").mkdir(exist_ok=True)
class Database:
    def __init__(self):
        self.db=sqlite3.connect("database/inspector.db")
        self.db.execute("""CREATE TABLE IF NOT EXISTS models(
        path TEXT PRIMARY KEY,size INTEGER,sha256 TEXT,md5 TEXT,crc32 INTEGER)""")
    def add_model(self,path,size,sha,md,crc):
        self.db.execute("INSERT OR REPLACE INTO models VALUES(?,?,?,?,?)",(path,size,sha,md,crc))
        self.db.commit()
    def close(self):
        self.db.close()
