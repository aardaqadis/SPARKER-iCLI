"""Executable procedural art tools, shared by the terminal and command studio.

The catalog counts distinct *art recipes*: silhouette, arrangement and internal
construction differ, rather than aliases, colors, sizes or strength presets.
Every recipe renders on demand at full canvas resolution. No extra assets or
dependencies are required; all mutations use the document's undo transaction.
"""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import itertools
import math
import random

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageEnhance, ImageFilter, ImageOps


@dataclass(frozen=True)
class ToolSpec:
    id: str
    name: str
    category: str
    kind: str
    description: str
    engine: str
    recipe: tuple[str, ...]

    @property
    def signature(self):
        return (self.engine, self.recipe)


CATEGORY_DESCRIPTIONS = {
    "botanical": "Leaf silhouettes with different growth arrangements and vein structures.",
    "rosettes": "Floral ornaments with distinct petals, ring constructions and centers.",
    "textile": "Stitch patterns with different paths, repeats and edge construction.",
    "geometric": "Geometric emblems, constructed from distinct shapes and ornament layouts.",
    "tessellation": "Repeatable decorative tiles with different tiling and interior structures.",
    "natural-media": "Textured brush footprints for dry media, foliage and stippled paint.",
    "effects": "Pixel-processing tools operating on the active layer and selection.",
}


def _catalog():
    result = {}
    families = (
        ("botanical", "stamp", "leaf",
         ("lance", "oval", "heart", "ginkgo", "oak", "maple", "fan", "arrow", "holly", "trifoliate"),
         ("single", "paired", "sprig", "whorl", "wreath"),
         ("midrib", "herringbone", "net", "dotted")),
        ("rosettes", "stamp", "rosette",
         ("round", "spear", "heart", "forked", "ribbon", "cup", "starburst", "spindle"),
         ("single-ring", "double-ring", "alternating", "spiral", "corona"),
         ("open", "bead", "seedwheel", "crosshatch", "star")),
        ("textile", "pattern", "stitch",
         ("ladder", "cross", "chevron", "chain", "herringbone", "feather", "braid", "loop", "wave", "zigzag"),
         ("stripe", "offset", "diagonal", "woven", "medallion"),
         ("plain", "knotted", "beaded", "barbed")),
        ("geometric", "stamp", "emblem",
         ("ring", "star", "arrow", "diamond", "hexagon", "triangle", "crescent", "lightning", "cross", "squircle"),
         ("orbit", "lattice", "fan", "weave", "radial"),
         ("contours", "notches", "cutouts", "hatching")),
        ("tessellation", "pattern", "tile",
         ("checker", "brick", "scale", "honeycomb", "triangle", "wave", "maze", "diamond", "basket", "arc"),
         ("rows", "stepped", "diagonal", "pinwheel", "mirrored"),
         ("solid", "outline", "dotted", "striped")),
        ("natural-media", "brush", "media",
         ("chalk", "fan", "rake", "splatter", "cloud", "sponge", "grass", "charcoal", "bristle", "dry"),
         ("centered", "slash", "crescent", "ring", "cross"),
         ("coarse", "fine", "flecked", "striated")),
    )
    for category, kind, engine, shapes, layouts, interiors in families:
        for shape, layout, interior in itertools.product(shapes, layouts, interiors):
            identifier = f"{category}.{shape}.{layout}.{interior}"
            name = f"{shape.replace('-', ' ').title()} / {layout.replace('-', ' ')} / {interior}"
            result[identifier] = ToolSpec(identifier, name, category, kind,
                f"{CATEGORY_DESCRIPTIONS[category]} {shape.title()} construction, {layout} arrangement, {interior} detail.",
                engine, (shape, layout, interior))
    effects = {
        "invert": "Invert RGB while preserving transparency.",
        "grayscale": "Convert luminance to neutral grayscale.",
        "sepia": "Warm monochrome with a three-channel sepia matrix.",
        "solarize": "Invert highlights above the middle tone.",
        "posterize": "Reduce each channel to four bits.",
        "autocontrast": "Expand each channel to its available tonal range.",
        "equalize": "Redistribute the channel histograms.",
        "gaussian-blur": "Soften pixels with a Gaussian kernel.",
        "box-blur": "Average pixels inside a square neighborhood.",
        "median": "Remove isolated specks using a median neighborhood.",
        "sharpen": "Sharpen local edges with a convolution kernel.",
        "unsharp-mask": "Sharpen detail using a blurred contrast mask.",
        "emboss": "Create a directional raised relief.",
        "find-edges": "Extract high-contrast edges.",
        "contour": "Trace image boundaries with a contour kernel.",
        "detail": "Enhance fine local detail.",
        "smooth": "Smooth pixels with a weighted kernel.",
        "smooth-more": "Use a wider weighted smoothing kernel.",
        "minimum": "Spread the darkest neighboring pixels.",
        "maximum": "Spread the brightest neighboring pixels.",
        "mode": "Replace pixels with the most common neighbor value.",
        "edge-enhance": "Increase local edge contrast.",
        "brightness": "Lift channel brightness by 25 percent.",
        "contrast": "Increase contrast around the image's middle tone.",
        "saturation": "Strengthen chroma without changing geometry.",
        "warm": "Raise red and gently reduce blue.",
        "cool": "Raise blue and gently reduce red.",
        "red-isolate": "Keep the red channel and suppress green and blue.",
        "green-isolate": "Keep the green channel and suppress red and blue.",
        "blue-isolate": "Keep the blue channel and suppress red and green.",
        "duotone": "Map luminance between the background and foreground colors.",
        "threshold": "Separate luminance into black and white.",
        "gamma-light": "Lift shadows with a nonlinear gamma curve.",
        "gamma-dark": "Deepen midtones with a nonlinear gamma curve.",
        "rgb-cycle": "Rotate red, green and blue channels.",
        "red-blue-swap": "Exchange red and blue channel information.",
        "vignette": "Shade the corners with an elliptical falloff.",
        "chromatic-shift": "Offset red and blue in opposite directions.",
        "pixelate": "Replace local blocks with their sampled color.",
        "halftone": "Convert luminance into variable-size printed dots.",
        "scanlines": "Alternate shaded horizontal scan lines.",
        "grain": "Add deterministic monochromatic film grain.",
        "bloom": "Blend a screened blur into highlights.",
        "oil-paint": "Combine median texture with increased saturation.",
        "pencil-sketch": "Extract a light monochrome contour sketch.",
        "sobel": "Compute horizontal and vertical gradient magnitude.",
        "relief": "Combine opposite directional derivatives.",
        "glow": "Combine brightened source pixels and a soft glow.",
    }
    for engine, description in effects.items():
        identifier = "effects." + engine
        result[identifier] = ToolSpec(identifier, engine.replace("-", " ").title(),
            "effects", "effect", description, "effect", (engine,))
    return result


