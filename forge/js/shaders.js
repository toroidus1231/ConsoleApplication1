// All GLSL source for the renderer. Three programs:
//   geomVS/FS  — main scene pass (PBR-lite, IBL approximation, sun + sky)
//   skyVS/FS   — procedural sky dome
//   postVS/FS  — composite + ACES tonemap + water-drop depth scan ripple
//
// Notes
// - The scene is rendered into an HDR target and then resolved through ACES
//   in the post pass, so highlights on metallic/glossy equipment behave.
// - "IBL approximation" here means a two-lobe environment fake (a top sky
//   color and a horizon color) plus a Fresnel-weighted reflection toward the
//   sky. Not a real prefiltered cubemap, but visually consistent.
// - The water-drop scan reads scene depth and animates concentric ripples
//   at iso-distance from the camera, modulating the post composite.

export const geomVS = /* glsl */`#version 300 es
precision highp float;

layout(location=0) in vec3 aPos;
layout(location=1) in vec3 aNormal;
layout(location=2) in vec2 aUv;
// per-instance: m0, m1, m2, m3 (model matrix columns)
layout(location=3) in vec4 iM0;
layout(location=4) in vec4 iM1;
layout(location=5) in vec4 iM2;
layout(location=6) in vec4 iM3;
// per-instance material (rgb baseColor + roughness; a stores metallic)
layout(location=7) in vec4 iAlb;
layout(location=8) in vec4 iMat;  // x metallic, y emissive, z aoFactor, w tint

uniform mat4 uView;
uniform mat4 uProj;

out vec3 vWorldPos;
out vec3 vNormal;
out vec2 vUv;
out vec3 vAlbedo;
out vec4 vMat;

void main() {
  mat4 M = mat4(iM0, iM1, iM2, iM3);
  vec4 wp = M * vec4(aPos, 1.0);
  vWorldPos = wp.xyz;
  // Approximate normal transform (uniform scale assumed for instances).
  vNormal = normalize(mat3(M) * aNormal);
  vUv = aUv;
  vAlbedo = iAlb.rgb;
  vMat = vec4(iAlb.a, iMat.x, iMat.y, iMat.z);  // roughness, metallic, emissive, aoFactor
  gl_Position = uProj * uView * wp;
}`;

