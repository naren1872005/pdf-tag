import os
import json
import shutil
from pathlib import Path
from typing import Optional, List, Dict, Any

# Demo edition: a project holds at most this many pages (the first N of the PDF).
DEMO_MAX_PAGES = 10


def count_pdf_pages(pdf_path: str) -> int:
    import pikepdf
    with pikepdf.Pdf.open(pdf_path) as pdf:
        return len(pdf.pages)


def write_first_pages(src_path: str, dst_path: str, max_pages: int) -> int:
    """Copy the PDF keeping only its first `max_pages` pages. Everything that would
    still point at a removed page is cleaned: outlines, the old tag tree, article
    threads, link annotations / named destinations to removed pages, and form
    fields living on removed pages. Returns the page count written."""
    import pikepdf
    with pikepdf.Pdf.open(src_path) as pdf:
        total = len(pdf.pages)
        if total <= max_pages:
            pdf.save(dst_path)
            return total
        kept = [pdf.pages[i] for i in range(max_pages)]
        kept_ids = {p.objgen for p in kept}
        root = pdf.Root

        def page_of_dest(dest, depth=0):
            """objgen of the page a destination points to, None if not a page dest."""
            if dest is None or depth > 8:
                return None
            if isinstance(dest, pikepdf.Array) and len(dest) > 0:
                first = dest[0]
                if isinstance(first, pikepdf.Dictionary) and first.is_indirect:
                    return first.objgen
                return None
            if isinstance(dest, pikepdf.Dictionary):
                return page_of_dest(dest.get("/D"), depth + 1)
            if isinstance(dest, (pikepdf.Name, pikepdf.String)):
                key = str(dest).lstrip("/")
                try:
                    names = root.get("/Names")
                    if names is not None and "/Dests" in names:
                        nt = pikepdf.NameTree(names.Dests)
                        if key in nt:
                            return page_of_dest(nt[key], depth + 1)
                except Exception:
                    pass
                old = root.get("/Dests")
                if old is not None and ("/" + key) in old:
                    return page_of_dest(old["/" + key], depth + 1)
            return None

        def points_to_removed(annot):
            d = annot.get("/Dest")
            a = annot.get("/A")
            if d is None and isinstance(a, pikepdf.Dictionary) and str(a.get("/S", "")) == "/GoTo":
                d = a.get("/D")
            if d is None:
                return False
            target = page_of_dest(d)
            return target is not None and target not in kept_ids

        # link annotations on kept pages that jump to removed pages
        for p in kept:
            annots = p.get("/Annots")
            if annots is None:
                continue
            keep = [a for a in annots if not (isinstance(a, pikepdf.Dictionary)
                                              and str(a.get("/Subtype", "")) == "/Link" and points_to_removed(a))]
            if len(keep) != len(annots):
                p.Annots = pikepdf.Array(keep)

        # widgets on kept pages (to prune form fields)
        kept_annots = set()
        for p in kept:
            for a in p.get("/Annots") or []:
                if isinstance(a, pikepdf.Dictionary) and a.is_indirect:
                    kept_annots.add(a.objgen)

        del pdf.pages[max_pages:]

        for key in ("/Outlines", "/StructTreeRoot", "/Threads"):
            if key in root:
                del root[key]
        if str(root.get("/PageMode", "")) in ("/UseOutlines", "/UseThumbs"):
            root.PageMode = pikepdf.Name("/UseNone")
        oa = root.get("/OpenAction")
        if oa is not None:
            tgt = page_of_dest(oa)
            if tgt is not None and tgt not in kept_ids:
                root.OpenAction = pikepdf.Array([pdf.pages[0].obj, pikepdf.Name("/Fit")])

        # named destinations to removed pages
        try:
            names = root.get("/Names")
            if names is not None and "/Dests" in names:
                nt = pikepdf.NameTree(names.Dests)
                for k in list(nt.keys()):
                    t = page_of_dest(nt[k])
                    if t is not None and t not in kept_ids:
                        del nt[k]
        except Exception:
            pass
        old = root.get("/Dests")
        if old is not None:
            for k in list(old.keys()):
                t = page_of_dest(old[k])
                if t is not None and t not in kept_ids:
                    del old[k]

        # form fields whose widgets were all on removed pages
        acro = root.get("/AcroForm")
        if acro is not None and "/Fields" in acro:
            def field_alive(f, depth=0):
                if depth > 20:
                    return True
                kids = f.get("/Kids")
                if kids is not None and len(kids) > 0:
                    alive = [k for k in kids if field_alive(k, depth + 1)]
                    if len(alive) != len(kids):
                        f.Kids = pikepdf.Array(alive)
                    return len(alive) > 0
                if "/Subtype" in f or "/Rect" in f:            # a widget
                    if f.is_indirect and f.objgen in kept_annots:
                        return True
                    pg = f.get("/P")
                    return pg is not None and pg.is_indirect and pg.objgen in kept_ids
                return True
            acro.Fields = pikepdf.Array([f for f in acro.Fields if field_alive(f)])

        pdf.remove_unreferenced_resources()
        pdf.save(dst_path)
        return max_pages


