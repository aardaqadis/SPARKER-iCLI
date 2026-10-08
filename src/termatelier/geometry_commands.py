"""Shared CLI adapters for geometric tools, tone controls and native effects."""
from __future__ import annotations

import json
import math

from PIL import Image

from .adjustment_tools import (ADJUSTMENTS, EFFECTS, ADJUSTMENT_SPECS, EFFECT_SPECS,
                               apply_adjustment, apply_effect)
from .geometry_tools import OPERATIONS, point, transform_image


def _effect_options(specs):
    return " ".join(f"[--{key} VALUE]" for key in dict.fromkeys(key for spec in specs.values() for key in spec))


HELP = {
    "geometry": "geometry list | translate DX DY | scale SX [SY] | rotate DEGREES | shear X [Y] | perspective TL_X,Y TR_X,Y BR_X,Y BL_X,Y | 3d RX RY RZ [--distance N] | unified [--scale X,Y] [--rotate N] [--shear X,Y] [--translate X,Y] | handles|cage --from 'X,Y;...' --to 'X,Y;...' | warp push|grow|shrink|whirl X Y RADIUS AMOUNT [--delta X,Y]; [--target layer|selection|all-layers] [--pivot X,Y] [--resample nearest|bilinear|bicubic] — canvas-sized transforms, simulated 3D plane and smooth weighted cage; cage/warp support nearest/bilinear",
    "tone": "tone list | curves|exposure|shadows-highlights|hue-saturation|color-balance|colorize|desaturate|levels " + _effect_options(ADJUSTMENT_SPECS) + " [--opacity 0..1] — color adjustments inside the selection; curves --points '0,0;128,160;255,255'; list shows defaults and bounds",
    "effect-filter": "effect-filter list | " + "|".join(EFFECTS) + " " + _effect_options(EFFECT_SPECS) + " [--opacity 0..1] — native filters inside selection; list shows each effect's defaults and bounds",
    "measure": "measure X,Y X,Y [X,Y ...] [--json] — segment distances, total length, direction angle and three-point interior angles in pixels/degrees",
    "distribute": "distribute horizontal|vertical|left|center-x|right|top|center-y|bottom|center --layers 'INDEX_OR_NAME,...' [--selection] — equal gaps or align multiple unlocked layers; indices start at 1",
}


def _catalog(names,specs):
    return "\n".join(name+": "+("; ".join(f"--{key} {value}" for key,value in specs[name].items()) or "no options")
                     for name in names)


def _layer_indices(doc,value):
    requested = value.split(",")
    if not 2 <= len(requested) <= len(doc.layers):
        raise ValueError("Specify at least two different layer names or 1-based indices.")
    selected = []
    for item in requested:
        item = item.strip()
        if item.isdigit():
            index = int(item)-1
            if not 0 <= index < len(doc.layers): raise ValueError("Layer index is outside the layer list.")
        else:
            matches = [i for i,layer in enumerate(doc.layers) if layer.name == item]
            if len(matches) != 1: raise ValueError(f"Layer name {item!r} must match exactly one layer.")
            index = matches[0]
        if index in selected: raise ValueError("Choose each layer only once.")
        if doc.layers[index].locked: raise ValueError(f"Layer {doc.layers[index].name!r} is locked.")
        selected.append(index)
    return selected


