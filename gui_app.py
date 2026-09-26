from __future__ import annotations
import sys
import time
from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtWidgets import *
from config import APP_NAME, DEFAULT_SCAN_PATH, DATABASE_PATH, VERSION
from scanner import scan_folder, scan_textures
from analysis_engine import rebuild_links, rebuild_families, rebuild_texture_families, rebuild_evidence, rebuild_texture_evidence
from database import Database
from gui_workspaces import AssetDialog, ComparePage, ConvertPage, KnowledgePage, SettingsPage

BG="#101215"; PANEL="#181b20"; CYAN="#43e8e8"; TEXT="#e8eaed"; MUTED="#8f98a3"

class Page(QWidget):
    def __init__(self,title,subtitle=""):
        super().__init__()
        self.box=QVBoxLayout(self); self.box.setContentsMargins(28,24,28,24)
        h=QLabel(title); h.setObjectName("title"); self.box.addWidget(h)
        s=QLabel(subtitle); s.setObjectName("muted"); self.box.addWidget(s)

class Card(QFrame):
    def __init__(self,title):
        super().__init__(); self.setObjectName("card")
        b=QVBoxLayout(self); t=QLabel(title.upper()); t.setObjectName("muted"); b.addWidget(t)
        self.value=QLabel("0"); self.value.setObjectName("metric"); b.addWidget(self.value)

class Dashboard(Page):
    def __init__(self,db):
        super().__init__("Dashboard","Asset intelligence overview"); self.db=db
        row=QHBoxLayout(); self.cards=[Card(x) for x in ("Models","Textures","Evidence","Review Queue")]
        for c in self.cards: row.addWidget(c)
        self.box.addLayout(row)
        self.table=QTableWidget(0,4); self.table.setHorizontalHeaderLabels(["Score","Asset A","Asset B","Reasons"])
        for i in (1,2,3): self.table.horizontalHeader().setSectionResizeMode(i,QHeaderView.Stretch)
        self.box.addWidget(self.table,1); self.refresh()
    def refresh(self):
        s=self.db.relationship_stats()
        rc=self.db.review_counts(); vals=[self.db.count_models(),self.db.count_textures(),s.get("evidence_pairs",0)+s.get("texture_evidence_pairs",0),sum(v for k,v in rc.items() if k not in ("reviewed","dismissed"))]
        for c,v in zip(self.cards,vals): c.value.setText(f"{int(v or 0):,}")
        rows=self.db.top_evidence(30); self.table.setRowCount(len(rows))
        for r,x in enumerate(rows):
            for c,v in enumerate((x["overall_score"],x["path_a"],x["path_b"],x["reasons"])):
                self.table.setItem(r,c,QTableWidgetItem(str(v or "")))

