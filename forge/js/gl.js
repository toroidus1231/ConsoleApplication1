// Thin WebGL2 helpers: program compilation, VAO/buffer bookkeeping, framebuffer.

export function getGL(canvas) {
  const gl = canvas.getContext("webgl2", {
    antialias: true,
    alpha: false,
    depth: true,
    stencil: false,
    powerPreference: "high-performance",
    premultipliedAlpha: false,
  });
  if (!gl) throw new Error("WebGL2 not available");
  // Enable common float attachments where supported.
  gl.getExtension("EXT_color_buffer_float");
  gl.getExtension("OES_texture_float_linear");
  return gl;
}

export function compileProgram(gl, vsSrc, fsSrc, name = "prog") {
  const compile = (type, src) => {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
      const log = gl.getShaderInfoLog(s);
      const stage = type === gl.VERTEX_SHADER ? "VS" : "FS";
      throw new Error(`[${name}/${stage}] ${log}\n` + numberLines(src));
    }
    return s;
  };
  const vs = compile(gl.VERTEX_SHADER, vsSrc);
  const fs = compile(gl.FRAGMENT_SHADER, fsSrc);
  const p = gl.createProgram();
  gl.attachShader(p, vs);
  gl.attachShader(p, fs);
  gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) {
    throw new Error(`[${name}/link] ${gl.getProgramInfoLog(p)}`);
  }
  gl.deleteShader(vs);
  gl.deleteShader(fs);

  // Cache uniform locations on demand.
  const cache = new Map();
  p.u = (n) => {
    if (cache.has(n)) return cache.get(n);
    const loc = gl.getUniformLocation(p, n);
    cache.set(n, loc);
    return loc;
  };
  return p;
}

function numberLines(s) {
  return s.split("\n").map((l, i) => `${String(i+1).padStart(3, " ")}: ${l}`).join("\n");
}

// Build a VAO from interleaved vertex data. attribs = [{name|loc, size, offset, stride}]
export function makeVAO(gl, prog, vertices, indices, attribs, stride) {
  const vao = gl.createVertexArray();
  gl.bindVertexArray(vao);

  const vbo = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, vbo);
  gl.bufferData(gl.ARRAY_BUFFER, vertices, gl.STATIC_DRAW);

  for (const a of attribs) {
    const loc = (typeof a.loc === "number") ? a.loc : gl.getAttribLocation(prog, a.name);
    if (loc < 0) continue;
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, a.size, gl.FLOAT, false, stride, a.offset);
  }

  let ibo = null, indexCount = 0;
  if (indices) {
    ibo = gl.createBuffer();
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ibo);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, indices, gl.STATIC_DRAW);
    indexCount = indices.length;
  }
  gl.bindVertexArray(null);
  return { vao, vbo, ibo, indexCount, vertCount: vertices.length / (stride / 4) };
}

// Create an HDR-ish render target (RGBA16F if available, else RGBA8).
export function makeRenderTarget(gl, w, h, { hdr = true, depth = true } = {}) {
  const fbo = gl.createFramebuffer();
  gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);

  const color = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, color);
  const colorFmt = hdr && gl.getExtension("EXT_color_buffer_float") !== null
    ? { internal: gl.RGBA16F, format: gl.RGBA, type: gl.HALF_FLOAT }
    : { internal: gl.RGBA8,   format: gl.RGBA, type: gl.UNSIGNED_BYTE };
  gl.texImage2D(gl.TEXTURE_2D, 0, colorFmt.internal, w, h, 0, colorFmt.format, colorFmt.type, null);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, color, 0);

  let depthTex = null;
  if (depth) {
    depthTex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, depthTex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.DEPTH_COMPONENT24, w, h, 0,
                  gl.DEPTH_COMPONENT, gl.UNSIGNED_INT, null);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.TEXTURE_2D, depthTex, 0);
  }

  const status = gl.checkFramebufferStatus(gl.FRAMEBUFFER);
  if (status !== gl.FRAMEBUFFER_COMPLETE) {
    throw new Error("framebuffer incomplete: 0x" + status.toString(16));
  }
  gl.bindFramebuffer(gl.FRAMEBUFFER, null);

  return { fbo, color, depth: depthTex, w, h };
}

// Fullscreen triangle (no VBO needed; uses gl_VertexID).
export const fullscreenVS = /* glsl */`#version 300 es
out vec2 vUv;
void main() {
  vec2 p = vec2((gl_VertexID == 1) ? 3.0 : -1.0,
                (gl_VertexID == 2) ? 3.0 : -1.0);
  vUv = p * 0.5 + 0.5;
  gl_Position = vec4(p, 0.0, 1.0);
}`;
