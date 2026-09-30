from __future__ import annotations
import sys
import time
from pathlib import Path
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QSettings
from PySide6.QtGui import QPixmap, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import *
from config import APP_NAME, DEFAULT_SCAN_PATH, DATABASE_PATH, VERSION
from scanner import scan_folder, scan_textures
from bg_family_resolver import analyze_indexed_bg
from analysis_engine import rebuild_links, rebuild_families, rebuild_texture_families, rebuild_evidence, rebuild_texture_evidence
from database import Database
from gui_workspaces import AssetDialog, ComparePage, ConvertPage, KnowledgePage, SettingsPage, ThumbnailStudio, DiagnosticsPage, VehicleVariantsPage, IntelligencePage, texture_pixmap
from model_thumbnail import cached_thumbnail

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
        self.box.addWidget(self.table,1)
        self.scan_status=QLabel(""); self.scan_status.setObjectName("muted"); self.box.addWidget(self.scan_status); self.refresh()
    def refresh(self):
        s=self.db.relationship_stats()
        rc=self.db.review_counts(); vals=[self.db.count_models(),self.db.count_textures(),s.get("evidence_pairs",0)+s.get("texture_evidence_pairs",0),sum(v for k,v in rc.items() if k not in ("reviewed","dismissed"))]
        for c,v in zip(self.cards,vals): c.value.setText(f"{int(v or 0):,}")
        rows=self.db.top_evidence(30); self.table.setRowCount(len(rows))
        for r,x in enumerate(rows):
            for c,v in enumerate((x["overall_score"],x["path_a"],x["path_b"],x["reasons"])):
                self.table.setItem(r,c,QTableWidgetItem(str(v or "")))
        scans=self.db.recent_scan_history(1)
        if scans:
            x=scans[0]; self.scan_status.setText(f"Last {x['scan_type']} scan • found {int(x['found'] or 0):,} • scanned {int(x['scanned'] or 0):,} • skipped {int(x['skipped'] or 0):,} • errors {int(x['errors'] or 0):,} • {float(x['elapsed'] or 0):,.1f}s")
        else:self.scan_status.setText("No scan history recorded yet.")