class Browser(Page):
    def __init__(self,db,kind):
        title="Models" if kind=="model" else "Textures"
        super().__init__(title,"Search and inspect indexed assets"); self.db=db; self.kind=kind; self.page=0
        row=QHBoxLayout(); self.search=QLineEdit(); self.search.setPlaceholderText("Filename, folder, path or hash")
        b=QPushButton("Search"); b.clicked.connect(self.reset_search); self.search.returnPressed.connect(self.reset_search)
        self.limit=QComboBox(); self.limit.addItems(["250","500","1000","2500","5000"]); self.limit.setCurrentText("1000")
        row.addWidget(self.search,1); row.addWidget(QLabel("Rows")); row.addWidget(self.limit); row.addWidget(b); self.box.addLayout(row)
        self.table=QTableWidget(0,5); self.table.setHorizontalHeaderLabels(["Filename","Folder","Path","Details","Status"])
        self.table.horizontalHeader().setSectionResizeMode(2,QHeaderView.Stretch); self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers); self.table.doubleClicked.connect(self.open_asset); self.box.addWidget(self.table,1)
        nav=QHBoxLayout(); self.prev=QPushButton("Previous"); self.next=QPushButton("Next"); self.info=QLabel("")
        self.prev.clicked.connect(lambda:self.change_page(-1)); self.next.clicked.connect(lambda:self.change_page(1))
        nav.addWidget(self.prev); nav.addWidget(self.next); nav.addWidget(self.info); nav.addStretch(); self.box.addLayout(nav); self.refresh()

    def reset_search(self): self.page=0; self.refresh()
    def change_page(self,d): self.page=max(0,self.page+d); self.refresh()

    def refresh(self):
        q=self.search.text().strip(); limit=int(self.limit.currentText())
        fetch=(self.page+1)*limit+1
        allrows=self.db.search_models(q,fetch) if self.kind=="model" else self.db.search_textures(q,fetch)
        start=self.page*limit; rows=allrows[start:start+limit]; has_next=len(allrows)>start+limit
        self.table.setRowCount(len(rows))
        for r,x in enumerate(rows):
            if self.kind=="model": vals=(x["filename"],x["folder"],x["relative_path"],f"{int(x['size'] or 0):,} bytes",(x["sha256"] or "")[:16])
            else: vals=(x["filename"],x["folder"],x["relative_path"],f"{x['width'] or x['dds_width'] or 0} x {x['height'] or x['dds_height'] or 0}",x["dds_format"] or x["analysis_status"])
            for col,v in enumerate(vals): self.table.setItem(r,col,QTableWidgetItem(str(v or "")))
            self.table.item(r,0).setData(Qt.UserRole,x["path"])
        self.prev.setEnabled(self.page>0); self.next.setEnabled(has_next)
        lo=start+1 if rows else 0; hi=start+len(rows); self.info.setText(f"Showing {lo:,}–{hi:,}" + (" • more available" if has_next else ""))

    def open_asset(self,index):
        item=self.table.item(index.row(),0)
        if item: AssetDialog(self.db,item.data(Qt.UserRole),self.kind,self).exec()

class Evidence(Page):
    def __init__(self,db):
        super().__init__("Evidence","Review model and texture similarity candidates"); self.db=db
        self.mode=QComboBox(); self.mode.addItems(["Model Evidence","Texture Evidence"]); self.mode.currentIndexChanged.connect(self.refresh); self.box.addWidget(self.mode)
        self.table=QTableWidget(0,5); self.table.setHorizontalHeaderLabels(["Score","Asset A","Asset B","Type","Reasons"])
        for i in (1,2,4): self.table.horizontalHeader().setSectionResizeMode(i,QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows); self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.doubleClicked.connect(self.open_evidence_asset)
        self.box.addWidget(self.table,1); self.refresh()
    def refresh(self):
        rows=self.db.top_evidence(200) if self.mode.currentIndex()==0 else self.db.top_texture_evidence(200)
        self.table.setRowCount(len(rows))
        for r,x in enumerate(rows):
            for c,v in enumerate((x["overall_score"],x["path_a"],x["path_b"],x["evidence_type"],x["reasons"])):
                self.table.setItem(r,c,QTableWidgetItem(str(v or "")))
            self.table.item(r,1).setData(Qt.UserRole,x["path_a"]); self.table.item(r,2).setData(Qt.UserRole,x["path_b"])

    def open_evidence_asset(self,index):
        col=1 if index.column()!=2 else 2; item=self.table.item(index.row(),col)
        if item:
            kind="model" if self.mode.currentIndex()==0 else "texture"
            AssetDialog(self.db,item.data(Qt.UserRole),kind,self).exec()

class ReviewQueue(Page):
    def __init__(self,db):
        super().__init__("Review Queue","Prioritized assets awaiting investigation"); self.db=db
        row=QHBoxLayout(); refresh=QPushButton("Refresh"); refresh.clicked.connect(self.refresh); row.addStretch(); row.addWidget(refresh); self.box.addLayout(row)
        self.table=QTableWidget(0,5); self.table.setHorizontalHeaderLabels(["Priority","Status","Asset","Tags","Updated"])
        self.table.horizontalHeader().setSectionResizeMode(2,QHeaderView.Stretch); self.table.horizontalHeader().setSectionResizeMode(3,QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows); self.table.setEditTriggers(QAbstractItemView.NoEditTriggers); self.table.doubleClicked.connect(self.open_asset)
        self.box.addWidget(self.table,1); self.refresh()
    def refresh(self):
        rows=self.db.review_queue(1000); self.table.setRowCount(len(rows))
        for r,x in enumerate(rows):
            vals=(x["priority"],x["status"],x["asset_path"],x["tags"],time.strftime("%Y-%m-%d %H:%M",time.localtime(x["updated"] or 0)) if x["updated"] else "")
            for col,v in enumerate(vals):self.table.setItem(r,col,QTableWidgetItem(str(v or "")))
            self.table.item(r,2).setData(Qt.UserRole,x["asset_path"])
    def open_asset(self,index):
        item=self.table.item(index.row(),2)
        if item:
            path=item.data(Qt.UserRole); kind=self.db.asset_kind(path)
            if kind:AssetDialog(self.db,path,kind,self).exec(); self.refresh()

