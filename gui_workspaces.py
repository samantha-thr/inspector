from __future__ import annotations
import csv, json, os, re, subprocess, time, threading
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
from pathlib import Path
from PySide6.QtCore import Qt, QSettings, QThread, Signal
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtWidgets import *
from config import APP_NAME, DATABASE_PATH, DEFAULT_SCAN_PATH
from there_texture_decoder import open_texture_image
from model_converter import SUPPORTED_OUTPUTS, conversion_readiness, execute_conversion_job, inspect_conversion_source, prepare_conversion_job
from model_thumbnail import cached_thumbnail, render_model_thumbnail, purge_thumbnail_cache, thumbnail_failure_count, thumbnail_failures, clear_thumbnail_failure, remove_cached_thumbnail, render_metadata, RENDER_VERSION, cached_variant_thumbnail, render_model_variant, remove_cached_variant, PersistentVariantWorker
from uv_intelligence import analyze_model_uv, compare_uv_fingerprints
from model_forensics import analyze_model_rows
from vehicle_resolver import resolve_products, resolution_summary, enrich_assignments, folder_configuration
from bg_family_resolver import analyze_bg_families

def texture_pixmap(path, max_w=560, max_h=440):
    try:
        img=open_texture_image(path); rgba=img.convert("RGBA")
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
        a=open_texture_image(path_a).convert("RGBA"); b=open_texture_image(path_b).convert("RGBA")
        if a.size!=b.size:b=b.resize(a.size,Image.Resampling.LANCZOS)
        diff=ImageChops.difference(a,b)
        stat=ImageStat.Stat(diff.convert("RGB")); mean=sum(stat.mean)/3.0
        similarity=max(0.0,100.0*(1.0-mean/255.0))
        shown=ImageEnhance.Contrast(diff).enhance(2.0)
        data=shown.tobytes("raw","RGBA");q=QImage(data,shown.width,shown.height,QImage.Format_RGBA8888).copy()
        return QPixmap.fromImage(q).scaled(max_w,max_h,Qt.KeepAspectRatio,Qt.SmoothTransformation),similarity
    except Exception:return QPixmap(),None

RENDER_REPORT_COLUMNS=[
    "folder","scope","created","pid","status","resolution_method","model","model_path",
    "binding_profile","material_count","color_material_count","assigned_material_count",
    "map_target_count","assigned_map_count","unassigned_maps","unassigned_color_materials",
    "unused_linked_textures","texture_count","textures","render_output","cached","returncode","message"
]

def _render_report_row(report,x):
    return [
        report.get("folder",""),report.get("scope",""),report.get("created",""),x.get("pid",""),
        x.get("status",""),x.get("method",""),x.get("model",""),x.get("model_path",""),
        x.get("binding_profile",""),x.get("material_count",""),x.get("color_material_count",""),
        x.get("assigned_material_count",""),x.get("map_target_count",""),x.get("assigned_map_count",""),
        " | ".join(x.get("unassigned_maps",[]))," | ".join(x.get("unassigned_color_materials",[])),
        " | ".join(x.get("unused_linked_textures",[])),x.get("texture_count",0),
        " | ".join(x.get("textures",[])),x.get("output",""),x.get("cached",False),
        x.get("returncode",""),x.get("message","")
    ]

