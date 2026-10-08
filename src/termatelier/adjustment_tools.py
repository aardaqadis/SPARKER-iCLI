"""Pure, validated color adjustments and image effects for editable stacks.

All results remain canvas-sized RGBA. Spatial/noise/color arithmetic works in
bounded NumPy tiles; neither artwork nor effect configuration is mutated.
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageChops, ImageColor, ImageFilter, ImageOps

from .geometry_tools import finite, point, points, remap_image

# Numeric entries are (default, minimum, maximum). Enum entries are tuples of
# permitted strings, first entry being the default. Integers retain their type.
ADJUSTMENT_SPECS = {
    "curves": {"channel": ("rgb","red","green","blue","alpha"), "points": "0,0;255,255"},
    "exposure": {"stops": (0.,-10.,10.), "offset": (0.,-1.,1.), "gamma": (1.,.1,10.)},
    "shadows-highlights": {"shadows": (0.,-1.,1.), "highlights": (0.,-1.,1.), "width": (.5,.05,1.)},
    "hue-saturation": {"hue": (0.,-360.,360.), "saturation": (1.,0.,3.), "lightness": (0.,-1.,1.)},
    "color-balance": {"red": (0.,-1.,1.), "green": (0.,-1.,1.), "blue": (0.,-1.,1.), "range": ("all","shadows","midtones","highlights")},
    "colorize": {"hue": (30.,0.,360.), "saturation": (.5,0.,1.), "lightness": (0.,-1.,1.)},
    "desaturate": {"method": ("luma","luminance","lightness","average","value")},
    "levels": {"black": (0,0,254), "white": (255,1,255), "gamma": (1.,.1,10.)},
}
EFFECT_SPECS = {
    "gaussian-blur": {"radius": (2.,0.,100.)},
    "motion-blur": {"length": (16,1,128), "angle": (0.,-360.,360.)},
    "pixelize": {"size": (8,1,512)},
    "denoise": {"radius": (1,1,3)},
    "red-eye": {"threshold": (1.5,1.,5.), "strength": (1.,0.,1.)},
    "ripple": {"amount": (8.,0.,512.), "period": (32.,1.,4096.), "phase": (0.,-360.,360.), "axis": ("x","y")},
    "waves": {"amount": (8.,0.,512.), "period": (32.,1.,4096.), "phase": (0.,-360.,360.), "center": "center"},
    "lens": {"amount": (.25,-.8,2.), "center": "center"},
    "whirl-pinch": {"angle": (90.,-720.,720.), "pinch": (0.,-.9,2.), "radius": (128.,1.,4096.), "center": "center"},
    "shadow": {"dx": (8,-4096,4096), "dy": (8,-4096,4096), "radius": (4.,0.,100.), "opacity": (.5,0.,1.), "color": "#000000"},
    "bloom": {"radius": (8.,0.,100.), "threshold": (.7,0.,1.), "strength": (.5,0.,4.)},
    "rgb-noise": {"amount": (.05,0.,1.), "seed": (0,0,2**32-1)},
    "hsv-noise": {"hue": (.03,0.,1.), "saturation": (.05,0.,1.), "value": (.05,0.,1.), "seed": (0,0,2**32-1)},
    "displacement": {"amount": (8.,-512.,512.), "channel": ("luma","red","green","blue","alpha"), "axis": ("both","x","y")},
    "bump-map": {"depth": (1.,0.,10.), "azimuth": (135.,-360.,360.), "elevation": (45.,1.,89.), "ambient": (.2,0.,1.)},
    "clouds": {"scale": (64,2,1024), "octaves": (5,1,8), "seed": (0,0,2**32-1), "from": "#000000", "to": "#ffffff"},
    "unsharp-mask": {"radius": (2.,0.,100.), "amount": (1.5,0.,10.), "threshold": (3,0,255)},
    "edge-detect": {}, "emboss": {}, "posterize": {"bits": (4,1,8)},
    "threshold": {"value": (128,0,255)},
}
ADJUSTMENTS = tuple(ADJUSTMENT_SPECS)
EFFECTS = tuple(EFFECT_SPECS)


def _validate(name, options, catalog):
    if not isinstance(name,str) or name not in catalog:
        raise ValueError(f"Unknown operation {name!r}. Choose from {', '.join(catalog)}.")
    if not isinstance(options,dict) or any(not isinstance(key,str) for key in options):
        raise ValueError("Operation options must be an object with string keys.")
    specs = catalog[name]
    unknown = set(options)-set(specs)
    if unknown: raise ValueError(f"Unknown option(s): {', '.join(sorted(unknown))}.")
    result = {}
    for key,spec in specs.items():
        value = options.get(key,spec[0] if isinstance(spec,tuple) else spec)
        if isinstance(spec,tuple):
            if isinstance(spec[0],str):
                if value not in spec: raise ValueError(f"{key} must be {'|'.join(spec)}.")
            else:
                value = finite(value,spec[1],spec[2])
                if isinstance(spec[0],int):
                    if value != int(value): raise ValueError(f"{key} must be an integer.")
                    value = int(value)
        elif key == "center":
            value = "center" if value == "center" else list(point(value))
        elif key == "points":
            curve = points(value,2,32)
            if any(not 0 <= number <= 255 for p in curve for number in p):
                raise ValueError("Curve coordinates must be 0..255.")
            if any(a[0] >= b[0] for a,b in zip(curve,curve[1:])):
                raise ValueError("Curve input points must increase strictly.")
            value = [list(p) for p in curve]
        elif key in ("color","from","to"):
            if not isinstance(value,str): raise ValueError(f"{key} must be a color name or hexadecimal string.")
            rgba = ImageColor.getcolor(value,"RGBA")
            value = "#{:02x}{:02x}{:02x}{:02x}".format(*rgba)
        result[key] = value
    if name == "levels" and result["black"] >= result["white"]:
        raise ValueError("Levels black must be less than white.")
    return result


def validate_adjustment(name, options=None):
    return _validate(name, {} if options is None else options, ADJUSTMENT_SPECS)


def validate_effect(name, options=None):
    return _validate(name, {} if options is None else options, EFFECT_SPECS)


def _rgba(image):
    if image.mode != "RGBA" or image.width*image.height > 4_194_304:
        raise ValueError("Effects require an RGBA image of at most 4,194,304 pixels.")


def rgb_to_hsv(rgb):
    maximum,minimum = rgb.max(axis=-1),rgb.min(axis=-1)
    difference = maximum-minimum
    safe = np.maximum(difference,1e-10)
    r,g,b = np.moveaxis(rgb,-1,0)
    hue = np.where(maximum == r, (g-b)/safe,
                   np.where(maximum == g,2+(b-r)/safe,4+(r-g)/safe))/6
    hue = np.where(difference>0,hue%1,0)
    saturation = np.where(maximum>0,difference/np.maximum(maximum,1e-10),0)
    return np.stack((hue,saturation,maximum),axis=-1)


def hsv_to_rgb(hsv):
    h,s,v = np.moveaxis(hsv,-1,0)
    h = (h%1)*6
    sector = np.floor(h).astype(np.int32)
    f = h-sector
    p,q,t = v*(1-s),v*(1-s*f),v*(1-s*(1-f))
    r = np.choose(sector%6,(v,q,p,p,t,v))
    g = np.choose(sector%6,(t,v,v,q,p,p))
    b = np.choose(sector%6,(p,p,t,v,v,q))
    return np.stack((r,g,b),axis=-1)


def rgb_to_hsl(rgb):
    maximum,minimum = rgb.max(axis=-1),rgb.min(axis=-1)
    lightness=(maximum+minimum)/2
    saturation=(maximum-minimum)/np.maximum(1-np.abs(2*lightness-1),1e-10)
    return np.stack((rgb_to_hsv(rgb)[...,0],saturation,lightness),axis=-1)


def hsl_to_rgb(hsl):
    hue,saturation,lightness=np.moveaxis(hsl,-1,0)
    value=lightness+saturation*np.minimum(lightness,1-lightness)
    hsv_saturation=np.where(value>0,2*(1-lightness/np.maximum(value,1e-10)),0)
    return hsv_to_rgb(np.stack((hue,hsv_saturation,value),axis=-1))


def _curve_table(control):
    # Cubic Hermite interpolation with shape-preserving tangents. Flat or
    # opposing slopes use zero tangents, keeping a curve inside each segment.
    data = np.asarray(control,dtype=np.float64)
    x,y = data[:,0],data[:,1]
    intervals = np.diff(x); slopes = np.diff(y)/intervals
    tangent = np.zeros(len(x))
    tangent[0],tangent[-1] = slopes[0],slopes[-1]
    for i in range(1,len(x)-1):
        if slopes[i-1]*slopes[i] > 0:
            w1,w2 = 2*intervals[i]+intervals[i-1], intervals[i]+2*intervals[i-1]
            tangent[i] = (w1+w2)/(w1/slopes[i-1]+w2/slopes[i])
    values = np.arange(256,dtype=float)
    result = np.interp(values,x,y)
    for i in range(len(intervals)):
        active = (values>=x[i]) & (values<=x[i+1])
        t = (values[active]-x[i])/intervals[i]
        result[active] = ((2*t**3-3*t**2+1)*y[i]+(t**3-2*t**2+t)*intervals[i]*tangent[i]+
                          (-2*t**3+3*t**2)*y[i+1]+(t**3-t**2)*intervals[i]*tangent[i+1])
    return np.clip(np.floor(result+.5),0,255).astype(np.uint8).tolist()


def apply_adjustment(image, name, **options):
    """Return adjusted RGBA pixels; alpha is retained except alpha curves."""
    _rgba(image)
    opts = validate_adjustment(name,options)
    if name == "curves":
        identity = list(range(256)); table = _curve_table(opts["points"])
        channels = [identity.copy() for _ in range(4)]
        indices = (0,1,2) if opts["channel"] == "rgb" else ({"red":0,"green":1,"blue":2,"alpha":3}[opts["channel"]],)
        for index in indices: channels[index] = table
        return image.point(sum(channels,[]))
    source = np.asarray(image,dtype=np.uint8)
    result = source.copy()
    for top in range(0,image.height,64):
        rgb = source[top:top+64,:,:3].astype(np.float32)/255
        luma = rgb @ np.array((.2126,.7152,.0722),dtype=np.float32)
        if name == "exposure":
            rgb = np.maximum(0,rgb*2**opts["stops"]+opts["offset"])**(1/opts["gamma"])
        elif name == "levels":
            rgb = np.clip((rgb*255-opts["black"])/(opts["white"]-opts["black"]),0,1)**(1/opts["gamma"])
        elif name == "shadows-highlights":
            width = opts["width"]
            shadow = np.maximum(0,1-luma/width)**2*opts["shadows"]
            highlight = np.maximum(0,1-(1-luma)/width)**2*opts["highlights"]
            shift = shadow-highlight
            rgb += np.where(shift[...,None] >= 0,(1-rgb)*shift[...,None],rgb*shift[...,None])
        elif name == "hue-saturation":
            hsl = rgb_to_hsl(rgb)
            hsl[...,0] = (hsl[...,0]+opts["hue"]/360)%1
            hsl[...,1] = np.clip(hsl[...,1]*opts["saturation"],0,1)
            light = opts["lightness"]
            hsl[...,2] = hsl[...,2]+(1-hsl[...,2])*light if light >= 0 else hsl[...,2]*(1+light)
            rgb = hsl_to_rgb(hsl)
        elif name == "color-balance":
            region = opts["range"]
            weight = (np.ones_like(luma) if region == "all" else
                      (1-luma)**2 if region == "shadows" else
                      luma**2 if region == "highlights" else 4*luma*(1-luma))
            rgb += weight[...,None]*np.asarray((opts["red"],opts["green"],opts["blue"]),dtype=np.float32)
        elif name == "colorize":
            value = luma+(1-luma)*opts["lightness"] if opts["lightness"] >= 0 else luma*(1+opts["lightness"])
            rgb = hsv_to_rgb(np.stack((np.full_like(value,opts["hue"]/360),np.full_like(value,opts["saturation"]),value),axis=-1))
        elif name == "desaturate":
            method = opts["method"]
            if method == "luma": gray = rgb @ np.array((.299,.587,.114),dtype=np.float32)
            elif method == "luminance":
                linear = np.where(rgb<=.04045,rgb/12.92,((rgb+.055)/1.055)**2.4)
                gray = linear @ np.array((.2126,.7152,.0722),dtype=np.float32)
                gray = np.where(gray<=.0031308,gray*12.92,1.055*gray**(1/2.4)-.055)
            elif method == "lightness": gray = (rgb.max(axis=-1)+rgb.min(axis=-1))/2
            elif method == "average": gray = rgb.mean(axis=-1)
            else: gray = rgb.max(axis=-1)
            rgb = np.repeat(gray[...,None],3,axis=-1)
        result[top:top+64,:,:3] = np.clip(np.floor(rgb*255+.5),0,255).astype(np.uint8)
    return Image.fromarray(result)


def _restore_alpha(rgb,source):
    result = rgb.convert("RGBA")
    result.putalpha(source.getchannel("A"))
    return result


def _motion(image,length,angle):
    if length == 1: return image.copy()
    source = np.asarray(image,dtype=np.uint8)
    output = np.empty_like(source)
    width,height = image.size
    radians = math.radians(angle)
    # Up to 65 equally spaced taps across the requested pixel length.
    offsets = np.linspace(-(length-1)/2,(length-1)/2,min(length,65))
    dx = np.rint(offsets*math.cos(radians)).astype(int)
    dy = np.rint(offsets*math.sin(radians)).astype(int)
    columns = np.arange(width)[None,:]
    for top in range(0,height,32):
        rows = np.arange(top,min(height,top+32))[:,None]
        total = np.zeros((len(rows),width,4),dtype=np.float32)
        for ox,oy in zip(dx,dy):
            pixels = source[np.clip(rows+oy,0,height-1),np.clip(columns+ox,0,width-1)].astype(np.float32)
            pixels[...,:3] *= pixels[...,3:4]/255
            total += pixels
        total /= len(offsets)
        total[...,:3] *= 255/np.maximum(total[...,3:4],1e-6)
        output[top:top+len(rows)] = np.clip(np.floor(total+.5),0,255).astype(np.uint8)
    return Image.fromarray(output)


def apply_effect(image,name,**options):
    """Return one reproducible canvas-sized effect without editing its input."""
    _rgba(image)
    opts = validate_effect(name,options)
    width,height = image.size
    if name == "gaussian-blur":
        if opts["radius"] == 0: return image.copy()
        return image.convert("RGBa").filter(ImageFilter.GaussianBlur(opts["radius"])).convert("RGBA")
    if name == "motion-blur": return _motion(image,opts["length"],opts["angle"])
    if name == "pixelize":
        block = opts["size"]
        columns,rows = math.ceil(width/block),math.ceil(height/block)
        # Extend edge pixels to whole blocks, so a requested 8px block is
        # exactly 8px even when the canvas width is not divisible by eight.
        padded = Image.new("RGBA",(columns*block,rows*block))
        padded.paste(image,(0,0))
        if padded.width > width:
            padded.paste(image.crop((width-1,0,width,height)).resize((padded.width-width,height)),(width,0))
        if padded.height > height:
            padded.paste(padded.crop((0,height-1,padded.width,height)).resize((padded.width,padded.height-height)),(0,height))
        reduced = padded.resize((columns,rows),Image.Resampling.BOX)
        return reduced.resize(padded.size,Image.Resampling.NEAREST).crop((0,0,width,height))
    if name == "denoise":
        return _restore_alpha(image.convert("RGB").filter(ImageFilter.MedianFilter(2*opts["radius"]+1)),image)
    if name in ("edge-detect","emboss","posterize","threshold","unsharp-mask"):
        rgb = image.convert("RGB")
        if name == "edge-detect": rgb = rgb.filter(ImageFilter.FIND_EDGES)
        elif name == "emboss": rgb = rgb.filter(ImageFilter.EMBOSS)
        elif name == "posterize": rgb = ImageOps.posterize(rgb,opts["bits"])
        elif name == "threshold": rgb = rgb.convert("L").point(lambda v:255 if v>=opts["value"] else 0).convert("RGB")
        else: rgb = rgb.filter(ImageFilter.UnsharpMask(opts["radius"],round(opts["amount"]*100),opts["threshold"]))
        return _restore_alpha(rgb,image)
    if name == "shadow":
        color = ImageColor.getcolor(opts["color"],"RGBA")
        alpha = image.getchannel("A").filter(ImageFilter.GaussianBlur(opts["radius"]))
        alpha = alpha.point([round(v*opts["opacity"]*color[3]/255) for v in range(256)])
        shifted = Image.new("L",image.size); shifted.paste(alpha,(opts["dx"],opts["dy"]))
        shadow = Image.new("RGBA",image.size,color); shadow.putalpha(shifted)
        result = Image.alpha_composite(shadow,image)
        # Hidden RGB outside a shadow remains the original data rather than
        # acquiring the shadow's RGB across an otherwise empty canvas.
        empty = result.getchannel("A").point([255]+[0]*255)
        return Image.composite(image,result,empty)
    if name == "clouds":
        rng = np.random.default_rng(opts["seed"])
        field = np.zeros((height,width),dtype=np.float32); weight = 0.
        for octave in range(opts["octaves"]):
            cell = max(1,opts["scale"]//2**octave)
            nw,nh = max(2,math.ceil(width/cell)),max(2,math.ceil(height/cell))
            noise = Image.fromarray(rng.integers(0,256,(nh,nw),dtype=np.uint8)).resize(image.size,Image.Resampling.BICUBIC)
            factor = .5**octave
            field += np.asarray(noise,dtype=np.float32)*factor; weight += factor
        gray = Image.fromarray(np.clip(np.floor(field/weight+.5),0,255).astype(np.uint8))
        dark,light = ImageColor.getcolor(opts["from"],"RGBA"),ImageColor.getcolor(opts["to"],"RGBA")
        channels = [gray.point([round(dark[i]+(light[i]-dark[i])*v/255) for v in range(256)]) for i in range(4)]
        return Image.merge("RGBA",channels)
    if name in ("ripple","waves","lens","whirl-pinch","displacement"):
        center = opts.get("center","center")
        cx,cy = (width/2,height/2) if center == "center" else point(center)
        if name == "displacement":
            channel = opts["channel"]
            map_image = (image.convert("L") if channel == "luma" else image.getchannel({"red":"R","green":"G","blue":"B","alpha":"A"}[channel]))
            field = np.asarray(map_image,dtype=np.float32)/127.5-1
        def mapper(x,y):
            if name == "ripple":
                phase = opts["phase"]*math.pi/180
                if opts["axis"] == "x": return x+opts["amount"]*np.sin(y*2*math.pi/opts["period"]+phase),y
                return x,y+opts["amount"]*np.sin(x*2*math.pi/opts["period"]+phase)
            ux,uy = x-cx,y-cy
            radius = np.sqrt(ux*ux+uy*uy)
            if name == "waves":
                shift = opts["amount"]*np.sin(radius*2*math.pi/opts["period"]+opts["phase"]*math.pi/180)
                factor = (radius+shift)/np.maximum(radius,1e-6)
                return cx+ux*factor,cy+uy*factor
            if name == "lens":
                factor = 1+opts["amount"]*(ux*ux+uy*uy)/max(1,(max(width,height)/2)**2)
                return cx+ux*factor,cy+uy*factor
            if name == "whirl-pinch":
                weight = np.maximum(0,1-radius/opts["radius"])**2
                factor = 1+opts["pinch"]*weight
                angle = -opts["angle"]*math.pi/180*weight
                return cx+(ux*np.cos(angle)-uy*np.sin(angle))*factor,cy+(ux*np.sin(angle)+uy*np.cos(angle))*factor
            shift = field[y.astype(np.int32),x.astype(np.int32)]*opts["amount"]
            return x+shift*(opts["axis"] != "y"),y+shift*(opts["axis"] != "x")
        return remap_image(image,mapper,resample="bilinear",border="edge")
    source = np.asarray(image,dtype=np.uint8)
    result = source.copy()
    rng = np.random.default_rng(opts.get("seed",0))
    if name == "bloom":
        cutoff = round(opts["threshold"]*255)
        rgb = image.convert("RGB").point([max(0,v-cutoff) for v in range(256)]*3)
        rgb = ImageChops.multiply(rgb,Image.merge("RGB",(image.getchannel("A"),)*3))
        rgb = rgb.filter(ImageFilter.GaussianBlur(opts["radius"]))
        glow = np.asarray(rgb,dtype=np.uint8)
    if name == "bump-map":
        # Include one halo row above and below each tile for continuous normals.
        heights = np.asarray(image.convert("L"),dtype=np.float32)/255
        azimuth,elevation = math.radians(opts["azimuth"]),math.radians(opts["elevation"])
        light = (math.cos(elevation)*math.cos(azimuth),math.cos(elevation)*math.sin(azimuth),math.sin(elevation))
    for top in range(0,height,64):
        rgb = source[top:top+64,:,:3].astype(np.float32)/255
        if name == "red-eye":
            red = (rgb[...,0]>opts["threshold"]*np.maximum(rgb[...,1],rgb[...,2])) & (rgb[...,0]>.2)
            replacement = (rgb[...,1]+rgb[...,2])/2
            rgb[...,0] = np.where(red,rgb[...,0]*(1-opts["strength"])+replacement*opts["strength"],rgb[...,0])
        elif name == "bloom": rgb += glow[top:top+64].astype(np.float32)/255*opts["strength"]
        elif name == "rgb-noise": rgb += rng.normal(0,opts["amount"],rgb.shape).astype(np.float32)
        elif name == "hsv-noise":
            hsv = rgb_to_hsv(rgb)
            hsv += rng.normal(0,(opts["hue"],opts["saturation"],opts["value"]),hsv.shape).astype(np.float32)
            hsv[...,0] %= 1; hsv[...,1:] = np.clip(hsv[...,1:],0,1)
            rgb = hsv_to_rgb(hsv)
        elif name == "bump-map":
            rows = np.arange(top,min(height,top+64))
            gx = (np.roll(heights[rows],-1,axis=1)-np.roll(heights[rows],1,axis=1))*opts["depth"]
            gx[:,0] = (heights[rows, min(1,width-1)]-heights[rows,0])*opts["depth"]
            gx[:,-1] = (heights[rows,-1]-heights[rows,max(0,width-2)])*opts["depth"]
            gy = (heights[np.minimum(rows+1,height-1)]-heights[np.maximum(rows-1,0)])*opts["depth"]
            normal = np.sqrt(1+gx*gx+gy*gy)
            shading = np.maximum(0,(-gx*light[0]-gy*light[1]+light[2])/normal)
            shading = opts["ambient"]+(1-opts["ambient"])*shading
            rgb *= shading[...,None]/max(light[2],.1)
        result[top:top+64,:,:3] = np.clip(np.floor(rgb*255+.5),0,255).astype(np.uint8)
    return Image.fromarray(result)