class Browser(Page):
    def __init__(self,db,kind):
        title="Models" if kind=="model" else "Textures"
        super().__init__(title,"Search and inspect indexed assets"); self.db=db; self.kind=kind; self.page=0
        row=QHBoxLayout(); self.search=QLineEdit(); self.search.setPlaceholderText("Filename, folder, path or hash")
        b=QPushButton("Search"); b.clicked.connect(self.reset_search); self.search.returnPressed.connect(self.reset_search)
        self.limit=QComboBox(); self.limit.addItems(["250","500","1000","2500","5000"]); self.limit.setCurrentText("250")
        row.addWidget(self.search,1); row.addWidget(QLabel("Rows")); row.addWidget(self.limit); row.addWidget(b); self.box.addLayout(row)
        self.table=QTableWidget(0,6); self.table.setHorizontalHeaderLabels(["Preview","Filename","Folder","Path","Details","Status"]); self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSectionResizeMode(3,QHeaderView.Stretch); self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers); self.table.doubleClicked.connect(self.open_asset); self.table.itemSelectionChanged.connect(self.selection_preview)
        split=QSplitter(Qt.Horizontal); split.addWidget(self.table); preview_box=QFrame(); preview_box.setObjectName("card"); pv=QVBoxLayout(preview_box); self.preview=QLabel("Select an asset for preview"); self.preview.setAlignment(Qt.AlignCenter); self.preview.setWordWrap(True); self.preview.setMinimumWidth(260); pv.addWidget(self.preview,1); split.addWidget(preview_box); split.setSizes([1100,300]); self.box.addWidget(split,1)
        nav=QHBoxLayout(); self.prev=QPushButton("Previous"); self.next=QPushButton("Next"); self.info=QLabel("")
        self.prev.clicked.connect(lambda:self.change_page(-1)); self.next.clicked.connect(lambda:self.change_page(1))
        nav.addWidget(self.prev); nav.addWidget(self.next); nav.addWidget(self.info); nav.addStretch(); self.box.addLayout(nav); self.refresh()

    def reset_search(self): self.page=0; self.refresh()
    def change_page(self,d): self.page=max(0,self.page+d); self.refresh()

    def refresh(self):
        q=self.search.text().strip(); limit=int(self.limit.currentText())
        start=self.page*limit
        rows,total=self.db.search_models_page(q,limit,start) if self.kind=="model" else self.db.search_textures_page(q,limit,start)
        has_next=start+len(rows)<total
        self.table.setSortingEnabled(False)
        self.table.clearContents(); self.table.setRowCount(len(rows))
        for r,x in enumerate(rows):
            preview=QTableWidgetItem()
            if self.kind=="model":
                p=cached_thumbnail(x["path"],512)
                if p: preview.setIcon(QIcon(str(p))); preview.setToolTip("Cached LOD0 render")
                vals=(x["filename"],x["folder"],x["relative_path"],f"{int(x['size'] or 0):,} bytes",(x["sha256"] or "")[:16])
            else:
                vals=(x["filename"],x["folder"],x["relative_path"],f"{x['width'] or x['dds_width'] or 0} x {x['height'] or x['dds_height'] or 0}",x["dds_format"] or x["analysis_status"])
            self.table.setItem(r,0,preview)
            for col,v in enumerate(vals,1): self.table.setItem(r,col,QTableWidgetItem(str(v or "")))
            self.table.item(r,1).setData(Qt.UserRole,x["path"])
            self.table.setRowHeight(r,56)
        self.table.setSortingEnabled(True)
        self.prev.setEnabled(self.page>0); self.next.setEnabled(has_next)
        lo=start+1 if rows else 0; hi=start+len(rows); self.info.setText(f"Showing {lo:,}–{hi:,} of {total:,}")

    def selection_preview(self):
        row=self.table.currentRow()
        if row<0:return
        item=self.table.item(row,1)
        if not item:return
        path=item.data(Qt.UserRole)
        if self.kind=="texture":
            pix=texture_pixmap(path,300,300)
            if pix.isNull():self.preview.setText(Path(path).name + chr(10) + chr(10) + "Preview unavailable")
            else:self.preview.setPixmap(pix);self.preview.setToolTip(Path(path).name)
        else:
            p=cached_thumbnail(path,512)
            if p:
                pix=QPixmap(str(p));self.preview.setPixmap(pix.scaled(300,300,Qt.KeepAspectRatio,Qt.SmoothTransformation))
            else:self.preview.setText(Path(path).name + chr(10) + chr(10) + "Double-click → Model Render → Generate to cache a visual thumbnail.")

    def open_asset(self,index):
        item=self.table.item(index.row(),1)
        if item: AssetDialog(self.db,item.data(Qt.UserRole),self.kind,self).exec()

