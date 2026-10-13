from typing import List, Dict, Any
from gui.edit_actions import GraphAction

class ActionHistory:
    """
    Manages the Undo and Redo stacks for Manual GUI Edits.
    Tracks whether the DocumentGraph differs from the baseline.
    """
    def __init__(self):
        self.undo_stack: List[GraphAction] = []
        self.redo_stack: List[GraphAction] = []
        self._saved_undo_count = 0

    def execute(self, action: GraphAction, elements: List[Dict[str, Any]]) -> bool:
        if action.apply(elements):
            self.undo_stack.append(action)
            self.redo_stack.clear()
            # If we were at a saved state but on the redo stack, the saved state is now lost!
            # If the saved state is ahead of us (i.e. we undid past it and then executed a new action),
            # we can never return to the saved state by simply undoing/redoing.
            # So if _saved_undo_count > len(self.undo_stack) - 1, the saved state is unreachable.
            if self._saved_undo_count > len(self.undo_stack) - 1:
                self._saved_undo_count = -1
            return True
        return False

    def undo(self, elements: List[Dict[str, Any]]) -> GraphAction:
        if self.undo_stack:
            action = self.undo_stack.pop()
            if action.undo(elements):
                self.redo_stack.append(action)
                return action
        return None

    def redo(self, elements: List[Dict[str, Any]]) -> GraphAction:
        if self.redo_stack:
            action = self.redo_stack.pop()
            if action.apply(elements):
                self.undo_stack.append(action)
                return action
        return None

    @property
    def is_modified(self) -> bool:
        # A saved state is unreachable if it was on a branch that was overwritten by a new execute()
        if self._saved_undo_count == -1:
            return True
        return len(self.undo_stack) != self._saved_undo_count

    def clear(self):
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._saved_undo_count = 0
        
    def save_checkpoint(self):
        self._saved_undo_count = len(self.undo_stack)
