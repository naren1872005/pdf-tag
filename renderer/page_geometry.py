# GUI/renderer/page_geometry.py
"""
One conversion from the semantic JSON frame to PDF user space, for every page.

The JSON (Gemini extractor / native extractor) describes a page the way it is
SEEN: the rendered page image, i.e. the CropBox area, after /Rotate, with a
bottom-left origin ("CropBox-local, bottom-up, in the displayed orientation").

PDF content, annotations and structure /BBox live in PDF user space: unrotated,
absolute coordinates where the MediaBox/CropBox may start anywhere.

PageFrame handles, per page and from the page's own dictionaries:
  * CropBox offset (any value),           * MediaBox with a non-zero origin,
  * CropBox/MediaBox inherited from /Pages, * boxes written "backwards",
  * /Rotate 0/90/180/270,                  * CropBox missing (falls back to MediaBox).
No value is specific to any document.
"""


def _inherited(page_dict, key):
    node, depth = page_dict, 0
    while node is not None and depth < 32:
        try:
            if key in node:
                return node[key]
            node = node.get("/Parent")
        except Exception:
            return None
        depth += 1
    return None


def _norm_box(box, default=(0.0, 0.0, 612.0, 792.0)):
    try:
        x0, y0, x1, y1 = [float(v) for v in box]
        return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    except Exception:
        return default


class PageFrame:
    def __init__(self, pike_page, rotation=None):
        page_dict = pike_page.obj if hasattr(pike_page, "obj") else pike_page
        media = _norm_box(_inherited(page_dict, "/MediaBox"))
        crop_raw = _inherited(page_dict, "/CropBox")
        crop = _norm_box(crop_raw, media) if crop_raw is not None else media
        # CropBox is clipped to the MediaBox (ISO 32000 14.11.2)
        crop = (max(crop[0], media[0]), max(crop[1], media[1]), min(crop[2], media[2]), min(crop[3], media[3]))
        if crop[2] <= crop[0] or crop[3] <= crop[1]:
            crop = media
        if rotation is None:
            try:
                rotation = int(_inherited(page_dict, "/Rotate") or 0)
            except Exception:
                rotation = 0
        self.rotation = rotation % 360
        self.crop = crop
        self.cw = crop[2] - crop[0]          # unrotated CropBox width
        self.ch = crop[3] - crop[1]          # unrotated CropBox height
        # size of the page as displayed (what Gemini sees)
        if self.rotation in (90, 270):
            self.view_w, self.view_h = self.ch, self.cw
        else:
            self.view_w, self.view_h = self.cw, self.ch

    @property
    def crop_offset(self):
        """Lower-left corner of the CropBox in PDF user space."""
        return (self.crop[0], self.crop[1])

    def view_point_to_pdf(self, x, y):
        """(x, y) in the displayed frame (bottom-up, CropBox-local) -> PDF user space."""
        r = self.rotation
        # displayed top-down coordinates
        u, v = x, self.view_h - y
        # undo the rotation (clockwise /Rotate) -> unrotated top-down, CropBox-local
        if r == 0:
            a, b = u, v
        elif r == 90:
            a, b = v, self.ch - u
        elif r == 180:
            a, b = self.cw - u, self.ch - v
        else:  # 270
            a, b = self.cw - v, u
        return (self.crop[0] + a, self.crop[3] - b)

    def json_bbox_to_pdf(self, bbox):
        """[x0, y0, x1, y1] JSON box -> [x0, y0, x1, y1] in PDF user space."""
        x0, y0, x1, y1 = [float(v) for v in bbox]
        pts = [self.view_point_to_pdf(x, y) for x in (x0, x1) for y in (y0, y1)]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return [min(xs), min(ys), max(xs), max(ys)]

    def is_simple(self):
        """True when JSON->PDF is a pure translation (no rotation)."""
        return self.rotation == 0


def frame_for(pdf, page):
    """Cached PageFrame for a pikepdf page (registry lives on the Pdf object)."""
    reg = getattr(pdf, "_page_frames", None)
    if reg is None:
        reg = {}
        try:
            pdf._page_frames = reg
        except Exception:
            pass
    page_dict = page.obj if hasattr(page, "obj") else page
    key = page_dict.objgen
    fr = reg.get(key)
    if fr is None:
        fr = PageFrame(page_dict)
        reg[key] = fr
    return fr
