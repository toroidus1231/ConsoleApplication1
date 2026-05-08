// Minimal vec3/mat4 utilities. Column-major mat4 stored as Float32Array(16).

export const vec3 = {
  create: (x = 0, y = 0, z = 0) => new Float32Array([x, y, z]),
  set: (o, x, y, z) => { o[0] = x; o[1] = y; o[2] = z; return o; },
  copy: (a) => new Float32Array([a[0], a[1], a[2]]),
  add:  (a, b) => new Float32Array([a[0]+b[0], a[1]+b[1], a[2]+b[2]]),
  sub:  (a, b) => new Float32Array([a[0]-b[0], a[1]-b[1], a[2]-b[2]]),
  scale:(a, s) => new Float32Array([a[0]*s,    a[1]*s,    a[2]*s]),
  dot:  (a, b) => a[0]*b[0] + a[1]*b[1] + a[2]*b[2],
  len:  (a) => Math.hypot(a[0], a[1], a[2]),
  norm: (a) => { const l = Math.hypot(a[0],a[1],a[2]) || 1; return new Float32Array([a[0]/l, a[1]/l, a[2]/l]); },
  cross:(a, b) => new Float32Array([
    a[1]*b[2] - a[2]*b[1],
    a[2]*b[0] - a[0]*b[2],
    a[0]*b[1] - a[1]*b[0],
  ]),
  lerp: (a, b, t) => new Float32Array([
    a[0] + (b[0]-a[0])*t,
    a[1] + (b[1]-a[1])*t,
    a[2] + (b[2]-a[2])*t,
  ]),
};