TOOLS = _catalog()


def list_tools(query="", category=None, kind=None):
    """Return stable, ordered specs; whitespace-separated terms all must match."""
    terms = str(query).casefold().split()
    category = str(category).casefold() if category is not None else None
    kind = str(kind).casefold() if kind is not None else None
    return [spec for spec in TOOLS.values()
            if (category is None or spec.category == category)
            and (kind is None or spec.kind == kind)
            and all(term in f"{spec.id} {spec.name} {spec.description}".casefold() for term in terms)]


def get_tool(identifier):
    if isinstance(identifier, ToolSpec):
        if TOOLS.get(identifier.id) != identifier:
            raise ValueError("Tool is not a registered catalog recipe.")
        return identifier
    key = str(identifier).strip().casefold()
    if key in TOOLS:
        return TOOLS[key]
    raise ValueError(f"Unknown library tool '{identifier}'. Use 'tools search' to find a tool ID.")


def catalog_counts():
    return {"total": len(TOOLS), "categories": {c: len(list_tools(category=c)) for c in CATEGORY_DESCRIPTIONS},
            "kinds": {k: len(list_tools(kind=k)) for k in ("stamp", "brush", "pattern", "effect")}}


def _point(cx, cy, radius, angle):
    return cx + radius * math.cos(angle), cy + radius * math.sin(angle)


def _poly(n, radius=1, phase=-math.pi / 2):
    return [_point(0, 0, radius, phase + i * math.tau / n) for i in range(n)]


def _transform(points, cx, cy, sx, sy=None, angle=0):
    sy = sx if sy is None else sy
    c, s = math.cos(angle), math.sin(angle)
    return [(cx + x * sx * c - y * sy * s, cy + x * sx * s + y * sy * c) for x, y in points]


def _leaf_shape(shape):
    if shape == "lance": return [(0, -1), (.3, -.3), (.22, .55), (0, 1), (-.22, .55), (-.3, -.3)]
    if shape == "oval": return [(math.sin(a) * .57, -math.cos(a)) for a in [i * math.tau / 40 for i in range(40)]]
    if shape == "heart": return [(0, -.52), (.34, -1), (.67, -.78), (.62, -.12), (0, 1), (-.62, -.12), (-.67, -.78), (-.34, -1)]
    if shape == "ginkgo": return [(0, 1), (.87, -.25), (.63, -.8), (.21, -1), (0, -.62), (-.21, -1), (-.63, -.8), (-.87, -.25)]
    if shape == "oak": return [(0,-1),(.26,-.8),(.17,-.5),(.58,-.42),(.32,-.08),(.62,.2),(.29,.39),(.35,.65),(0,1),(-.35,.65),(-.29,.39),(-.62,.2),(-.32,-.08),(-.58,-.42),(-.17,-.5),(-.26,-.8)]
    if shape == "maple": return [(0,-1),(.18,-.45),(.65,-.75),(.48,-.22),(.9,-.13),(.45,.3),(.5,.6),(.15,.47),(0,1),(-.15,.47),(-.5,.6),(-.45,.3),(-.9,-.13),(-.48,-.22),(-.65,-.75),(-.18,-.45)]
    if shape == "fan": return [(0,1)] + [_point(0,0,1,a) for a in [math.pi + i * math.pi / 24 for i in range(25)]]
    if shape == "arrow": return [(0,-1),(.72,.15),(.27,-.04),(.19,1),(-.19,1),(-.27,-.04),(-.72,.15)]
    if shape == "holly": return [(0,-1),(.18,-.64),(.51,-.57),(.27,-.28),(.64,.04),(.29,.21),(.53,.65),(.21,.55),(0,1),(-.21,.55),(-.53,.65),(-.29,.21),(-.64,.04),(-.27,-.28),(-.51,-.57),(-.18,-.64)]
    return [(0,-1),(.35,-.72),(.24,-.2),(.65,-.35),(.7,.12),(.21,.32),(0,1),(-.21,.32),(-.7,.12),(-.65,-.35),(-.24,-.2),(-.35,-.72)]


