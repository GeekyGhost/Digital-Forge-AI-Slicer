/** Toolpath preview: thick extrusion lines in world units, coloured by feature, filament or speed. */

import * as THREE from "three";
import { LineSegments2 } from "three/addons/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/addons/lines/LineSegmentsGeometry.js";
import { LineMaterial } from "three/addons/lines/LineMaterial.js";
import { engine } from "./engine";

export interface PreviewInfo {
  fields: Record<string, { offset: number; bytes: number; type: string; width: number }>;
  layers: { index: number; z: number; start: number; end: number }[];
  features: { name: string; color: string }[];
  stats: Record<string, any>;
}

export type ColorMode = "feature" | "filament" | "speed";

export class GcodeView {
  readonly group = new THREE.Group();
  info: PreviewInfo | null = null;
  private segments!: Float32Array;
  private feature!: Uint8Array;
  private tool!: Uint8Array;
  private speed!: Float32Array;
  private travels!: Float32Array;
  private lines: LineSegments2 | null = null;
  private travelLines: THREE.LineSegments | null = null;
  private material: LineMaterial;
  private travelMaterial: THREE.LineBasicMaterial;
  private clipLow = new THREE.Plane(new THREE.Vector3(0, 0, 1), 0);
  private clipHigh = new THREE.Plane(new THREE.Vector3(0, 0, -1), 1000);
  mode: ColorMode = "feature";
  hiddenFeatures = new Set<number>();
  filamentColors: string[] = [];
  speedRange: [number, number] = [0, 1];

  constructor() {
    this.material = new LineMaterial({ vertexColors: true, linewidth: 0.42, worldUnits: true, clipping: true });
    this.material.clippingPlanes = [this.clipLow, this.clipHigh];
    this.travelMaterial = new THREE.LineBasicMaterial({ color: 0x5bb8ff, transparent: true, opacity: 0.35, clippingPlanes: [this.clipLow, this.clipHigh] });
    this.group.name = "toolpaths";
  }

  async load(taskId: string): Promise<PreviewInfo> {
    const [info, buffer] = await Promise.all([
      engine.get<PreviewInfo>(`/api/tasks/${taskId}/files/preview.json`),
      engine.binary(`/api/tasks/${taskId}/files/preview.bin`),
    ]);
    const field = (name: string) => info.fields[name];
    this.segments = new Float32Array(buffer, field("segments").offset, field("segments").bytes / 4);
    this.feature = new Uint8Array(buffer, field("feature").offset, field("feature").bytes);
    this.tool = new Uint8Array(buffer, field("tool").offset, field("tool").bytes);
    this.speed = new Float32Array(buffer, field("speed").offset, field("speed").bytes / 4);
    this.travels = new Float32Array(buffer, field("travels").offset, field("travels").bytes / 4);
    this.info = info;
    this.layerCache = null;
    this.filamentColors = info.stats.colors ?? [];
    // Multi-colour prints are easiest to read in their real filament colours.
    this.mode = this.filamentColors.length > 1 ? "filament" : "feature";
    const sorted = Float32Array.from(this.speed).sort();
    this.speedRange = [sorted[Math.floor(sorted.length * 0.02)] ?? 0, sorted[Math.floor(sorted.length * 0.98)] ?? 1];
    this.build();
    return info;
  }

  private build() {
    this.dispose();
    const geometry = new LineSegmentsGeometry();
    geometry.setPositions(this.segments);
    geometry.setColors(this.colors());
    this.lines = new LineSegments2(geometry, this.material);
    this.lines.frustumCulled = false;
    this.group.add(this.lines);
    const tg = new THREE.BufferGeometry();
    tg.setAttribute("position", new THREE.BufferAttribute(this.travels, 3));
    this.travelLines = new THREE.LineSegments(tg, this.travelMaterial);
    this.travelLines.visible = false;
    this.group.add(this.travelLines);
  }

