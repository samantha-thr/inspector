from __future__ import annotations
import json, os, subprocess, time, threading
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
from pathlib import Path
from PySide6.QtCore import Qt, QSettings, QThread, Signal
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtWidgets import *
from config import APP_NAME, DATABASE_PATH, DEFAULT_SCAN_PATH
from there_texture_decoder import open_texture_image
from model_converter import SUPPORTED_OUTPUTS, conversion_readiness, execute_conversion_job, inspect_conversion_source, prepare_conversion_job
from model_thumbnail import cached_thumbnail, render_model_thumbnail, purge_thumbnail_cache, thumbnail_failure_count, thumbnail_failures, clear_thumbnail_failure, remove_cached_thumbnail, render_metadata, RENDER_VERSION, cached_variant_thumbnail, render_model_variant, remove_cached_variant, PersistentVariantWorker

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
        r=QHBoxLayout();self.search=QLineEdit();self.search.setPlaceholderText("Optional filename/folder/path filter");self.search.returnPressed.connect(self.refresh)
        self.count=QComboBox();self.count.addItems(["25","50","100","250","500","1000","All"]);self.count.setCurrentText("100")
        self.workers=QComboBox();self.workers.addItems(["1","2","3","4"]);self.workers.setCurrentText("2");self.engine=QComboBox();self.engine.addItems(["Persistent (fast)","One-shot (safe)"]);self.workers.setToolTip("Parallel Blender render processes. 2 is a safe default; 3–4 may be faster on high-core systems.")
        self.view=QComboBox();self.view.addItems(["Cached only","All models","Missing renders","Failed renders"])
        go=QPushButton("Render Missing");go.clicked.connect(self.start);refresh=QPushButton("Refresh Gallery");refresh.clicked.connect(self.refresh)
        for w in (self.search,QLabel("Batch"),self.count,QLabel("Workers"),self.workers,QLabel("View"),self.view,go,refresh):r.addWidget(w)
        self.view.currentIndexChanged.connect(self.reset_gallery);self.count.currentIndexChanged.connect(self.reset_gallery);self.search.textChanged.connect(self.reset_gallery)
        b.addLayout(r);self.progress=QProgressBar();self.status=QLabel("Ready");b.addWidget(self.progress);b.addWidget(self.status)
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