export const geomFS = /* glsl */`#version 300 es
precision highp float;

in vec3 vWorldPos;
in vec3 vNormal;
in vec2 vUv;
in vec3 vAlbedo;
in vec4 vMat;  // r roughness, g metallic, b emissive, a aoFactor

uniform vec3 uCamPos;
uniform vec3 uSunDir;       // toward the sun
uniform vec3 uSunColor;
uniform vec3 uSkyTop;
uniform vec3 uSkyHorizon;
uniform vec3 uGroundColor;
uniform float uTime;

layout(location=0) out vec4 oColor;

const float PI = 3.14159265358979;

// -------- BRDF helpers (GGX / Schlick) --------
float D_GGX(float NoH, float a) {
  float a2 = a*a;
  float d = (NoH*NoH)*(a2-1.0)+1.0;
  return a2 / max(PI*d*d, 1e-5);
}
float V_Smith(float NoV, float NoL, float a) {
  float k = (a+1.0); k = (k*k)*0.125;
  float gv = NoV/(NoV*(1.0-k)+k);
  float gl = NoL/(NoL*(1.0-k)+k);
  return gv*gl;
}
vec3 F_Schlick(vec3 F0, float u) {
  return F0 + (1.0 - F0) * pow(1.0 - u, 5.0);
}

// Fake IBL: two-lobe sky.
vec3 sampleEnv(vec3 dir) {
  float t = clamp(dir.y * 0.5 + 0.5, 0.0, 1.0);
  vec3 sky = mix(uSkyHorizon, uSkyTop, smoothstep(0.0, 0.6, t));
  vec3 ground = uGroundColor;
  return mix(ground, sky, smoothstep(-0.05, 0.05, dir.y));
}

void main() {
  vec3 N = normalize(vNormal);
  vec3 V = normalize(uCamPos - vWorldPos);
  vec3 L = normalize(uSunDir);
  vec3 H = normalize(L + V);

  float roughness = clamp(vMat.r, 0.045, 1.0);
  float metallic  = clamp(vMat.g, 0.0, 1.0);
  float emissive  = vMat.b;
  float ao        = vMat.a;

  vec3 albedo = vAlbedo;
  vec3 F0 = mix(vec3(0.04), albedo, metallic);
  vec3 diffuseColor = albedo * (1.0 - metallic);

  float NoL = max(dot(N, L), 0.0);
  float NoV = max(dot(N, V), 1e-4);
  float NoH = max(dot(N, H), 0.0);
  float VoH = max(dot(V, H), 0.0);

  float a = roughness * roughness;
  float D = D_GGX(NoH, a);
  float Vs = V_Smith(NoV, NoL, a);
  vec3  F = F_Schlick(F0, VoH);
  vec3 specular = (D * Vs) * F;
  vec3 kd = (1.0 - F) * (1.0 - metallic);
  vec3 direct = (kd * diffuseColor / PI + specular) * uSunColor * NoL;

  // ---- Approximated IBL: irradiance from sky hemisphere + reflection ----
  vec3 irradiance = mix(uGroundColor, uSkyTop, N.y * 0.5 + 0.5);
  // Reflection is blurred for high roughness via lerp toward sky-average.
  vec3 R = reflect(-V, N);
  vec3 reflColor = sampleEnv(R);
  vec3 envAvg = mix(uSkyHorizon, uSkyTop, 0.5);
  vec3 reflBlurred = mix(reflColor, envAvg, roughness);
  vec3 Fenv = F_Schlick(F0, NoV);
  vec3 indirect = (1.0 - metallic) * diffuseColor * irradiance
                + Fenv * reflBlurred * (1.0 - roughness * 0.7);

  // Soft floor-fakes-AO: lower-half hemisphere occlusion proxy.
  float aoFactor = mix(0.55, 1.0, ao);

  vec3 color = direct + indirect * aoFactor;

  // Emissive (server LEDs / signage).
  if (emissive > 0.0) {
    color += albedo * emissive;
  }

  // Distance-based aerial perspective fog.
  float dist = length(uCamPos - vWorldPos);
  float fog = 1.0 - exp(-dist * 0.0028);
  vec3 fogColor = mix(uSkyHorizon, uSkyTop * 0.9, 0.4);
  color = mix(color, fogColor, fog * 0.55);

  oColor = vec4(color, 1.0);
}`;

// ----------------------------------------------------------------- sky

export const skyVS = /* glsl */`#version 300 es
precision highp float;
out vec3 vDir;
uniform mat4 uInvViewProj;
void main() {
  vec2 p = vec2((gl_VertexID == 1) ? 3.0 : -1.0,
                (gl_VertexID == 2) ? 3.0 : -1.0);
  vec4 wp = uInvViewProj * vec4(p, 1.0, 1.0);
  vDir = wp.xyz / wp.w;
  gl_Position = vec4(p, 1.0, 1.0);
}`;

export const skyFS = /* glsl */`#version 300 es
precision highp float;

in vec3 vDir;
uniform vec3 uCamPos;
uniform vec3 uSunDir;
uniform vec3 uSunColor;
uniform vec3 uSkyTop;
uniform vec3 uSkyHorizon;

layout(location=0) out vec4 oColor;

void main() {
  vec3 d = normalize(vDir - uCamPos);
  float t = clamp(d.y * 0.5 + 0.5, 0.0, 1.0);
  vec3 sky = mix(uSkyHorizon, uSkyTop, smoothstep(0.0, 0.55, t));

  // Sun disk + bloom halo.
  float sd = max(dot(d, normalize(uSunDir)), 0.0);
  float disk = smoothstep(0.9994, 0.9998, sd);
  float halo = pow(sd, 220.0) * 0.6 + pow(sd, 32.0) * 0.08;

  vec3 color = sky + uSunColor * (disk * 6.0 + halo);

  // Subtle horizon haze.
  float horizonGlow = pow(1.0 - abs(d.y), 6.0) * 0.25;
  color += uSunColor * horizonGlow * smoothstep(0.0, 0.3, dot(d, normalize(uSunDir)));

  oColor = vec4(color, 1.0);
  gl_FragDepth = 0.99999;
}`;

