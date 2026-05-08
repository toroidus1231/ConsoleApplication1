// WebGL2 renderer. Single instanced-cube primitive feeds all opaque geometry
// via per-instance model matrix + material. Renders into HDR target, then
// composites with ACES + the water-drop depth scan in a post pass.

import * as gl_util from "./gl.js";
import { mat4, vec3, clamp, smoothstep } from "./math.js";
import { geomVS, geomFS, skyVS, skyFS, postFS } from "./shaders.js";

// Inverse mat4 (allocates) for post pass uniforms (called once per frame).
function invertMat4(out, a) {
  const a00=a[0],a01=a[1],a02=a[2],a03=a[3];
  const a10=a[4],a11=a[5],a12=a[6],a13=a[7];
  const a20=a[8],a21=a[9],a22=a[10],a23=a[11];
  const a30=a[12],a31=a[13],a32=a[14],a33=a[15];
  const b00=a00*a11-a01*a10, b01=a00*a12-a02*a10, b02=a00*a13-a03*a10;
  const b03=a01*a12-a02*a11, b04=a01*a13-a03*a11, b05=a02*a13-a03*a12;
  const b06=a20*a31-a21*a30, b07=a20*a32-a22*a30, b08=a20*a33-a23*a30;
  const b09=a21*a32-a22*a31, b10=a21*a33-a23*a31, b11=a22*a33-a23*a32;
  let det = b00*b11 - b01*b10 + b02*b09 + b03*b08 - b04*b07 + b05*b06;
  if (!det) return null;
  det = 1.0/det;
  out[0]  = (a11*b11 - a12*b10 + a13*b09)*det;
  out[1]  = (a02*b10 - a01*b11 - a03*b09)*det;
  out[2]  = (a31*b05 - a32*b04 + a33*b03)*det;
  out[3]  = (a22*b04 - a21*b05 - a23*b03)*det;
  out[4]  = (a12*b08 - a10*b11 - a13*b07)*det;
  out[5]  = (a00*b11 - a02*b08 + a03*b07)*det;
  out[6]  = (a32*b02 - a30*b05 - a33*b01)*det;
  out[7]  = (a20*b05 - a22*b02 + a23*b01)*det;
  out[8]  = (a10*b10 - a11*b08 + a13*b06)*det;
  out[9]  = (a01*b08 - a00*b10 - a03*b06)*det;
  out[10] = (a30*b04 - a31*b02 + a33*b00)*det;
  out[11] = (a21*b02 - a20*b04 - a23*b00)*det;
  out[12] = (a11*b07 - a10*b09 - a12*b06)*det;
  out[13] = (a00*b09 - a01*b07 + a02*b06)*det;
  out[14] = (a31*b01 - a30*b03 - a32*b00)*det;
  out[15] = (a20*b03 - a21*b01 + a22*b00)*det;
  return out;
}

// Cube mesh: 24 unique verts (6 faces × 4 corners) so per-face normals are flat.
function cubeMesh() {
  const v = [];
  const n = [];
  const u = [];
  const idx = [];
  const faces = [
    { n: [ 0, 0, 1], q: [[-0.5,-0.5, 0.5],[0.5,-0.5, 0.5],[0.5, 0.5, 0.5],[-0.5, 0.5, 0.5]] },
    { n: [ 0, 0,-1], q: [[ 0.5,-0.5,-0.5],[-0.5,-0.5,-0.5],[-0.5, 0.5,-0.5],[ 0.5, 0.5,-0.5]] },
    { n: [ 1, 0, 0], q: [[ 0.5,-0.5, 0.5],[ 0.5,-0.5,-0.5],[ 0.5, 0.5,-0.5],[ 0.5, 0.5, 0.5]] },
    { n: [-1, 0, 0], q: [[-0.5,-0.5,-0.5],[-0.5,-0.5, 0.5],[-0.5, 0.5, 0.5],[-0.5, 0.5,-0.5]] },
    { n: [ 0, 1, 0], q: [[-0.5, 0.5, 0.5],[ 0.5, 0.5, 0.5],[ 0.5, 0.5,-0.5],[-0.5, 0.5,-0.5]] },
    { n: [ 0,-1, 0], q: [[-0.5,-0.5,-0.5],[ 0.5,-0.5,-0.5],[ 0.5,-0.5, 0.5],[-0.5,-0.5, 0.5]] },
  ];
  let base = 0;
  for (const f of faces) {
    for (const p of f.q) v.push(...p);
    for (let i = 0; i < 4; i++) n.push(...f.n);
    u.push(0,0, 1,0, 1,1, 0,1);
    idx.push(base, base+1, base+2, base, base+2, base+3);
    base += 4;
  }
  // Interleave: pos(3) + n(3) + uv(2) = 8 floats * 24 = 192
  const verts = new Float32Array(24 * 8);
  for (let i = 0; i < 24; i++) {
    verts[i*8+0] = v[i*3+0]; verts[i*8+1] = v[i*3+1]; verts[i*8+2] = v[i*3+2];
    verts[i*8+3] = n[i*3+0]; verts[i*8+4] = n[i*3+1]; verts[i*8+5] = n[i*3+2];
    verts[i*8+6] = u[i*2+0]; verts[i*8+7] = u[i*2+1];
  }
  return { verts, indices: new Uint16Array(idx) };
}

