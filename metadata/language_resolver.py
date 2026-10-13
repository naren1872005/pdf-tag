# GUI_V1/metadata/language_resolver.py
import re
from typing import Optional, Tuple

class LanguageResolver:
    """
    Deterministically resolves and normalizes the document language.
    Follows a strict hierarchy and does not use OS locales.
    """
    
    # Explicit mapping to BCP 47 canonical tags
    NORMALIZATION_MAP = {
        "en_us": "en-US",
        "en-us": "en-US",
        "english (us)": "en-US",
        "english (united states)": "en-US",
        "en_gb": "en-GB",
        "en-gb": "en-GB",
        "english (uk)": "en-GB",
        "english (united kingdom)": "en-GB",
        "de_de": "de-DE",
        "de-de": "de-DE",
        "german (germany)": "de-DE",
        "fr_fr": "fr-FR",
        "fr-fr": "fr-FR",
        "french (france)": "fr-FR"
    }

    # Specifically blacklisted ambiguous terms
    AMBIGUOUS_TERMS = {"english", "german", "french", "spanish", "italian", "chinese"}

    @classmethod
    def resolve(
        cls, 
        document_config: Optional[str] = None, 
        client_config: Optional[str] = None, 
        application_config: Optional[str] = None
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Resolves the document language following the strict order:
        1. Explicit document/project language
        2. Existing client configuration
        3. Existing application configuration
        """
        
        configs = [
            (document_config, "document"),
            (client_config, "client_config"),
            (application_config, "application_config")
        ]
        
        for val, source in configs:
            if val is not None and str(val).strip():
                normalized = cls.normalize(str(val))
                if normalized:
                    return normalized, source
                    
        return None, None
        
    @classmethod
    def normalize(cls, lang: str) -> Optional[str]:
        """
        Normalizes supported language representations into a canonical BCP 47 value.
        Returns None if ambiguous, empty, or unresolvable.
        """
        lang_clean = lang.strip().lower()
        
        if not lang_clean:
            return None
            
        if lang_clean in cls.NORMALIZATION_MAP:
            return cls.NORMALIZATION_MAP[lang_clean]
            
        if lang_clean in cls.AMBIGUOUS_TERMS:
            return None
            
        # Basic BCP 47 format check (e.g. 'en', 'en-US', 'es-419')
        if re.match(r"^[a-z]{2,3}(-[a-z0-9]{2,8})?$", lang_clean):
            parts = lang_clean.split('-')
            if len(parts) == 2:
                # Ensure the region/script part is correctly capitalized
                return f"{parts[0]}-{parts[1].upper()}"
            return lang_clean
            
        return None
