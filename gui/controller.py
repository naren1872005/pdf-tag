import os
from engine.project_manager import ProjectManager

class AppController:
    """
    Mediates between the GUI and the underlying Tagging Engine.
    Ensures that business logic does not leak into the GUI layer.
    """
    def __init__(self, project_manager: ProjectManager):
        self.project_manager = project_manager

    def create_project(self, name: str, pdf_path: str, max_pages=None):
        self.project_manager.create_project(name, pdf_path, max_pages=max_pages)

    def open_project(self, project_path: str):
        self.project_manager.open_project(project_path)

    def close_project(self):
        self.project_manager.close_project()

    def get_recent_projects(self):
        return self.project_manager.get_recent_projects()
        
    def get_current_project_info(self):
        return self.project_manager.get_current_project_info()

    def get_project_metadata(self):
        return self.project_manager.get_project_metadata()
        
    def save_project_metadata(self, metadata_dict: dict):
        self.project_manager.save_project_metadata(metadata_dict)

    def get_semantic_json_path(self):
        info = self.get_current_project_info()
        if not info: return None
        return os.path.join(info["path"], "semantic.json")
        
    def has_semantic_json(self):
        path = self.get_semantic_json_path()
        return path is not None and os.path.exists(path)
