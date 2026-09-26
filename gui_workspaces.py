from __future__ import annotations
import json, os, subprocess
from pathlib import Path
from PySide6.QtCore import Qt, QSettings, QThread, Signal
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtWidgets import *
from config import APP_NAME, DATABASE_PATH, DEFAULT_SCAN_PATH
from model_converter import SUPPORTED_OUTPUTS, conversion_readiness, execute_conversion_job, inspect_conversion_source, prepare_conversion_job
from model_thumbnail import cached_thumbnail, render_model_thumbnail, purge_thumbnail_cache, thumbnail_failure_count

def texture_pixmap(path, max_w=560, max_h=440):
    try:
        from PIL import Image
        img=Image.open(path); img.load(); rgba=img.convert("RGBA")
        data=rgba.tobytes("raw","RGBA")
        q=QImage(data,rgba.width,rgba.height,QImage.Format_RGBA8888).copy()
        return QPixmap.fromImage(q).scaled(max_w,max_h,Qt.KeepAspectRatio,Qt.SmoothTransformation)
    except Exception:
        pix=QPixmap(str(path))
        if not pix.isNull(): return pix.scaled(max_w,max_h,Qt.KeepAspectRatio,Qt.SmoothTransformation)
    return QPixmap()

def texture_diff_pixmap(path_a,path_b,max_w=900,max_h=220):
    try:
        from PIL import Image, ImageChops, ImageEnhance, ImageStat
        a=Image.open(path_a).convert("RGBA"); b=Image.open(path_b).convert("RGBA")
        if a.size!=b.size:b=b.resize(a.size,Image.Resampling.LANCZOS)
        diff=ImageChops.difference(a,b)
        stat=ImageStat.Stat(diff.convert("RGB")); mean=sum(stat.mean)/3.0
        similarity=max(0.0,100.0*(1.0-mean/255.0))
        shown=ImageEnhance.Contrast(diff).enhance(2.0)
        data=shown.tobytes("raw","RGBA");q=QImage(data,shown.width,shown.height,QImage.Format_RGBA8888).copy()
        return QPixmap.fromImage(q).scaled(max_w,max_h,Qt.KeepAspectRatio,Qt.SmoothTransformation),similarity
    except Exception:return QPixmap(),None

def reveal(p):
    try: subprocess.Popen(["explorer","/select,",str(Path(p))]) if os.name=="nt" else None
    except Exception: pass

class Task(QThread):
    done=Signal(object); failed=Signal(str)
    def __init__(self,fn,*args): super().__init__(); self.fn=fn; self.args=args
    def run(self):
        try:self.done.emit(self.fn(*self.args))
        except Exception as e:self.failed.emit(f"{type(e).__name__}: {e}")

class TextureThumb(QFrame):
    def __init__(self,path,title="",subtitle="",parent=None):
        super().__init__(parent); self.path=str(path); self.setObjectName("card"); self.setMinimumWidth(190); self.setMaximumWidth(260)
        b=QVBoxLayout(self); image=QLabel(); image.setAlignment(Qt.AlignCenter); image.setMinimumSize(170,150)
        pix=texture_pixmap(self.path,220,180)
        if pix.isNull(): image.setText("No preview")
        else:image.setPixmap(pix)
        b.addWidget(image)
        name=QLabel(title or Path(self.path).name); name.setWordWrap(True); name.setAlignment(Qt.AlignCenter); b.addWidget(name)
        if subtitle:
            sub=QLabel(subtitle); sub.setWordWrap(True); sub.setAlignment(Qt.AlignCenter); sub.setStyleSheet("color:#8f98a3"); b.addWidget(sub)