  private colors(): Float32Array {
    const n = this.feature.length;
    const out = new Float32Array(n * 6);
    const c = new THREE.Color();
    const palette = (this.mode === "feature" ? this.info!.features.map((f) => f.color) : this.filamentColors).map((hex) => new THREE.Color(hex).convertSRGBToLinear());
    const [lo, hi] = this.speedRange;
    const layerOf = this.layerIndex();
    const seg = this.segments;
    const slow = new THREE.Color("#2f6bff").convertSRGBToLinear();
    const mid = new THREE.Color("#3fd17a").convertSRGBToLinear();
    const fast = new THREE.Color("#ff4b3a").convertSRGBToLinear();
    for (let i = 0; i < n; i++) {
      if (this.mode === "speed") {
        const t = Math.min(1, Math.max(0, (this.speed[i] - lo) / Math.max(hi - lo, 1e-6)));
        if (t < 0.5) c.copy(slow).lerp(mid, t * 2);
        else c.copy(mid).lerp(fast, (t - 0.5) * 2);
      } else {
        const index = this.mode === "feature" ? this.feature[i] : this.tool[i];
        c.copy(palette[index] ?? palette[0] ?? new THREE.Color(0x999999));
      }
      // Fake lighting: brightness from line direction, plus alternating layers,
      // so walls read as surfaces instead of a flat silhouette.
      const dx = seg[i * 6 + 3] - seg[i * 6];
      const dy = seg[i * 6 + 4] - seg[i * 6 + 1];
      const len = Math.hypot(dx, dy) || 1;
      // A wall's surface normal is perpendicular to its line direction.
      const along = (dx * 0.6 + dy * 0.8) / len;
      const facing = Math.sqrt(Math.max(0, 1 - along * along));
      const shade = (0.3 + 0.7 * facing) * (layerOf[i] % 2 ? 0.88 : 1);
      c.multiplyScalar(shade);
      out[i * 6] = out[i * 6 + 3] = c.r;
      out[i * 6 + 1] = out[i * 6 + 4] = c.g;
      out[i * 6 + 2] = out[i * 6 + 5] = c.b;
    }
    return out;
  }

  private layerCache: Uint32Array | null = null;
  private layerIndex(): Uint32Array {
    if (this.layerCache && this.layerCache.length === this.feature.length) return this.layerCache;
    const out = new Uint32Array(this.feature.length);
    for (const layer of this.info!.layers) out.fill(layer.index, layer.start, layer.end);
    this.layerCache = out;
    return out;
  }

  recolor() {
    if (!this.lines) return;
    (this.lines.geometry as LineSegmentsGeometry).setColors(this.colors());
  }

  /** Hidden features collapse to zero length so they don't draw at all. */
  applyHidden() {
    if (!this.lines) return;
    const positions = Float32Array.from(this.segments);
    for (let i = 0; i < this.feature.length; i++) {
      if (this.hiddenFeatures.has(this.feature[i])) {
        positions[i * 6 + 3] = positions[i * 6];
        positions[i * 6 + 4] = positions[i * 6 + 1];
        positions[i * 6 + 5] = positions[i * 6 + 2];
      }
    }
    (this.lines.geometry as LineSegmentsGeometry).setPositions(positions);
    this.recolor();
  }

  setLayerRange(low: number, high: number) {
    if (!this.info?.layers.length) return;
    const layers = this.info.layers;
    const zLow = layers[Math.max(0, Math.min(low, layers.length - 1))].z;
    const zHigh = layers[Math.max(0, Math.min(high, layers.length - 1))].z;
    const thickness = layers.length > 1 ? Math.max(0.05, layers[1].z - layers[0].z) : 0.2;
    this.clipLow.constant = -(zLow - thickness + 1e-3);
    this.clipHigh.constant = zHigh + 1e-3;
  }

  showTravels(on: boolean) {
    if (this.travelLines) this.travelLines.visible = on;
  }

  resize(width: number, height: number) {
    this.material.resolution.set(width, height);
  }

  dispose() {
    if (this.lines) {
      this.lines.geometry.dispose();
      this.group.remove(this.lines);
      this.lines = null;
    }
    if (this.travelLines) {
      this.travelLines.geometry.dispose();
      this.group.remove(this.travelLines);
      this.travelLines = null;
    }
  }
}