export const mat4 = {
  create: () => {
    const m = new Float32Array(16);
    m[0] = m[5] = m[10] = m[15] = 1;
    return m;
  },
  identity: (o) => {
    o.fill(0);
    o[0] = o[5] = o[10] = o[15] = 1;
    return o;
  },
  copy: (a) => new Float32Array(a),

  perspective: (out, fovy, aspect, near, far) => {
    const f = 1.0 / Math.tan(fovy / 2);
    const nf = 1 / (near - far);
    out.fill(0);
    out[0]  = f / aspect;
    out[5]  = f;
    out[10] = (far + near) * nf;
    out[11] = -1;
    out[14] = 2 * far * near * nf;
    return out;
  },

  ortho: (out, l, r, b, t, n, f) => {
    const lr = 1/(l-r), bt = 1/(b-t), nf = 1/(n-f);
    out.fill(0);
    out[0]  = -2 * lr;
    out[5]  = -2 * bt;
    out[10] =  2 * nf;
    out[12] = (l+r)*lr;
    out[13] = (t+b)*bt;
    out[14] = (f+n)*nf;
    out[15] = 1;
    return out;
  },

  lookAt: (out, eye, center, up) => {
    const ex = eye[0], ey = eye[1], ez = eye[2];
    let zx = ex - center[0], zy = ey - center[1], zz = ez - center[2];
    let zl = Math.hypot(zx, zy, zz) || 1;
    zx /= zl; zy /= zl; zz /= zl;
    let xx = up[1]*zz - up[2]*zy;
    let xy = up[2]*zx - up[0]*zz;
    let xz = up[0]*zy - up[1]*zx;
    let xl = Math.hypot(xx, xy, xz) || 1;
    xx /= xl; xy /= xl; xz /= xl;
    const yx = zy*xz - zz*xy;
    const yy = zz*xx - zx*xz;
    const yz = zx*xy - zy*xx;
    out[0]=xx; out[1]=yx; out[2]=zx; out[3]=0;
    out[4]=xy; out[5]=yy; out[6]=zy; out[7]=0;
    out[8]=xz; out[9]=yz; out[10]=zz; out[11]=0;
    out[12]=-(xx*ex + xy*ey + xz*ez);
    out[13]=-(yx*ex + yy*ey + yz*ez);
    out[14]=-(zx*ex + zy*ey + zz*ez);
    out[15]=1;
    return out;
  },

  multiply: (out, a, b) => {
    const a00=a[0],a01=a[1],a02=a[2],a03=a[3];
    const a10=a[4],a11=a[5],a12=a[6],a13=a[7];
    const a20=a[8],a21=a[9],a22=a[10],a23=a[11];
    const a30=a[12],a31=a[13],a32=a[14],a33=a[15];
    for (let i = 0; i < 4; i++) {
      const b0 = b[i*4], b1 = b[i*4+1], b2 = b[i*4+2], b3 = b[i*4+3];
      out[i*4  ] = b0*a00 + b1*a10 + b2*a20 + b3*a30;
      out[i*4+1] = b0*a01 + b1*a11 + b2*a21 + b3*a31;
      out[i*4+2] = b0*a02 + b1*a12 + b2*a22 + b3*a32;
      out[i*4+3] = b0*a03 + b1*a13 + b2*a23 + b3*a33;
    }
    return out;
  },

  translate: (out, x, y, z) => {
    mat4.identity(out);
    out[12] = x; out[13] = y; out[14] = z;
    return out;
  },

  scale: (out, sx, sy, sz) => {
    mat4.identity(out);
    out[0] = sx; out[5] = sy; out[10] = sz;
    return out;
  },

  rotateY: (out, rad) => {
    const c = Math.cos(rad), s = Math.sin(rad);
    mat4.identity(out);
    out[0] = c;  out[2] = s;
    out[8] = -s; out[10] = c;
    return out;
  },

  rotateX: (out, rad) => {
    const c = Math.cos(rad), s = Math.sin(rad);
    mat4.identity(out);
    out[5] = c;  out[6] = s;
    out[9] = -s; out[10] = c;
    return out;
  },

  // Compose translation, Y rotation, scale into out.
  compose: (out, tx, ty, tz, ry, sx, sy, sz) => {
    const c = Math.cos(ry), s = Math.sin(ry);
    out[0] = c*sx;  out[1] = 0;    out[2] = -s*sx; out[3] = 0;
    out[4] = 0;     out[5] = sy;   out[6] = 0;     out[7] = 0;
    out[8] = s*sz;  out[9] = 0;    out[10] = c*sz; out[11] = 0;
    out[12] = tx;   out[13] = ty;  out[14] = tz;   out[15] = 1;
    return out;
  },

  // Inverse-transpose 3x3 of a model matrix (returns 9-elt Float32Array, column-major).
  // For uniform-scaled rigid transforms, this reduces to the model 3x3.
  normalMatrix: (out9, m) => {
    // Extract upper-left 3x3
    const a00=m[0],a01=m[1],a02=m[2];
    const a10=m[4],a11=m[5],a12=m[6];
    const a20=m[8],a21=m[9],a22=m[10];
    const det = a00*(a11*a22 - a12*a21)
              - a01*(a10*a22 - a12*a20)
              + a02*(a10*a21 - a11*a20);
    if (!det) {
      out9[0] = 1; out9[1] = 0; out9[2] = 0;
      out9[3] = 0; out9[4] = 1; out9[5] = 0;
      out9[6] = 0; out9[7] = 0; out9[8] = 1;
      return out9;
    }
    const id = 1/det;
    out9[0] = (a11*a22 - a12*a21) * id;
    out9[1] = (a02*a21 - a01*a22) * id;
    out9[2] = (a01*a12 - a02*a11) * id;
    out9[3] = (a12*a20 - a10*a22) * id;
    out9[4] = (a00*a22 - a02*a20) * id;
    out9[5] = (a02*a10 - a00*a12) * id;
    out9[6] = (a10*a21 - a11*a20) * id;
    out9[7] = (a01*a20 - a00*a21) * id;
    out9[8] = (a00*a11 - a01*a10) * id;
    return out9;
  },
};

export const clamp = (x, a, b) => x < a ? a : (x > b ? b : x);
export const lerp  = (a, b, t) => a + (b - a) * t;
export const smoothstep = (a, b, t) => {
  const x = clamp((t - a) / (b - a), 0, 1);
  return x*x*(3 - 2*x);
};
