class BasePlugin:
    """
    The base interface for all plugins in the pipeline.
    """
    def __init__(self, config=None):
        self.config = config or {}
        
    @property
    def name(self) -> str:
        return self.__class__.__name__

    def run(self, document_context):
        """
        Execute the plugin against the provided DocumentContext.
        """
        raise NotImplementedError("Plugins must implement the run() method.")