def _leaf(recipe, n):
    shape, layout, vein = recipe
    mask = Image.new("L", (n, n)); draw = ImageDraw.Draw(mask)
    if layout == "single": places = [(n*.5,n*.5,n*.42,0)]
    elif layout == "paired": places = [(n*.31,n*.5,n*.31,-.4),(n*.7,n*.47,n*.31,.4)]
    elif layout == "sprig": places = [(n*.36,n*(.27+i*.2),n*.2,(-1 if i%2 else 1)*.85) for i in range(3)] + [(n*.64,n*.4,n*.22,.8)]
    elif layout == "whorl": places = [(*_point(n*.5,n*.5,n*.18,a),n*.26,a+math.pi/2) for a in [i*math.tau/5 for i in range(5)]]
    else: places = [(*_point(n*.5,n*.5,n*.29,a),n*.17,a+math.pi/2+.65) for a in [i*math.tau/8 for i in range(8)]]
    if layout in ("sprig", "paired"):
        draw.line([(n*.5,n*.93),(n*.48,n*.1)],fill=210,width=2)
    for cx,cy,r,a in places:
        leaf = Image.new("L", (n,n)); ld = ImageDraw.Draw(leaf)
        ld.polygon(_transform(_leaf_shape(shape),cx,cy,r*.7,r,a),fill=255)
        silhouette = leaf.copy()
        def line(points,width=1):
            ld.line(_transform(points,cx,cy,r*.7,r,a),fill=25,width=width)
        line([(0,-.85),(0,.85)],2)
        if vein == "herringbone":
            for t in (-.55,-.25,.05,.35):
                line([(-.55,t-.2),(0,t+.15),(.55,t-.2)])
        elif vein == "net":
            for t in (-.55,-.2,.15,.5):
                line([(-.52,t-.2),(0,t),(.52,t-.2)])
                line([(-.45,t+.12),(0,t-.18),(.45,t+.12)])
        elif vein == "dotted":
            for y in (-.6,-.3,0,.3,.6):
                for x in (-.19,.19):
                    px,py = _transform([(x,y)],cx,cy,r*.7,r,a)[0]
                    ld.ellipse((px-1.5,py-1.5,px+1.5,py+1.5),fill=0)
        mask = ImageChops.lighter(mask,ImageChops.multiply(leaf,silhouette))
    return mask


def _petal(shape):
    if shape == "round": return [(math.sin(a)*.23, -.58-math.cos(a)*.42) for a in [i*math.tau/32 for i in range(32)]]
    if shape == "spear": return [(0,0),(.18,-.5),(0,-1),(-.18,-.5)]
    if shape == "heart": return [(0,0),(.27,-.6),(.2,-.94),(0,-.77),(-.2,-.94),(-.27,-.6)]
    if shape == "forked": return [(0,0),(.25,-1),(.02,-.69),(-.25,-1),(-.14,-.35)]
    if shape == "ribbon": return [(0,0),(.26,-.35),(.32,-.8),(.1,-1),(.17,-.6),(-.13,-.31)]
    if shape == "cup": return [(0,0),(.3,-.75),(.2,-1),(.12,-.62),(-.12,-.62),(-.2,-1),(-.3,-.75)]
    if shape == "starburst": return [(0,0),(.13,-.3),(.25,-.5),(.13,-.65),(0,-1),(-.13,-.65),(-.25,-.5),(-.13,-.3)]
    return [(0,0),(.19,-.2),(.1,-.76),(0,-1),(-.1,-.76),(-.19,-.2)]


def _rosette(recipe,n):
    shape,layout,center=recipe
    image=Image.new("L",(n,n));d=ImageDraw.Draw(image);cx=cy=n*.5
    rings = [(8,n*.43,0)]
    if layout=="double-ring": rings=[(10,n*.43,0),(6,n*.25,.24)]
    elif layout=="alternating": rings=[(5,n*.44,0),(5,n*.3,math.pi/5)]
    elif layout=="spiral": rings=[(9,n*.43,.2)]
    elif layout=="corona": rings=[(12,n*.44,0),(12,n*.25,math.pi/12)]
    for count,r,phase in rings:
        for i in range(count):
            a=i*math.tau/count+phase
            ox=oy=0
            if layout=="spiral": ox,oy=_point(0,0,n*.025*i,a)
            points=_transform(_petal(shape),cx+ox,cy+oy,r,r,a)
            d.polygon(points,fill=230 if layout=="corona" and r<n*.3 else 255)
            d.line(_transform([(0,-.25),(0,-.87)],cx+ox,cy+oy,r,r,a),fill=45,width=1)
    rr=n*.105
    d.ellipse((cx-rr,cy-rr,cx+rr,cy+rr),fill=0)
    if center=="bead": d.ellipse((cx-rr*.72,cy-rr*.72,cx+rr*.72,cy+rr*.72),fill=255)
    elif center=="seedwheel":
        for a in [i*math.tau/7 for i in range(7)]:
            x,y=_point(cx,cy,rr*.61,a);d.ellipse((x-2,y-2,x+2,y+2),fill=255)
    elif center=="crosshatch":
        d.line((cx-rr,cy,cx+rr,cy),fill=255,width=2);d.line((cx,cy-rr,cx,cy+rr),fill=255,width=2)
        d.line((cx-rr*.6,cy-rr*.6,cx+rr*.6,cy+rr*.6),fill=255,width=1)
    elif center=="star":
        points=[_point(cx,cy,rr*(1 if i%2==0 else .42),-math.pi/2+i*math.pi/5) for i in range(10)]
        d.polygon(points,fill=255)
    return image