class Worker(QThread):
    progress=Signal(dict); finished_ok=Signal(object); failed=Signal(str)
    def __init__(self,fn,*args,**kwargs):
        super().__init__(); self.fn=fn; self.args=args; self.kwargs=kwargs
    def run(self):
        try:
            names=self.fn.__code__.co_varnames; kw=dict(self.kwargs)
            if "progress_callback" in names: kw["progress_callback"]=self.progress.emit
            elif "callback" in names: kw["callback"]=self.progress.emit
            self.finished_ok.emit(self.fn(*self.args,**kw))
        except Exception as exc: self.failed.emit(f"{type(exc).__name__}: {exc}")

class Analysis(Page):
    def __init__(self,changed):
        super().__init__("Scan & Analysis","Run scans and rebuild Inspector intelligence without the command line")
        self.changed=changed; self.worker=None; self.started_at=0.0; self.sequence=[]; self.sequence_index=0; self.sequence_name=""
        row=QHBoxLayout(); self.root=QLineEdit(DEFAULT_SCAN_PATH); browse=QPushButton("Browse"); browse.clicked.connect(self.browse)
        row.addWidget(QLabel("Resources")); row.addWidget(self.root,1); row.addWidget(browse); self.box.addLayout(row)

        card=QFrame(); card.setObjectName("card"); cb=QVBoxLayout(card)
        t=QLabel("COMPLETE ASSET SCAN"); t.setObjectName("muted"); cb.addWidget(t)
        d=QLabel("Scan models and textures, then rebuild links, families and evidence in one operation."); d.setWordWrap(True); d.setObjectName("muted"); cb.addWidget(d)
        buttons=QHBoxLayout()
        inc=QPushButton("Run Incremental Scan"); inc.clicked.connect(lambda:self.run_complete(False))
        full=QPushButton("Run Full Scan"); full.clicked.connect(lambda:self.run_complete(True))
        buttons.addWidget(inc); buttons.addWidget(full); cb.addLayout(buttons); self.box.addWidget(card)

        advanced=QGroupBox("Individual / Advanced Jobs"); grid=QGridLayout(advanced)
        jobs=[("Scan Models",lambda:self.start(scan_folder,self.root.text())),("Scan Textures",lambda:self.start(scan_textures,self.root.text())),
              ("Rebuild Links",lambda:self.start(rebuild_links)),("Model Families",lambda:self.start(rebuild_families)),
              ("Texture Families",lambda:self.start(rebuild_texture_families)),("Model Evidence",lambda:self.start(rebuild_evidence)),
              ("Texture Evidence",lambda:self.start(rebuild_texture_evidence))]
        for i,(label,fn) in enumerate(jobs):
            b=QPushButton(label); b.clicked.connect(fn); grid.addWidget(b,i//3,i%3)
        self.box.addWidget(advanced)

        self.job_label=QLabel("Ready"); self.job_label.setObjectName("muted"); self.box.addWidget(self.job_label)
        self.progress=QProgressBar(); self.progress.setRange(0,100); self.progress.setFormat("%p%"); self.box.addWidget(self.progress)
        stats=QHBoxLayout(); self.percent_label=QLabel("0.0%"); self.elapsed_label=QLabel("Elapsed: 00:00"); self.eta_label=QLabel("Remaining: --:--")
        for x in (self.percent_label,self.elapsed_label,self.eta_label): x.setObjectName("muted"); stats.addWidget(x)
        stats.addStretch(); self.box.addLayout(stats)
        self.status=QLabel("Ready"); self.status.setObjectName("muted"); self.box.addWidget(self.status); self.box.addStretch()
        self.timer=QTimer(self); self.timer.setInterval(1000); self.timer.timeout.connect(self.refresh_clock)

    @staticmethod
    def fmt(seconds):
        if seconds is None or seconds<0:return "--:--"
        seconds=int(seconds); h,rem=divmod(seconds,3600); m,s=divmod(rem,60)
        return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

    def browse(self):
        p=QFileDialog.getExistingDirectory(self,"There resource folder",self.root.text())
        if p:self.root.setText(p)

    def run_complete(self,full):
        if self.worker and self.worker.isRunning(): QMessageBox.information(self,APP_NAME,"A job is already running."); return
        root=self.root.text(); self.sequence_name="Full Scan" if full else "Incremental Scan"
        self.sequence=[("Models",scan_folder,(root,),{"full_rescan":full}),("Textures",scan_textures,(root,),{"full_rescan":full}),
                       ("Links",rebuild_links,(),{}),("Model Families",rebuild_families,(),{}),("Texture Families",rebuild_texture_families,(),{}),
                       ("Model Evidence",rebuild_evidence,(),{}),("Texture Evidence",rebuild_texture_evidence,(),{})]
        self.sequence_index=0; self.start_sequence()

    def start_sequence(self):
        if self.sequence_index>=len(self.sequence):
            self.progress.setValue(100); self.percent_label.setText("100.0%"); self.job_label.setText(self.sequence_name+" complete")
            self.status.setText("All scan and analysis stages completed."); self.timer.stop(); self.changed(); self.sequence=[]; return
        label,fn,args,kwargs=self.sequence[self.sequence_index]; self.start(fn,*args,_sequence=True,_label=label,**kwargs)

    def start(self,fn,*args,_sequence=False,_label=None,**kwargs):
        if self.worker and self.worker.isRunning(): QMessageBox.information(self,APP_NAME,"A job is already running."); return
        self.started_at=time.time(); self.timer.start(); self.progress.setValue(0); self.percent_label.setText("0.0%")
        self.elapsed_label.setText("Elapsed: 00:00"); self.eta_label.setText("Remaining: --:--")
        self.job_label.setText((self.sequence_name+" • " if _sequence else "")+(_label or fn.__name__)); self.status.setText("Starting...")
        self.worker=Worker(fn,*args,**kwargs); self.worker.progress.connect(self.update_progress)
        self.worker.finished_ok.connect(self.sequence_done if _sequence else self.done); self.worker.failed.connect(self.fail); self.worker.start()

    def update_progress(self,d):
        total=int(d.get("total",0) or 0); idx=int(d.get("index",0) or 0); pct=(idx*100.0/total) if total else 0.0
        self.progress.setValue(min(100,int(pct))); self.percent_label.setText(f"{pct:.1f}%")
        elapsed=max(time.time()-self.started_at,.001); eta=(elapsed/idx*(total-idx)) if idx and total else None
        self.elapsed_label.setText("Elapsed: "+self.fmt(elapsed)); self.eta_label.setText("Remaining: "+self.fmt(eta))
        name=d.get("relative_path") or d.get("file") or d.get("method") or d.get("status") or ""; speed=idx/elapsed if idx else 0
        self.status.setText(f"{idx:,} / {total:,}   {name}   •   {speed:,.1f}/sec")

    def refresh_clock(self):
        if self.started_at:self.elapsed_label.setText("Elapsed: "+self.fmt(time.time()-self.started_at))

    def sequence_done(self,result):
        self.progress.setValue(100); self.percent_label.setText("100.0%"); self.sequence_index+=1; self.start_sequence()

    def done(self,result):
        self.progress.setValue(100); self.percent_label.setText("100.0%"); self.timer.stop(); self.status.setText("Complete")
        self.elapsed_label.setText("Elapsed: "+self.fmt(time.time()-self.started_at)); self.eta_label.setText("Remaining: 00:00"); self.changed()

    def fail(self,error):
        self.timer.stop(); self.sequence=[]; self.status.setText(error); QMessageBox.critical(self,"Job failed",error)

class Placeholder(Page):
    def __init__(self,title,subtitle):
        super().__init__(title,subtitle)
        c=QFrame(); c.setObjectName("card"); b=QVBoxLayout(c)
        x=QLabel("Foundation ready"); x.setObjectName("metric"); b.addWidget(x)
        d=QLabel("This workspace is connected to the 3.0 application shell and is ready for the next implementation pass."); d.setWordWrap(True); b.addWidget(d); b.addStretch()
        self.box.addWidget(c,1)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.db=Database()
        self.setWindowTitle(f"{APP_NAME} 3.0 GUI Preview"); self.resize(1450,900); self.setMinimumSize(1050,680)
        root=QWidget(); self.setCentralWidget(root); shell=QHBoxLayout(root); shell.setContentsMargins(0,0,0,0); shell.setSpacing(0)
        side=QFrame(); side.setObjectName("sidebar"); side.setFixedWidth(230); sb=QVBoxLayout(side); sb.setContentsMargins(18,22,18,18)
        brand=QLabel("THERE" + chr(10) + "INSPECTOR"); brand.setObjectName("brand"); sb.addWidget(brand)
        sub=QLabel("Asset Intelligence Suite"); sub.setObjectName("muted"); sb.addWidget(sub); sb.addSpacing(20)
        self.nav=QListWidget(); self.nav.setObjectName("nav")
        names=["Dashboard","Models","Textures","Evidence","Review Queue","Compare","Convert","Knowledge","Scan & Analysis","Settings"]
        self.nav.addItems(names); self.nav.setCurrentRow(0); sb.addWidget(self.nav,1)
        db_label=QLabel("Database" + chr(10) + str(DATABASE_PATH)); db_label.setWordWrap(True); db_label.setObjectName("muted"); db_label.setToolTip(str(DATABASE_PATH))
        sb.addWidget(db_label); shell.addWidget(side)
        self.stack=QStackedWidget()
        pages=[Dashboard(self.db),Browser(self.db,"model"),Browser(self.db,"texture"),Evidence(self.db),ReviewQueue(self.db),\n               ComparePage(self.db),ConvertPage(),KnowledgePage(self.db),Analysis(self.refresh_all),SettingsPage()]
        for p in pages:self.stack.addWidget(p)
        shell.addWidget(self.stack,1); self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
    def refresh_all(self):
        for i in range(self.stack.count()):
            page = self.stack.widget(i)
            if hasattr(page, "refresh"):
                page.refresh()

    def closeEvent(self,event):
        self.db.close(); super().closeEvent(event)

STYLE=f"""
QWidget{{background:{BG};color:{TEXT};font-family:'Segoe UI';font-size:10pt}}
QFrame#sidebar{{background:#0c0e11;border-right:1px solid #282d34}}
QLabel#brand{{color:{CYAN};font-size:21pt;font-weight:800;letter-spacing:2px}}
QLabel#title{{font-size:24pt;font-weight:700;color:white}}
QLabel#muted{{color:{MUTED}}} QLabel#metric{{font-size:23pt;font-weight:700;color:white}}
QFrame#card{{background:{PANEL};border:1px solid #2b3038;border-radius:10px}}
QListWidget#nav{{background:transparent;border:0;outline:0}}
QListWidget#nav::item{{padding:12px;margin:2px;border-radius:7px;color:#b8c0ca}}
QListWidget#nav::item:selected{{background:#16383c;color:{CYAN};font-weight:700}}
QLineEdit,QComboBox{{background:{PANEL};border:1px solid #343b45;border-radius:6px;padding:8px}}
QPushButton{{background:#243038;color:{CYAN};border:1px solid #31515a;border-radius:6px;padding:8px 14px;font-weight:600}}
QTableWidget{{background:{PANEL};border:1px solid #2b3038;gridline-color:#292e35}}
QHeaderView::section{{background:#20252b;color:#aeb6c0;border:0;padding:8px;font-weight:700}}
"""

def main():
    app=QApplication(sys.argv); app.setApplicationName(APP_NAME); app.setStyle("Fusion"); app.setStyleSheet(STYLE)
    window=MainWindow(); window.show(); return app.exec()

if __name__=="__main__": raise SystemExit(main())