def texture_gallery(items):
    area=QScrollArea(); area.setWidgetResizable(True); host=QWidget(); grid=QGridLayout(host); grid.setAlignment(Qt.AlignTop|Qt.AlignLeft)
    for i,item in enumerate(items):
        grid.addWidget(TextureThumb(item["path"],item.get("title",""),item.get("subtitle","")),i//4,i%4)
    if not items:grid.addWidget(QLabel("No linked textures available for visual preview."),0,0)
    area.setWidget(host); return area

class AssetDialog(QDialog):
    def __init__(self,db,path,kind,parent=None):
        super().__init__(parent); self.db=db; self.path=path; self.kind=kind; self.resize(1000,720); self.setWindowTitle("Asset Profile")
        box=QVBoxLayout(self); row=db.model_by_query(path) if kind=="model" else db.texture_by_query(path)
        if not row: box.addWidget(QLabel("Asset not found.")); return
        h=QLabel(row["filename"]); h.setObjectName("title"); box.addWidget(h); box.addWidget(QLabel(row["relative_path"]))
        tabs=QTabWidget(); box.addWidget(tabs,1)
        visual_items=[]
        if kind=="texture":
            visual_items=[{"path":row["path"],"title":row["filename"],"subtitle":f"{row['width'] or row['dds_width'] or 0} × {row['height'] or row['dds_height'] or 0}"}]
        else:
            model_visual=QWidget(); mv=QVBoxLayout(model_visual); self.model_preview=QLabel(); self.model_preview.setAlignment(Qt.AlignCenter); self.model_preview.setMinimumHeight(420)
            cached=cached_thumbnail(row["path"])
            if cached:
                pix=QPixmap(str(cached)); self.model_preview.setPixmap(pix.scaled(700,420,Qt.KeepAspectRatio,Qt.SmoothTransformation))
            else:self.model_preview.setText("No cached model render yet. Generate a LOD0 thumbnail to create one.")
            mv.addWidget(self.model_preview,1); mr=QHBoxLayout(); render=QPushButton("Generate / Refresh Model Render"); render.clicked.connect(lambda:self.render_model(row))
            mr.addStretch();mr.addWidget(render);mr.addStretch();mv.addLayout(mr);tabs.addTab(model_visual,"Model Render")
            for link in db.links_for_model(row["path"],24):
                visual_items.append({"path":link["texture_path"],"title":Path(link["texture_path"]).name,"subtitle":f"Link score {link['score']}"})
        tabs.addTab(texture_gallery(visual_items),"Visual")
        over=QWidget(); form=QFormLayout(over)
        keys=["folder","size","sha256","som_version","filename_type"] if kind=="model" else ["folder","size","sha256","width","height","dds_format","analysis_status","ahash"]
        for k in keys:
            if k in row.keys(): form.addRow(k.replace("_"," ").title(),QLabel(str(row[k] or "")))
        tabs.addTab(over,"Overview")
        rel=QTableWidget(); links=db.links_for_model(row["path"],100) if kind=="model" else db.links_for_texture(row["path"],100)
        rel.setColumnCount(3); rel.setHorizontalHeaderLabels(["Score","Related Asset","Details"]); rel.setRowCount(len(links))
        for i,x in enumerate(links):
            vals=(x["score"],x["texture_relative_path"],x["dds_format"]) if kind=="model" else (x["score"],x["model_relative_path"],x["folder"])
            for j,v in enumerate(vals):rel.setItem(i,j,QTableWidgetItem(str(v or "")))
        rel.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch); tabs.addTab(rel,"Relationships")
        ev=QTableWidget(); rows=db.evidence_for_model(row["path"],100) if kind=="model" else db.texture_evidence_for_texture(row["path"],100)
        ev.setColumnCount(4); ev.setHorizontalHeaderLabels(["Score","Asset A","Asset B","Reasons"]); ev.setRowCount(len(rows))
        for i,x in enumerate(rows):
            for j,v in enumerate((x["overall_score"],x["path_a"],x["path_b"],x["reasons"])):ev.setItem(i,j,QTableWidgetItem(str(v or "")))
        for j in (1,2,3):ev.horizontalHeader().setSectionResizeMode(j,QHeaderView.Stretch)
        tabs.addTab(ev,"Evidence")
        similar=QScrollArea();similar.setWidgetResizable(True);sh=QWidget();sg=QGridLayout(sh);sg.setAlignment(Qt.AlignTop|Qt.AlignLeft);seen=set();shown=0
        for x in rows[:24]:
            other=x["path_b"] if x["path_a"]==row["path"] else x["path_a"]
            if other in seen:continue
            seen.add(other);card=QFrame();card.setObjectName("card");cv=QVBoxLayout(card);im=QLabel();im.setAlignment(Qt.AlignCenter);im.setMinimumSize(180,145)
            if kind=="texture":pix=texture_pixmap(other,210,160)
            else:
                cp=cached_thumbnail(other,512);pix=QPixmap(str(cp)).scaled(210,160,Qt.KeepAspectRatio,Qt.SmoothTransformation) if cp else QPixmap()
            if pix.isNull():im.setText("No visual cached")
            else:im.setPixmap(pix)
            cv.addWidget(im);nm=QLabel(Path(other).name);nm.setWordWrap(True);nm.setAlignment(Qt.AlignCenter);cv.addWidget(nm);sc=QLabel(f"Evidence {x['overall_score']}");sc.setAlignment(Qt.AlignCenter);cv.addWidget(sc)
            sg.addWidget(card,shown//4,shown%4);shown+=1
        if not shown:sg.addWidget(QLabel("No visual evidence candidates available."),0,0)
        similar.setWidget(sh);tabs.addTab(similar,"Similar Visuals")
        review=QWidget(); vb=QVBoxLayout(review); current=db.get_asset_review(row["path"])
        rr=QHBoxLayout(); self.status=QComboBox(); self.status.addItems(["new","reviewing","reviewed","dismissed","confirmed"]); self.priority=QComboBox(); self.priority.addItems(["low","normal","high","critical"])
        if current:self.status.setCurrentText(current["status"] or "new"); self.priority.setCurrentText(current["priority"] or "normal")
        rr.addWidget(QLabel("Status"));rr.addWidget(self.status);rr.addWidget(QLabel("Priority"));rr.addWidget(self.priority);rr.addStretch();vb.addLayout(rr)
        self.tags=QLineEdit(", ".join(db.tags_for_asset(row["path"])));self.tags.setPlaceholderText("tags, comma separated");vb.addWidget(self.tags)
        self.note=QPlainTextEdit();self.note.setPlaceholderText("Add investigation note...");vb.addWidget(self.note)
        hist=QListWidget()
        for n in db.notes_for_asset(row["path"],25):hist.addItem(str(n["note"]))
        vb.addWidget(hist,1);save=QPushButton("Save Review");save.clicked.connect(lambda:self.save(row["path"]));vb.addWidget(save);tabs.addTab(review,"Review")
        actions=QHBoxLayout(); show=QPushButton("Show File in Explorer"); show.clicked.connect(lambda:reveal(row["path"])); actions.addWidget(show); actions.addStretch()
        close=QDialogButtonBox(QDialogButtonBox.Close);close.rejected.connect(self.reject);actions.addWidget(close);box.addLayout(actions)
    def render_model(self,row):
        links=self.db.links_for_model(row["path"],100); textures=[x["texture_path"] for x in links if x["texture_path"]]
        self.model_preview.setText("Rendering LOD0 in Blender…"); QApplication.processEvents()
        self.render_task=Task(render_model_thumbnail,row["path"],textures,512,True)
        self.render_task.done.connect(self.render_finished); self.render_task.failed.connect(lambda e:QMessageBox.critical(self,APP_NAME,e)); self.render_task.start()
    def render_finished(self,result):
        if result.get("success"):
            pix=QPixmap(result["output"]); self.model_preview.setPixmap(pix.scaled(700,420,Qt.KeepAspectRatio,Qt.SmoothTransformation))
        else:
            self.model_preview.setText("Render failed."); QMessageBox.warning(self,APP_NAME,result.get("message") or result.get("log","Unknown render error")[-1500:])
    def save(self,path):
        self.db.set_asset_review(path,self.status.currentText(),self.priority.currentText())
        old=set(self.db.tags_for_asset(path));new={x.strip().lower() for x in self.tags.text().split(",") if x.strip()}
        for x in new-old:self.db.add_asset_tag(path,x)
        for x in old-new:self.db.remove_asset_tag(path,x)
        if self.note.toPlainText().strip():self.db.add_asset_note(path,self.note.toPlainText());self.note.clear()
        QMessageBox.information(self,APP_NAME,"Review saved.")

class ThumbnailBatchTask(QThread):
    progress=Signal(int,int,str); done=Signal(object)
    def __init__(self,models,force=False):
        super().__init__();self.models=models;self.force=force
    def run(self):
        ok=failed=cached=0
        for i,item in enumerate(self.models,1):
            row,textures=item
            self.progress.emit(i,len(self.models),row["filename"])
            if cached_thumbnail(row["path"]) and not self.force:
                cached+=1;continue
            try:
                r=render_model_thumbnail(row["path"],textures,512,self.force)
                if r.get("success"):ok+=1
                else:failed+=1
            except Exception:failed+=1
        self.done.emit({"rendered":ok,"cached":cached,"failed":failed,"total":len(self.models)})

class ThumbnailStudio(QWidget):
    def __init__(self,db):
        super().__init__();self.db=db;self.task=None;self.cards=[];b=QVBoxLayout(self);b.setContentsMargins(28,24,28,24)
        h=QLabel("Visual Library");h.setObjectName("title");b.addWidget(h);b.addWidget(QLabel("Build, browse and manage cached LOD0 renders for the indexed model library."))
        r=QHBoxLayout();self.search=QLineEdit();self.search.setPlaceholderText("Optional filename/folder/path filter");self.search.returnPressed.connect(self.refresh)
        self.count=QComboBox();self.count.addItems(["25","50","100","250","500","1000"]);self.count.setCurrentText("100")
        self.view=QComboBox();self.view.addItems(["Cached only","All models","Missing renders"])
        go=QPushButton("Render Missing");go.clicked.connect(self.start);refresh=QPushButton("Refresh Gallery");refresh.clicked.connect(self.refresh)
        for w in (self.search,QLabel("Batch"),self.count,QLabel("View"),self.view,go,refresh):r.addWidget(w)
        self.view.currentIndexChanged.connect(self.refresh);self.count.currentIndexChanged.connect(self.refresh)
        b.addLayout(r);self.progress=QProgressBar();self.status=QLabel("Ready");b.addWidget(self.progress);b.addWidget(self.status)
        self.area=QScrollArea();self.area.setWidgetResizable(True);b.addWidget(self.area,1);self.refresh()

    def make_card(self,row,p):
        card=QFrame();card.setObjectName("card");card.setMinimumWidth(225);v=QVBoxLayout(card)
        im=QLabel();im.setAlignment(Qt.AlignCenter);im.setMinimumSize(210,170)
        if p:
            pix=QPixmap(str(p));im.setPixmap(pix.scaled(210,170,Qt.KeepAspectRatio,Qt.SmoothTransformation))
        else:im.setText("No cached render")
        v.addWidget(im)
        n=QLabel(row["filename"]);n.setWordWrap(True);n.setAlignment(Qt.AlignCenter);v.addWidget(n)
        buttons=QHBoxLayout();open_b=QPushButton("Open");open_b.clicked.connect(lambda _,path=row["path"]:AssetDialog(self.db,path,"model",self).exec());buttons.addWidget(open_b)
        if p:
            delete=QPushButton("Remove Render");delete.clicked.connect(lambda _,path=row["path"]:self.remove_render(path));buttons.addWidget(delete)
        else:
            render=QPushButton("Render");render.clicked.connect(lambda _,rr=dict(row):self.render_one(rr));buttons.addWidget(render)
        v.addLayout(buttons);return card

    def refresh(self):
        rows,total=self.db.search_models_page(self.search.text().strip(),int(self.count.currentText()),0)
        host=QWidget();grid=QGridLayout(host);grid.setAlignment(Qt.AlignTop|Qt.AlignLeft);shown=0;cached_count=missing_count=0
        mode=self.view.currentText()
        for row in rows:
            p=cached_thumbnail(row["path"])
            if p:cached_count+=1
            else:missing_count+=1
            if mode=="Cached only" and not p:continue
            if mode=="Missing renders" and p:continue
            grid.addWidget(self.make_card(row,p),shown//4,shown%4);shown+=1
        if not shown:grid.addWidget(QLabel("No models match this view."),0,0)
        self.area.setWidget(host)
        self.status.setText(f"{shown:,} shown • {cached_count:,} cached • {missing_count:,} missing in current selection • {total:,} matching models")

    def remove_render(self,path):
        p=cached_thumbnail(path)
        if not p:return
        answer=QMessageBox.question(self,APP_NAME,f"Remove cached render for {Path(path).name}?

The model and database record will not be changed.",QMessageBox.Yes|QMessageBox.No)
        if answer==QMessageBox.Yes:
            Path(p).unlink(missing_ok=True);self.refresh()

    def render_one(self,row):
        links=self.db.links_for_model(row["path"],100);textures=[x["texture_path"] for x in links if x["texture_path"]]
        self.progress.setRange(0,1);self.progress.setValue(0);self.status.setText(f"Rendering {row['filename']}…")
        self.task=ThumbnailBatchTask([(row,textures)],False);self.task.progress.connect(self.on_progress);self.task.done.connect(self.finished);self.task.start()

    def start(self):
        rows,_=self.db.search_models_page(self.search.text().strip(),int(self.count.currentText()),0)
        if not rows:return
        items=[]
        for row in rows:
            if cached_thumbnail(row["path"]):continue
            links=self.db.links_for_model(row["path"],100);items.append((dict(row),[x["texture_path"] for x in links if x["texture_path"]]))
        if not items:
            self.status.setText("All models in this batch already have cached renders.");return
        self.progress.setRange(0,len(items));self.progress.setValue(0);self.status.setText(f"Starting {len(items):,} missing renders…")
        self.task=ThumbnailBatchTask(items,False);self.task.progress.connect(self.on_progress);self.task.done.connect(self.finished);self.task.start()
    def on_progress(self,i,total,name):
        self.progress.setValue(i);self.status.setText(f"Rendering {i:,} / {total:,} • {name}")
    def finished(self,result):
        self.progress.setValue(self.progress.maximum());self.refresh()
        self.status.setText(f"Complete • rendered {result['rendered']:,} • already cached {result['cached']:,} • failed {result['failed']:,} • {result['total']:,} processed")

class ComparePage(QWidget):
    def __init__(self,db):
        super().__init__();self.db=db;b=QVBoxLayout(self);b.setContentsMargins(28,24,28,24)
        h=QLabel("Compare");h.setObjectName("title");b.addWidget(h);b.addWidget(QLabel("Side-by-side visual and forensic comparison"))
        r=QHBoxLayout();self.kind=QComboBox();self.kind.addItems(["Models","Textures"]);self.a=QLineEdit();self.b=QLineEdit()
        self.a.setPlaceholderText("Asset A filename/path");self.b.setPlaceholderText("Asset B filename/path");go=QPushButton("Compare");go.clicked.connect(self.compare)
        for w in (self.kind,self.a,self.b,go):r.addWidget(w)
        b.addLayout(r)
        visual=QHBoxLayout();self.left_preview=QLabel("Asset A preview");self.right_preview=QLabel("Asset B preview")
        for p in (self.left_preview,self.right_preview):
            p.setAlignment(Qt.AlignCenter);p.setMinimumHeight(240);p.setStyleSheet("background:#181b20;border:1px solid #2b3038;border-radius:8px")
        visual.addWidget(self.left_preview,1);visual.addWidget(self.right_preview,1);b.addLayout(visual)
        self.diff_preview=QLabel("Texture difference view");self.diff_preview.setAlignment(Qt.AlignCenter);self.diff_preview.setMinimumHeight(110)
        self.diff_preview.setStyleSheet("background:#181b20;border:1px solid #2b3038;border-radius:8px");b.addWidget(self.diff_preview)
        self.table=QTableWidget(0,3);self.table.setHorizontalHeaderLabels(["Property","Asset A","Asset B"])
        self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch);self.table.horizontalHeader().setSectionResizeMode(2,QHeaderView.Stretch);b.addWidget(self.table,1)
    def compare(self):
        model=self.kind.currentIndex()==0
        a=self.db.model_by_query(self.a.text()) if model else self.db.texture_by_query(self.a.text())
        bb=self.db.model_by_query(self.b.text()) if model else self.db.texture_by_query(self.b.text())
        if not a or not bb:
            QMessageBox.warning(self,APP_NAME,"Could not find both assets.");return
        if not model:
            pa=texture_pixmap(a["path"],520,240);pb=texture_pixmap(bb["path"],520,240)
            self.left_preview.setPixmap(pa) if not pa.isNull() else self.left_preview.setText("Preview unavailable")
            self.right_preview.setPixmap(pb) if not pb.isNull() else self.right_preview.setText("Preview unavailable")
            dp,sim=texture_diff_pixmap(a["path"],bb["path"])
            if not dp.isNull():
                self.diff_preview.setPixmap(dp);self.diff_preview.setToolTip(f"Mean pixel similarity: {sim:.2f}%")
            else:self.diff_preview.setText("Difference preview unavailable")
        else:
            self.diff_preview.setText("Pixel difference view applies to textures.")
            for label,row in ((self.left_preview,a),(self.right_preview,bb)):
                p=cached_thumbnail(row["path"],512)
                if p:
                    pix=QPixmap(str(p));label.setPixmap(pix.scaled(520,240,Qt.KeepAspectRatio,Qt.SmoothTransformation))
                else:label.setText("No cached model render. Open Asset Profile → Model Render to generate.")
        keys=["filename","folder","size","sha256"]+(["som_version","string_fingerprint","prefix_4k_sha256","middle_4k_sha256","suffix_4k_sha256"] if model else ["width","height","dds_format","ahash","histogram_hash","avg_r","avg_g","avg_b","alpha_coverage","edge_density"])
        self.table.setRowCount(len(keys))
        for i,k in enumerate(keys):
            for j,v in enumerate((k,str(a[k] or ""),str(bb[k] or ""))):self.table.setItem(i,j,QTableWidgetItem(v))

class ConvertPage(QWidget):
    def __init__(self):
        super().__init__();self.task=None;b=QVBoxLayout(self);b.setContentsMargins(28,24,28,24);h=QLabel("Convert");h.setObjectName("title");b.addWidget(h)
        r=conversion_readiness();b.addWidget(QLabel(f"Decoder: {r.geometry_decoder_name}  •  Blender: {r.blender_path or 'not detected'}"))
        row=QHBoxLayout();self.source=QLineEdit();browse=QPushButton("Browse");browse.clicked.connect(self.browse);row.addWidget(self.source,1);row.addWidget(browse);b.addLayout(row)
        opts=QHBoxLayout();self.format=QComboBox();self.format.addItems(list(SUPPORTED_OUTPUTS));self.format.setCurrentText("blend");inspect=QPushButton("Inspect");inspect.clicked.connect(self.inspect);convert=QPushButton("Convert");convert.clicked.connect(self.convert)
        for w in (QLabel("Output"),self.format,inspect,convert):opts.addWidget(w)
        opts.addStretch();b.addLayout(opts);self.details=QPlainTextEdit();self.details.setReadOnly(True);b.addWidget(self.details,1)
    def browse(self):
        p,_=QFileDialog.getOpenFileName(self,"Select There model","","There Models (*.model)")
        if p:self.source.setText(p)
    def inspect(self):
        try:self.details.setPlainText(json.dumps(inspect_conversion_source(self.source.text()),indent=2,default=str))
        except Exception as e:QMessageBox.critical(self,APP_NAME,str(e))
    def convert(self):
        try:job=prepare_conversion_job(self.source.text(),self.format.currentText())
        except Exception as e:QMessageBox.critical(self,APP_NAME,str(e));return
        self.details.setPlainText("Converting...");self.task=Task(execute_conversion_job,job);self.task.done.connect(self.converted);self.task.failed.connect(lambda e:QMessageBox.critical(self,APP_NAME,e));self.task.start()
    def converted(self,result):
        self.details.setPlainText(json.dumps(result,indent=2,default=str))
        if result.get("success") and result.get("output") and QMessageBox.question(self,APP_NAME,"Conversion complete. Show output?")==QMessageBox.Yes:reveal(result["output"])

class KnowledgePage(QWidget):
    def __init__(self,db):
        super().__init__();self.db=db;b=QVBoxLayout(self);b.setContentsMargins(28,24,28,24);h=QLabel("Knowledge");h.setObjectName("title");b.addWidget(h)
        r=QHBoxLayout();self.type=QLineEdit();self.key=QLineEdit();self.value=QLineEdit();self.type.setPlaceholderText("rule type");self.key.setPlaceholderText("key");self.value.setPlaceholderText("value");add=QPushButton("Add / Update");add.clicked.connect(self.add)
        for w in (self.type,self.key,self.value,add):r.addWidget(w)
        b.addLayout(r);self.table=QTableWidget(0,5);self.table.setHorizontalHeaderLabels(["Type","Key","Value","Confidence","Notes"]);self.table.horizontalHeader().setSectionResizeMode(2,QHeaderView.Stretch);self.table.horizontalHeader().setSectionResizeMode(4,QHeaderView.Stretch);b.addWidget(self.table,1);self.refresh()
    def refresh(self):
        rows=self.db.knowledge_rules(limit=500);self.table.setRowCount(len(rows))
        for i,x in enumerate(rows):
            for j,v in enumerate((x["rule_type"],x["key"],x["value"],x["confidence"],x["notes"])):self.table.setItem(i,j,QTableWidgetItem(str(v or "")))
    def add(self):
        if self.type.text().strip() and self.key.text().strip():self.db.upsert_knowledge_rule(self.type.text().strip(),self.key.text().strip(),self.value.text().strip(),source="GUI");self.refresh()

class DiagnosticsPage(QWidget):
    def __init__(self,db):
        super().__init__();self.db=db;b=QVBoxLayout(self);b.setContentsMargins(28,24,28,24)
        h=QLabel("Diagnostics");h.setObjectName("title");b.addWidget(h);b.addWidget(QLabel("Environment, database and rendering readiness checks"))
        self.table=QTableWidget(0,3);self.table.setHorizontalHeaderLabels(["Check","Status","Details"]);self.table.horizontalHeader().setSectionResizeMode(2,QHeaderView.Stretch);b.addWidget(self.table,1)
        r=QHBoxLayout();run=QPushButton("Run Checks");run.clicked.connect(self.refresh);r.addWidget(run);r.addStretch();b.addLayout(r);self.refresh()
    def refresh(self):
        checks=[]
        checks.append(("Database","OK" if Path(DATABASE_PATH).exists() else "WARN",f"{DATABASE_PATH} • {self.db.count_models():,} models • {self.db.count_textures():,} textures"))
        ready=conversion_readiness();checks.append(("Native decoder","OK" if ready.geometry_decoder_available else "FAIL",ready.geometry_decoder_name))
        checks.append(("Blender","OK" if ready.blender_found else "WARN",ready.blender_path or "Not detected; model renders and BLEND/GLB conversion unavailable"))
        try:
            from PIL import features
            checks.append(("Pillow","OK","Image preview engine available"))
        except Exception as e:checks.append(("Pillow","FAIL",str(e)))
        cache=Path("cache/model_thumbnails");n=len(list(cache.glob("*.png"))) if cache.exists() else 0
        failures=thumbnail_failure_count();checks.append(("Visual cache","OK" if not failures else "WARN",f"{n:,} cached model renders • {failures:,} logged render failures"))
        try:
            self.db.db.execute("PRAGMA quick_check").fetchone();checks.append(("SQLite quick check","OK","Database responded successfully"))
        except Exception as e:checks.append(("SQLite quick check","FAIL",str(e)))
        self.table.setRowCount(len(checks))
        for i,row in enumerate(checks):
            for j,v in enumerate(row):self.table.setItem(i,j,QTableWidgetItem(str(v)))

class SettingsPage(QWidget):
    def __init__(self,db=None):
        super().__init__();self.db=db;self.settings=QSettings("ThereInspector","ThereInspector");b=QVBoxLayout(self);b.setContentsMargins(28,24,28,24);h=QLabel("Settings");h.setObjectName("title");b.addWidget(h);f=QFormLayout();self.resource=QLineEdit(self.settings.value("resource_path",DEFAULT_SCAN_PATH));self.blender=QLineEdit(self.settings.value("blender_path",""));f.addRow("Resource folder",self.resource);f.addRow("Blender executable",self.blender);f.addRow("Database",QLabel(str(DATABASE_PATH)));b.addLayout(f);buttons=QHBoxLayout();save=QPushButton("Save Settings");save.clicked.connect(self.save);export=QPushButton("Export Diagnostic Snapshot");export.clicked.connect(self.export_snapshot);clear=QPushButton("Clear Model Thumbnail Cache");clear.clicked.connect(self.clear_cache);buttons.addWidget(save);buttons.addWidget(export);buttons.addWidget(clear);buttons.addStretch();b.addLayout(buttons);b.addStretch()
    def save(self):
        self.settings.setValue("resource_path",self.resource.text());self.settings.setValue("blender_path",self.blender.text())
        if self.blender.text().strip():os.environ["BLENDER_EXE"]=self.blender.text().strip()
        QMessageBox.information(self,APP_NAME,"Settings saved.")

    def clear_cache(self):
        n=purge_thumbnail_cache(); QMessageBox.information(self,APP_NAME,f"Removed {n:,} cached model thumbnails.")
    def export_snapshot(self):
        if not self.db:return
        path,_=QFileDialog.getSaveFileName(self,"Export diagnostic snapshot","inspector_diagnostic.json","JSON (*.json)")
        if not path:return
        data={"database":str(DATABASE_PATH),"models":self.db.count_models(),"textures":self.db.count_textures(),
              "relationships":self.db.relationship_stats(),"reviews":self.db.review_counts(),
              "recent_scans":[dict(x) for x in self.db.recent_scan_history(20)],
              "recent_analysis":[dict(x) for x in self.db.recent_analysis_runs(20)]}
        Path(path).write_text(json.dumps(data,indent=2,default=str),encoding="utf-8")
        QMessageBox.information(self,APP_NAME,"Diagnostic snapshot exported.")