def write_render_report_files(report):
    """Persist every resolver render run locally as CSV + JSON."""
    folder=str(report.get("folder") or "products")
    safe_folder=re.sub(r"[^A-Za-z0-9_.-]+","_",folder).strip("_") or "products"
    scope=str(report.get("scope") or "render")
    stamp=time.strftime("%Y%m%d-%H%M%S")
    root=Path(__file__).resolve().parent/"reports"/"product_variants"/safe_folder
    root.mkdir(parents=True,exist_ok=True)
    base=root/f"{stamp}-{scope}"
    result=report.get("result") or {};records=result.get("records") or []
    json_path=base.with_suffix(".json");csv_path=base.with_suffix(".csv")
    json_path.write_text(json.dumps(report,indent=2),encoding="utf-8")
    with open(csv_path,"w",newline="",encoding="utf-8-sig") as fh:
        w=csv.writer(fh);w.writerow(RENDER_REPORT_COLUMNS)
        for x in records:w.writerow(_render_report_row(report,x))
    return {"csv":str(csv_path),"json":str(json_path)}

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
        if kind=="model":
            family=QScrollArea();family.setWidgetResizable(True);fh=QWidget();fg=QGridLayout(fh);fg.setAlignment(Qt.AlignTop|Qt.AlignLeft);members=db.family_members_for_model(row["path"],100)
            for i,m in enumerate(members):
                card=QFrame();card.setObjectName("card");cv=QVBoxLayout(card);im=QLabel();im.setAlignment(Qt.AlignCenter);im.setMinimumSize(180,145);cp=cached_thumbnail(m["path"])
                if cp:im.setPixmap(QPixmap(str(cp)).scaled(210,160,Qt.KeepAspectRatio,Qt.SmoothTransformation))
                else:im.setText("No render")
                cv.addWidget(im);nm=QLabel(m["filename"]);nm.setAlignment(Qt.AlignCenter);nm.setWordWrap(True);cv.addWidget(nm);fn=QLabel(m["family_name"]);fn.setAlignment(Qt.AlignCenter);cv.addWidget(fn)
                op=QPushButton("Open");op.clicked.connect(lambda _,p=m["path"]:AssetDialog(self.db,p,"model",self).exec());cv.addWidget(op);fg.addWidget(card,i//4,i%4)
            if not members:fg.addWidget(QLabel("This model is not currently assigned to a model family."),0,0)
            family.setWidget(fh);tabs.addTab(family,"Family")
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
    def __init__(self,models,force=False,workers=1):
        super().__init__();self.models=models;self.force=force;self.workers=max(1,int(workers))
    def _render(self,item):
        row,textures=item
        if cached_thumbnail(row["path"]) and not self.force:return row,"cached",None
        try:
            result=render_model_thumbnail(row["path"],textures,512,self.force)
            return row,("rendered" if result.get("success") else "failed"),result
        except Exception as exc:return row,"failed",{"message":str(exc)}
    def run(self):
        counts={"rendered":0,"failed":0,"cached":0};total=len(self.models);completed=0;started=time.monotonic()
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures={pool.submit(self._render,item):item for item in self.models}
            for future in as_completed(futures):
                row,status,result=future.result();counts[status]+=1;completed+=1
                elapsed=max(time.monotonic()-started,.001);rate=completed/elapsed;remaining=(total-completed)/rate if rate else 0
                self.progress.emit(completed,total,f"{row['filename']} • {rate:.2f}/sec • ETA {self._fmt(remaining)}")
        self.done.emit({**counts,"total":total,"elapsed":time.monotonic()-started,"workers":self.workers})
    @staticmethod
    def _fmt(seconds):
        seconds=max(0,int(seconds));h,rem=divmod(seconds,3600);m,s=divmod(rem,60)
        return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"

class ThumbnailStudio(QWidget):
    def __init__(self,db):
        super().__init__();self.db=db;self.task=None;self.cards=[];self.gallery_page=0;self.gallery_page_size=250;b=QVBoxLayout(self);b.setContentsMargins(28,24,28,24)
        h=QLabel("Visual Library");h.setObjectName("title");b.addWidget(h);b.addWidget(QLabel("Build, browse and manage cached LOD0 renders for the indexed model library."))
        filters=QHBoxLayout();self.search=QLineEdit();self.search.setPlaceholderText("Optional filename/folder/path filter");self.search.returnPressed.connect(self.refresh)
        self.count=QComboBox();self.count.addItems(["25","50","100","250","500","1000","All"]);self.count.setCurrentText("100")
        self.workers=QComboBox();self.workers.addItems(["1","2","3","4"]);self.workers.setCurrentText("2");self.engine=QComboBox();self.engine.addItems(["Persistent (fast)","One-shot (safe)"]);self.workers.setToolTip("Parallel Blender render processes. 2 is a safe default; 3–4 may be faster on high-core systems.")
        self.view=QComboBox();self.view.addItems(["Cached only","All models","Missing renders","Failed renders"])
        for w in (self.search,QLabel("Batch"),self.count,QLabel("View"),self.view):filters.addWidget(w)
        filters.setStretch(0,1);b.addLayout(filters)
        actions=QHBoxLayout();go=QPushButton("Render Missing");go.clicked.connect(self.start);refresh=QPushButton("Refresh Gallery");refresh.clicked.connect(self.refresh)
        for w in (QLabel("Workers"),self.workers,go,refresh):actions.addWidget(w)
        actions.addStretch();b.addLayout(actions)
        self.view.currentIndexChanged.connect(self.reset_gallery);self.count.currentIndexChanged.connect(self.reset_gallery);self.search.textChanged.connect(self.reset_gallery)
        self.compatibility=QLabel("Model compatibility: not analyzed yet");self.compatibility.setWordWrap(True);b.addWidget(self.compatibility)
        self.progress=QProgressBar();self.status=QLabel("Ready");b.addWidget(self.progress);b.addWidget(self.status)
        self.area=QScrollArea();self.area.setWidgetResizable(True);b.addWidget(self.area,1)
        nav=QHBoxLayout();self.gallery_prev=QPushButton("Previous 250");self.gallery_next=QPushButton("Next 250");self.gallery_page_label=QLabel()
        self.gallery_prev.clicked.connect(self.prev_gallery);self.gallery_next.clicked.connect(self.next_gallery)
        nav.addWidget(self.gallery_prev);nav.addWidget(self.gallery_next);nav.addWidget(self.gallery_page_label);nav.addStretch();b.addLayout(nav);self.refresh()

    def make_card(self,row,p):
        card=QFrame();card.setObjectName("card");card.setMinimumWidth(225);v=QVBoxLayout(card)
        im=QLabel();im.setAlignment(Qt.AlignCenter);im.setMinimumSize(210,170)
        if p:
            pix=QPixmap(str(p));im.setPixmap(pix.scaled(210,170,Qt.KeepAspectRatio,Qt.SmoothTransformation))
        else:im.setText("No cached render")
        v.addWidget(im)
        n=QLabel(row["filename"]);n.setWordWrap(True);n.setAlignment(Qt.AlignCenter);v.addWidget(n)
        if p:
            meta=render_metadata(row["path"]);rv=meta.get("render_version","legacy");ts=meta.get("rendered")
            detail=f"Render v{rv}" + (f" • {time.strftime('%Y-%m-%d %H:%M',time.localtime(ts))}" if ts else "")
            md=QLabel(detail);md.setAlignment(Qt.AlignCenter);md.setStyleSheet("color:#8f98a3;font-size:9pt");v.addWidget(md)
        buttons=QHBoxLayout();open_b=QPushButton("Open");open_b.clicked.connect(lambda _,path=row["path"]:AssetDialog(self.db,path,"model",self).exec());buttons.addWidget(open_b)
        if p:
            delete=QPushButton("Remove Render");delete.clicked.connect(lambda _,path=row["path"]:self.remove_render(path));buttons.addWidget(delete)
        else:
            render=QPushButton("Render");render.clicked.connect(lambda _,rr=dict(row):self.render_one(rr));buttons.addWidget(render)
        v.addLayout(buttons);return card

    def reset_gallery(self):
        self.gallery_page=0;self.refresh()

    def prev_gallery(self):
        if self.gallery_page>0:self.gallery_page-=1;self.refresh()

    def next_gallery(self):
        self.gallery_page+=1;self.refresh()

    def _matching_gallery_rows(self):
        term=self.search.text().strip();mode=self.view.currentText();failed_map={x["model"]:x for x in thumbnail_failures(True)}
        matches=[];offset=0;page_size=1000;total=0;cached_total=missing_total=0
        while True:
            rows,total=self.db.search_models_page(term,page_size,offset)
            if not rows:break
            for row in rows:
                p=cached_thumbnail(row["path"])
                if p:cached_total+=1
                else:missing_total+=1
                include=(mode=="All models" or (mode=="Cached only" and p) or (mode=="Missing renders" and not p) or (mode=="Failed renders" and row["path"] in failed_map))
                if include:matches.append((row,p,failed_map.get(row["path"])))
            offset+=len(rows)
            if offset>=total:break
        return matches,total,cached_total,missing_total

    def refresh(self):
        matches,total,cached_total,missing_total=self._matching_gallery_rows()
        pages=max(1,(len(matches)+self.gallery_page_size-1)//self.gallery_page_size)
        if self.gallery_page>=pages:self.gallery_page=max(0,pages-1)
        lo=self.gallery_page*self.gallery_page_size;hi=min(lo+self.gallery_page_size,len(matches));page=matches[lo:hi]
        host=QWidget();grid=QGridLayout(host);grid.setAlignment(Qt.AlignTop|Qt.AlignLeft)
        for shown,(row,p,failure) in enumerate(page):
            card=self.make_card(row,p)
            if failure:
                msg=str(failure.get("message",""));err=QLabel("FAILED: "+msg[-240:]);err.setWordWrap(True);err.setToolTip(msg);card.layout().insertWidget(1,err)
            grid.addWidget(card,shown//4,shown%4)
        if not page:grid.addWidget(QLabel("No models match this view."),0,0)
        self.area.setWidget(host);self.area.verticalScrollBar().setValue(0)
        self.gallery_prev.setEnabled(self.gallery_page>0);self.gallery_next.setEnabled(hi<len(matches))
        self.gallery_page_label.setText(f"Page {self.gallery_page+1:,} of {pages:,} • showing {lo+1 if matches else 0:,}–{hi:,} of {len(matches):,} in this view")
        self.status.setText(f"{len(matches):,} in view • {cached_total:,} cached • {missing_total:,} missing • {len(thumbnail_failures(True)):,} unresolved failures • {total:,} matching models")

    def remove_render(self,path):
        p=cached_thumbnail(path)
        if not p:return
        answer=QMessageBox.question(self,APP_NAME,f"Remove cached render for {Path(path).name}?" + chr(10) + chr(10) + "The model and database record will not be changed.",QMessageBox.Yes|QMessageBox.No)
        if answer==QMessageBox.Yes:
            remove_cached_thumbnail(path);self.refresh()

    def render_one(self,row):
        links=self.db.links_for_model(row["path"],100);textures=[x["texture_path"] for x in links if x["texture_path"]]
        self.progress.setRange(0,1);self.progress.setValue(0);self.status.setText(f"Rendering {row['filename']}…")
        self.task=ThumbnailBatchTask([(row,textures)],False,1);self.task.progress.connect(self.on_progress);self.task.done.connect(self.finished);self.task.start()

    def start(self):
        all_mode=self.count.currentText()=="All";target=None if all_mode else int(self.count.currentText());term=self.search.text().strip();items=[];offset=0;page_size=1000 if all_mode else max(250,target);checked=0;first_missing=None
        while all_mode or len(items)<target:
            rows,total=self.db.search_models_page(term,page_size,offset)
            if not rows:break
            for row in rows:
                checked+=1
                if cached_thumbnail(row["path"]):continue
                if first_missing is None:first_missing=offset+(rows.index(row))+1
                links=self.db.links_for_model(row["path"],100)
                items.append((dict(row),[x["texture_path"] for x in links if x["texture_path"]]))
                if not all_mode and len(items)>=target:break
            offset+=len(rows)
            if offset>=total:break
        if not items:
            self.status.setText(f"All {checked:,} matching models already have cached renders.");return
        self.progress.setRange(0,len(items));self.progress.setValue(0)
        scope="all remaining" if all_mode else f"{len(items):,}"
        self.status.setText(f"Starting {scope} missing renders • {len(items):,} queued • first uncached model #{first_missing:,} • skipped {checked-len(items):,} cached models")
        self.task=ThumbnailBatchTask(items,False,int(self.workers.currentText()));self.task.progress.connect(self.on_progress);self.task.done.connect(self.finished);self.task.start()
    def on_progress(self,i,total,name):
        self.progress.setValue(i);self.status.setText(f"Rendering {i:,} / {total:,} • {name}")
    def finished(self,result):
        self.progress.setValue(self.progress.maximum());self.refresh()
        elapsed=result.get("elapsed",0);rate=result["total"]/elapsed if elapsed else 0
        self.status.setText(f"Complete • rendered {result['rendered']:,} • cached {result['cached']:,} • failed {result['failed']:,} • {result['total']:,} processed • {result.get('workers',1)} workers • {rate:.2f}/sec")

class VariantRenderTask(QThread):
    progress=Signal(int,int,str,float,float);done=Signal(object)
    def __init__(self,model,sets,workers=2,persistent=True):
        super().__init__();self.model=model;self.sets=sets;self.workers=max(1,workers);self.persistent=persistent;self.cancelled=False;self.cancel_event=threading.Event();self.active_workers=[];self.worker_lock=threading.Lock()
    def cancel(self):
        self.cancelled=True;self.cancel_event.set()
        with self.worker_lock:
            for worker in list(self.active_workers):
                try: worker.close(force=True)
                except Exception: pass
    def run(self):
        ok=failed=cached=0;completed=0;total=len(self.sets);started=time.monotonic();failures=[];local=threading.local()
        seed=self.sets[0][1] if self.sets else []
        def make_worker():
            w=PersistentVariantWorker(self.model["path"],seed,512,self.cancel_event,threading.get_ident())
            with self.worker_lock:self.active_workers.append(w)
            return w
        def drop_worker(w):
            if not w:return
            try:w.close(force=True)
            except Exception:pass
            with self.worker_lock:
                if w in self.active_workers:self.active_workers.remove(w)
        def one(item):
            pid,paths=item
            if self.cancel_event.is_set():return pid,paths,{"success":False,"cancelled":True}
            if not self.persistent:return pid,paths,render_model_variant(self.model["path"],paths,512,False,self.cancel_event)
            try:
                if not getattr(local,"worker",None):local.worker=make_worker();local.jobs=0
                if local.jobs>=200:
                    drop_worker(local.worker);local.worker=make_worker();local.jobs=0
                r=local.worker.render(pid,paths,False);local.jobs+=1
                if not r.get("success") and not r.get("cached") and not r.get("cancelled") and (local.worker.proc is None or local.worker.proc.poll() is not None):
                    drop_worker(local.worker);local.worker=make_worker();local.jobs=0;r=local.worker.render(pid,paths,False);local.jobs+=1
                return pid,paths,r
            except Exception as exc:
                return pid,paths,{"success":False,"cancelled":self.cancel_event.is_set(),"message":str(exc)}
        pool=ThreadPoolExecutor(max_workers=self.workers);futures={}
        try:
            iterator=iter(self.sets)
            for _ in range(self.workers):
                try:
                    item=next(iterator);futures[pool.submit(one,item)]=item
                except StopIteration:break
            while futures and not self.cancel_event.is_set():
                done,_=wait(tuple(futures),timeout=.20,return_when=FIRST_COMPLETED)
                if not done:continue
                for future in done:
                    item=futures.pop(future,None)
                    try:pid,paths,r=future.result()
                    except Exception as exc:
                        pid=item[0] if item else "error";paths=item[1] if item else [];r={"success":False,"message":str(exc)}
                    if r.get("cancelled"):continue
                    completed+=1
                    if r.get("cached"):cached+=1
                    elif r.get("success"):ok+=1
                    else:
                        failed+=1;failures.append({"pid":pid,"paths":list(paths),"returncode":r.get("returncode"),"message":r.get("message") or "Blender render failed","log":r.get("log",""),"output":r.get("output","")})
                    elapsed=max(time.monotonic()-started,.001);rate=completed/elapsed;remaining=(total-completed)/rate if rate else 0
                    self.progress.emit(completed,total,pid,rate,remaining)
                    if not self.cancel_event.is_set():
                        try:
                            item=next(iterator);futures[pool.submit(one,item)]=item
                        except StopIteration:pass
            if self.cancel_event.is_set():
                for future in futures:future.cancel()
        finally:
            self.cancel_event.set()
            with self.worker_lock:
                workers=list(self.active_workers)
            for worker in workers:drop_worker(worker)
            pool.shutdown(wait=True,cancel_futures=True)
        self.done.emit({"rendered":ok,"cached":cached,"failed":failed,"total":total,"cancelled":self.cancelled,"completed":completed,"failures":failures,"engine":"persistent" if self.persistent else "one-shot"})

class ResolvedVariantRenderTask(QThread):
    progress=Signal(int,int,str,float,float);done=Signal(object)
    def __init__(self,assignments,workers=2):
        super().__init__();self.assignments=list(assignments);self.workers=max(1,int(workers));self.cancelled=False;self.cancel_event=threading.Event()
    def cancel(self):
        self.cancelled=True;self.cancel_event.set()
    def _one(self,item):
        pid=item["pid"];model=item["model"];paths=item["textures"]
        if self.cancel_event.is_set():return item,{"success":False,"cancelled":True}
        try:return item,render_model_variant(model["path"],paths,512,False,self.cancel_event)
        except Exception as exc:return item,{"success":False,"message":str(exc)}
    def run(self):
        total=len(self.assignments);started=time.monotonic();completed=ok=cached=failed=0;failures=[];outputs=[];records=[]
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures={pool.submit(self._one,x):x for x in self.assignments}
            for future in as_completed(futures):
                if self.cancel_event.is_set():break
                item,result=future.result()
                if result.get("cancelled"):continue
                completed+=1
                if result.get("cached"):
                    cached+=1;status="cached"
                elif result.get("success"):
                    ok+=1;status="rendered"
                else:
                    failed+=1;status="failed";failures.append({"pid":item["pid"],"model":item["model"]["filename"],"message":result.get("message") or "Render failed","returncode":result.get("returncode"),"log":result.get("log","")})
                bindings=result.get("texture_bindings") or []
                bind_summary=next((b for b in bindings if b.get("summary")),{})
                record={"pid":item["pid"],"state":item.get("state","resolved"),"method":item.get("method",""),"model":item["model"]["filename"],"model_path":item["model"]["path"],"textures":list(item["textures"]),"texture_count":len(item["textures"]),"status":status,"output":result.get("output") or "","cached":bool(result.get("cached")),"returncode":result.get("returncode"),"message":result.get("message") or "","binding_profile":result.get("binding_profile") or "","material_count":bind_summary.get("material_count",""),"color_material_count":bind_summary.get("color_material_count",""),"assigned_material_count":bind_summary.get("assigned_material_count",""),"map_target_count":bind_summary.get("map_target_count",""),"assigned_map_count":bind_summary.get("assigned_map_count",""),"unassigned_maps":bind_summary.get("unassigned_maps",[]),"unassigned_color_materials":bind_summary.get("unassigned_color_materials",[]),"unused_linked_textures":bind_summary.get("unused_linked_textures",[]),"texture_bindings":bindings}
                records.append(record)
                if result.get("success"):outputs.append({"pid":item["pid"],"model":item["model"],"textures":item["textures"],"output":result.get("output"),"cached":bool(result.get("cached")),"binding_profile":result.get("binding_profile") or "","binding_summary":bind_summary})
                elapsed=max(time.monotonic()-started,.001);rate=completed/elapsed;remaining=(total-completed)/rate if rate else 0
                self.progress.emit(completed,total,item["pid"],rate,remaining)
            if self.cancel_event.is_set():
                for future in futures:future.cancel()
        records.sort(key=lambda x:int(x["pid"]) if str(x["pid"]).isdigit() else str(x["pid"]))
        successful=[x for x in records if x["status"] in ("rendered","cached")]
        binding_review=sum(1 for x in successful if x.get("unassigned_maps") or x.get("unused_linked_textures"))
        binding_complete=len(successful)-binding_review
        unsupported=sum(1 for x in records if x["status"]=="failed" and "SOM version" in (x.get("message") or ""))
        self.done.emit({"rendered":ok,"cached":cached,"failed":failed,"total":total,"completed":completed,"cancelled":self.cancelled,"failures":failures,"outputs":outputs,"records":records,"binding_complete":binding_complete,"binding_review":binding_review,"unsupported":unsupported,"engine":"one-shot resolved-model"})


class VehicleVariantsPage(QWidget):
    def __init__(self,db):
        super().__init__();self.db=db;self.task=None;self.bg_task=None;self.sets=[];self.template_refs=[];self.template_assignments={};self.model=None;self.raw_texture_count=0;b=QVBoxLayout(self);b.setContentsMargins(28,24,28,24)
        h=QLabel("Product Variants");h.setObjectName("title");b.addWidget(h);b.addWidget(QLabel("Resolve product models from client evidence and render verified product/model combinations without manual model guessing."))
        # Keep legacy model/template selectors internally for BG research, but the
        # normal workflow is now folder -> resolver -> render.
        select_row=QHBoxLayout();self.folder=QComboBox();self.folder.setMinimumWidth(260);self.folder.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed);self.folder.setToolTip("Indexed asset folder")
        self.model_box=QComboBox();self.template_box=QComboBox()
        load=QPushButton("Load Folder");load.clicked.connect(self.load_folder);self.load_button=load
        self.view_filter=QComboBox();self.view_filter.addItems(["Rendered","Missing resolved","Unresolved","All resolved"]);self.view_filter.currentIndexChanged.connect(self.refresh)
        self.populate_folders()
        for w in (QLabel("Folder"),self.folder,load,QLabel("View"),self.view_filter):select_row.addWidget(w)
        select_row.setStretch(1,3);select_row.addStretch();b.addLayout(select_row)
        action_row=QHBoxLayout();self.workers=QComboBox();self.workers.addItems(["1","2","3","4"]);self.workers.setCurrentText("2")
        self.engine=QComboBox();self.engine.addItems(["One-shot (safe)","Persistent (experimental)"]);self.engine.setCurrentIndex(0);self.engine.setMinimumWidth(165)
        self.render_button=QPushButton("Render All Resolved");self.render_button.setToolTip("Render every missing product with a verified model assignment in the selected folder; unresolved products are skipped");self.render_button.clicked.connect(self.render_all_resolved)
        self.cancel_button=QPushButton("Stop / Cancel");self.cancel_button.setEnabled(False);self.cancel_button.clicked.connect(self.cancel_render)
        self.delete_all_button=QPushButton("Delete Resolved Renders");self.delete_all_button.clicked.connect(self.delete_all_resolved_variants)
        self.failures_button=QPushButton("Failures");self.failures_button.setEnabled(False);self.failures_button.clicked.connect(self.show_failures);self.last_failures=[];self.last_render_report=None;self.last_auto_report=None
        self.export_render_button=QPushButton("Export Last Render");self.export_render_button.setEnabled(False);self.export_render_button.clicked.connect(self.export_last_render)
        uv_analyze=QPushButton("Analyze UV Families");uv_analyze.clicked.connect(self.analyze_uv_families);self.uv_analyze_button=uv_analyze
        bg_analyze=QPushButton("Analyze BG Families");bg_analyze.clicked.connect(self.analyze_bg_families);self.bg_analyze_button=bg_analyze
        resolve=QPushButton("Resolution Preview");resolve.clicked.connect(self.show_resolution_preview);self.resolve_button=resolve
        config=QPushButton("Folder Configuration");config.clicked.connect(self.show_folder_configuration);self.config_button=config
        self.sample_size=QComboBox();self.sample_size.addItems(["10","25","50","100"]);self.sample_size.setCurrentText("50");self.sample_size.setToolTip("Deterministic spread across the missing exact-PID population")
        sample=QPushButton("Render Sample");sample.clicked.connect(self.render_resolved_sample);self.sample_button=sample
        for w in (QLabel("Workers"),self.workers,config,uv_analyze,bg_analyze,resolve,QLabel("Sample"),self.sample_size,sample,self.render_button,self.cancel_button,self.failures_button,self.export_render_button,self.delete_all_button):action_row.addWidget(w)
        action_row.addStretch();b.addLayout(action_row)
        self.compatibility=QLabel("Resolver status: verified PID/model matches and explicit official default-model rules.");self.compatibility.setWordWrap(True);b.addWidget(self.compatibility)
        self.progress=QProgressBar();self.status=QLabel("Ready");b.addWidget(self.progress);b.addWidget(self.status)
        self.area=QScrollArea();self.area.setWidgetResizable(True);b.addWidget(self.area,1);self.model_box.currentIndexChanged.connect(self.refresh);self.template_box.currentIndexChanged.connect(self.refresh);self.load_folder()
    def folder_name(self):
        return (self.folder.currentData() or self.folder.currentText()).strip()

    def populate_folders(self):
        current=self.folder_name() if self.folder.count() else "bg"
        self.folder.blockSignals(True);self.folder.clear()
        rows=self.db.asset_folders()
        for row in rows:
            folder=row["folder"];mc=int(row["model_count"] or 0);tc=int(row["texture_count"] or 0)
            self.folder.addItem(f"{folder}   •   {mc:,} models / {tc:,} textures",folder)
        idx=self.folder.findData(current)
        if idx<0:idx=self.folder.findData("bg")
        if idx>=0:self.folder.setCurrentIndex(idx)
        self.folder.blockSignals(False)

    @staticmethod
    def product_id(name):
        import re
        m=re.match(r"^(\d+)_([1-9]\d*)\.",str(name),re.IGNORECASE)
        return (m.group(1),int(m.group(2))) if m else None
    def load_folder(self):
        folder=self.folder_name();previous=self.model_box.currentText()
        self.status.setText(f"Loading {folder}…");self.load_button.setEnabled(False);QApplication.processEvents()
        try:
            models=self.db.models_in_folder(folder,10000);self.model_box.blockSignals(True);self.model_box.clear()
            for m in models:self.model_box.addItem(m["filename"],dict(m))
            if previous:
                idx=self.model_box.findText(previous)
                if idx>=0:self.model_box.setCurrentIndex(idx)
            self.model_box.blockSignals(False);self.discover();self.bg_analyze_button.setEnabled(folder.lower()=="bg");self.refresh()
            if self.raw_texture_count:
                self.status.setText(f"Loaded {len(models):,} models • scanned {self.raw_texture_count:,} textures • discovered {len(self.sets):,} PID sets from {folder}")
            else:
                dbg=self.db.texture_folder_debug(folder,8)
                samples=" | ".join(f"{x['folder']!r}: {x['count']:,} e.g. {x['sample']}" for x in dbg)
                self.status.setText(f"Loaded {len(models):,} models • scanned 0 textures for {folder} • DB matches: {samples or 'none'}")
        finally:self.load_button.setEnabled(True)
    @staticmethod
    def is_template_texture(filename):
        import re
        return bool(re.match(r"^t\d{3}[a-z0-9]*_",str(filename),re.IGNORECASE))

    @staticmethod
    def layout_signature(path):
        try:
            from PIL import Image,ImageFilter,ImageStat
            im=open_texture_image(path).convert("RGBA").resize((64,64),Image.Resampling.BILINEAR)
            rgb=im.convert("L").filter(ImageFilter.FIND_EDGES);alpha=im.getchannel("A");vals=[]
            for src in (rgb,alpha):
                for y in range(0,64,8):
                    for x in range(0,64,8):
                        vals.append(ImageStat.Stat(src.crop((x,y,x+8,y+8))).mean[0]/255.0)
            return vals
        except Exception:return None

    @staticmethod
    def signature_similarity(a,b):
        if not a or not b or len(a)!=len(b):return 0.0
        import math
        rms=math.sqrt(sum((x-y)**2 for x,y in zip(a,b))/len(a))
        return max(0.0,1.0-rms)

    def classify_templates(self,textures):
        previous=self.template_box.currentData()
        refs=[]
        for t in textures:
            if not self.is_template_texture(t["filename"]):continue
            sig=self.layout_signature(t["path"])
            if sig:refs.append({"filename":t["filename"],"path":t["path"],"sig":sig})
        self.template_refs=refs;self.template_assignments={}
        self.template_box.blockSignals(True);self.template_box.clear()
        self.template_box.addItem("Choose reference template…",None)
        for r in refs:self.template_box.addItem(r["filename"],r["path"])
        if previous:
            idx=self.template_box.findData(previous)
            if idx>=0:self.template_box.setCurrentIndex(idx)
        self.template_box.blockSignals(False)
        # Keep scores against every reference. Several stock templates can share one
        # geometry/UV family, so choosing a single global winner is incorrect.
        for pid,paths in self.sets:
            body=next((p for p in paths if (self.product_id(Path(p).name) or (None,None))[1]==1),None)
            sig=self.layout_signature(body) if body else None
            if not sig:continue
            scores={}
            for r in refs:scores[r["path"]]=self.signature_similarity(sig,r["sig"])
            self.template_assignments[pid]={"scores":scores}

    def compatible_sets(self,model=None):
        if not (model or self.current_model()):return []
        template_path=self.template_box.currentData()
        if not template_path:return []
        # Directly compare against the selected reference. Do not make same-family
        # templates eliminate one another in a winner-take-all contest.
        threshold=0.78
        return [x for x in self.sets if self.template_assignments.get(x[0],{}).get("scores",{}).get(template_path,0.0)>=threshold]

    def discover(self):
        groups={};textures=self.db.textures_in_folder(self.folder_name(),100000);self.raw_texture_count=len(textures)
        for t in textures:
            parsed=self.product_id(t["filename"])
            if not parsed:continue
            pid,slot=parsed;groups.setdefault(pid,{})[slot]=t["path"]
        self.sets=[(pid,[slots[k] for k in sorted(slots)]) for pid,slots in sorted(groups.items(),key=lambda x:int(x[0]))]
        self.classify_templates(textures)

    def current_model(self):
        d=self.model_box.currentData();return d if d else None

    def _basic_resolved_assignments(self):
        folder=self.folder_name();models=self.db.models_in_folder(folder,10000)
        return resolve_products(folder,models,self.sets)

    def refresh(self):
        assignments=self._basic_resolved_assignments();summary=resolution_summary(assignments)
        mode=self.view_filter.currentText() if hasattr(self,"view_filter") else "Rendered"
        host=QWidget();grid=QGridLayout(host);grid.setAlignment(Qt.AlignTop|Qt.AlignLeft);shown=0;cached_count=0;missing_count=0
        filtered=[]
        for x in assignments:
            cached=None
            if x["state"]=="resolved" and x["model"]:
                cached=cached_variant_thumbnail(x["model"]["path"],x["textures"])
                if cached:cached_count+=1
                else:missing_count+=1
            include=(mode=="Rendered" and cached) or (mode=="Missing resolved" and x["state"]=="resolved" and not cached) or (mode=="Unresolved" and x["state"]=="unresolved") or (mode=="All resolved" and x["state"]=="resolved")
            if include:filtered.append((x,cached))
        for x,cached in filtered[:500]:
            card=QFrame();card.setObjectName("card");card.setMinimumWidth(225);v=QVBoxLayout(card);im=QLabel();im.setAlignment(Qt.AlignCenter);im.setMinimumSize(210,170)
            if cached:
                pix=QPixmap(str(cached));im.setPixmap(pix.scaled(210,170,Qt.KeepAspectRatio,Qt.SmoothTransformation))
            else:
                im.setText("Unresolved" if x["state"]=="unresolved" else "Missing render")
            v.addWidget(im)
            model_name=x["model"]["filename"] if x["model"] else "No model resolved"
            n=QLabel(f"PID {x['pid']}\n{model_name}");n.setAlignment(Qt.AlignCenter);n.setWordWrap(True);v.addWidget(n)
            method=QLabel(x["method"]);method.setAlignment(Qt.AlignCenter);method.setStyleSheet("color:#8f98a3");v.addWidget(method)
            buttons=QHBoxLayout();tex=QPushButton("Textures");tex.clicked.connect(lambda _,pp=x["textures"]:self.open_textures(pp));buttons.addWidget(tex)
            if cached:
                delete=QPushButton("Delete Render");delete.clicked.connect(lambda _,xx=x:self.delete_resolved_variant(xx));buttons.addWidget(delete)
            v.addLayout(buttons);grid.addWidget(card,shown//4,shown%4);shown+=1
        if not shown:grid.addWidget(QLabel("No products match this view."),0,0)
        elif len(filtered)>500:grid.addWidget(QLabel(f"Showing first 500 of {len(filtered):,}; use the view filter to narrow the list."),(shown//4)+1,0,1,4)
        self.area.setWidget(host)
        self.compatibility.setText(f"Resolver • {summary['resolved']:,} resolved • {summary['unresolved']:,} unresolved • verified model assignments only")
        self.status.setText(f"{len(self.sets):,} PID sets • {cached_count:,} rendered • {missing_count:,} resolved/missing • {summary['unresolved']:,} unresolved • showing {min(len(filtered),500):,}")

    def _resolved_assignments(self):
        return enrich_assignments(self._basic_resolved_assignments())

    def analyze_bg_families(self):
        if self.folder_name().lower()!="bg":
            QMessageBox.information(self,APP_NAME,"BG family analysis is only applicable to the bg resource folder.");return
        if self.bg_task and self.bg_task.isRunning():
            QMessageBox.information(self,APP_NAME,"BG family analysis is already running.");return
        models=self.db.models_in_folder("bg",10000)
        self.bg_analyze_button.setEnabled(False)
        for control in (self.sample_button,self.render_button,self.delete_all_button,self.resolve_button):
            control.setEnabled(False)
        self.progress.setRange(0,0)
        self.status.setText(f"Analyzing {len(self.sets):,} BG product textures against decoded model UV families… Existing results remain read-only until analysis completes.")
        self.bg_task=Task(analyze_bg_families,self.sets,models)
        self.bg_task.done.connect(self.bg_family_analysis_finished)
        self.bg_task.failed.connect(self.bg_family_analysis_failed)
        self.bg_task.start()

    def bg_family_analysis_failed(self,message):
        self.progress.setRange(0,100);self.progress.setValue(0);self.bg_analyze_button.setEnabled(True);self.bg_task=None
        for control in (self.sample_button,self.render_button,self.delete_all_button,self.resolve_button):
            control.setEnabled(True)
        QMessageBox.critical(self,APP_NAME,f"BG family analysis failed:\n{message}")
        self.status.setText("BG family analysis failed.")

    def bg_family_analysis_finished(self,result):
        self.progress.setRange(0,100);self.progress.setValue(100);self.bg_analyze_button.setEnabled(True);self.bg_task=None
        for control in (self.sample_button,self.render_button,self.delete_all_button,self.resolve_button):
            control.setEnabled(True)
        if not result.get("ok"):
            QMessageBox.warning(self,APP_NAME,result.get("message") or "BG family analysis did not produce a usable result.")
            self.status.setText("BG family analysis did not produce a usable result.");return
        counts=result.get("counts") or {};self.refresh()
        d=QDialog(self);d.setWindowTitle("BG Family Analysis");d.resize(760,500);v=QVBoxLayout(d)
        title=QLabel("BG family analysis complete");title.setObjectName("title");v.addWidget(title)
        text=QPlainTextEdit();text.setReadOnly(True)
        anchors=result.get("anchors") or []
        resolved_by_model={}
        for x in (result.get("assignments") or {}).values():
            if x.get("state")=="resolved" and x.get("family_model"):
                name=x["family_model"];resolved_by_model[name]=resolved_by_model.get(name,0)+1
        lines=[
            f"Products analyzed: {len(result.get('assignments') or {}):,}",
            f"Resolved to paintable BG families: {counts.get('resolved',0):,}",
            f"Ambiguous / unresolved: {counts.get('ambiguous',0):,}",
            f"Special or non-paintable family matches: {counts.get('special',0):,}",
            f"Low-information / solid-color textures: {counts.get('low_information',0):,}",
            f"Missing body texture: {counts.get('missing_body',0):,}",
            "",
            "Resolved by model:"
        ]
        if resolved_by_model:
            lines += [f"  {name}: {count:,}" for name,count in sorted(resolved_by_model.items())]
        else:
            lines += ["  none"]
        lines += ["","Verified reference families:"]
        lines += [f"  {a.get('model')}  <-  {a.get('template')}  ({'paintable' if a.get('paintable') else 'special/non-paintable'})" for a in anchors]
        if result.get("report_csv"):
            lines += ["",f"CSV report: {result['report_csv']}",f"JSON report: {result.get('report_json','')}"]
        text.setPlainText("\n".join(lines));v.addWidget(text,1)
        note=QLabel("Resolution uses background-normalized product evidence plus the decoded model body UV coverage. Ambiguous products remain unresolved and are excluded from rendering.")
        note.setWordWrap(True);v.addWidget(note)
        close=QDialogButtonBox(QDialogButtonBox.Close);close.rejected.connect(d.reject);v.addWidget(close);d.exec()
        self.status.setText(f"BG analysis complete • {counts.get('resolved',0):,} resolved • {counts.get('ambiguous',0):,} ambiguous • {counts.get('low_information',0):,} low-information • {counts.get('special',0):,} special/non-paintable")

    def show_folder_configuration(self):
        folder=self.folder_name();models=self.db.models_in_folder(folder,10000);textures=self.db.textures_in_folder(folder,100000)
        cfg=folder_configuration(folder,models,textures)
        d=QDialog(self);d.setWindowTitle(f"Folder Configuration — {folder}");d.resize(1120,760);v=QVBoxLayout(d)
        head=QLabel(f"{folder} • client resource inventory");head.setObjectName("title");v.addWidget(head)
        summary=QLabel(f"Product/PID models: {len(cfg['product_models']):,} • Official models: {len(cfg['official_models']):,} • Product textures: {len(cfg['product_textures']):,} • Official textures: {len(cfg['official_textures']):,} • PID .aconf: {len(cfg['product_aconf']):,} • Official .aconf: {len(cfg['official_aconf']):,}")
        summary.setWordWrap(True);v.addWidget(summary)
        tabs=QTabWidget();v.addWidget(tabs,1)
        def make_table(rows,columns):
            t=QTableWidget(len(rows),len(columns));t.setHorizontalHeaderLabels([x[0] for x in columns]);t.setEditTriggers(QAbstractItemView.NoEditTriggers);t.setSelectionBehavior(QAbstractItemView.SelectRows)
            for r,row in enumerate(rows):
                for c,(_,key) in enumerate(columns):t.setItem(r,c,QTableWidgetItem(str(row.get(key,""))))
            h=t.horizontalHeader();h.setSectionResizeMode(QHeaderView.Interactive);h.setStretchLastSection(True);t.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded);t.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            return t
        tabs.addTab(make_table(cfg["product_models"],[("Product model","filename"),("Path","path")]),f"PID Models ({len(cfg['product_models']):,})")
        tabs.addTab(make_table(cfg["official_models"],[("Official model","filename"),("Path","path")]),f"Official Models ({len(cfg['official_models']):,})")
        tabs.addTab(make_table(cfg["official_textures"],[("Official/template texture","filename"),("Path","path")]),f"Official Textures ({len(cfg['official_textures']):,})")
        tabs.addTab(make_table(cfg["aconf"],[("Config","filename"),("Origin","origin"),("PID","pid"),("Path","path")]),f"ACONF ({len(cfg['aconf']):,})")
        close=QDialogButtonBox(QDialogButtonBox.Close);close.rejected.connect(d.reject);v.addWidget(close);d.exec()

    def show_resolution_preview(self):
        folder=self.folder_name();assignments=self._resolved_assignments();summary=resolution_summary(assignments)
        d=QDialog(self);d.setWindowTitle(f"Product Resolution Preview — {folder}");d.resize(1180,780);v=QVBoxLayout(d)
        head=QLabel();head.setObjectName("title");v.addWidget(head)
        note=QLabel("Resolution uses verified client evidence: exact numeric PID models and explicitly configured official default models. ACONF presence/raw strings are evidence only; unknown fields are not interpreted.");note.setWordWrap(True);v.addWidget(note)
        controls=QHBoxLayout();flt=QComboBox();flt.addItems(["All","Resolved","Unresolved","Exact PID","Official default","BG template family"]);export=QPushButton("Export Resolution Report");details=QPushButton("ACONF Details")
        controls.addWidget(QLabel("Filter"));controls.addWidget(flt);controls.addStretch();controls.addWidget(details);controls.addWidget(export);v.addLayout(controls)
        table=QTableWidget(0,7);table.setHorizontalHeaderLabels(["PID","State","Resolved model","Method","Textures","ACONF","Config strings"]);table.setSelectionBehavior(QAbstractItemView.SelectRows);table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh=table.horizontalHeader();hh.setSectionResizeMode(QHeaderView.Interactive)
        for c,w in enumerate((140,115,220,230,85,90,360)):table.setColumnWidth(c,w)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded);table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded);v.addWidget(table,1)
        shown=[]
        def matches(x):
            mode=flt.currentText()
            return mode=="All" or (mode=="Resolved" and x["state"]=="resolved") or (mode=="Unresolved" and x["state"]=="unresolved") or (mode=="Exact PID" and x["method"]=="exact PID model") or (mode=="Official default" and x["method"]=="official default model") or (mode=="BG template family" and x["method"]=="BG structural family match")
        def populate():
            nonlocal shown
            shown=[x for x in assignments if matches(x)];table.setRowCount(len(shown))
            for r,x in enumerate(shown):
                ae=x.get("aconf") or {};strings=" | ".join(ae.get("strings",[])[:4])
                vals=(x["pid"],x["state"].upper(),x["model"]["filename"] if x["model"] else "—",x["method"],str(len(x["textures"])),"YES" if ae.get("exists") else "—",strings)
                for c,val in enumerate(vals):table.setItem(r,c,QTableWidgetItem(str(val)))
            head.setText(f"{len(assignments):,} product sets • {summary['resolved']:,} resolved • {summary['unresolved']:,} unresolved • showing {len(shown):,}")
        flt.currentIndexChanged.connect(populate)
        def show_aconf():
            r=table.currentRow()
            if r<0 or r>=len(shown):return
            x=shown[r];ae=x.get("aconf") or {}
            if not ae.get("exists"):
                QMessageBox.information(d,APP_NAME,f"PID {x['pid']} has no sibling {x['pid']}.aconf file.");return
            dlg=QDialog(d);dlg.setWindowTitle(f"ACONF Evidence — {x['pid']}");dlg.resize(850,520);lay=QVBoxLayout(dlg)
            lay.addWidget(QLabel(ae.get("path","")));raw=QPlainTextEdit();raw.setReadOnly(True);raw.setPlainText("\n".join(ae.get("strings",[])) or "(No printable strings found.)");lay.addWidget(raw,1)
            bb=QDialogButtonBox(QDialogButtonBox.Close);bb.rejected.connect(dlg.reject);lay.addWidget(bb);dlg.exec()
        details.clicked.connect(show_aconf)
        def do_export():
            path,_=QFileDialog.getSaveFileName(d,"Export Resolution Report",f"there-inspector-{folder}-resolution.csv","CSV (*.csv);;JSON (*.json)")
            if not path:return
            if path.lower().endswith(".json"):
                serial=[]
                for x in assignments:
                    serial.append({"pid":x["pid"],"state":x["state"],"model":x["model"]["filename"] if x["model"] else None,"model_path":x["model"]["path"] if x["model"] else None,"method":x["method"],"textures":x["textures"],"aconf":x.get("aconf")})
                Path(path).write_text(json.dumps({"folder":folder,"summary":summary,"assignments":serial},indent=2),encoding="utf-8")
            else:
                if not path.lower().endswith(".csv"):path += ".csv"
                with open(path,"w",newline="",encoding="utf-8-sig") as fh:
                    w=csv.writer(fh);w.writerow(["pid","state","resolved_model","model_path","method","texture_count","textures","aconf_exists","aconf_path","aconf_strings"])
                    for x in assignments:
                        ae=x.get("aconf") or {};w.writerow([x["pid"],x["state"],x["model"]["filename"] if x["model"] else "",x["model"]["path"] if x["model"] else "",x["method"],len(x["textures"])," | ".join(x["textures"]),bool(ae.get("exists")),ae.get("path") or ""," | ".join(ae.get("strings",[]))])
            self.status.setText(f"Exported vehicle resolution report • {path}")
        export.clicked.connect(do_export)
        by=QPlainTextEdit();by.setReadOnly(True);by.setMaximumHeight(125)
        lines=["Resolved by model:"]+[f"  {k}: {n:,}" for k,n in sorted(summary["by_model"].items())]
        lines+=["","Resolution methods:"]+[f"  {k}: {n:,}" for k,n in sorted(summary["by_method"].items())]
        by.setPlainText("\n".join(lines));v.addWidget(by)
        populate();close=QDialogButtonBox(QDialogButtonBox.Close);close.rejected.connect(d.reject);v.addWidget(close);d.exec()
        self.status.setText(f"Resolution preview • {summary['resolved']:,} resolved • {summary['unresolved']:,} unresolved • no guesses")

    def render_resolved_sample(self):
        if self.task and self.task.isRunning():
            QMessageBox.information(self,APP_NAME,"A render task is already running.");return
        assignments=[x for x in self._resolved_assignments() if x["state"]=="resolved" and x.get("model")]
        missing=[x for x in assignments if not cached_variant_thumbnail(x["model"]["path"],x["textures"])]
        target=int(self.sample_size.currentText())
        if not missing:
            self.status.setText("No missing resolved products are available for sampling.");return

        def spread(items,count):
            if count<=0 or not items:return []
            if len(items)<=count:return list(items)
            if count==1:return [items[len(items)//2]]
            last=len(items)-1;idxs=[]
            for i in range(count):
                idx=int(round(i*last/(count-1)))
                if idx not in idxs:idxs.append(idx)
            return [items[i] for i in idxs]

        exact=[x for x in missing if x["method"]=="exact PID model"]
        defaults=[x for x in missing if x["method"]=="official default model"]
        other=[x for x in missing if x["method"] not in ("exact PID model","official default model")]
        if len(missing)<=target:
            sample=list(missing)
        elif defaults and exact:
            default_slots=min(len(defaults),max(1,target//2))
            sample=spread(defaults,default_slots)+spread(exact,target-default_slots)
        elif defaults:
            sample=spread(defaults,target)
        elif exact:
            sample=spread(exact,target)
        else:
            sample=spread(other,target)
        sample=sorted(sample,key=lambda x:int(x["pid"]) if str(x["pid"]).isdigit() else str(x["pid"]))

        methods=resolution_summary(sample)["by_method"]
        method_text=" • ".join(f"{k}: {v:,}" for k,v in sorted(methods.items()))
        preview="\n".join(f"{x['pid']} → {x['model']['filename']} ({x['method']})" for x in sample[:12])
        if len(sample)>12:preview+=f"\n… plus {len(sample)-12} more"
        answer=QMessageBox.question(
            self,"Render Resolved Sample",
            f"Render a {len(sample)}-product validation sample from {len(missing):,} missing resolved products?\n\n"
            f"{method_text}\n\n{preview}\n\nUnresolved products are excluded. The sampled PID list and binding results are exportable.",
            QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
        if answer!=QMessageBox.Yes:return
        self.progress.setRange(0,len(sample));self.progress.setValue(0);self.sample_button.setEnabled(False);self.cancel_button.setEnabled(True)
        self.status.setText(f"Rendering {len(sample):,} resolved sample products…")
        self.task=ResolvedVariantRenderTask(sample,int(self.workers.currentText()));self.task.progress.connect(self.on_progress);self.task.done.connect(self.resolved_sample_finished);self.task.start()

    def resolved_sample_finished(self,result):
        self.sample_button.setEnabled(True);self.cancel_button.setEnabled(False);self.task=None;self.last_failures=result.get("failures",[]);self.failures_button.setEnabled(bool(self.last_failures))
        self.last_render_report={"folder":self.folder_name(),"scope":"sample","created":time.strftime("%Y-%m-%d %H:%M:%S"),"result":result};self.export_render_button.setEnabled(True)
        try:self.last_auto_report=write_render_report_files(self.last_render_report)
        except Exception:self.last_auto_report=None
        outputs=result.get("outputs",[])
        d=QDialog(self);d.setWindowTitle("Resolved Sample Results");d.resize(1120,760);v=QVBoxLayout(d)
        h=QLabel(f"{len(outputs):,} successful/cached • {result.get('failed',0):,} failed");h.setObjectName("title");v.addWidget(h)
        area=QScrollArea();area.setWidgetResizable(True);host=QWidget();grid=QGridLayout(host);grid.setAlignment(Qt.AlignTop|Qt.AlignLeft)
        for i,x in enumerate(outputs):
            card=QFrame();card.setObjectName("card");cv=QVBoxLayout(card);im=QLabel();im.setAlignment(Qt.AlignCenter);p=QPixmap(str(x["output"]))
            if not p.isNull():im.setPixmap(p.scaled(240,190,Qt.KeepAspectRatio,Qt.SmoothTransformation))
            else:im.setText("Preview unavailable")
            cv.addWidget(im);lab=QLabel(f"PID {x['pid']}\n{x['model']['filename']}");lab.setAlignment(Qt.AlignCenter);lab.setWordWrap(True);cv.addWidget(lab)
            bs=x.get("binding_summary") or {};bind=QLabel(f"{x.get('binding_profile') or 'binding unknown'} • maps {bs.get('assigned_map_count','?')}/{bs.get('map_target_count','?')} assigned");bind.setAlignment(Qt.AlignCenter);bind.setWordWrap(True);bind.setStyleSheet("color:#8f98a3");cv.addWidget(bind)
            if bs.get("unassigned_maps") or bs.get("unused_linked_textures"):
                warn=QLabel(f"Binding review • unassigned maps: {len(bs.get('unassigned_maps',[]))} • unused textures: {len(bs.get('unused_linked_textures',[]))}");warn.setAlignment(Qt.AlignCenter);warn.setWordWrap(True);cv.addWidget(warn)
            grid.addWidget(card,i//4,i%4)
        if not outputs:grid.addWidget(QLabel("No successful renders."),0,0)
        area.setWidget(host);v.addWidget(area,1)
        buttons=QHBoxLayout();export=QPushButton("Export Results");export.clicked.connect(lambda:self.export_last_render(d));buttons.addWidget(export);buttons.addStretch();bb=QDialogButtonBox(QDialogButtonBox.Close);bb.rejected.connect(d.reject);buttons.addWidget(bb);v.addLayout(buttons);d.exec()
        report_note=f" • auto-report: {self.last_auto_report['csv']}" if self.last_auto_report else ""
        self.status.setText(f"Sample complete • rendered {result.get('rendered',0):,} • failed {result.get('failed',0):,} • binding complete {result.get('binding_complete',0):,} • review {result.get('binding_review',0):,}{report_note}")

    def export_last_render(self,parent=None):
        report=self.last_render_report
        if not report:return
        folder=report.get("folder") or "products";scope=report.get("scope") or "render"
        path,_=QFileDialog.getSaveFileName(parent or self,"Export Render Results",f"there-inspector-{folder}-{scope}-render-results.csv","CSV (*.csv);;JSON (*.json)")
        if not path:return
        result=report.get("result") or {};records=result.get("records") or []
        try:
            if path.lower().endswith(".json"):
                Path(path).write_text(json.dumps(report,indent=2),encoding="utf-8")
            else:
                if not path.lower().endswith(".csv"):path += ".csv"
                with open(path,"w",newline="",encoding="utf-8-sig") as fh:
                    w=csv.writer(fh)
                    w.writerow(["folder","scope","created","pid","status","resolution_method","model","model_path","binding_profile","material_count","color_material_count","assigned_material_count","map_target_count","assigned_map_count","unassigned_maps","unassigned_color_materials","unused_linked_textures","texture_count","textures","render_output","cached","returncode","message"])
                    for x in records:
                        w.writerow([folder,scope,report.get("created",""),x.get("pid",""),x.get("status",""),x.get("method",""),x.get("model",""),x.get("model_path",""),x.get("binding_profile",""),x.get("material_count",""),x.get("color_material_count",""),x.get("assigned_material_count",""),x.get("map_target_count",""),x.get("assigned_map_count","")," | ".join(x.get("unassigned_maps",[]))," | ".join(x.get("unassigned_color_materials",[]))," | ".join(x.get("unused_linked_textures",[])),x.get("texture_count",0)," | ".join(x.get("textures",[])),x.get("output",""),x.get("cached",False),x.get("returncode",""),x.get("message","")])
            self.status.setText(f"Exported {len(records):,} render result records • {path}")
        except Exception as exc:
            QMessageBox.critical(parent or self,APP_NAME,f"Render result export failed:\n{exc}")

    def delete_resolved_variant(self,item):
        if not item.get("model"):return
        remove_cached_variant(item["model"]["path"],item["textures"]);self.refresh()

    def delete_all_resolved_variants(self):
        if self.task and self.task.isRunning():
            QMessageBox.warning(self,APP_NAME,"Stop the current render task before deleting cached renders.");return
        assignments=[x for x in self._basic_resolved_assignments() if x["state"]=="resolved" and x.get("model")]
        cached=[x for x in assignments if cached_variant_thumbnail(x["model"]["path"],x["textures"])]
        if not cached:
            self.status.setText("No cached resolver-driven renders in this folder.");return
        answer=QMessageBox.question(self,"Delete Resolved Renders",f"Delete {len(cached):,} cached product variant renders from {self.folder_name()}?\n\nOnly generated PNG previews are removed. Source models, textures and database records are untouched.",QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
        if answer!=QMessageBox.Yes:return
        removed=0
        for x in cached:
            if remove_cached_variant(x["model"]["path"],x["textures"]):removed+=1
        self.refresh();self.status.setText(f"Deleted {removed:,} cached resolver-driven renders.")

    def render_all_resolved(self):
        if self.task and self.task.isRunning():
            QMessageBox.information(self,APP_NAME,"A render task is already running.");return
        all_assignments=self._basic_resolved_assignments()
        assignments=[x for x in all_assignments if x["state"]=="resolved" and x.get("model")]
        unresolved=sum(1 for x in all_assignments if x["state"]=="unresolved")
        cached=[x for x in assignments if cached_variant_thumbnail(x["model"]["path"],x["textures"])]
        missing=[x for x in assignments if not cached_variant_thumbnail(x["model"]["path"],x["textures"])]
        if not missing:
            self.status.setText(f"All resolved products are already rendered • {len(cached):,} cached • {unresolved:,} unresolved skipped.");return
        methods=resolution_summary(missing)["by_method"]
        detail="\n".join(f"  {k}: {v:,}" for k,v in sorted(methods.items()))
        msg=(f"Render {len(missing):,} missing resolved products from {self.folder_name()}?\n\n"
             f"Resolved total: {len(assignments):,}\nAlready rendered: {len(cached):,}\nTo render now: {len(missing):,}\n"
             f"Unresolved and skipped: {unresolved:,}\n\nResolution evidence:\n{detail}\n\n"
             "The batch will continue past individual failures, retain successful renders, and automatically save CSV and JSON reports.")
        answer=QMessageBox.question(self,"Render All Resolved Products",msg,QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
        if answer!=QMessageBox.Yes:return
        self.progress.setRange(0,len(missing));self.progress.setValue(0);self.render_button.setEnabled(False);self.sample_button.setEnabled(False);self.cancel_button.setEnabled(True)
        self.status.setText(f"Rendering {len(missing):,} products with verified model assignments • {unresolved:,} unresolved skipped…")
        self.task=ResolvedVariantRenderTask(missing,int(self.workers.currentText()));self.task.progress.connect(self.on_progress);self.task.done.connect(self.resolved_full_finished);self.task.start()

    def resolved_full_finished(self,result):
        self.render_button.setEnabled(True);self.sample_button.setEnabled(True);self.cancel_button.setEnabled(False);self.task=None
        self.last_failures=result.get("failures",[]);self.failures_button.setEnabled(bool(self.last_failures))
        self.last_render_report={"folder":self.folder_name(),"scope":"resolved-missing","created":time.strftime("%Y-%m-%d %H:%M:%S"),"result":result};self.export_render_button.setEnabled(True)
        try:self.last_auto_report=write_render_report_files(self.last_render_report)
        except Exception:self.last_auto_report=None
        self.refresh()
        if not result.get("cancelled"):self.progress.setValue(self.progress.maximum())
        status=("Render stopped" if result.get("cancelled") else "Render complete")
        report_note=f" • report {self.last_auto_report['csv']}" if self.last_auto_report else ""
        self.status.setText(f"{status} • completed {result.get('completed',0):,}/{result.get('total',0):,} • rendered {result.get('rendered',0):,} • failed {result.get('failed',0):,} • binding complete {result.get('binding_complete',0):,} • review {result.get('binding_review',0):,}{report_note}")

        d=QDialog(self);d.setWindowTitle("Resolved Render Results");d.resize(760,480);v=QVBoxLayout(d)
        title=QLabel("Render batch completed" if not result.get("cancelled") else "Render batch stopped");title.setObjectName("title");v.addWidget(title)
        summary=QPlainTextEdit();summary.setReadOnly(True)
        lines=[
            f"Folder: {self.folder_name()}",
            f"Completed: {result.get('completed',0):,} / {result.get('total',0):,}",
            f"Rendered: {result.get('rendered',0):,}",
            f"Failed: {result.get('failed',0):,}",
            f"Unsupported model decoder: {result.get('unsupported',0):,}",
            f"Binding complete: {result.get('binding_complete',0):,}",
            f"Binding review: {result.get('binding_review',0):,}",
        ]
        if self.last_auto_report:
            lines += ["",f"CSV report: {self.last_auto_report['csv']}",f"JSON report: {self.last_auto_report['json']}"]
        summary.setPlainText("\n".join(lines));v.addWidget(summary,1)
        buttons=QHBoxLayout();export=QPushButton("Export Copy…");export.clicked.connect(lambda:self.export_last_render(d));buttons.addWidget(export)
        if self.last_failures:
            failures=QPushButton(f"Failures ({len(self.last_failures):,})");failures.clicked.connect(self.show_failures);buttons.addWidget(failures)
        buttons.addStretch();close=QDialogButtonBox(QDialogButtonBox.Close);close.rejected.connect(d.reject);buttons.addWidget(close);v.addLayout(buttons);d.exec()

    def analyze_uv_families(self):
        """Decode every model in the selected folder and group exact LOD0 UV layouts."""
        folder=self.folder_name();models=self.db.models_in_folder(folder,10000)
        if not models:
            QMessageBox.information(self,APP_NAME,f"No indexed models were found in {folder}.");return
        self.uv_analyze_button.setEnabled(False);self.status.setText(f"Analyzing LOD0 UV topology for {len(models):,} {folder} model(s)…");QApplication.processEvents()
        results=[];failures=[]
        for i,row in enumerate(models,1):
            try:
                info=analyze_model_uv(row["path"],0)
                results.append((dict(row),info))
                self.status.setText(f"UV analysis {i:,} / {len(models):,} • {row['filename']}")
                QApplication.processEvents()
            except Exception as exc:
                failures.append((dict(row),str(exc)))
        self.uv_analyze_button.setEnabled(True)
        families={}
        for row,info in results:
            families.setdefault(info["layout_sha256"],[]).append((row,info))

        d=QDialog(self);d.setWindowTitle(f"UV Families — {folder}");d.resize(1180,760);v=QVBoxLayout(d)
        summary=QLabel(f"{len(results):,} models analyzed • {len(families):,} exact LOD0 UV families • {len(failures):,} decode failure(s).  Families are derived from model UV coordinates, not texture artwork.")
        summary.setWordWrap(True);v.addWidget(summary)
        split=QSplitter(Qt.Horizontal);table=QTableWidget(0,5);table.setHorizontalHeaderLabels(["Family","Models","Triangles","UV points","Layout fingerprint"]);table.setSelectionBehavior(QAbstractItemView.SelectRows);table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        preview=QWidget();pv=QVBoxLayout(preview);image=QLabel("Select a UV family");image.setAlignment(Qt.AlignCenter);image.setMinimumSize(500,500);details=QPlainTextEdit();details.setReadOnly(True);details.setMaximumHeight(170);pv.addWidget(image,1);pv.addWidget(details)
        family_rows=[]
        for family_no,(digest,members) in enumerate(sorted(families.items(),key=lambda x:(-len(x[1]),x[0])),1):
            family_rows.append((family_no,digest,members))
        table.setRowCount(len(family_rows))
        for r,(family_no,digest,members) in enumerate(family_rows):
            info=members[0][1];names=", ".join(x[0]["filename"] for x in members)
            vals=(f"UV-{family_no:02d}",names,str(info["triangle_count"]),str(info["unique_uv_points"]),digest[:16]+"…")
            for c,val in enumerate(vals):table.setItem(r,c,QTableWidgetItem(val))
        table.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch);table.horizontalHeader().setSectionResizeMode(4,QHeaderView.Stretch)
        def selected():
            r=table.currentRow()
            if r<0:return
            family_no,digest,members=family_rows[r];row,info=members[0]
            pix=QPixmap(info["wireframe"])
            if not pix.isNull():image.setPixmap(pix.scaled(520,520,Qt.KeepAspectRatio,Qt.SmoothTransformation))
            else:image.setText("Wireframe preview unavailable")
            lines=[f"UV-{family_no:02d}",f"layout_sha256: {digest}",f"material_topology_sha256: {info['topology_sha256']}",f"models: {', '.join(x[0]['filename'] for x in members)}",f"triangles: {info['triangle_count']}",f"unique UV points: {info['unique_uv_points']}",f"bounds: {info['bounds']}",f"wireframe: {info['wireframe']}"]
            details.setPlainText("\n".join(lines))
        table.itemSelectionChanged.connect(selected);split.addWidget(table);split.addWidget(preview);split.setStretchFactor(0,1);split.setStretchFactor(1,1);v.addWidget(split,1)
        if failures:
            fail=QLabel("Failures: "+" | ".join(f"{x[0]['filename']}: {x[1]}" for x in failures));fail.setWordWrap(True);v.addWidget(fail)
        close=QDialogButtonBox(QDialogButtonBox.Close);close.rejected.connect(d.reject);v.addWidget(close)
        if family_rows:table.selectRow(0)
        d.exec()
        self.status.setText(f"UV analysis complete • {len(results):,} models • {len(families):,} exact LOD0 UV families • {len(failures):,} failures")

    def delete_variant(self,paths):
        if not self.model:return
        remove_cached_variant(self.model["path"],paths);self.refresh()

    def delete_all_variants(self):
        if not self.model:return
        if self.task and self.task.isRunning():
            QMessageBox.warning(self,APP_NAME,"Stop the current render batch before deleting cached renders.");return
        cached=[paths for _,paths in self.compatible_sets(self.model) if cached_variant_thumbnail(self.model["path"],paths)]
        if not cached:
            self.status.setText("No cached variant renders to delete for this model.");return
        name=self.model.get("filename",Path(self.model["path"]).name)
        msg=f"Delete all {len(cached):,} cached vehicle variant renders for {name}?\\n\\nOnly generated preview PNGs will be removed. Source models, DDS textures, and database records will NOT be changed."
        box=QMessageBox(QMessageBox.Warning,"Delete All Variant Renders",msg,QMessageBox.Cancel,self)
        delete=box.addButton("Delete All Renders",QMessageBox.DestructiveRole);box.setDefaultButton(QMessageBox.Cancel);box.exec()
        if box.clickedButton()!=delete:return
        removed=sum(1 for paths in cached if remove_cached_variant(self.model["path"],paths))
        self.refresh();self.status.setText(f"Deleted {removed:,} cached variant renders for {name}.")

    def cancel_render(self):
        if self.task and self.task.isRunning():
            self.task.cancel();self.cancel_button.setEnabled(False);self.status.setText("Stopping now… active Blender processes are being terminated and no new variants will start.")

    def open_textures(self,paths):
        d=QDialog(self);d.setWindowTitle("Texture Set");d.resize(900,650);lay=QGridLayout(d)
        present={self.product_id(Path(p).name)[1] for p in paths if self.product_id(Path(p).name)}
        is_bg=self.folder_name().lower()=="bg"
        roles={}
        if is_bg:
            roles={1:"Body"}
            roles.update({2:"Window Color",3:"Window Transparency"} if (2 in present and 3 in present and 4 not in present) else {3:"Window Color",4:"Window Transparency"})
        for i,p in enumerate(paths):
            parsed=self.product_id(Path(p).name);slot=parsed[1] if parsed else None
            if slot:
                subtitle=f"_{slot} • {roles.get(slot,'BG Texture')}" if is_bg else f"Texture slot _{slot} • model-driven"
            else:subtitle=""
            lay.addWidget(TextureThumb(p,Path(p).name,subtitle),i//3,i%3)
        d.exec()
    def render_missing(self):
        if self.task and self.task.isRunning():
            self.status.setText("A render batch is still stopping. Wait for Stopped before starting again.");return
        self.model=self.current_model()
        if not self.model:return
        compatible=self.compatible_sets(self.model)
        if not self.template_box.currentData():
            QMessageBox.warning(self,APP_NAME,"Choose the reference template that belongs to the selected model before rendering.");return
        QMessageBox.warning(self,APP_NAME,"Automatic template classification is temporarily in analysis-only mode.\n\nThe current coarse similarity metric is not selective enough to safely decide which PID sets belong on this model. No render batch was started.");return
        missing=[x for x in compatible if not cached_variant_thumbnail(self.model["path"],x[1])]
        if not missing:self.status.setText("All mapped texture sets for this model/template are already rendered.");return
        model_name=self.model.get("filename",Path(self.model["path"]).name)
        answer=QMessageBox.question(self,"Confirm Variant Model",f"Render {len(missing):,} texture sets matched to {self.template_box.currentText()} on {model_name}?\\n\\nThe model/template pairing is explicit; Inspector will not guess it. Uncertain/unmapped sets are excluded.",QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
        if answer!=QMessageBox.Yes:return
        self.progress.setRange(0,len(missing));self.progress.setValue(0);self.progress.setFormat("%v / %m • %p%");self.render_button.setEnabled(False);self.cancel_button.setEnabled(True);worker_count=int(self.workers.currentText());engine_name="persistent" if self.engine.currentIndex()==0 else "one-shot";self.status.setText(f"Starting {engine_name} render • {len(missing):,} missing variants • {worker_count} worker(s)…");self.task=VariantRenderTask(self.model,missing,worker_count,self.engine.currentIndex()==0);self.task.progress.connect(self.on_progress);self.task.done.connect(self.finished);self.task.start()
    def on_progress(self,i,total,pid,rate,remaining):
        self.progress.setValue(i);self.status.setText(f"Rendering {i:,} / {total:,} • PID {pid} • {rate:.2f}/sec • ETA {ThumbnailBatchTask._fmt(remaining)}")
    def show_failures(self):
        if not self.last_failures:return
        d=QDialog(self);d.setWindowTitle(f"Vehicle Variant Failures ({len(self.last_failures)})");d.resize(1000,650);v=QVBoxLayout(d)
        table=QTableWidget(len(self.last_failures),3);table.setHorizontalHeaderLabels(["PID","Return code","Reason"]);table.horizontalHeader().setStretchLastSection(True);table.setSelectionBehavior(QAbstractItemView.SelectRows);table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        detail=QPlainTextEdit();detail.setReadOnly(True);detail.setMinimumHeight(220)
        for row,item in enumerate(self.last_failures):
            table.setItem(row,0,QTableWidgetItem(str(item.get("pid",""))));table.setItem(row,1,QTableWidgetItem(str(item.get("returncode",""))));table.setItem(row,2,QTableWidgetItem(str(item.get("message","Render failed"))))
        def selected():
            row=table.currentRow()
            if row<0:return
            x=self.last_failures[row];detail.setPlainText(f"PID: {x.get('pid')}\nReturn code: {x.get('returncode')}\nOutput: {x.get('output','')}\n\n{x.get('log','')}")
        table.itemSelectionChanged.connect(selected);v.addWidget(table,1);v.addWidget(QLabel("Blender / renderer details"));v.addWidget(detail)
        export=QPushButton("Export Failure Report");export.clicked.connect(lambda:self.export_failures(d));v.addWidget(export)
        if self.last_failures:table.selectRow(0)
        d.exec()
    def export_failures(self,parent=None):
        if not self.last_failures:return
        path,_=QFileDialog.getSaveFileName(parent or self,"Export Failure Report","vehicle_variant_failures.json","JSON (*.json)")
        if not path:return
        Path(path).write_text(json.dumps({"model":self.model.get("filename") if self.model else None,"folder":self.folder_name(),"created":time.strftime("%Y-%m-%d %H:%M:%S"),"failures":self.last_failures},indent=2),encoding="utf-8")
        self.status.setText(f"Exported {len(self.last_failures):,} failures to {path}")

    def finished(self,result):
        self.render_button.setEnabled(True);self.cancel_button.setEnabled(False);self.task=None;self.last_failures=result.get("failures",[]);self.failures_button.setEnabled(bool(self.last_failures));self.refresh()
        if result.get("cancelled"):
            self.status.setText(f"Stopped • completed {result['completed']:,} • rendered {result['rendered']:,} • failed {result['failed']:,}")
        else:
            self.progress.setValue(self.progress.maximum());self.status.setText(f"Complete • rendered {result['rendered']:,} • cached {result['cached']:,} • failed {result['failed']:,}")

class IntelligencePage(QWidget):
    def __init__(self,db):
        super().__init__();self.db=db;self.task=None;self.result=None;b=QVBoxLayout(self);b.setContentsMargins(28,24,28,24)
        h=QLabel("Intelligence");h.setObjectName("title");b.addWidget(h)
        sub=QLabel("Evidence-first model analysis. Inspector reports relationships it can prove from deterministic fingerprints; unsupported assets remain unknown.");sub.setWordWrap(True);b.addWidget(sub)
        row=QHBoxLayout();self.folder=QComboBox();self.folder.setMinimumWidth(260)
        self.folder.addItem("All indexed model folders",None)
        for x in self.db.asset_folders():
            if int(x["model_count"] or 0):self.folder.addItem(f"{x['folder']}  •  {int(x['model_count']):,} models",x["folder"])
        idx=self.folder.findData("bg")
        if idx>=0:self.folder.setCurrentIndex(idx)
        go=QPushButton("Analyze Model Facts");go.clicked.connect(self.analyze);self.go=go
        export=QPushButton("Export Results");export.clicked.connect(self.export_results);export.setEnabled(False);self.export_button=export
        row.addWidget(QLabel("Scope"));row.addWidget(self.folder,1);row.addWidget(go);row.addWidget(export);b.addLayout(row)
        cards=QHBoxLayout();self.models_card=QLabel("Models\n—");self.relationship_card=QLabel("Proven relationships\n—");self.unknown_card=QLabel("Unsupported / unknown\n—")
        for c in (self.models_card,self.relationship_card,self.unknown_card):
            c.setObjectName("card");c.setAlignment(Qt.AlignCenter);c.setMinimumHeight(72);cards.addWidget(c)
        b.addLayout(cards)
        self.status=QLabel("Ready. Start with BG to validate the evidence model.");self.status.setWordWrap(True);b.addWidget(self.status)
        self.table=QTableWidget(0,6);self.table.setHorizontalHeaderLabels(["Evidence","LOD","Asset A","Asset B","Measured result","Fingerprint"]);self.table.setSelectionBehavior(QAbstractItemView.SelectRows);self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header=self.table.horizontalHeader();header.setSectionResizeMode(QHeaderView.Interactive);header.setStretchLastSection(False);header.setMinimumSectionSize(70)
        for col,width in enumerate((190,75,220,220,560,220)):self.table.setColumnWidth(col,width)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel);self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel);self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded);self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded);self.table.setWordWrap(False);b.addWidget(self.table,1)
        self.unknown=QPlainTextEdit();self.unknown.setReadOnly(True);self.unknown.setMaximumHeight(120);self.unknown.setPlaceholderText("Unsupported / unknown assets will be listed here instead of guessed.");b.addWidget(self.unknown)

    def _rows(self):
        folder=self.folder.currentData()
        if folder:return self.db.models_in_folder(folder,100000)
        rows=[];offset=0
        while True:
            page,total=self.db.search_models_page("",2000,offset);rows.extend(page);offset+=len(page)
            if not page or offset>=total:break
        return rows

    def analyze(self):
        if self.task and self.task.isRunning():return
        rows=self._rows()
        if not rows:self.status.setText("No models in this scope.");return
        self.go.setEnabled(False);self.status.setText(f"Reading deterministic model facts for {len(rows):,} model(s)… No similarity thresholds are used.")
        self.task=Task(analyze_model_rows,rows);self.task.done.connect(self.finished);self.task.failed.connect(self.failed);self.task.start()

    def failed(self,error):
        self.go.setEnabled(True);self.task=None;self.status.setText(error);QMessageBox.critical(self,APP_NAME,error)

    def finished(self,result):
        self.go.setEnabled(True);self.export_button.setEnabled(True);self.task=None;self.result=result;rels=result["relationships"];unknown=result["unsupported"]
        self.models_card.setText(f"Models\n{result['models']:,}");self.relationship_card.setText(f"Proven relationships\n{len(rels):,}");self.unknown_card.setText(f"Unsupported / unknown\n{len(unknown):,}")
        self.table.setRowCount(len(rels))
        for r,x in enumerate(rels):
            lod="—" if x["lod"]<0 else f"LOD{x['lod']}"
            for c,v in enumerate((x["evidence"],lod,x["filename_a"],x["filename_b"],x.get("details",""),(x["fingerprint"][:20]+"…") if x.get("fingerprint") else "—")):self.table.setItem(r,c,QTableWidgetItem(str(v)))
            self.table.item(r,2).setToolTip(x["asset_a"]);self.table.item(r,3).setToolTip(x["asset_b"])
        self.unknown.setPlainText("\n".join(f"{x['filename']}: {x['reason']}" for x in unknown))
        counts=" • ".join(f"{k}: {v:,}" for k,v in sorted(result["counts"].items()))
        self.status.setText(f"Complete • {result['decoded']:,}/{result['models']:,} decoded • {len(rels):,} proven relationships" + (f" • {counts}" if counts else " • no exact relationships in this scope"))


    def export_results(self):
        if not self.result:return
        folder=self.folder.currentData() or "all-models";suggested=f"there-inspector-{folder}-model-facts.csv"
        path,_=QFileDialog.getSaveFileName(self,"Export Intelligence Results",suggested,"CSV files (*.csv);;JSON files (*.json)")
        if not path:return
        try:
            if path.lower().endswith(".json"):Path(path).write_text(json.dumps(self.result,indent=2),encoding="utf-8")
            else:
                if not path.lower().endswith(".csv"):path += ".csv"
                with open(path,"w",newline="",encoding="utf-8-sig") as fh:
                    w=csv.writer(fh);w.writerow(["record_type","evidence","lod","asset_a","asset_b","measured_result","fingerprint","geometry_shared","geometry_a","geometry_b","uv_shared","uv_a","uv_b","reason"])
                    for x in self.result["relationships"]:w.writerow(["relationship",x.get("evidence",""),x.get("lod",""),x.get("asset_a",""),x.get("asset_b",""),x.get("details",""),x.get("fingerprint",""),x.get("geometry_shared",""),x.get("geometry_a",""),x.get("geometry_b",""),x.get("uv_shared",""),x.get("uv_a",""),x.get("uv_b",""),""])
                    for x in self.result["unsupported"]:w.writerow(["unsupported","","",x.get("path",""),"","","","","","","","","",x.get("reason","")])
            self.status.setText(f"Exported {len(self.result['relationships']):,} relationships and {len(self.result['unsupported']):,} unsupported records • {path}")
        except Exception as exc:QMessageBox.critical(self,APP_NAME,f"Export failed:\\n{exc}")


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
