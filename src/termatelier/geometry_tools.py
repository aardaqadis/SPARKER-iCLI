"""Bounded geometric mappings for pixel layers and grayscale selection masks.

Matrices map source to destination. A transparent/zero border is used; output
always retains the canvas size. Cage deformation is smooth weighted handle
displacement, and 3D is a projected plane rather than a mesh renderer.
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw

RESAMPLING = {"nearest": Image.Resampling.NEAREST,
              "bilinear": Image.Resampling.BILINEAR, "bicubic": Image.Resampling.BICUBIC}
OPERATIONS = ("translate", "scale", "rotate", "shear", "perspective", "3d", "unified", "handles", "cage", "warp")


def finite(value, low=-32768, high=32768):
    if isinstance(value,bool): raise ValueError("Expected a number, not a boolean.")
    try:
        value = float(value)
    except (TypeError,ValueError) as error:
        raise ValueError("Expected a finite number.") from error
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"Expected a finite number in {low}..{high}.")
    return value


def point(value):
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value,(tuple,list,np.ndarray)) or len(value) != 2:
        raise ValueError("Expected an X,Y point.")
    return tuple(finite(v) for v in value)


def points(value, minimum=1, maximum=32):
    if isinstance(value, str):
        value = value.split(";")
    if not isinstance(value,(tuple,list,np.ndarray)) or not minimum <= len(value) <= maximum:
        raise ValueError(f"Expected {minimum}..{maximum} points separated by semicolons.")
    return [point(item) for item in value]


def _translation(x, y):
    return np.array(((1, 0, x), (0, 1, y), (0, 0, 1)), dtype=np.float64)


def _homography(source, destination):
    source, destination = np.asarray(source, dtype=float), np.asarray(destination, dtype=float)
    rows, values = [], []
    for (x, y), (u, v) in zip(source, destination):
        rows.extend(((x, y, 1, 0, 0, 0, -u*x, -u*y), (0, 0, 0, x, y, 1, -v*x, -v*y)))
        values.extend((u, v))
    try:
        result = np.linalg.solve(np.asarray(rows), values)
    except np.linalg.LinAlgError as error:
        raise ValueError("The transform handles are degenerate or collinear.") from error
    matrix = np.append(result, 1).reshape(3, 3)
    if not np.isfinite(matrix).all() or abs(np.linalg.det(matrix)) < 1e-10:
        raise ValueError("The transform collapses the image.")
    return matrix


def _convex(quad):
    crosses = []
    for i in range(4):
        a, b, c = np.asarray(quad[i]), np.asarray(quad[(i+1)%4]), np.asarray(quad[(i+2)%4])
        ab, bc = b-a, c-b
        crosses.append(ab[0]*bc[1]-ab[1]*bc[0])
    return all(v > 1e-8 for v in crosses) or all(v < -1e-8 for v in crosses)


def geometry_matrix(operation, size, **options):
    """Build a source-to-destination affine or projective transform."""
    if operation not in OPERATIONS:
        raise ValueError(f"Unknown geometry operation {operation!r}.")
    width, height = size
    pivot = point(options.get("pivot", (width/2, height/2)))
    if operation == "perspective":
        target = points(options.get("corners", ()), 4, 4)
        if not _convex(target):
            raise ValueError("Perspective corners must form a non-crossing convex quadrilateral.")
        return _homography(((0, 0), (width, 0), (width, height), (0, height)), target)
    if operation == "handles":
        source = points(options.get("source", ()), 1, 4)
        destination = points(options.get("destination", ()), len(source), len(source))
        if len(source) == 1:
            return _translation(destination[0][0]-source[0][0], destination[0][1]-source[0][1])
        if len(source) == 4:
            return _homography(source, destination)
        if len(source) == 3:
            try:
                affine = np.linalg.solve(np.column_stack((source, np.ones(3))), destination).T
            except np.linalg.LinAlgError as error:
                raise ValueError("Affine handles must not be collinear.") from error
            return np.vstack((affine, (0, 0, 1)))
        a, b = np.asarray(source)
        c, d = np.asarray(destination)
        before, after = b-a, d-c
        denominator = float(before @ before)
        if denominator < 1e-10 or float(after @ after) < 1e-10:
            raise ValueError("Similarity handles must be distinct.")
        real = float(before @ after) / denominator
        imaginary = (before[0]*after[1]-before[1]*after[0]) / denominator
        linear = np.array(((real, -imaginary, 0), (imaginary, real, 0), (0, 0, 1)))
        return _translation(*c) @ linear @ _translation(*(-a))
    if operation == "3d":
        rx, ry, rz = (math.radians(finite(options.get(key, 0), -85 if key != "rz" else -360,
                                         85 if key != "rz" else 360)) for key in ("rx", "ry", "rz"))
        cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
        rotation = (np.array(((cz,-sz,0),(sz,cz,0),(0,0,1))) @
                    np.array(((cy,0,sy),(0,1,0),(-sy,0,cy))) @
                    np.array(((1,0,0),(0,cx,-sx),(0,sx,cx))))
        distance = finite(options.get("distance", max(size)*2), max(size), max(size)*100)
        # Perspective denominator is distance + z. The plane is centered at
        # the chosen pivot and projected through a camera at -distance.
        r = rotation
        projection = np.array(((distance*r[0,0], distance*r[0,1], 0),
                               (distance*r[1,0], distance*r[1,1], 0),
                               (r[2,0], r[2,1], distance)))
        if any(distance+r[2,0]*(x-pivot[0])+r[2,1]*(y-pivot[1]) <= 1e-6
               for x,y in ((0,0),(width,0),(width,height),(0,height))):
            raise ValueError("The projected plane crosses the camera; use a nearer pivot or greater distance.")
        return _translation(*pivot) @ projection @ _translation(-pivot[0], -pivot[1])
    tx = finite(options.get("dx", 0)); ty = finite(options.get("dy", 0))
    sx = finite(options.get("sx", 1), .01, 100); sy = finite(options.get("sy", sx), .01, 100)
    angle = math.radians(finite(options.get("angle", 0), -360, 360))
    shx = finite(options.get("shx", 0), -10, 10); shy = finite(options.get("shy", 0), -10, 10)
    if operation == "translate": return _translation(tx, ty)
    if operation == "scale": angle = shx = shy = tx = ty = 0
    elif operation == "rotate": sx = sy = 1; shx = shy = tx = ty = 0
    elif operation == "shear": sx = sy = 1; angle = tx = ty = 0
    elif operation != "unified":
        raise ValueError(f"{operation} uses a deformation field rather than a matrix.")
    shear = np.array(((1,shx,0),(shy,1,0),(0,0,1)), dtype=float)
    rotation = np.array(((math.cos(angle),-math.sin(angle),0),
                         (math.sin(angle),math.cos(angle),0),(0,0,1)))
    return (_translation(tx+pivot[0], ty+pivot[1]) @ rotation @ shear @
            np.diag((sx,sy,1)) @ _translation(-pivot[0],-pivot[1]))


def remap_image(image, mapper, *, resample="bilinear", border="transparent"):
    """Sample a bounded inverse displacement field in 64-row tiles.

    Bilinear RGBA sampling uses premultiplied alpha so translucent edges do
    not acquire dark fringes. Tiles keep temporary buffers bounded at 4MP.
    """
    if resample not in ("nearest", "bilinear"):
        raise ValueError("Deformation fields support nearest or bilinear sampling.")
    if image.mode not in ("RGBA", "L"):
        raise ValueError("Geometry requires an RGBA image or L mask.")
    if border not in ("transparent", "edge", "wrap"):
        raise ValueError("Unknown sampling border.")
    width, height = image.size
    source = np.asarray(image, dtype=np.uint8)
    if image.mode == "L": source = source[..., None]
    output = np.empty_like(source)
    columns = np.arange(width, dtype=np.float32)[None, :]

    def fetch(x, y):
        if border == "wrap": return source[y % height, x % width].astype(np.float32)
        samples = source[np.clip(y,0,height-1), np.clip(x,0,width-1)].astype(np.float32)
        if border == "transparent":
            samples *= ((x >= 0) & (x < width) & (y >= 0) & (y < height))[..., None]
        return samples

    for top in range(0, height, 64):
        rows = np.arange(top, min(height,top+64), dtype=np.float32)[:,None]
        x, y = mapper(np.broadcast_to(columns,(len(rows),width)), np.broadcast_to(rows,(len(rows),width)))
        x, y = np.broadcast_arrays(x,y)
        if not np.isfinite(x).all() or not np.isfinite(y).all():
            raise ValueError("The deformation generated nonfinite coordinates.")
        # Bounds also prevent extreme coordinates from overflowing integer casts.
        x = np.clip(x,-1_000_000,1_000_000); y = np.clip(y,-1_000_000,1_000_000)
        if resample == "nearest":
            sampled = fetch(np.floor(x+.5).astype(np.int32),np.floor(y+.5).astype(np.int32))
        else:
            ix, iy = np.floor(x).astype(np.int32), np.floor(y).astype(np.int32)
            fx, fy = x-ix, y-iy
            sampled = np.zeros((*x.shape,source.shape[2]), dtype=np.float32)
            raw_rgb = np.zeros((*x.shape,3),dtype=np.float32) if image.mode == "RGBA" else None
            for ox,oy,weight in ((0,0,(1-fx)*(1-fy)),(1,0,fx*(1-fy)),(0,1,(1-fx)*fy),(1,1,fx*fy)):
                pixels = fetch(ix+ox,iy+oy)
                if image.mode == "RGBA":
                    raw_rgb += pixels[...,:3] * weight[...,None]
                    pixels[...,:3] *= pixels[...,3:4]/255
                sampled += pixels*weight[...,None]
            if image.mode == "RGBA":
                alpha = sampled[...,3:4]
                sampled[...,:3] = np.where(alpha>0, sampled[...,:3]*255/np.maximum(alpha,1e-6),raw_rgb)
        output[top:top+len(rows)] = np.clip(np.floor(sampled+.5),0,255).astype(np.uint8)
    return Image.fromarray(output[...,0] if image.mode == "L" else output)


def transform_image(image, operation, *, resample="nearest", **options):
    """Transform a whole canvas-sized layer/mask without changing dimensions."""
    if image.mode not in ("L", "RGBA") or image.width*image.height > 4_194_304:
        raise ValueError("Transforms require an RGBA/L image of at most 4,194,304 pixels.")
    if resample not in RESAMPLING:
        raise ValueError("Resampling must be nearest, bilinear or bicubic.")
    if operation == "warp":
        mode = options.get("mode", "push")
        if mode not in ("push","grow","shrink","whirl"):
            raise ValueError("Warp mode must be push, grow, shrink or whirl.")
        cx,cy = point(options.get("center",(image.width/2,image.height/2)))
        radius = finite(options.get("radius",32),1,2048)
        amount = finite(options.get("amount",.5),-2,2)
        dx,dy = point(options.get("delta",(radius/2,0)))
        if mode in ("grow","shrink") and amount < 0:
            raise ValueError("Grow/shrink amount must be nonnegative.")
        def mapper(x,y):
            ux,uy = x-cx,y-cy
            weight = np.maximum(0,1-(ux*ux+uy*uy)/(radius*radius))**2
            if mode == "push": return x-dx*weight*amount,y-dy*weight*amount
            if mode in ("grow","shrink"):
                factor = 1/(1+amount*weight) if mode == "grow" else 1+amount*weight
                return cx+ux*factor,cy+uy*factor
            angle = -amount*math.pi*weight
            return cx+ux*np.cos(angle)-uy*np.sin(angle),cy+ux*np.sin(angle)+uy*np.cos(angle)
        return remap_image(image,mapper,resample=resample)
    if operation == "cage":
        source = points(options.get("source",()),3,32)
        destination = points(options.get("destination",()),len(source),len(source))
        if abs(sum(a[0]*b[1]-a[1]*b[0] for a,b in zip(source,source[1:]+source[:1]))) < 1e-8:
            raise ValueError("The cage must enclose a nonempty area.")
        area = Image.new("L",image.size)
        draw = ImageDraw.Draw(area)
        draw.polygon(source,fill=255); draw.polygon(destination,fill=255)
        source,destination = np.asarray(source,dtype=np.float32),np.asarray(destination,dtype=np.float32)
        displacement = destination-source
        def mapper(x,y):
            total = np.zeros_like(x); mx = np.zeros_like(x); my = np.zeros_like(y)
            for (px,py),(dx,dy) in zip(destination,displacement):
                weight = 1/np.maximum((x-px)**2+(y-py)**2,.01)
                total += weight; mx += weight*dx; my += weight*dy
            return x-mx/total,y-my/total
        warped = remap_image(image,mapper,resample=resample)
        return Image.composite(warped,image,area)
    matrix = geometry_matrix(operation,image.size,**options)
    try:
        inverse = np.linalg.inv(matrix)
    except np.linalg.LinAlgError as error:
        raise ValueError("The transform is singular.") from error
    if abs(inverse[2,2]) < 1e-10:
        raise ValueError("The transform crosses the camera plane.")
    inverse /= inverse[2,2]
    if np.allclose(matrix/matrix[2,2],np.eye(3),rtol=0,atol=1e-12): return image.copy()
    coefficients = tuple(inverse.flat)[:8]
    return image.transform(image.size,Image.Transform.PERSPECTIVE,coefficients,RESAMPLING[resample])