class VehicleVariantsPage(QWidget):
    def __init__(self,db):
        super().__init__();self.db=db;self.task=None;self.sets=[];self.model=None;self.raw_texture_count=0;b=QVBoxLayout(self);b.setContentsMargins(28,24,28,24)
        h=QLabel("Vehicle Variants");h.setObjectName("title");b.addWidget(h);b.addWidget(QLabel("Render complete PID texture sets on a shared vehicle model for true 3D design previews."))
        r=QHBoxLayout();self.folder=QComboBox();self.folder.setMinimumWidth(170);self.folder.setToolTip("Indexed asset folder");self.model_box=QComboBox();load=QPushButton("Load Folder");load.clicked.connect(self.load_folder);self.load_button=load
        self.populate_folders()
        self.workers=QComboBox();self.workers.addItems(["1","2","3","4"]);self.workers.setCurrentText("2");self.engine=QComboBox();self.engine.addItems(["Persistent (fast)","One-shot (safe)"]);render=QPushButton("Render Missing Variants");render.clicked.connect(self.render_missing);self.render_button=render
        self.cancel_button=QPushButton("Stop / Cancel");self.cancel_button.setEnabled(False);self.cancel_button.clicked.connect(self.cancel_render)
        self.delete_all_button=QPushButton("Delete All Renders");self.delete_all_button.clicked.connect(self.delete_all_variants)
        self.failures_button=QPushButton("Failures");self.failures_button.setEnabled(False);self.failures_button.clicked.connect(self.show_failures);self.last_failures=[]
        for w in (QLabel("Folder"),self.folder,load,QLabel("Vehicle model"),self.model_box,QLabel("Workers"),self.workers,QLabel("Engine"),self.engine,render,self.cancel_button,self.failures_button,self.delete_all_button):r.addWidget(w)
        b.addLayout(r);self.progress=QProgressBar();self.status=QLabel("Ready");b.addWidget(self.progress);b.addWidget(self.status)
        self.area=QScrollArea();self.area.setWidgetResizable(True);b.addWidget(self.area,1);self.model_box.currentIndexChanged.connect(self.refresh);self.load_folder()
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
            self.model_box.blockSignals(False);self.discover();self.refresh()
            if self.raw_texture_count:
                self.status.setText(f"Loaded {len(models):,} models • scanned {self.raw_texture_count:,} textures • discovered {len(self.sets):,} PID sets from {folder}")
            else:
                dbg=self.db.texture_folder_debug(folder,8)
                samples=" | ".join(f"{x['folder']!r}: {x['count']:,} e.g. {x['sample']}" for x in dbg)
                self.status.setText(f"Loaded {len(models):,} models • scanned 0 textures for {folder} • DB matches: {samples or 'none'}")
        finally:self.load_button.setEnabled(True)
    def discover(self):
        groups={};textures=self.db.textures_in_folder(self.folder_name(),100000);self.raw_texture_count=len(textures)
        for t in textures:
            parsed=self.product_id(t["filename"])
            if not parsed:continue
            pid,slot=parsed;groups.setdefault(pid,{})[slot]=t["path"]
        self.sets=[(pid,[slots[k] for k in sorted(slots)]) for pid,slots in sorted(groups.items(),key=lambda x:int(x[0]))]
    def current_model(self):
        d=self.model_box.currentData();return d if d else None
    def refresh(self):
        self.model=self.current_model();host=QWidget();grid=QGridLayout(host);grid.setAlignment(Qt.AlignTop|Qt.AlignLeft);shown=0;cached=0
        if self.model:
            for pid,paths in self.sets:
                p=cached_variant_thumbnail(self.model["path"],paths)
                if not p:continue
                cached+=1;card=QFrame();card.setObjectName("card");v=QVBoxLayout(card);im=QLabel();im.setAlignment(Qt.AlignCenter);pix=QPixmap(str(p));im.setPixmap(pix.scaled(210,170,Qt.KeepAspectRatio,Qt.SmoothTransformation));v.addWidget(im)
                n=QLabel(f"PID {pid} • {len(paths)} texture(s)");n.setAlignment(Qt.AlignCenter);v.addWidget(n)
                buttons=QHBoxLayout();tex=QPushButton("Open Texture Set");tex.clicked.connect(lambda _,pp=paths:self.open_textures(pp));delete=QPushButton("Delete Render");delete.clicked.connect(lambda _,pp=paths:self.delete_variant(pp));buttons.addWidget(tex);buttons.addWidget(delete);v.addLayout(buttons);grid.addWidget(card,shown//4,shown%4);shown+=1
        if not shown:grid.addWidget(QLabel("No variant renders yet. Choose a model and click Render Missing Variants."),0,0)
        self.area.setWidget(host);self.status.setText(f"{len(self.sets):,} PID texture sets discovered • {cached:,} rendered for selected model")
    def delete_variant(self,paths):
        if not self.model:return
        remove_cached_variant(self.model["path"],paths);self.refresh()

    def delete_all_variants(self):
        if not self.model:return
        if self.task and self.task.isRunning():
            QMessageBox.warning(self,APP_NAME,"Stop the current render batch before deleting cached renders.");return
        cached=[paths for _,paths in self.sets if cached_variant_thumbnail(self.model["path"],paths)]
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
        roles={1:"Body"}
        roles.update({2:"Window Color",3:"Window Transparency"} if (2 in present and 3 in present and 4 not in present) else {3:"Window Color",4:"Window Transparency"})
        for i,p in enumerate(paths):
            parsed=self.product_id(Path(p).name);slot=parsed[1] if parsed else None
            subtitle=f"_{slot} • {roles.get(slot,'Texture')}" if slot else ""
            lay.addWidget(TextureThumb(p,Path(p).name,subtitle),i//3,i%3)
        d.exec()
    def render_missing(self):
        if self.task and self.task.isRunning():
            self.status.setText("A render batch is still stopping. Wait for Stopped before starting again.");return
        self.model=self.current_model()
        if not self.model:return
        missing=[x for x in self.sets if not cached_variant_thumbnail(self.model["path"],x[1])]
        if not missing:self.status.setText("All discovered texture sets are already rendered on this model.");return
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