class Evidence(Page):
    def __init__(self,db):
        super().__init__("Evidence","Review model and texture similarity candidates"); self.db=db
        controls=QHBoxLayout(); self.mode=QComboBox(); self.mode.addItems(["Model Evidence","Texture Evidence"])
        self.minimum=QSpinBox(); self.minimum.setRange(0,100); self.minimum.setValue(50); self.limit=QComboBox(); self.limit.addItems(["250","500","1000","2500"]); self.limit.setCurrentText("1000")
        refresh=QPushButton("Refresh")
        for w in (self.mode,self.minimum,self.limit): 
            if hasattr(w,"currentIndexChanged"): w.currentIndexChanged.connect(self.refresh)
        self.minimum.valueChanged.connect(self.refresh); refresh.clicked.connect(self.refresh)
        controls.addWidget(self.mode); controls.addWidget(QLabel("Min score")); controls.addWidget(self.minimum); controls.addWidget(QLabel("Rows")); controls.addWidget(self.limit); controls.addStretch(); controls.addWidget(refresh); self.box.addLayout(controls)
        self.table=QTableWidget(0,5); self.table.setHorizontalHeaderLabels(["Score","Asset A","Asset B","Type","Reasons"]); self.table.setSortingEnabled(True)
        for i in (1,2,4): self.table.horizontalHeader().setSectionResizeMode(i,QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows); self.table.setEditTriggers(QAbstractItemView.NoEditTriggers); self.table.doubleClicked.connect(self.open_evidence_asset)
        self.box.addWidget(self.table,1); self.refresh()
    def refresh(self):
        limit=int(self.limit.currentText()); rows=self.db.top_evidence(limit) if self.mode.currentIndex()==0 else self.db.top_texture_evidence(limit)
        rows=[x for x in rows if int(x["overall_score"] or 0)>=self.minimum.value()]
        self.table.setSortingEnabled(False); self.table.setRowCount(len(rows))
        for r,x in enumerate(rows):
            for col,v in enumerate((x["overall_score"],x["path_a"],x["path_b"],x["evidence_type"],x["reasons"])):self.table.setItem(r,col,QTableWidgetItem(str(v or "")))
            self.table.item(r,1).setData(Qt.UserRole,x["path_a"]); self.table.item(r,2).setData(Qt.UserRole,x["path_b"])
        self.table.setSortingEnabled(True)
    def open_evidence_asset(self,index):
        col=1 if index.column()!=2 else 2; item=self.table.item(index.row(),col)
        if item:
            kind="model" if self.mode.currentIndex()==0 else "texture"; AssetDialog(self.db,item.data(Qt.UserRole),kind,self).exec()

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
        self.changed=changed; self.worker=None; self.started_at=0.0; self.sequence=[]; self.sequence_index=0; self.sequence_name=""; self.last_bg_result=None
        row=QHBoxLayout(); self.settings=QSettings("ThereInspector","ThereInspector"); self.root=QLineEdit(self.settings.value("resource_path",DEFAULT_SCAN_PATH)); browse=QPushButton("Browse"); browse.clicked.connect(self.browse)
        row.addWidget(QLabel("Resources")); row.addWidget(self.root,1); row.addWidget(browse); self.box.addLayout(row)

        card=QFrame(); card.setObjectName("card"); cb=QVBoxLayout(card)
        t=QLabel("COMPLETE ASSET SCAN"); t.setObjectName("muted"); cb.addWidget(t)
        d=QLabel("Scan models and textures, automatically resolve new/changed BG products, then rebuild links, families and evidence."); d.setWordWrap(True); d.setObjectName("muted"); cb.addWidget(d)
        buttons=QHBoxLayout()
        inc=QPushButton("Run Incremental Scan"); inc.clicked.connect(lambda:self.run_complete(False))
        full=QPushButton("Run Full Scan"); full.clicked.connect(lambda:self.run_complete(True))
        buttons.addWidget(inc); buttons.addWidget(full); cb.addLayout(buttons); self.box.addWidget(card)

        advanced=QGroupBox("Individual / Advanced Jobs"); grid=QGridLayout(advanced)
        jobs=[("Scan Models",lambda:self.start(scan_folder,self.root.text())),("Scan Textures",lambda:self.start(scan_textures,self.root.text())),
              ("Refresh BG Resolver",lambda:self.start(analyze_indexed_bg)),("Rebuild Links",lambda:self.start(rebuild_links)),
              ("Model Families",lambda:self.start(rebuild_families)),("Texture Families",lambda:self.start(rebuild_texture_families)),
              ("Model Evidence",lambda:self.start(rebuild_evidence)),("Texture Evidence",lambda:self.start(rebuild_texture_evidence))]
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
        root=self.root.text(); self.sequence_name="Full Scan" if full else "Incremental Scan"; self.last_bg_result=None
        self.sequence=[("Models",scan_folder,(root,),{"full_rescan":full}),("Textures",scan_textures,(root,),{"full_rescan":full}),
                       ("BG Resolver",analyze_indexed_bg,(),{}),
                       ("Links",rebuild_links,(),{}),("Model Families",rebuild_families,(),{}),("Texture Families",rebuild_texture_families,(),{}),
                       ("Model Evidence",rebuild_evidence,(),{}),("Texture Evidence",rebuild_texture_evidence,(),{})]
        self.sequence_index=0; self.start_sequence()

    def start_sequence(self):
        if self.sequence_index>=len(self.sequence):
            self.progress.setValue(100); self.percent_label.setText("100.0%"); self.job_label.setText(self.sequence_name+" complete")
            if self.last_bg_result and self.last_bg_result.get("ok"):
                bg=self.last_bg_result.get("continuous") or {}
                self.status.setText(
                    f"Complete • BG: {int(bg.get('affected',0)):,} new/changed • "
                    f"{int(bg.get('affected_resolved',0)):,} resolved • "
                    f"{int(bg.get('affected_needs_review',0)):,} new reviews • "
                    f"{int(bg.get('total_needs_review',0)):,} total need review"
                )
            else:self.status.setText("All scan and analysis stages completed.")
            self.timer.stop(); self.changed(); self.sequence=[]; return
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
        self.progress.setValue(100); self.percent_label.setText("100.0%")
        if self.sequence_index<len(self.sequence) and self.sequence[self.sequence_index][0]=="BG Resolver":
            self.last_bg_result=result
        self.sequence_index+=1; self.start_sequence()

    def done(self,result):
        self.progress.setValue(100); self.percent_label.setText("100.0%"); self.timer.stop()
        bg=result.get("continuous") if isinstance(result,dict) else None
        if bg:
            self.status.setText(
                f"BG resolver complete • {int(bg.get('affected',0)):,} new/changed • "
                f"{int(bg.get('affected_resolved',0)):,} resolved • "
                f"{int(bg.get('affected_needs_review',0)):,} new reviews • "
                f"{int(bg.get('total_needs_review',0)):,} total need review"
            )
        else:self.status.setText("Complete")
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
    PAGE_COUNT=14

    def __init__(self):
        super().__init__(); self.db=Database()
        self.setWindowTitle(f"{APP_NAME} {VERSION}"); self.resize(1450,900); self.setMinimumSize(1050,680)
        root=QWidget(); self.setCentralWidget(root); shell=QHBoxLayout(root); shell.setContentsMargins(0,0,0,0); shell.setSpacing(0)
        side=QFrame(); side.setObjectName("sidebar"); side.setFixedWidth(230); sb=QVBoxLayout(side); sb.setContentsMargins(18,22,18,18)
        brand=QLabel("THERE" + chr(10) + "INSPECTOR"); brand.setObjectName("brand"); sb.addWidget(brand)
        sub=QLabel("Asset Intelligence Suite"); sub.setObjectName("muted"); sb.addWidget(sub); sb.addSpacing(20)
        self.nav=QTreeWidget(); self.nav.setObjectName("nav"); self.nav.setHeaderHidden(True); self.nav.setIndentation(14)
        nav_groups=[
            ("Home",[("Dashboard",0)]),
            ("Assets",[("Models",1),("Textures",2),("Visual Library",3),("Product Variants",4)]),
            ("Forensics",[("Intelligence",5),("Evidence",6),("Review Queue",7),("Compare",8)]),
            ("Tools",[("Convert",9),("Knowledge",10),("Scan & Analysis",11)]),
            ("System",[("Diagnostics",12),("Settings",13)]),
        ]
        self.nav_items={}
        for group,items in nav_groups:
            parent=QTreeWidgetItem([group]); parent.setFlags(parent.flags() & ~Qt.ItemIsSelectable); self.nav.addTopLevelItem(parent)
            for label,index in items:
                child=QTreeWidgetItem([label]); child.setData(0,Qt.UserRole,index); parent.addChild(child); self.nav_items[index]=child
            parent.setExpanded(True)
        sb.addWidget(self.nav,1)
        db_label=QLabel("Database" + chr(10) + str(DATABASE_PATH)); db_label.setWordWrap(True); db_label.setObjectName("muted"); db_label.setToolTip(str(DATABASE_PATH))
        sb.addWidget(db_label); shell.addWidget(side)

        self.stack=QStackedWidget()
        self._pages={}
        self._dirty_pages=set()
        self._page_factories={
            0:lambda:Dashboard(self.db),
            1:lambda:Browser(self.db,"model"),
            2:lambda:Browser(self.db,"texture"),
            3:lambda:ThumbnailStudio(self.db),
            4:lambda:VehicleVariantsPage(self.db),
            5:lambda:IntelligencePage(self.db),
            6:lambda:Evidence(self.db),
            7:lambda:ReviewQueue(self.db),
            8:lambda:ComparePage(self.db),
            9:lambda:ConvertPage(),
            10:lambda:KnowledgePage(self.db),
            11:lambda:Analysis(self.refresh_all),
            12:lambda:DiagnosticsPage(self.db),
            13:lambda:SettingsPage(self.db),
        }
        for _ in range(self.PAGE_COUNT):
            holder=QWidget(); lay=QVBoxLayout(holder); lay.addStretch()
            msg=QLabel("Workspace loads when opened"); msg.setAlignment(Qt.AlignCenter); msg.setObjectName("muted")
            lay.addWidget(msg); lay.addStretch(); self.stack.addWidget(holder)
        self._ensure_page(0)
        shell.addWidget(self.stack,1)

        self.nav.currentItemChanged.connect(self._nav_changed)
        self.nav.setCurrentItem(self.nav_items[0])
        self.statusBar().showMessage(f"{self.db.count_models():,} models • {self.db.count_textures():,} textures • {DATABASE_PATH}")
        QShortcut(QKeySequence("F5"),self,activated=self.refresh_all)
        QShortcut(QKeySequence("Ctrl+1"),self,activated=lambda:self.select_page(0))
        QShortcut(QKeySequence("Ctrl+2"),self,activated=lambda:self.select_page(1))
        QShortcut(QKeySequence("Ctrl+3"),self,activated=lambda:self.select_page(2))

    def _ensure_page(self,index):
        page=self._pages.get(index)
        if page is None:
            factory=self._page_factories.get(index)
            if factory is None:return None
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                page=factory()
                old=self.stack.widget(index)
                self.stack.removeWidget(old); old.deleteLater()
                self.stack.insertWidget(index,page)
                self._pages[index]=page
            finally:
                QApplication.restoreOverrideCursor()
        elif index in self._dirty_pages and hasattr(page,"refresh"):
            page.refresh()
        self._dirty_pages.discard(index)
        return page

    def _nav_changed(self,current,previous):
        if current is None:return
        index=current.data(0,Qt.UserRole)
        if index is None:return
        self._ensure_page(int(index))
        self.stack.setCurrentIndex(int(index))

    def select_page(self,index):
        item=self.nav_items.get(index)
        if item:self.nav.setCurrentItem(item)

    def refresh_all(self):
        current=self.stack.currentIndex()
        for index in list(self._pages):
            if index not in (0,current):self._dirty_pages.add(index)
        for index in dict.fromkeys((0,current)):
            page=self._pages.get(index)
            if page is not None and hasattr(page,"refresh"):page.refresh()
            self._dirty_pages.discard(index)
        self.statusBar().showMessage(f"{self.db.count_models():,} models • {self.db.count_textures():,} textures • {DATABASE_PATH}")

    def closeEvent(self,event):
        self.db.close(); super().closeEvent(event)