// FLOATS_PER_INSTANCE: m0..m3 (16) + albedo+roughness (4) + matvec (4) = 24
const FPI = 24;

export class Renderer {
  constructor(canvas) {
    this.canvas = canvas;
    this.gl = gl_util.getGL(canvas);
    const gl = this.gl;

    gl.enable(gl.DEPTH_TEST);
    gl.depthFunc(gl.LEQUAL);
    gl.enable(gl.CULL_FACE);
    gl.cullFace(gl.BACK);

    this.geomProg = gl_util.compileProgram(gl, geomVS, geomFS, "geom");
    this.skyProg  = gl_util.compileProgram(gl, skyVS,  skyFS,  "sky");
    this.postProg = gl_util.compileProgram(gl, gl_util.fullscreenVS, postFS, "post");

    // Cube VAO.
    const cube = cubeMesh();
    this.cubeBuf = gl_util.makeVAO(gl, this.geomProg, cube.verts, cube.indices, [
      { loc: 0, size: 3, offset: 0,  stride: 32 },
      { loc: 1, size: 3, offset: 12, stride: 32 },
      { loc: 2, size: 2, offset: 24, stride: 32 },
    ], 32);

    // Instance buffer (rebuilt on demand by setInstances).
    this.instanceBuf = gl.createBuffer();
    gl.bindVertexArray(this.cubeBuf.vao);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.instanceBuf);
    const stride = FPI * 4;
    for (let i = 0; i < 4; i++) {
      gl.enableVertexAttribArray(3 + i);
      gl.vertexAttribPointer(3 + i, 4, gl.FLOAT, false, stride, i * 16);
      gl.vertexAttribDivisor(3 + i, 1);
    }
    gl.enableVertexAttribArray(7);
    gl.vertexAttribPointer(7, 4, gl.FLOAT, false, stride, 16 * 4);
    gl.vertexAttribDivisor(7, 1);
    gl.enableVertexAttribArray(8);
    gl.vertexAttribPointer(8, 4, gl.FLOAT, false, stride, 20 * 4);
    gl.vertexAttribDivisor(8, 1);
    gl.bindVertexArray(null);

    // FBO (HDR + depth).
    this._size = [canvas.width, canvas.height];
    this.target = gl_util.makeRenderTarget(gl, canvas.width, canvas.height);

    // Empty VAO for fullscreen passes.
    this.emptyVao = gl.createVertexArray();

    // Camera + uniforms.
    this.view = mat4.create();
    this.proj = mat4.create();
    this.invProj = mat4.create();
    this.invView = mat4.create();
    this.invViewProj = mat4.create();

    this.exposure = 1.05;
    this.vignette = 0.45;
    this.instanceCount = 0;
    this.skyTop = [0.32, 0.55, 0.92];
    this.skyHorizon = [0.78, 0.84, 0.92];
    this.groundColor = [0.18, 0.18, 0.20];
    this.sunColor = [3.4, 3.0, 2.4];
    this.sunDir = vec3.norm([0.55, 0.62, 0.45]);