class ProjectManager:
    def __init__(self, workspace_dir: str = "workspace"):
        self.workspace_dir = Path(workspace_dir)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.recent_projects_file = self.workspace_dir / "recent_projects.json"
        
        self.current_project_path: Optional[Path] = None
        self.current_project_name: Optional[str] = None
        
        # Ensure recent projects file exists
        if not self.recent_projects_file.exists():
            self._save_recent_projects([])

    def _load_recent_projects(self) -> List[str]:
        try:
            with open(self.recent_projects_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def _save_recent_projects(self, projects: List[str]):
        with open(self.recent_projects_file, "w", encoding="utf-8") as f:
            json.dump(projects, f, indent=4)

    def get_recent_projects(self) -> List[str]:
        """Returns a list of recent project paths."""
        recent = self._load_recent_projects()
        # Filter out projects that no longer exist
        valid_recent = [p for p in recent if Path(p).exists()]
        if len(valid_recent) != len(recent):
            self._save_recent_projects(valid_recent)
        return valid_recent

    def _add_to_recent(self, project_path: Path):
        recent = self.get_recent_projects()
        project_str = str(project_path.resolve())
        if project_str in recent:
            recent.remove(project_str)
        recent.insert(0, project_str)
        # Keep only top 10
        self._save_recent_projects(recent[:10])

    def create_project(self, project_name: str, source_pdf_path: str, max_pages: Optional[int] = None) -> bool:
        """Creates a new project in the workspace and copies the PDF. With
        max_pages only the first max_pages pages are copied. On any failure the
        half-created project folder is removed again."""
        project_path = self.workspace_dir / project_name
        
        if project_path.exists():
            raise FileExistsError(f"Project '{project_name}' already exists in workspace.")
            
        source_pdf = Path(source_pdf_path)
        if not source_pdf.exists() or not source_pdf.is_file():
            raise FileNotFoundError(f"Source PDF not found at {source_pdf_path}")
            
        project_path.mkdir(parents=True, exist_ok=True)
        try:
            target_pdf = project_path / "input.pdf"
            if max_pages:
                write_first_pages(str(source_pdf), str(target_pdf), max_pages)
            else:
                shutil.copy2(source_pdf, target_pdf)
            return self.open_project(str(project_path))
        except Exception:
            self.current_project_path = None
            self.current_project_name = None
            shutil.rmtree(project_path, ignore_errors=True)
            raise

    def open_project(self, project_path_str: str) -> bool:
        """Opens an existing project."""
        project_path = Path(project_path_str)
        if not project_path.exists() or not project_path.is_dir():
            raise FileNotFoundError(f"Project directory not found at {project_path_str}")
            
        input_pdf = project_path / "input.pdf"
        if not input_pdf.exists():
            raise FileNotFoundError(f"No input.pdf found in project directory {project_path_str}")
            
        self.current_project_path = project_path
        self.current_project_name = project_path.name
        
        self._ensure_profile(project_path)
        
        self._add_to_recent(self.current_project_path)
        return True

    def close_project(self):
        """Closes the current project."""
        self.current_project_path = None
        self.current_project_name = None

    def get_current_project_info(self) -> Optional[Dict[str, Any]]:
        if not self.current_project_path:
            return None
            
        profile = None
        profile_path = self.current_project_path / "profile.json"
        if profile_path.exists():
            try:
                with open(profile_path, "r", encoding="utf-8") as f:
                    profile = json.load(f)
            except Exception:
                pass
                
        metadata = self.get_project_metadata()
                
        return {
            "name": self.current_project_name,
            "path": str(self.current_project_path),
            "input_pdf": str(self.current_project_path / "input.pdf"),
            "profile": profile,
            "project_metadata": metadata
        }

    def get_project_metadata(self) -> dict:
        """Returns the user-controlled metadata from metadata.json."""
        if not self.current_project_path:
            return {}
        meta_path = self.current_project_path / "metadata.json"
        if meta_path.exists():
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}
        
    def save_project_metadata(self, metadata_dict: dict):
        """Saves user-controlled metadata to metadata.json."""
        if not self.current_project_path:
            raise RuntimeError("No project is currently open.")
        meta_path = self.current_project_path / "metadata.json"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(metadata_dict, f, indent=4)

    def list_all_projects(self) -> List[str]:
        """Lists all projects in the workspace."""
        projects = []
        for item in self.workspace_dir.iterdir():
            if item.is_dir() and (item / "input.pdf").exists():
                projects.append(str(item.resolve()))
        return projects

    def _ensure_profile(self, project_path: Path):
        from engine.document_profiler import DocumentProfiler
        input_pdf = project_path / "input.pdf"
        profile_json = project_path / "profile.json"
        
        needs_profile = True
        current_hash = DocumentProfiler._compute_hash(str(input_pdf))
        current_size = os.path.getsize(str(input_pdf))
        current_modified = os.path.getmtime(str(input_pdf))
        
        if profile_json.exists():
            try:
                with open(profile_json, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                    # Check cache validity
                    if existing.get("pdf_hash") == current_hash and existing.get("pdf_size") == current_size:
                        needs_profile = False
            except Exception:
                pass
                
        if needs_profile:
            profile_data = DocumentProfiler.profile(str(input_pdf))
            with open(profile_json, "w", encoding="utf-8") as f:
                json.dump(profile_data, f, indent=4)