// ----------------------------------------------------------------- post

export const postFS = /* glsl */`#version 300 es
precision highp float;

in vec2 vUv;
uniform sampler2D uHDR;
uniform sampler2D uDepth;
uniform float uExposure;
uniform float uTime;
uniform float uRippleT;        // [0..1] water-drop progress, or <0 to disable
uniform float uVignette;
uniform vec2  uResolution;
uniform mat4  uInvProj;
uniform mat4  uInvView;
uniform vec3  uCamPos;

layout(location=0) out vec4 oColor;

// ACES filmic tonemap (Krzysztof Narkowicz fit).
vec3 aces(vec3 x) {
  const float a = 2.51, b = 0.03, c = 2.43, d = 0.59, e = 0.14;
  return clamp((x*(a*x + b)) / (x*(c*x + d) + e), 0.0, 1.0);
}

// Reconstruct world position from depth buffer (sampler depth in [0..1]).
vec3 worldFromDepth(vec2 uv, float depth) {
  vec4 ndc = vec4(uv * 2.0 - 1.0, depth * 2.0 - 1.0, 1.0);
  vec4 view = uInvProj * ndc; view /= view.w;
  vec4 world = uInvView * view;
  return world.xyz;
}

void main() {
  vec3 hdr = texture(uHDR, vUv).rgb;

  // ---- Water-drop depth-scan effect ----
  // A growing spherical wavefront expands from the drop origin, rippling
  // anything it sweeps over. The wavefront is in WORLD distance from a
  // chosen origin, so it crawls believably across geometry.
  if (uRippleT >= 0.0) {
    float depth = texture(uDepth, vUv).r;
    if (depth < 0.999999) {
      vec3 wp = worldFromDepth(vUv, depth);
      // Origin: just above the building roof, slowly descending — the drop.
      float t = uRippleT;
      vec3 origin = vec3(0.0, mix(40.0, 6.0, smoothstep(0.0, 0.3, t)), 0.0);

      float d  = length(wp - origin);
      // Front radius and width.
      float R  = 220.0 * t;
      float w  = 14.0;
      float band = smoothstep(R + w, R, d) * smoothstep(R - 4.0*w, R - w, d);

      // Ripple is a sin wave inside the band, fading with t.
      float ripple = sin((d - R) * 0.45 - uTime * 7.0) * 0.5 + 0.5;
      float fade   = (1.0 - smoothstep(0.5, 1.0, t));

      // Color tint and slight UV warp via simple chromatic shift.
      vec3 scanColor = vec3(0.55, 0.9, 1.4) * band * ripple * fade;

      // Mild aberration at the wavefront.
      vec2 dir = (vUv - 0.5);
      float ab = band * 0.0035 * fade;
      vec3 ca;
      ca.r = texture(uHDR, vUv + dir * ab).r;
      ca.g = hdr.g;
      ca.b = texture(uHDR, vUv - dir * ab).b;
      hdr = mix(hdr, ca, band * fade);

      // Boost intensity a hair on the front so it reads as a light pulse.
      hdr += scanColor * 0.6;
    }
  }

  // Exposure + ACES.
  vec3 mapped = aces(hdr * uExposure);

  // Vignette.
  vec2 q = vUv - 0.5;
  float v = 1.0 - dot(q, q) * uVignette;
  mapped *= v;

  // Tiny film grain to fight banding.
  float n = fract(sin(dot(vUv * uResolution, vec2(12.9898, 78.233)) + uTime * 7.0) * 43758.5453);
  mapped += (n - 0.5) * (1.0/255.0);

  // Encode to sRGB-ish.
  oColor = vec4(pow(mapped, vec3(1.0/2.2)), 1.0);
}`;