STYLE=f"""
QWidget{{background:{BG};color:{TEXT};font-family:'Segoe UI';font-size:10pt}}
QFrame#sidebar{{background:#0c0e11;border-right:1px solid #282d34}}
QLabel#brand{{color:{CYAN};font-size:21pt;font-weight:800;letter-spacing:2px}}
QLabel#title{{font-size:24pt;font-weight:700;color:white}}
QLabel#muted{{color:{MUTED}}} QLabel#metric{{font-size:23pt;font-weight:700;color:white}}
QFrame#card{{background:{PANEL};border:1px solid #2b3038;border-radius:10px}}
QTreeWidget#nav{{background:transparent;border:0;outline:0}}
QTreeWidget#nav::item{{padding:7px 8px;margin:1px;border-radius:6px;color:#b8c0ca}}
QTreeWidget#nav::item:selected{{background:#16383c;color:{CYAN};font-weight:700}}
QTreeWidget#nav::branch{{background:transparent}}
QLineEdit,QComboBox{{background:{PANEL};border:1px solid #343b45;border-radius:6px;padding:8px}}
QPushButton{{background:#243038;color:{CYAN};border:1px solid #31515a;border-radius:6px;padding:8px 14px;font-weight:600}}
QTableWidget{{background:{PANEL};border:1px solid #2b3038;gridline-color:#292e35}}
QHeaderView::section{{background:#20252b;color:#aeb6c0;border:0;padding:8px;font-weight:700}}
"""

def main():
    app=QApplication(sys.argv); app.setApplicationName(APP_NAME); app.setStyle("Fusion"); app.setStyleSheet(STYLE)
    window=MainWindow(); window.show(); return app.exec()

if __name__=="__main__": raise SystemExit(main())