def _stitch_unit(shape,n):
    im=Image.new("L",(n,n));d=ImageDraw.Draw(im);w=max(1,n//15)
    def ln(p): d.line([(x*n,y*n) for x,y in p],fill=255,width=w,joint="curve")
    if shape=="ladder":
        ln([(.2,.08),(.2,.92)]);ln([(.8,.08),(.8,.92)])
        for y in (.2,.4,.6,.8): ln([(.2,y),(.8,y)])
    elif shape=="cross": ln([(.1,.1),(.9,.9)]);ln([(.9,.1),(.1,.9)]);ln([(.5,0),(.5,1)])
    elif shape=="chevron":
        for y in (.18,.48,.78): ln([(.1,y-.12),(.5,y+.18),(.9,y-.12)])
    elif shape=="chain":
        for y in (.08,.33,.58):d.ellipse((n*.25,n*y,n*.75,n*(y+.36)),outline=255,width=w)
    elif shape=="herringbone":
        for y in (.12,.38,.64,.9):ln([(.15,y-.13),(.85,y+.13)]);ln([(.15,y+.13),(.85,y-.13)])
    elif shape=="feather":
        ln([(.5,0),(.5,1)])
        for y in (.18,.42,.66):ln([(.1,y-.13),(.5,y+.13),(.9,y-.13)])
    elif shape=="braid":
        for off in (.14,.45):ln([(off,0),(.9,.3),(.1,.65),(1-off,1)])
    elif shape=="loop":
        for y in (.05,.37,.69):d.arc((n*.1,n*y,n*.9,n*(y+.4)),0,320,fill=255,width=w)
    elif shape=="wave":
        ln([(.5+math.sin(i*math.tau/16)*.35,i/32) for i in range(33)])
        ln([(.25+math.sin(i*math.tau/16)*.2,i/32) for i in range(33)])
    else:ln([(.1,0),(.9,.2),(.1,.4),(.9,.6),(.1,.8),(.9,1)])
    return im


def _stitch(recipe,n):
    shape,layout,edge=recipe
    im=Image.new("L",(n,n));unit=_stitch_unit(shape,28)
    if layout=="stripe": places=[(x,y,0) for x in (9,51,93) for y in (6,38,70,102)]
    elif layout=="offset": places=[(x+(14 if row%2 else 0),y,0) for row,y in enumerate((0,32,64,96)) for x in (-7,35,77,119)]
    elif layout=="diagonal": places=[(x,y,45) for y in (-10,32,74,116) for x in (-10,32,74,116)]
    elif layout=="woven": places=[(x,y,90 if (x+y)//32%2 else 0) for y in (0,32,64,96) for x in (0,32,64,96)]
    else: places=[(*_point(n*.5-14,n*.5-14,n*.34,a),-math.degrees(a)) for a in [i*math.tau/8 for i in range(8)]]
    # Each edge construction adds a different seam, rather than a strength tweak.
    for x,y,angle in places:
        tile=unit.rotate(angle,resample=Image.Resampling.BICUBIC)
        _paste_union(im,tile,round(x),round(y))
    d=ImageDraw.Draw(im)
    if edge=="plain":d.line((3,3,n-4,3,n-4,n-4,3,n-4,3,3),fill=130,width=1)
    elif edge=="knotted":
        for t in range(8,n,16):
            for x,y in ((t,5),(t,n-6),(5,t),(n-6,t)):d.line((x-3,y-3,x+3,y+3),fill=255,width=2);d.line((x-3,y+3,x+3,y-3),fill=255,width=2)
    elif edge=="beaded":
        for t in range(5,n,11):
            for x,y in ((t,5),(t,n-6),(5,t),(n-6,t)):d.ellipse((x-2,y-2,x+2,y+2),fill=255)
    else:
        for t in range(5,n,12):
            for x,y in ((t,5),(t,n-6),(5,t),(n-6,t)):d.polygon([(x-4,y+3),(x,y-3),(x+4,y+3)],fill=255)
    return im


def _emblem_unit(shape,n):
    im=Image.new("L",(n,n));d=ImageDraw.Draw(im);c=n/2;r=n*.44
    if shape=="ring":d.ellipse((c-r,c-r,c+r,c+r),outline=255,width=max(2,n//7))
    elif shape=="star":d.polygon([_point(c,c,r*(1 if i%2==0 else .42),-math.pi/2+i*math.pi/5) for i in range(10)],fill=255)
    elif shape=="arrow":d.polygon(_transform([(-.8,-.2),(.15,-.2),(.15,-.6),(.9,0),(.15,.6),(.15,.2),(-.8,.2)],c,c,r),fill=255)
    elif shape=="diamond":d.polygon(_transform([(0,-1),(.6,0),(0,1),(-.6,0)],c,c,r),fill=255)
    elif shape in ("hexagon","triangle"):d.polygon(_transform(_poly(6 if shape=="hexagon" else 3),c,c,r),fill=255)
    elif shape=="crescent":d.ellipse((c-r,c-r,c+r,c+r),fill=255);d.ellipse((c-r*.15,c-r*.95,c+r*1.55,c+r*.75),fill=0)
    elif shape=="lightning":d.polygon(_transform([(-.15,-1),(.75,-1),(.15,-.12),(.66,-.12),(-.63,1),(-.22,.18),(-.7,.18)],c,c,r),fill=255)
    elif shape=="cross":d.rectangle((c-r*.28,c-r,c+r*.28,c+r),fill=255);d.rectangle((c-r,c-r*.28,c+r,c+r*.28),fill=255)
    else:d.rounded_rectangle((c-r,c-r,c+r,c+r),radius=n*.2,fill=255)
    return im


def _emblem(recipe,n):
    shape,layout,detail=recipe
    im=Image.new("L",(n,n))
    if layout=="orbit": places=[(*_point(n*.5,n*.5,n*.29,a),25,math.degrees(a)) for a in [i*math.tau/6 for i in range(6)]]+[(n*.5,n*.5,38,0)]
    elif layout=="lattice":places=[(x,y,29,0) for x in (23,64,105) for y in (23,64,105)]
    elif layout=="fan":places=[(n*.5+math.sin(a)*n*.24,n*.65-math.cos(a)*n*.26,42,math.degrees(a)) for a in (-1.1,-.55,0,.55,1.1)]
    elif layout=="weave":places=[(30,33,50,45),(96,33,50,-45),(30,95,50,-45),(96,95,50,45)]
    else:places=[(*_point(n*.5,n*.5,n*.25,a),42,math.degrees(a)+90) for a in [i*math.tau/7 for i in range(7)]]
    for cx,cy,sz,angle in places:
        unit=_emblem_unit(shape,sz).rotate(angle,resample=Image.Resampling.BICUBIC)
        silhouette=unit.copy()
        if detail=="contours":
            rim=unit.filter(ImageFilter.MinFilter(3));unit=ImageChops.subtract(unit,rim)
            unit=ImageChops.lighter(unit,rim.point(lambda x:x//3))
        elif detail=="notches":
            dd=ImageDraw.Draw(unit)
            for t in range(4,sz,8):dd.line((t,0,t+5,sz),fill=0,width=2)
        elif detail=="cutouts":
            dd=ImageDraw.Draw(unit);rr=sz*.12;dd.ellipse((sz*.5-rr,sz*.5-rr,sz*.5+rr,sz*.5+rr),fill=0)
            dd.line((0,sz*.5,sz,sz*.5),fill=0,width=1)
        else:
            dd=ImageDraw.Draw(unit)
            for t in range(-sz,sz*2,5):dd.line((t,0,t-sz,sz),fill=60,width=1)
        unit=ImageChops.multiply(unit,silhouette)
        x,y=round(cx-sz/2),round(cy-sz/2)
        # Lighter union retains overlapping emblems without squaring edge alpha.
        _paste_union(im,unit,x,y)
    return im


def _tile_unit(shape,style,n=26):
    silhouette=Image.new("L",(n,n));d=ImageDraw.Draw(silhouette);p=n-1
    if shape=="checker":d.rectangle((1,1,p//2,p//2),fill=255);d.rectangle((p//2+1,p//2+1,p-1,p-1),fill=255)
    elif shape=="brick":d.rectangle((1,3,p-1,p-3),fill=255)
    elif shape=="scale":d.pieslice((1,1,p-1,p*1.4),180,360,fill=255)
    elif shape=="honeycomb":d.polygon(_transform(_poly(6,1,0),n/2,n/2,n*.45),fill=255)
    elif shape=="triangle":d.polygon([(n/2,1),(p-1,p-1),(1,p-1)],fill=255)
    elif shape=="wave":d.polygon([(x,n*.5+math.sin(x*math.tau/n)*n*.25) for x in range(n)]+[(p,p),(0,p)],fill=255)
    elif shape=="maze":d.line([(2,2),(p-2,2),(p-2,p-2),(6,p-2),(6,7),(p-7,7),(p-7,p-7),(11,p-7)],fill=255,width=4)
    elif shape=="diamond":d.polygon([(n/2,1),(p-1,n/2),(n/2,p-1),(1,n/2)],fill=255)
    elif shape=="basket":
        for x in (2,9,16):d.rectangle((x,1,x+4,p-1),fill=255)
    else:d.arc((1,1,p-1,p-1),0,270,fill=255,width=5)
    if style=="solid":return silhouette
    if style=="outline":return ImageChops.subtract(silhouette,silhouette.filter(ImageFilter.MinFilter(3)))
    interior=Image.new("L",(n,n));dd=ImageDraw.Draw(interior)
    if style=="dotted":
        for y in range(3,n,6):
            for x in range(3,n,6):dd.ellipse((x-1,y-1,x+1,y+1),fill=255)
    else:
        for y in range(2,n,5):dd.line((0,y,n,y),fill=255,width=2)
    return ImageChops.multiply(silhouette,interior)


def _tile(recipe,n):
    shape,layout,style=recipe;unit=_tile_unit(shape,style)
    im=Image.new("L",(n,n));step=28
    for row,y in enumerate(range(-step,n+step,step)):
        for col,x in enumerate(range(-step,n+step,step)):
            tile=unit;xx,yy=x,y
            if layout=="stepped":xx+=14*(row%2)
            elif layout=="diagonal":xx+=row*9;yy+=col*3;tile=tile.rotate(25,resample=Image.Resampling.BICUBIC)
            elif layout=="pinwheel":tile=tile.rotate(((row+col)%4)*90)
            elif layout=="mirrored":tile=ImageOps.mirror(tile) if col%2 else tile;tile=ImageOps.flip(tile) if row%2 else tile;xx+=7*(row%2)
            _paste_union(im,tile,xx,yy)
    # Distinct repeat boundaries complete the row, step, diagonal or pinwheel seam.
    d=ImageDraw.Draw(im)
    if layout=="rows":d.line((0,n-2,n,n-2),fill=80,width=1)
    elif layout=="stepped":d.line((0,n-3,n*.5,n-3,n*.5,1,n,1),fill=100,width=1)
    elif layout=="diagonal":d.line((0,n*.62,n*.62,0),fill=100,width=1)
    elif layout=="pinwheel":d.arc((n*.3,n*.3,n*.7,n*.7),0,270,fill=180,width=2)
    else:d.line((n*.5,0,n*.5,n),fill=100,width=1)
    return im


def _media(recipe,n,seed,density):
    mark,layout,surface=recipe
    rng=random.Random(int.from_bytes(hashlib.sha256((repr(recipe)+str(seed)).encode()).digest()[:8],"big"))
    image=Image.new("L",(n,n));d=ImageDraw.Draw(image)
    count=round((140 if surface=="fine" else 70)*density)
    def loc():
        a=rng.uniform(0,math.tau);r=math.sqrt(rng.random())
        if layout=="centered":return n*(.5+math.cos(a)*r*.4),n*(.5+math.sin(a)*r*.4)
        if layout=="slash":return n*(.18+r*.62),n*(.83-r*.62+rng.uniform(-.09,.09))
        if layout=="crescent":return n*(.5+math.cos(a*.68-1.1)*(.24+r*.18)),n*(.5+math.sin(a*.68-1.1)*(.24+r*.18))
        if layout=="ring":return n*(.5+math.cos(a)*(.3+r*.1)),n*(.5+math.sin(a)*(.3+r*.1))
        return (n*(.5+rng.uniform(-.06,.06)),n*(.1+r*.8)) if rng.random()<.5 else (n*(.1+r*.8),n*(.5+rng.uniform(-.06,.06)))
    for _ in range(max(1,count)):
        x,y=loc();r=rng.uniform(1,3.8 if surface=="coarse" else 1.8);value=rng.randint(130,255)
        if mark=="chalk":d.rectangle((x-r,y-r*.5,x+r,y+r*.5),fill=value)
        elif mark=="fan":d.line((x,y,x+r*2,y-r*5),fill=value,width=1)
        elif mark=="rake":d.line((x,y-4,x,y+5),fill=value,width=1)
        elif mark=="splatter":d.ellipse((x-r,y-r,x+r,y+r),fill=value);d.line((x,y,x+r*2,y+r*3),fill=value,width=1)
        elif mark=="cloud":d.ellipse((x-r*2,y-r*2,x+r*2,y+r*2),fill=value//2)
        elif mark=="sponge":d.polygon([(x-r,y),(x,y-r),(x+r,y+r),(x-r*.5,y+r)],fill=value)
        elif mark=="grass":d.line((x,y,x+rng.uniform(-6,6),y-rng.uniform(6,16)),fill=value,width=1)
        elif mark=="charcoal":d.line((x-4,y-2,x+4,y+2),fill=value,width=max(1,round(r)))
        elif mark=="bristle":d.line((x,y-7,x+2,y+7),fill=value,width=1)
        else:d.arc((x-r*3,y-r,x+r*3,y+r),20,180,fill=value,width=1)
    if surface=="flecked":
        for _ in range(count//2):
            x,y=loc();d.ellipse((x-1,y-1,x+1,y+1),fill=0)
    elif surface=="striated":
        for y in range(0,n,5):d.line((0,y,n,y-10),fill=0,width=1)
    if mark=="cloud":image=image.filter(ImageFilter.GaussianBlur(1.2))
    return image


def _paste_union(destination,source,x,y):
    left,top=max(0,x),max(0,y);right,bottom=min(destination.width,x+source.width),min(destination.height,y+source.height)
    if left>=right or top>=bottom:return
    box=(left,top,right,bottom)
    portion=source.crop((left-x,top-y,right-x,bottom-y))
    destination.paste(ImageChops.lighter(destination.crop(box),portion),box)


def _repeat_tile(tip,size,phase=(0,0)):
    """Tile by doubling painted rectangles, so size=1 does not loop per pixel."""
    width,height=size
    shifted=ImageChops.offset(tip,-phase[0],-phase[1]) if phase!=(0,0) else tip
    result=Image.new("L",size);result.paste(shifted,(0,0))
    filled_width=min(width,tip.width);filled_height=min(height,tip.height)
    while filled_width<width:
        part=min(filled_width,width-filled_width)
        result.paste(result.crop((0,0,part,filled_height)),(filled_width,0))
        filled_width+=part
    while filled_height<height:
        part=min(filled_height,height-filled_height)
        result.paste(result.crop((0,0,width,part)),(0,filled_height))
        filled_height+=part
    return result


@lru_cache(maxsize=256)
def _base_tip(identifier,seed,density):
    spec=get_tool(identifier);n=128
    engines={"leaf":_leaf,"rosette":_rosette,"stitch":_stitch,"emblem":_emblem,"tile":_tile}
    if spec.engine=="media":return _media(spec.recipe,n,seed,density)
    return engines[spec.engine](spec.recipe,n)


def _finite(value,label,low,high):
    try:number=float(value)
    except (TypeError,ValueError):raise ValueError(f"{label} must be a number.") from None
    if not math.isfinite(number) or not low<=number<=high:raise ValueError(f"{label} must be between {low:g} and {high:g}.")
    return number


def render_tip(spec,size,seed=0,angle=0,density=1):
    """Render a drawable tool's grayscale footprint, independently of document zoom."""
    spec=get_tool(spec)
    if spec.kind=="effect":raise ValueError("Effects process pixels; they do not have brush footprints.")
    size=round(_finite(size,"Size",1,1024));angle=_finite(angle,"Angle",-360000,360000)
    density=_finite(density,"Density",.05,5)
    seed=int(_finite(seed,"Seed",-2**31,2**31-1))
    result=_base_tip(spec.id,seed,density).copy()
    if angle%360:result=result.rotate(angle%360,resample=Image.Resampling.BICUBIC)
    if result.size!=(size,size):result=result.resize((size,size),Image.Resampling.LANCZOS)
    return result


def _effect(source,name,foreground,background,seed):
    rgb=source.convert("RGB");alpha=source.getchannel("A");w,h=rgb.size
    filters={"gaussian-blur":ImageFilter.GaussianBlur(2),"box-blur":ImageFilter.BoxBlur(2),
        "median":ImageFilter.MedianFilter(3),"sharpen":ImageFilter.SHARPEN,
        "unsharp-mask":ImageFilter.UnsharpMask(2,180,3),"emboss":ImageFilter.EMBOSS,
        "find-edges":ImageFilter.FIND_EDGES,"contour":ImageFilter.CONTOUR,"detail":ImageFilter.DETAIL,
        "smooth":ImageFilter.SMOOTH,"smooth-more":ImageFilter.SMOOTH_MORE,
        "minimum":ImageFilter.MinFilter(3),"maximum":ImageFilter.MaxFilter(3),"mode":ImageFilter.ModeFilter(3),
        "edge-enhance":ImageFilter.EDGE_ENHANCE}
    if name in filters:result=rgb.filter(filters[name])
    elif name=="invert":result=ImageOps.invert(rgb)
    elif name=="grayscale":result=ImageOps.grayscale(rgb).convert("RGB")
    elif name=="sepia":result=rgb.convert("RGB",(.393,.769,.189,0,.349,.686,.168,0,.272,.534,.131,0))
    elif name=="solarize":result=ImageOps.solarize(rgb,128)
    elif name=="posterize":result=ImageOps.posterize(rgb,4)
    elif name=="autocontrast":result=ImageOps.autocontrast(rgb,cutoff=1)
    elif name=="equalize":result=ImageOps.equalize(rgb)
    elif name=="brightness":result=ImageEnhance.Brightness(rgb).enhance(1.25)
    elif name=="contrast":result=ImageEnhance.Contrast(rgb).enhance(1.5)
    elif name=="saturation":result=ImageEnhance.Color(rgb).enhance(1.6)
    elif name in ("warm","cool"):
        r,g,b=rgb.split();lift=lambda im:im.point(lambda v:min(255,round(v*1.08+12)));lower=lambda im:im.point(lambda v:round(v*.9))
        result=Image.merge("RGB",(lift(r),g,lower(b)) if name=="warm" else (lower(r),g,lift(b)))
    elif name.endswith("-isolate"):
        index={"red-isolate":0,"green-isolate":1,"blue-isolate":2}[name]
        channels=rgb.split();zero=Image.new("L",rgb.size);result=Image.merge("RGB",tuple(channels[i] if i==index else zero for i in range(3)))
    elif name=="duotone":result=ImageOps.colorize(ImageOps.grayscale(rgb),background[:3],foreground[:3])
    elif name=="threshold":result=ImageOps.grayscale(rgb).point(lambda v:255 if v>=128 else 0).convert("RGB")
    elif name.startswith("gamma-"):result=rgb.point([round(255*(v/255)**(.65 if name=="gamma-light" else 1.6)) for v in range(256)]*3)
    elif name=="rgb-cycle":r,g,b=rgb.split();result=Image.merge("RGB",(g,b,r))
    elif name=="red-blue-swap":r,g,b=rgb.split();result=Image.merge("RGB",(b,g,r))
    elif name=="vignette":
        values=bytes(round(255*max(.2,1-.6*((x/max(1,w-1)*2-1)**2+(y/max(1,h-1)*2-1)**2))) for y in range(h) for x in range(w))
        shade=Image.frombytes("L",(w,h),values)
        result=ImageChops.multiply(rgb,Image.merge("RGB",(shade,shade,shade)))
    elif name=="chromatic-shift":
        r,g,b=rgb.split();result=Image.merge("RGB",(ImageChops.offset(r,2,0),g,ImageChops.offset(b,-2,0)))
    elif name=="pixelate":result=rgb.resize((max(1,w//6),max(1,h//6)),Image.Resampling.BOX).resize((w,h),Image.Resampling.NEAREST)
    elif name=="halftone":
        gray=ImageOps.grayscale(rgb);result=Image.new("RGB",(w,h),"white");d=ImageDraw.Draw(result)
        for y in range(0,h,6):
            for x in range(0,w,6):
                tone=gray.crop((x,y,min(w,x+6),min(h,y+6))).resize((1,1),Image.Resampling.BOX).getpixel((0,0));r=2.8*math.sqrt(1-tone/255)
                d.ellipse((x+3-r,y+3-r,x+3+r,y+3+r),fill="black")
    elif name=="scanlines":
        result=rgb.copy();d=ImageDraw.Draw(result)
        dark=ImageEnhance.Brightness(rgb).enhance(.55)
        for y in range(0,h,3):result.paste(dark.crop((0,y,w,min(h,y+1))),(0,y))
    elif name=="grain":
        rng=random.Random(seed);noise=Image.frombytes("L",(w,h),bytes(rng.randint(80,175) for _ in range(w*h)))
        result=ImageChops.add(rgb,Image.merge("RGB",(noise,noise,noise)),1,-128)
    elif name=="bloom":result=ImageChops.screen(rgb,ImageEnhance.Brightness(rgb.filter(ImageFilter.GaussianBlur(4))).enhance(.45))
    elif name=="oil-paint":result=ImageEnhance.Color(rgb.filter(ImageFilter.MedianFilter(5))).enhance(1.4)
    elif name=="pencil-sketch":result=ImageOps.invert(ImageOps.grayscale(rgb).filter(ImageFilter.FIND_EDGES)).convert("RGB")
    elif name in ("sobel","relief"):
        gray=ImageOps.grayscale(rgb)
        a=gray.filter(ImageFilter.Kernel((3,3),(-1,0,1,-2,0,2,-1,0,1),scale=1,offset=128))
        b=gray.filter(ImageFilter.Kernel((3,3),(-1,-2,-1,0,0,0,1,2,1),scale=1,offset=128))
        result=(ImageChops.add(ImageChops.difference(a,Image.new("L",(w,h),128)),ImageChops.difference(b,Image.new("L",(w,h),128))) if name=="sobel" else ImageChops.add(a,ImageOps.invert(b),2)).convert("RGB")
    elif name=="glow":result=ImageChops.screen(ImageEnhance.Brightness(rgb).enhance(1.1),rgb.filter(ImageFilter.GaussianBlur(7)))
    else:raise ValueError(f"Unknown effect recipe: {name}")
    result=result.convert("RGBA");result.putalpha(alpha)
    return result


def _region(doc,box):
    if box is None:return (0,0,doc.width-1,doc.height-1)
    if len(box)!=4:raise ValueError("Box needs x0,y0,x1,y1.")
    a,b,c,d=(round(_finite(v,"Box coordinate",-1_000_000,1_000_000)) for v in box)
    return min(a,c),min(b,d),max(a,c),max(b,d)


def build_mask(spec,canvas_size,*,points=None,box=None,size=32,seed=0,angle=0,density=1):
    """Build a canvas-sized L mask without changing a document or undo history.

    Useful for mouse-stroke previews: paint this mask against the stroke's
    original image, then commit or cancel the existing document transaction.
    Density controls the number of marks in natural-media footprints.
    """
    from .model import valid_size
    spec=get_tool(spec)
    if spec.kind=="effect":raise ValueError("Effects process pixels; they do not have drawable masks.")
    if len(canvas_size)!=2:raise ValueError("Canvas size needs width,height.")
    width,height=canvas_size;valid_size(width,height)
    class Canvas:
        size=(width,height)
    doc=Canvas();doc.width,doc.height=width,height
    bounds=_region(doc,box);x0,y0,x1,y1=bounds
    clip=(max(0,x0),max(0,y0),min(doc.width,x1+1),min(doc.height,y1+1))
    tip=render_tip(spec,size,seed,angle,density)
    mask=Image.new("L",doc.size)
    if clip[0]>=clip[2] or clip[1]>=clip[3]:return mask
    if spec.kind=="pattern":
        # Clip before allocation and tile by rectangle doubling, even at size=1.
        left,top,right,bottom=clip
        pattern=_repeat_tile(tip,(right-left,bottom-top),((left-x0)%tip.width,(top-y0)%tip.height))
        mask.paste(pattern,(left,top))
    elif box is not None and points is None:
        # Fit to original box; a partly off-canvas box clips instead of squashing.
        scale_x=tip.width/(x1-x0+1);scale_y=tip.height/(y1-y0+1)
        fitted=tip.transform((clip[2]-clip[0],clip[3]-clip[1]),Image.Transform.AFFINE,
            (scale_x,0,(clip[0]-x0)*scale_x,0,scale_y,(clip[1]-y0)*scale_y),Image.Resampling.BICUBIC)
        mask.paste(fitted,(clip[0],clip[1]))
    else:
        coords=list(points) if points is not None else [(doc.width//2,doc.height//2)]
        if not 1<=len(coords)<=10000:raise ValueError("Provide between 1 and 10,000 points.")
        checked=[]
        for point in coords:
            if len(point)!=2:raise ValueError("Each point needs x,y.")
            checked.append(tuple(_finite(v,"Point coordinate",-1_000_000,1_000_000) for v in point))
        dabs=[]
        if spec.kind=="brush":
            spacing=max(1,tip.width*.18);dabs.append(checked[0])
            for a,b in zip(checked,checked[1:]):
                distance=math.dist(a,b);steps=max(1,math.ceil(distance/spacing))
                if len(dabs)+steps>50000:raise ValueError("Stroke exceeds 50,000 brush dabs; split the gesture.")
                dabs.extend((a[0]+(b[0]-a[0])*i/steps,a[1]+(b[1]-a[1])*i/steps) for i in range(1,steps+1))
        else:dabs=checked
        for x,y in dabs:_paste_union(mask,tip,round(x-tip.width/2),round(y-tip.height/2))
    return mask


def apply_tool(doc,tool_id,*,points=None,box=None,color="#e79335",background="#ffffff",size=32,
               opacity=1,seed=0,amount=1,angle=0,density=1):
    """Apply a library recipe to the editable layer, honoring selection and undo.

    Points are pixel centers. Boxes have inclusive endpoints. Patterns tile the
    box (or canvas); effects process a box (or canvas). A brush interpolates dabs
    along the gesture. If inside a mouse-stroke transaction, this operation uses
    that transaction. Effect amount 0..1 interpolates; 1..4 intensifies processing.
    """
    spec=get_tool(tool_id);doc.ensure_editable()
    opacity=_finite(opacity,"Opacity",0,1);amount=_finite(amount,"Amount",0,4)
    foreground=ImageColor.getcolor(color,"RGBA");back=ImageColor.getcolor(background,"RGBA")
    if spec.kind=="effect":
        x0,y0,x1,y1=_region(doc,box)
        clip=(max(0,x0),max(0,y0),min(doc.width,x1+1),min(doc.height,y1+1))
        if clip[0]>=clip[2] or clip[1]>=clip[3]:return f"{spec.name}: outside canvas"
        seed=int(_finite(seed,"Seed",-2**31,2**31-1))
        source=doc.layer.image;region=source.crop(clip)
        processed=_effect(region,spec.recipe[0],foreground,back,seed)
        processed=Image.blend(region,processed,amount)
        mask=Image.new("L",doc.size);ImageDraw.Draw(mask).rectangle((clip[0],clip[1],clip[2]-1,clip[3]-1),fill=round(255*opacity))
        mask=doc.clipped(mask)
        target=source.copy();target.paste(processed,clip)
        with doc.edit(spec.name) if doc.pending is None else nullcontext():
            doc.layer.image=Image.composite(target,source,mask)
        return spec.name
    mask=build_mask(spec,doc.size,points=points,box=box,size=size,seed=seed,angle=angle,density=density)
    with doc.edit(spec.name) if doc.pending is None else nullcontext():
        doc.paint_mask(mask,color,opacity)
    return spec.name
