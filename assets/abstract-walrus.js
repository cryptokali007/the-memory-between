const TAU = Math.PI * 2;
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

function seeded(seed) {
  let s = seed >>> 0;
  return () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

function makeTaperedTube(THREE, points, radii, material, segments = 64, radial = 12, name = 'Tapered filament') {
  const curve = new THREE.CatmullRomCurve3(points.map((p) => new THREE.Vector3(...p)), false, 'centripetal');
  const frames = curve.computeFrenetFrames(segments, false);
  const positions = [];
  const uvs = [];
  const indices = [];
  const controls = radii.length > 1 ? radii : [radii[0], radii[0]];
  const radiusAt = (u) => {
    const p = u * (controls.length - 1);
    const i = Math.min(controls.length - 2, Math.floor(p));
    const f0 = p - i;
    const f = f0 * f0 * (3 - 2 * f0);
    return Math.max(0.004, controls[i] + (controls[i + 1] - controls[i]) * f);
  };

  for (let i = 0; i <= segments; i++) {
    const u = i / segments;
    const c = curve.getPointAt(u);
    const r = radiusAt(u);
    const n = frames.normals[i];
    const b = frames.binormals[i];
    for (let j = 0; j <= radial; j++) {
      const v = j / radial;
      const a = v * TAU;
      const ca = Math.cos(a);
      const sa = Math.sin(a);
      positions.push(
        c.x + r * (n.x * ca + b.x * sa),
        c.y + r * (n.y * ca + b.y * sa),
        c.z + r * (n.z * ca + b.z * sa)
      );
      uvs.push(v, u);
    }
  }
  for (let i = 0; i < segments; i++) {
    for (let j = 0; j < radial; j++) {
      const a = (radial + 1) * i + j;
      const b = (radial + 1) * (i + 1) + j;
      const c = (radial + 1) * i + j + 1;
      const d = (radial + 1) * (i + 1) + j + 1;
      indices.push(a, b, c, b, d, c);
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
  geometry.setIndex(indices);
  geometry.computeVertexNormals();
  const mesh = new THREE.Mesh(geometry, material);
  mesh.name = name;
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  return mesh;
}

function shellPanelGeometry(THREE, phi0, phi1, radius, latSegments = 34, phiSegments = 18) {
  const positions = [];
  const uvs = [];
  const indices = [];
  const lat0 = -1.22;
  const lat1 = 1.22;
  for (let i = 0; i <= latSegments; i++) {
    const v = i / latSegments;
    const lat = lat0 + (lat1 - lat0) * v;
    for (let j = 0; j <= phiSegments; j++) {
      const u = j / phiSegments;
      const phi = phi0 + (phi1 - phi0) * u;
      const r = radius + 0.065 * Math.sin(Math.PI * u) * Math.sin(Math.PI * v);
      positions.push(
        r * Math.cos(lat) * Math.cos(phi),
        r * Math.sin(lat) * 0.92,
        r * Math.cos(lat) * Math.sin(phi)
      );
      uvs.push(u, v);
    }
  }
  for (let i = 0; i < latSegments; i++) {
    for (let j = 0; j < phiSegments; j++) {
      const a = i * (phiSegments + 1) + j;
      const b = (i + 1) * (phiSegments + 1) + j;
      const c = i * (phiSegments + 1) + j + 1;
      const d = (i + 1) * (phiSegments + 1) + j + 1;
      indices.push(a, b, c, b, d, c);
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
  geometry.setIndex(indices);
  geometry.computeVertexNormals();
  return geometry;
}

function curveThrough(THREE, points) {
  return new THREE.CatmullRomCurve3(points.map((p) => new THREE.Vector3(...p)), false, 'centripetal');
}

function physical(THREE, color, opts = {}) {
  return new THREE.MeshPhysicalMaterial({
    color,
    roughness: opts.roughness ?? 0.3,
    metalness: opts.metalness ?? 0.25,
    clearcoat: opts.clearcoat ?? 0.5,
    clearcoatRoughness: opts.clearcoatRoughness ?? 0.2,
    emissive: opts.emissive ?? 0x000000,
    emissiveIntensity: opts.emissiveIntensity ?? 0,
    transparent: !!opts.transparent,
    opacity: opts.opacity ?? 1,
    depthWrite: opts.depthWrite ?? true,
    side: opts.side ?? THREE.FrontSide
  });
}

function readBand(audio, name, time) {
  if (!audio) return 0;
  const direct = audio[name];
  if (Number.isFinite(direct)) return clamp(direct, 0, 1);
  const values = audio.envelopes && audio.envelopes[name];
  if (!values || typeof values.length !== 'number' || values.length < 1) return 0;
  const fpsValue = Number(audio.frame_rate ?? audio.frameRate ?? 50);
  const fps = Number.isFinite(fpsValue) && fpsValue > 0 ? fpsValue : 50;
  const x = clamp(time * fps, 0, values.length - 1);
  const i = Math.floor(x);
  const f = x - i;
  const a = Number(values[i]) || 0;
  const b = Number(values[Math.min(i + 1, values.length - 1)]) || 0;
  return clamp(a + (b - a) * f, 0, 1);
}

export function createAbstractWalrus(THREE) {
  if (!THREE || !THREE.MeshPhysicalMaterial || !THREE.BufferGeometry) {
    throw new TypeError('createAbstractWalrus requires the Three.js namespace.');
  }

  const root = new THREE.Group();
  root.name = 'Walrus Abstract Guardian';
  root.userData.kind = 'procedural-sculpture';
  root.scale.z = 0.88;
  const resources = new Set();
  const sphere = new THREE.SphereGeometry(1, 64, 48);
  resources.add(sphere);

  const deep = physical(THREE, 0x07111d, {
    roughness: 0.36,
    metalness: 0.34,
    clearcoat: 0.48,
    emissive: 0x06191e,
    emissiveIntensity: 0.08
  });
  const coreMat = physical(THREE, 0x0b5354, {
    roughness: 0.31,
    metalness: 0.25,
    clearcoat: 0.42,
    clearcoatRoughness: 0.18,
    emissive: 0x126a67,
    emissiveIntensity: 0.28
  });
  const panelMats = [
    physical(THREE, 0x6dbab3, { roughness: 0.27, metalness: 0.42, clearcoat: 0.78 }),
    physical(THREE, 0x8fc8c0, { roughness: 0.3, metalness: 0.38, clearcoat: 0.72 }),
    physical(THREE, 0x4d9995, { roughness: 0.31, metalness: 0.48, clearcoat: 0.66 }),
    physical(THREE, 0xb7c4b7, { roughness: 0.34, metalness: 0.25, clearcoat: 0.58 }),
    physical(THREE, 0x5caea7, { roughness: 0.28, metalness: 0.44, clearcoat: 0.72 }),
    physical(THREE, 0x79bcb5, { roughness: 0.3, metalness: 0.37, clearcoat: 0.74 }),
    physical(THREE, 0x609b98, { roughness: 0.33, metalness: 0.42, clearcoat: 0.62 }),
    physical(THREE, 0x80bdb7, { roughness: 0.29, metalness: 0.4, clearcoat: 0.76 })
  ];
  const copper = physical(THREE, 0xa97046, { roughness: 0.27, metalness: 0.68, clearcoat: 0.66 });
  const brightCopper = physical(THREE, 0xc18a5e, { roughness: 0.24, metalness: 0.62, clearcoat: 0.7 });
  const silver = physical(THREE, 0xc3d2cc, { roughness: 0.3, metalness: 0.5, clearcoat: 0.62 });
  const ivory = physical(THREE, 0xe2d2b5, {
    roughness: 0.25,
    metalness: 0.03,
    clearcoat: 0.48,
    clearcoatRoughness: 0.16,
    emissive: 0x17130a,
    emissiveIntensity: 0.03
  });
  const teal = physical(THREE, 0x48d5cc, { roughness: 0.26, metalness: 0.4, clearcoat: 0.7 });
  const glintMat = physical(THREE, 0xa97046, {
    roughness: 0.22,
    metalness: 0.3,
    clearcoat: 0.48,
    emissive: 0x79502f,
    emissiveIntensity: 0.13
  });
  const nucleusMat = physical(THREE, 0x35c9bf, {
    roughness: 0.24,
    metalness: 0.22,
    clearcoat: 0.52,
    emissive: 0x0e7772,
    emissiveIntensity: 0.19
  });
  for (const m of [deep, coreMat, ...panelMats, copper, brightCopper, silver, ivory, teal, glintMat, nucleusMat]) {
    resources.add(m);
  }

  const backing = new THREE.Mesh(sphere, deep);
  backing.name = 'Deep teal shadow shell';
  backing.scale.set(2.88, 2.65, 2.88);
  backing.castShadow = true;
  backing.receiveShadow = true;
  root.add(backing);

  const core = new THREE.Mesh(sphere, coreMat);
  core.name = 'Luminous deep-teal inner core';
  core.scale.set(2.42, 2.24, 2.42);
  core.castShadow = true;
  core.receiveShadow = true;
  root.add(core);

  const shellRadius = 3.08;
  const step = TAU / 8;
  const panelWidth = step * 0.79;
  const panels = [];
  for (let i = 0; i < 8; i++) {
    const center = i * step + Math.PI / 8;
    const geometry = shellPanelGeometry(THREE, center - panelWidth * 0.5, center + panelWidth * 0.5, shellRadius);
    resources.add(geometry);
    const pivot = new THREE.Group();
    pivot.name = 'Sculpted shell sector ' + (i + 1);
    const mesh = new THREE.Mesh(geometry, panelMats[i]);
    mesh.name = 'Pearlescent teal shell';
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    pivot.add(mesh);
    root.add(pivot);
    panels.push({ pivot, phase: i * 0.79 });
  }

  const seams = new THREE.Group();
  seams.name = 'Copper meridian inlays';
  root.add(seams);
  for (let i = 0; i < 8; i += 2) {
    const phi = i * step;
    const points = [];
    for (let j = 0; j <= 12; j++) {
      const lat = -1.2 + 2.4 * j / 12;
      const r = shellRadius + 0.035;
      points.push([
        r * Math.cos(lat) * Math.cos(phi),
        r * Math.sin(lat) * 0.92,
        r * Math.cos(lat) * Math.sin(phi)
      ]);
    }
    const rail = makeTaperedTube(THREE, points, [0.018, 0.027, 0.018], copper, 44, 8, 'Copper shell seam');
    seams.add(rail);
    resources.add(rail.geometry);
  }

  const latitudeSeams = new THREE.Group();
  latitudeSeams.name = 'Fine curved shell bands';
  root.add(latitudeSeams);
  for (const lat of [-0.72, 0.72]) {
    const points = [];
    for (let j = 0; j <= 72; j++) {
      const phi = TAU * j / 72;
      const r = shellRadius + 0.018;
      points.push([
        r * Math.cos(lat) * Math.cos(phi),
        r * Math.sin(lat) * 0.92,
        r * Math.cos(lat) * Math.sin(phi)
      ]);
    }
    const curve = curveThrough(THREE, points);
    const geometry = new THREE.TubeGeometry(curve, 128, 0.016, 8, true);
    resources.add(geometry);
    const mesh = new THREE.Mesh(geometry, brightCopper);
    mesh.name = 'Copper latitude inlay';
    mesh.castShadow = true;
    latitudeSeams.add(mesh);
  }

  const gimbal = new THREE.Group();
  gimbal.name = 'Exposed copper inner architecture';
  root.add(gimbal);
  const hoopGeometry = new THREE.TorusGeometry(1.53, 0.032, 10, 128);
  resources.add(hoopGeometry);
  const hoopA = new THREE.Mesh(hoopGeometry, copper);
  hoopA.rotation.x = Math.PI / 2;
  hoopA.name = 'Gimbal hoop A';
  gimbal.add(hoopA);
  const hoopB = new THREE.Mesh(hoopGeometry, brightCopper);
  hoopB.rotation.y = Math.PI / 2;
  hoopB.name = 'Gimbal hoop B';
  gimbal.add(hoopB);
  const hoopC = new THREE.Mesh(hoopGeometry, copper);
  hoopC.rotation.z = Math.PI / 5;
  hoopC.name = 'Gimbal hoop C';
  gimbal.add(hoopC);

  const nucleus = new THREE.Mesh(sphere, nucleusMat);
  nucleus.name = 'Central teal nucleus';
  nucleus.scale.setScalar(0.47);
  gimbal.add(nucleus);

  for (let i = 0; i < 12; i++) {
    const a = i * TAU / 12;
    const y = Math.sin(a) * 0.65;
    const pair = [
      [Math.cos(a) * 1.62, y * 1.62, Math.sin(a) * 1.62],
      [Math.cos(a) * 2.62, y * 2.62, Math.sin(a) * 2.62]
    ];
    const strut = makeTaperedTube(THREE, pair, [0.038, 0.022], i % 3 === 0 ? brightCopper : copper, 20, 7, 'Core radial strut');
    gimbal.add(strut);
    resources.add(strut.geometry);
  }

  const tusks = [];
  for (const sign of [-1, 1]) {
    const pivot = new THREE.Group();
    pivot.name = sign < 0 ? 'Left ivory tusk' : 'Right ivory tusk';
    pivot.position.set(sign * 0.82, -1.82, 2.0);
    const geometryMesh = makeTaperedTube(
      THREE,
      [
        [0, 0, 0],
        [-sign * 0.04, -0.63, 0.07],
        [-sign * 0.11, -1.55, 0.12],
        [-sign * 0.23, -2.58, 0.06],
        [-sign * 0.3, -3.58, -0.12]
      ],
      [0.3, 0.28, 0.22, 0.14, 0.018],
      ivory,
      88,
      18,
      'Curved ivory tusk'
    );
    pivot.add(geometryMesh);
    root.add(pivot);
    tusks.push({ pivot, sign, phase: sign * 0.8 });
    resources.add(geometryMesh.geometry);
  }

  const whiskers = [];
  const lengths = [3.55, 4.2, 4.85, 5.35, 5.0, 4.55, 3.85];
  const filamentMats = [copper, silver, brightCopper, teal, copper, silver, brightCopper];
  const tips = [];
  for (const sign of [-1, 1]) {
    for (let i = 0; i < 7; i++) {
      const row = i - 3;
      const x = sign * 2.35;
      const y = row * 0.3;
      const z = Math.sqrt(Math.max(0.2, 3.02 * 3.02 - x * x - (y / 0.92) ** 2)) - 0.02;
      const pivot = new THREE.Group();
      pivot.name = (sign < 0 ? 'Left' : 'Right') + ' swept whisker ' + (i + 1);
      pivot.position.set(x, y, z);
      const length = lengths[i] * (sign < 0 ? 0.97 : 1);
      const fan = row * 0.27 + 0.045 * Math.sin(i * 1.73 + (sign < 0 ? 1 : 0));
      const points = [
        [0, 0, 0],
        [sign * length * 0.2, fan * 0.12, 0.15],
        [sign * length * 0.48, fan * 0.42, 0.08],
        [sign * length * 0.76, fan * 0.76, -0.18],
        [sign * length, fan, -0.48]
      ];
      const filament = makeTaperedTube(THREE, points, [0.095, 0.083, 0.058, 0.033, 0.009], filamentMats[i], 64, 10, 'Solid tapered whisker');
      pivot.add(filament);
      root.add(pivot);
      whiskers.push({ pivot, sign, row, phase: i * 0.63 + (sign < 0 ? 0.7 : 0) });
      resources.add(filament.geometry);

      const socket = new THREE.Mesh(sphere, i % 2 ? brightCopper : copper);
      socket.name = 'Whisker root socket';
      socket.position.set(0, 0, -0.015);
      socket.scale.set(0.12, 0.115, 0.095);
      pivot.add(socket);

      if (i === 1 || i === 5) {
        const tip = new THREE.Mesh(sphere, nucleusMat);
        tip.name = 'Selective teal filament glint';
        const end = points[points.length - 1];
        tip.position.set(end[0], end[1], end[2]);
        tip.scale.setScalar(0.065);
        pivot.add(tip);
        tips.push({ phase: i * 0.8 + sign, base: 0.19 });
      }
    }
  }

  const detailGeo = new THREE.SphereGeometry(0.047, 12, 10);
  resources.add(detailGeo);
  const detail = new THREE.InstancedMesh(detailGeo, brightCopper, 18);
  detail.name = 'Restrained copper seam studs';
  const dummy = new THREE.Object3D();
  let stud = 0;
  for (let i = 0; i < 6; i++) {
    const phi = (i + 0.35) * TAU / 6;
    for (const lat of [-0.38, 0.38, 0]) {
      const r = shellRadius + 0.052;
      dummy.position.set(
        r * Math.cos(lat) * Math.cos(phi),
        r * Math.sin(lat) * 0.92,
        r * Math.cos(lat) * Math.sin(phi)
      );
      dummy.scale.setScalar(lat === 0 ? 0.78 : 0.62);
      dummy.updateMatrix();
      detail.setMatrixAt(stud++, dummy.matrix);
    }
  }
  detail.instanceMatrix.needsUpdate = true;
  root.add(detail);

  const glintCount = 26;
  const glintGeometry = new THREE.IcosahedronGeometry(0.055, 0);
  resources.add(glintGeometry);
  const glints = new THREE.InstancedMesh(glintGeometry, glintMat, glintCount);
  glints.name = 'Fine moving shell glints';
  root.add(glints);
  const rand = seeded(0x5a17f00d);
  const glintPhase = [];
  for (let i = 0; i < glintCount; i++) glintPhase.push([rand() * TAU, rand() * TAU, 0.018 + rand() * 0.027]);

  const state = { time: 0, low: 0, mid: 0, high: 0, rms: 0, objectCount: 0, motionSignature: 0 };

  function seek(timeSeconds, audioLevels = null) {
    const t = Number.isFinite(timeSeconds) ? Math.max(0, timeSeconds) : 0;
    const low = readBand(audioLevels, 'low', t);
    const mid = readBand(audioLevels, 'mid', t);
    const high = readBand(audioLevels, 'high', t);
    const rms = readBand(audioLevels, 'rms', t);

    root.rotation.set(
      0.018 * Math.sin(t * 0.19) + 0.006 * Math.sin(t * 0.43),
      0.055 * Math.sin(t * 0.13) + 0.008 * Math.sin(t * 0.23),
      0.016 * Math.sin(t * 0.17 + 0.4)
    );
    const pulse = 1 + 0.009 * Math.sin(t * 0.72) + 0.012 * low + 0.006 * rms;
    core.scale.set(2.42 * pulse, 2.24 * pulse, 2.42 * pulse);
    coreMat.emissiveIntensity = 0.24 + 0.085 * low + 0.035 * rms;
    nucleus.scale.setScalar(0.47 * (1 + 0.012 * Math.sin(t * 0.86 + 0.3) + 0.016 * low));

    gimbal.rotation.set(
      0.04 * Math.sin(t * 0.24) + 0.025 * mid,
      t * 0.14 + 0.035 * Math.sin(t * 0.29 + 0.6),
      0.025 * Math.sin(t * 0.31 + 1.1)
    );

    panels.forEach(({ pivot, phase }) => {
      const p = 1 + 0.0025 * Math.sin(t * 0.32 + phase) + 0.002 * low;
      pivot.scale.set(p, p, p);
      pivot.rotation.y = 0.0022 * Math.sin(t * 0.28 + phase);
      pivot.rotation.x = 0.0018 * Math.sin(t * 0.23 + phase * 1.2);
    });

    whiskers.forEach(({ pivot, sign, row, phase }) => {
      const wave = Math.sin(t * (0.52 + Math.abs(row) * 0.026) + phase);
      const secondary = Math.sin(t * 0.81 + phase * 1.7);
      const amount = 0.014 + 0.027 * mid + 0.006 * rms;
      pivot.rotation.z = sign * (0.012 * row + amount * wave);
      pivot.rotation.y = sign * (0.009 * secondary + 0.012 * high * Math.sin(t * 1.1 + phase));
      pivot.rotation.x = 0.008 * Math.sin(t * 0.39 + phase) + 0.006 * high;
    });

    tusks.forEach(({ pivot, sign, phase }) => {
      pivot.rotation.z = sign * (0.004 * Math.sin(t * 0.31 + phase) + 0.003 * mid);
      pivot.rotation.x = 0.003 * Math.sin(t * 0.27 + phase);
    });

    seams.rotation.y = 0.004 * Math.sin(t * 0.2);
    latitudeSeams.rotation.y = -0.003 * Math.sin(t * 0.17 + 0.5);
    glintMat.emissiveIntensity = 0.1 + 0.16 * high + 0.035 * Math.sin(t * 1.7);
    nucleusMat.emissiveIntensity = 0.16 + 0.07 * high + 0.025 * Math.sin(t * 1.3);

    const matrix = new THREE.Object3D();
    for (let i = 0; i < glintCount; i++) {
      const [a0, b0, size] = glintPhase[i];
      const a = a0 + t * (0.07 + (i % 4) * 0.012);
      const b = b0 + 0.12 * Math.sin(t * 0.22 + i * 0.4);
      const r = 3.15 + 0.025 * Math.sin(t * 0.49 + i * 1.1);
      const y = Math.sin(b) * r * 0.91;
      const ring = r * Math.cos(b);
      matrix.position.set(Math.cos(a) * ring, y, Math.sin(a) * ring);
      const twinkle = 0.58 + 0.42 * Math.sin(t * 2.1 + i * 1.73);
      const sizeNow = size * (0.78 + 0.22 * twinkle + 0.25 * high);
      matrix.scale.setScalar(sizeNow / 0.055);
      matrix.rotation.set(a * 0.5, b, t * 0.16 + i);
      matrix.updateMatrix();
      glints.setMatrixAt(i, matrix.matrix);
    }
    glints.instanceMatrix.needsUpdate = true;

    state.time = t;
    state.low = low;
    state.mid = mid;
    state.high = high;
    state.rms = rms;
    state.motionSignature =
      root.rotation.y + pulse + gimbal.rotation.y + whiskers[3].pivot.rotation.z + tusks[1].pivot.rotation.z;
    return root;
  }

  root.traverse((object) => {
    if (object.geometry) resources.add(object.geometry);
    const list = Array.isArray(object.material) ? object.material : [object.material];
    for (const material of list) {
      if (!material) continue;
      resources.add(material);
      for (const value of Object.values(material)) {
        if (value && value.isTexture) resources.add(value);
      }
    }
  });
  root.traverse(() => { state.objectCount += 1; });

  function getState() {
    return {
      time: state.time,
      low: state.low,
      mid: state.mid,
      high: state.high,
      rms: state.rms,
      objectCount: state.objectCount,
      motionSignature: state.motionSignature,
      rootRotation: [root.rotation.x, root.rotation.y, root.rotation.z],
      rootScale: [root.scale.x, root.scale.y, root.scale.z],
      coreScale: [core.scale.x, core.scale.y, core.scale.z],
      gimbalRotation: [gimbal.rotation.x, gimbal.rotation.y, gimbal.rotation.z],
      whiskerMotion: whiskers.map(({ pivot }) => [pivot.rotation.x, pivot.rotation.y, pivot.rotation.z])
    };
  }

  function dispose() {
    if (root.parent) root.parent.remove(root);
    for (const resource of resources) {
      if (resource && typeof resource.dispose === 'function') resource.dispose();
    }
    resources.clear();
  }

  seek(0);
  return { root, seek, getState, dispose };
}