def execute_geometry(session,command,tokens):
    if command not in HELP: return None
    from .commands import CommandError, CommandResult, _args, _count, _number, _fraction
    doc = session.document
    if command == "measure":
        args,opts = _args(tokens,flags=("json",))
        _count(args,2,128)
        vertices = [point(item) for item in args]
        distances = [math.dist(a,b) for a,b in zip(vertices,vertices[1:])]
        direction = math.degrees(math.atan2(vertices[-1][1]-vertices[0][1],vertices[-1][0]-vertices[0][0]))
        angles = []
        for a,b,c in zip(vertices,vertices[1:],vertices[2:]):
            ab,cb = (a[0]-b[0],a[1]-b[1]),(c[0]-b[0],c[1]-b[1])
            denominator = math.hypot(*ab)*math.hypot(*cb)
            angles.append(None if denominator == 0 else math.degrees(math.acos(max(-1,min(1,(ab[0]*cb[0]+ab[1]*cb[1])/denominator)))))
        data = {"points": vertices,"segments":distances,"length":sum(distances),"direction":direction,"angles":angles,"units":"pixels/degrees"}
        return CommandResult(json.dumps(data) if opts else f"Length {sum(distances):.3f} px · direction {direction:.3f}° · segments {', '.join(f'{value:.3f}' for value in distances)} px"+(f" · angles {angles}" if angles else ""))
    if command in ("tone","effect-filter"):
        if not tokens: raise CommandError(HELP[command])
        name = tokens[0].lower()
        names,specs,function = ((ADJUSTMENTS,ADJUSTMENT_SPECS,apply_adjustment) if command == "tone" else
                                (EFFECTS,EFFECT_SPECS,apply_effect))
        if name == "list":
            args,opts = _args(tokens[1:],flags=("json",)); _count(args,0)
            return CommandResult(json.dumps(specs) if opts else _catalog(names,specs))
        if name not in specs: raise CommandError(f"Unknown {command} operation. Use {command} list.")
        args,opts = _args(tokens[1:],options=tuple(specs[name])+("opacity",))
        _count(args,0)
        opacity = _fraction(opts.pop("opacity",1))
        doc.ensure_editable()
        def perform():
            source = doc.layer.image
            result = function(source,name,**opts)
            if opacity != 1: result = Image.blend(source,result,opacity)
            doc.layer.image = Image.composite(result,source,doc.selection) if doc.selection is not None else result
        return session._edit(f"{command} {name}",perform)
    if command == "distribute":
        args,opts = _args(tokens,options=("layers",),flags=("selection",))
        _count(args,1)
        mode = args[0]
        if mode not in ("horizontal","vertical","left","center-x","right","top","center-y","bottom","center"):
            raise CommandError(HELP[command])
        if "layers" not in opts: raise CommandError("Choose layers with --layers INDEX_OR_NAME,... .")
        selected = _layer_indices(doc,opts["layers"])
        bounds = {index:doc.layers[index].rendered().getchannel("A").getbbox() for index in selected}
        if any(box is None for box in bounds.values()): raise CommandError("Every selected layer must contain visible pixels.")
        target = (0,0,doc.width,doc.height)
        if opts.get("selection"):
            target = doc.selection.getbbox() if doc.selection is not None else None
            if target is None: raise CommandError("A nonempty selection is needed for --selection.")
        offsets = {}
        if mode in ("horizontal","vertical"):
            axis = 0 if mode == "horizontal" else 1
            ordered = sorted(selected,key=lambda index:bounds[index][axis])
            low = bounds[ordered[0]][axis]; high = bounds[ordered[-1]][axis+2]
            total = sum(bounds[index][axis+2]-bounds[index][axis] for index in selected)
            gap = (high-low-total)/(len(ordered)-1)
            cursor = low
            for index in ordered:
                shift = round(cursor)-bounds[index][axis]
                offsets[index] = (shift,0) if axis == 0 else (0,shift)
                cursor += bounds[index][axis+2]-bounds[index][axis]+gap
        else:
            for index,box in bounds.items():
                dx = target[0]-box[0] if mode == "left" else target[2]-box[2] if mode == "right" else (target[0]+target[2]-box[0]-box[2])//2 if mode in ("center-x","center") else 0
                dy = target[1]-box[1] if mode == "top" else target[3]-box[3] if mode == "bottom" else (target[1]+target[3]-box[1]-box[3])//2 if mode in ("center-y","center") else 0
                offsets[index] = (dx,dy)
        def perform():
            for index,(dx,dy) in offsets.items():
                layer = doc.layers[index]
                layer.image = transform_image(layer.image,"translate",dx=dx,dy=dy)
                if layer.mask is not None: layer.mask = transform_image(layer.mask,"translate",dx=dx,dy=dy)
                if layer.text_recipe is not None:
                    x,y = layer.text_recipe["point"]
                    layer.text_recipe["point"] = [x+dx,y+dy]
        return session._edit(f"Distribute {mode}",perform)
    if not tokens: raise CommandError(HELP[command])
    operation = tokens[0].lower()
    if operation == "list":
        _count(tokens,1); return CommandResult(", ".join(OPERATIONS))
    specific = {"translate":(),"scale":(),"rotate":(),"shear":(),"perspective":(),"3d":("distance",),
                "unified":("scale","rotate","shear","translate"),"handles":("from","to"),
                "cage":("from","to"),"warp":("delta",)}
    if operation not in specific: raise CommandError("Unknown geometry operation; use geometry list.")
    args,opts = _args(tokens[1:],options=("target","pivot","resample")+specific[operation])
    target = opts.pop("target","layer"); resample = opts.pop("resample","nearest")
    if target not in ("layer","selection","all-layers"): raise CommandError("Target must be layer, selection or all-layers.")
    kwargs = {}
    if "pivot" in opts: kwargs["pivot"] = point(opts.pop("pivot"))
    if operation == "translate":
        _count(args,2); kwargs.update(dx=_number(args[0]),dy=_number(args[1]))
    elif operation in ("scale","shear"):
        _count(args,1,2)
        keys = ("sx","sy") if operation == "scale" else ("shx","shy")
        kwargs[keys[0]] = _number(args[0]); kwargs[keys[1]] = _number(args[1]) if len(args)>1 else kwargs[keys[0]] if operation == "scale" else 0
    elif operation == "rotate": _count(args,1); kwargs["angle"] = _number(args[0])
    elif operation == "perspective": _count(args,4); kwargs["corners"] = [point(item) for item in args]
    elif operation == "3d":
        _count(args,3); kwargs.update(zip(("rx","ry","rz"),map(_number,args)))
        if "distance" in opts: kwargs["distance"] = _number(opts["distance"])
    elif operation == "unified":
        _count(args,0)
        for key,keys in (("scale",("sx","sy")),("shear",("shx","shy")),("translate",("dx","dy"))):
            if key in opts: kwargs.update(zip(keys,point(opts[key])))
        if "rotate" in opts: kwargs["angle"] = _number(opts["rotate"])
    elif operation in ("handles","cage"):
        _count(args,0)
        if "from" not in opts or "to" not in opts: raise CommandError("Specify matching --from and --to handle points.")
        kwargs.update(source=opts["from"],destination=opts["to"])
    else:
        _count(args,5); kwargs.update(mode=args[0],center=(_number(args[1]),_number(args[2])),radius=_number(args[3]),amount=_number(args[4]))
        if "delta" in opts: kwargs["delta"] = point(opts["delta"])
    if target == "selection":
        if doc.selection is None: raise CommandError("Create a selection before transforming it.")
    elif target == "layer": doc.ensure_editable()
    elif any(layer.locked for layer in doc.layers): raise CommandError("Unlock all layers before transforming all-layers.")
    def perform():
        if target == "selection":
            doc.selection = transform_image(doc.selection,operation,resample=resample,**kwargs)
        else:
            layers = doc.layers if target == "all-layers" else [doc.layer]
            for layer in layers:
                layer.image = transform_image(layer.image,operation,resample=resample,**kwargs)
                if layer.mask is not None: layer.mask = transform_image(layer.mask,operation,resample=resample,**kwargs)
                if layer.text_recipe is not None:
                    if operation == "translate":
                        x,y = layer.text_recipe["point"]
                        layer.text_recipe["point"] = [x+kwargs["dx"],y+kwargs["dy"]]
                    else:
                        # A raster shear/cage/projective transform cannot be
                        # regenerated from a typography recipe without losing
                        # that transform. Keep the pixels and retire its recipe.
                        layer.text_recipe = None
    return session._edit(f"Geometry {operation} ({target})",perform)
