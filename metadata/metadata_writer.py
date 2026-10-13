# GUI_V1/metadata/metadata_writer.py
import pikepdf
from metadata.metadata_model import DocumentMetadata
from metadata.xmp_builder import XmpBuilder

class MetadataWriter:
    """
    Writes DocumentMetadata into a pikepdf.Pdf object.
    Does NOT resolve or guess any data.
    """
    
    @staticmethod
    def write(pdf: pikepdf.Pdf, metadata: DocumentMetadata):
        # 1. Catalog /Lang
        if metadata.language:
            pdf.Root.Lang = str(metadata.language)
            
        # 2. Document Info
        if metadata.title:
            pdf.docinfo.Title = metadata.title
        if metadata.author:
            pdf.docinfo.Author = metadata.author
        if metadata.subject:
            pdf.docinfo.Subject = metadata.subject
        if metadata.keywords:
            pdf.docinfo.Keywords = metadata.keywords
            
        # 3. XMP Metadata
        # We explicitly generate XMP using our XmpBuilder
        xmp_str = XmpBuilder.build(metadata)
        xmp_stream = pdf.make_stream(xmp_str.encode("utf-8"))
        xmp_stream.Type = pikepdf.Name("/Metadata")
        xmp_stream.Subtype = pikepdf.Name("/XML")
        pdf.Root.Metadata = xmp_stream
        
        # 4. ViewerPreferences /DisplayDocTitle
        if metadata.display_doc_title is True:
            if "/ViewerPreferences" not in pdf.Root:
                pdf.Root.ViewerPreferences = pikepdf.Dictionary()
            pdf.Root.ViewerPreferences.DisplayDocTitle = True

        # 5. Default Initial View Properties
        pdf.Root.PageMode = pikepdf.Name("/UseNone")       # Navigation tab: Page Only
        pdf.Root.PageLayout = pikepdf.Name("/SinglePage")  # Page layout: Single Page
        if len(pdf.pages) > 0:                             # Magnification: Fit Page
            pdf.Root.OpenAction = pikepdf.Array([pdf.pages[0].obj, pikepdf.Name("/Fit")])