    this.rippleT = -1;
  }

  resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.round(this.canvas.clientWidth  * dpr);
    const h = Math.round(this.canvas.clientHeight * dpr);
    if (w === this._size[0] && h === this._size[1]) return;
    this.canvas.width = w; this.canvas.height = h;
    this._size = [w, h];

    const gl = this.gl;
    // Recreate target.
    gl.deleteFramebuffer(this.target.fbo);
    gl.deleteTexture(this.target.color);
    gl.deleteTexture(this.target.depth);
    this.target = gl_util.makeRenderTarget(gl, w, h);
  }

  // instances: Float32Array of length N * FPI
  setInstances(data) {
    const gl = this.gl;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.instanceBuf);
    gl.bufferData(gl.ARRAY_BUFFER, data, gl.DYNAMIC_DRAW);
    this.instanceCount = data.length / FPI;
  }

  setCamera(eye, target, up, fovYRad) {
    const aspect = this._size[0] / Math.max(1, this._size[1]);
    mat4.lookAt(this.view, eye, target, up || [0, 1, 0]);
    mat4.perspective(this.proj, fovYRad, aspect, 0.1, 2000);
    invertMat4(this.invProj, this.proj);
    invertMat4(this.invView, this.view);
    const vp = mat4.create();
    mat4.multiply(vp, this.proj, this.view);
    invertMat4(this.invViewProj, vp);
    this.camPos = eye;
  }

  setEnvironment(env) {
    if (env.skyTop)      this.skyTop = env.skyTop;
    if (env.skyHorizon)  this.skyHorizon = env.skyHorizon;
    if (env.groundColor) this.groundColor = env.groundColor;
    if (env.sunColor)    this.sunColor = env.sunColor;
    if (env.sunDir)      this.sunDir = vec3.norm(env.sunDir);
    if (typeof env.exposure === "number") this.exposure = env.exposure;
  }

  triggerRipple() { this.rippleT = 0; this._rippleStart = performance.now(); }

  render(time) {
    const gl = this.gl;
    this.resize();
    const [w, h] = this._size;

    // Update ripple progress.
    if (this.rippleT >= 0) {
      const t = (performance.now() - this._rippleStart) / 4200;
      this.rippleT = t;
      if (t > 1.0) this.rippleT = -1;
    }

    // ---- Pass 1: scene into HDR target ----
    gl.bindFramebuffer(gl.FRAMEBUFFER, this.target.fbo);
    gl.viewport(0, 0, w, h);
    gl.clearColor(0, 0, 0, 1);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);

    // Sky first (writes far depth so geometry can overwrite).
    gl.depthMask(true);
    gl.disable(gl.CULL_FACE);
    gl.useProgram(this.skyProg);
    gl.bindVertexArray(this.emptyVao);
    gl.uniformMatrix4fv(this.skyProg.u("uInvViewProj"), false, this.invViewProj);
    gl.uniform3fv(this.skyProg.u("uCamPos"),     this.camPos);
    gl.uniform3fv(this.skyProg.u("uSunDir"),     this.sunDir);
    gl.uniform3fv(this.skyProg.u("uSunColor"),   this.sunColor);
    gl.uniform3fv(this.skyProg.u("uSkyTop"),     this.skyTop);
    gl.uniform3fv(this.skyProg.u("uSkyHorizon"), this.skyHorizon);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    gl.enable(gl.CULL_FACE);

    // Geometry.
    if (this.instanceCount > 0) {
      gl.useProgram(this.geomProg);
      gl.uniformMatrix4fv(this.geomProg.u("uView"), false, this.view);
      gl.uniformMatrix4fv(this.geomProg.u("uProj"), false, this.proj);
      gl.uniform3fv(this.geomProg.u("uCamPos"),     this.camPos);
      gl.uniform3fv(this.geomProg.u("uSunDir"),     this.sunDir);
      gl.uniform3fv(this.geomProg.u("uSunColor"),   this.sunColor);
      gl.uniform3fv(this.geomProg.u("uSkyTop"),     this.skyTop);
      gl.uniform3fv(this.geomProg.u("uSkyHorizon"), this.skyHorizon);
      gl.uniform3fv(this.geomProg.u("uGroundColor"), this.groundColor);
      gl.uniform1f (this.geomProg.u("uTime"), time);

      gl.bindVertexArray(this.cubeBuf.vao);
      gl.drawElementsInstanced(
        gl.TRIANGLES, this.cubeBuf.indexCount, gl.UNSIGNED_SHORT, 0, this.instanceCount);
      gl.bindVertexArray(null);
    }

    // ---- Pass 2: post composite to screen ----
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.viewport(0, 0, w, h);
    gl.disable(gl.DEPTH_TEST);
    gl.useProgram(this.postProg);
    gl.bindVertexArray(this.emptyVao);

    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.target.color);
    gl.uniform1i(this.postProg.u("uHDR"), 0);

    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, this.target.depth);
    gl.uniform1i(this.postProg.u("uDepth"), 1);

    gl.uniform1f(this.postProg.u("uExposure"), this.exposure);
    gl.uniform1f(this.postProg.u("uTime"), time);
    gl.uniform1f(this.postProg.u("uRippleT"), this.rippleT);
    gl.uniform1f(this.postProg.u("uVignette"), this.vignette);
    gl.uniform2f(this.postProg.u("uResolution"), w, h);
    gl.uniformMatrix4fv(this.postProg.u("uInvProj"), false, this.invProj);
    gl.uniformMatrix4fv(this.postProg.u("uInvView"), false, this.invView);
    gl.uniform3fv(this.postProg.u("uCamPos"), this.camPos);

    gl.drawArrays(gl.TRIANGLES, 0, 3);
    gl.enable(gl.DEPTH_TEST);
  }
}

export const FLOATS_PER_INSTANCE = FPI;
