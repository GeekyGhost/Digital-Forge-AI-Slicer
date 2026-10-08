/** Z-up millimetre viewport with the Kobra S1 build plate, selection and transform gizmos. */

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { TransformControls } from "three/addons/controls/TransformControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { acceleratedRaycast, computeBoundsTree, disposeBoundsTree } from "three-mesh-bvh";
import { engine } from "./engine";
import { state, type SceneItem } from "./state";

THREE.BufferGeometry.prototype.computeBoundsTree = computeBoundsTree;
THREE.BufferGeometry.prototype.disposeBoundsTree = disposeBoundsTree;
THREE.Mesh.prototype.raycast = acceleratedRaycast;
THREE.Object3D.DEFAULT_UP.set(0, 0, 1);

export const BED = { x: 250, y: 250, z: 250 };

export interface Pick {
  item: SceneItem;
  mesh: THREE.Mesh;
  faceIndex: number;
  point: THREE.Vector3;
  normal: THREE.Vector3; // world space
}

export type PickHandler = (pick: Pick | null, event: PointerEvent, phase: "down" | "move" | "up") => boolean;

export class Viewport {
  readonly renderer: THREE.WebGLRenderer;
  readonly scene = new THREE.Scene();
  readonly camera: THREE.PerspectiveCamera;
  readonly controls: OrbitControls;
  readonly gizmo: TransformControls;
  readonly itemsGroup = new THREE.Group();
  readonly overlay = new THREE.Group();
  private helpers = new Map<string, THREE.Box3Helper>();
  private raycaster = new THREE.Raycaster();
  private dirty = true;
  private down: { x: number; y: number } | null = null;
  pickHandler: PickHandler | null = null;
  gizmoEnabled = true;
  showHelpers = true;
  filamentView = false;
  onTransformEnd: (() => void) | null = null;
  resizeListeners: ((width: number, height: number) => void)[] = [];

  constructor(private container: HTMLElement) {
    this.renderer = new THREE.WebGLRenderer({ antialias: true, logarithmicDepthBuffer: false });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;
    this.renderer.localClippingEnabled = true;
    container.appendChild(this.renderer.domElement);

    this.scene.background = new THREE.Color("#2e343e");
    const pmrem = new THREE.PMREMGenerator(this.renderer);
    this.scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    this.scene.add(new THREE.HemisphereLight(0xdfe6f0, 0x2a2420, 0.7));
    const key = new THREE.DirectionalLight(0xffffff, 1.4);
    key.position.set(-200, -300, 500);
    this.scene.add(key);
    const rim = new THREE.DirectionalLight(0xbfd4ff, 0.5);
    rim.position.set(400, 400, 200);
    this.scene.add(rim);

    this.camera = new THREE.PerspectiveCamera(38, 1, 1, 10000);
    this.camera.up.set(0, 0, 1);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.12;
    this.controls.screenSpacePanning = true;
    this.controls.addEventListener("change", () => this.render());

    this.scene.add(this.buildBed(), this.itemsGroup, this.overlay);

    this.gizmo = new TransformControls(this.camera, this.renderer.domElement);
    this.gizmo.setSize(0.8);
    this.gizmo.addEventListener("dragging-changed", (e) => {
      const dragging = (e as unknown as { value: boolean }).value;
      this.controls.enabled = !dragging;
      if (dragging) this.captureFollowers();
      else this.onTransformEnd?.();
    });
    this.gizmo.addEventListener("objectChange", () => {
      this.moveFollowers();
      this.refreshHelpers();
      state.emit("transform");
    });
    this.gizmo.addEventListener("change", () => this.render());
    this.scene.add(this.gizmo.getHelper());

    this.home();
    new ResizeObserver(() => this.resize()).observe(container);
    this.resize();
    this.bindPointer();
    const loop = () => {
      requestAnimationFrame(loop);
      if (this.controls.update() || this.dirty) {
        this.dirty = false;
        this.renderer.render(this.scene, this.camera);
      }
    };
    loop();
  }

  render() {
    this.dirty = true;
  }

  private resize() {
    const { clientWidth: w, clientHeight: h } = this.container;
    if (!w || !h) return;
    this.renderer.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.resizeListeners.forEach((l) => l(w, h));
    this.render();
  }

  private buildBed(): THREE.Group {
    const bed = new THREE.Group();
    bed.name = "bed";
    const plate = new THREE.Mesh(
      new THREE.PlaneGeometry(BED.x, BED.y),
      new THREE.MeshStandardMaterial({ color: 0x23272e, roughness: 0.92, metalness: 0.1 }),
    );
    plate.position.set(BED.x / 2, BED.y / 2, -0.05);
    bed.add(plate);
    const minor: number[] = [];
    const major: number[] = [];
    for (let v = 0; v <= BED.x; v += 10) {
      const target = v % 50 === 0 ? major : minor;
      target.push(v, 0, 0, v, BED.y, 0, 0, v, 0, BED.x, v, 0);
    }
    const grid = (positions: number[], color: number, opacity: number) => {
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
      return new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color, transparent: true, opacity }));
    };
    bed.add(grid(minor, 0x5a6270, 0.35), grid(major, 0x7a8494, 0.6));
    const volume = new THREE.Box3Helper(new THREE.Box3(new THREE.Vector3(0, 0, 0), new THREE.Vector3(BED.x, BED.y, BED.z)), 0x4a5260);
    (volume.material as THREE.LineBasicMaterial).transparent = true;
    (volume.material as THREE.LineBasicMaterial).opacity = 0.5;
    bed.add(volume);
    const axes = new THREE.AxesHelper(18);
    axes.position.set(0, 0, 0.05);
    bed.add(axes);
    return bed;
  }

  // Camera ------------------------------------------------------------------
  home() {
    this.view("iso");
  }

  view(kind: "iso" | "top" | "front" | "right") {
    const target = new THREE.Vector3(BED.x / 2, BED.y / 2, 30);
    const offsets = {
      iso: new THREE.Vector3(-230, -330, 280),
      top: new THREE.Vector3(0, -0.01, 520),
      front: new THREE.Vector3(0, -480, 40),
      right: new THREE.Vector3(480, 0, 40),
    };
    this.controls.target.copy(target);
    this.camera.position.copy(target).add(offsets[kind]);
    this.camera.updateProjectionMatrix();
    this.render();
  }

  frame(items: SceneItem[]) {
    const box = new THREE.Box3();
    items.forEach((i) => box.expandByObject(i.node));
    if (box.isEmpty()) return this.home();
    const centre = box.getCenter(new THREE.Vector3());
    const radius = box.getSize(new THREE.Vector3()).length() / 2;
    const dir = this.camera.position.clone().sub(this.controls.target).normalize();
    const distance = radius / Math.sin(THREE.MathUtils.degToRad(this.camera.fov / 2)) * 1.15;
    this.controls.target.copy(centre);
    this.camera.position.copy(centre).addScaledVector(dir, distance);
    this.render();
  }

  // Items --------------------------------------------------------------------
  async load(item: SceneItem): Promise<void> {
    const loader = new GLTFLoader();
    const gltf = await loader.loadAsync(engine.resource(`/api/objects/${item.objectId}/display.glb`));
    item.node.clear();
    gltf.scene.traverse((child) => {
      if ((child as THREE.Mesh).isMesh) {
        const mesh = child as THREE.Mesh;
        mesh.geometry.computeBoundsTree();
        mesh.userData.originalMaterial = mesh.material;
        const mat = mesh.material as THREE.MeshStandardMaterial;
        if (mat && "side" in mat) mat.side = THREE.DoubleSide; // open meshes still look solid
      }
    });
    item.node.add(gltf.scene);
    item.node.userData.uid = item.uid;
    item.node.visible = item.visible;
    if (!this.itemsGroup.children.includes(item.node)) this.itemsGroup.add(item.node);
    this.applyMaterial(item);
    this.refreshHelpers();
  }

  remove(item: SceneItem) {
    if (this.gizmo.object === item.node) this.gizmo.detach();
    this.itemsGroup.remove(item.node);
    item.node.traverse((child) => {
      const mesh = child as THREE.Mesh;
      if (mesh.isMesh) {
        mesh.geometry.disposeBoundsTree?.();
        mesh.geometry.dispose();
      }
    });
    this.refreshHelpers();
  }

  setFilamentView(on: boolean) {
    this.filamentView = on;
    state.items.forEach((i) => this.applyMaterial(i));
    this.render();
  }

  applyMaterial(item: SceneItem) {
    const color = new THREE.Color(state.filamentColor(item.filament));
    item.node.traverse((child) => {
      const mesh = child as THREE.Mesh;
      if (!mesh.isMesh || mesh.userData.overlay) return;
      if (this.filamentView) {
        if (!mesh.userData.filamentMaterial) {
          mesh.userData.filamentMaterial = new THREE.MeshStandardMaterial({ roughness: 0.55, metalness: 0.05, side: THREE.DoubleSide });
        }
        (mesh.userData.filamentMaterial as THREE.MeshStandardMaterial).color.copy(color);
        mesh.material = mesh.userData.filamentMaterial;
      } else if (mesh.userData.originalMaterial) {
        mesh.material = mesh.userData.originalMaterial;
      }
    });
    this.render();
  }

  /** Recompute selection outlines and out-of-bed warnings. */
  refreshHelpers() {
    for (const helper of this.helpers.values()) this.overlay.remove(helper);
    this.helpers.clear();
    if (!this.showHelpers) return this.render();
    const selected = new Set(state.selection);
    for (const item of state.items) {
      if (!item.visible) continue;
      const box = new THREE.Box3().setFromObject(item.node);
      const outside = this.outsideBed(box);
      item.node.userData.outOfBed = outside;
      if (selected.has(item.uid) || outside) {
        const helper = new THREE.Box3Helper(box, outside ? 0xe2695a : 0xf0b453);
        this.helpers.set(item.uid, helper);
        this.overlay.add(helper);
      }
    }
    this.render();
  }

  outsideBed(box: THREE.Box3): boolean {
    if (box.isEmpty()) return false;
    const e = 0.01;
    return box.min.x < -e || box.min.y < -e || box.max.x > BED.x + e || box.max.y > BED.y + e || box.max.z > BED.z + e || box.min.z < -0.5;
  }

  // Everything selected (and every part of a selected assembly) moves with the gizmo.
  private followers: { node: THREE.Object3D; start: THREE.Matrix4 }[] = [];
  private leaderStart = new THREE.Matrix4();

  private captureFollowers() {
    const leader = this.gizmo.object;
    if (!leader) return;
    leader.updateMatrix();
    this.leaderStart.copy(leader.matrix);
    const uids = new Set(state.selection);
    for (const item of state.selected) {
      if (item.assembly) state.items.filter((i) => i.assembly === item.assembly).forEach((i) => uids.add(i.uid));
    }
    this.followers = state.items
      .filter((i) => uids.has(i.uid) && i.node !== leader)
      .map((i) => (i.node.updateMatrix(), { node: i.node, start: i.node.matrix.clone() }));
  }

  private moveFollowers() {
    const leader = this.gizmo.object;
    if (!leader || !this.followers.length) return;
    leader.updateMatrix();
    const delta = leader.matrix.clone().multiply(this.leaderStart.clone().invert());
    for (const f of this.followers) {
      f.node.matrix.copy(delta).multiply(f.start);
      f.node.matrix.decompose(f.node.position, f.node.quaternion, f.node.scale);
    }
  }

  attachGizmo(item: SceneItem | undefined, mode?: "translate" | "rotate" | "scale") {
    if (mode) this.gizmo.setMode(mode);
    if (item && this.gizmoEnabled && item.visible) this.gizmo.attach(item.node);
    else this.gizmo.detach();
    this.render();
  }

  // Picking --------------------------------------------------------------------
  private bindPointer() {
    const el = this.renderer.domElement;
    el.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      this.down = { x: e.clientX, y: e.clientY };
      if (this.pickHandler && !this.gizmo.dragging) {
        if (this.pickHandler(this.pick(e), e, "down")) {
          this.controls.enabled = false;
          el.setPointerCapture(e.pointerId);
        }
      }
    });
    el.addEventListener("pointermove", (e) => {
      if (this.pickHandler && !this.controls.enabled && this.down) this.pickHandler(this.pick(e), e, "move");
      else if (this.pickHandler && !this.down) this.pickHandler(this.pick(e), e, "move");
    });
    el.addEventListener("pointerup", (e) => {
      if (e.button !== 0 || !this.down) return;
      const moved = Math.hypot(e.clientX - this.down.x, e.clientY - this.down.y) > 4;
      const wasHandling = !this.controls.enabled && !this.gizmo.dragging;
      this.down = null;
      if (wasHandling && this.pickHandler) {
        this.pickHandler(this.pick(e), e, "up");
        this.controls.enabled = true;
        return;
      }
      this.controls.enabled = true;
      if (moved || this.gizmo.dragging) return;
      if (this.pickHandler && this.pickHandler(this.pick(e), e, "up")) return;
      const hit = this.pick(e);
      if (!hit) {
        if (!e.shiftKey) state.select([]);
        return;
      }
      // Clicking one colour part selects its whole multi-part object (Alt picks a single part).
      const group = hit.item.assembly && !e.altKey ? state.items.filter((i) => i.assembly === hit.item.assembly && i.visible).map((i) => i.uid) : [hit.item.uid];
      if (e.shiftKey || e.ctrlKey) {
        const set = new Set(state.selection);
        const on = !set.has(hit.item.uid);
        group.forEach((u) => (on ? set.add(u) : set.delete(u)));
        state.select([...set]);
      } else {
        state.select(group);
      }
    });
  }

  pick(event: PointerEvent | MouseEvent): Pick | null {
    const rect = this.renderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
    this.raycaster.setFromCamera(ndc, this.camera);
    (this.raycaster as unknown as { firstHitOnly: boolean }).firstHitOnly = true;
    const targets = state.items.filter((i) => i.visible).map((i) => i.node);
    const hits = this.raycaster.intersectObjects(targets, true).filter((h) => !h.object.userData.overlay && visibleDeep(h.object));
    const hit = hits[0];
    if (!hit || hit.faceIndex === undefined || hit.faceIndex === null) return null;
    let node: THREE.Object3D | null = hit.object;
    while (node && !node.userData.uid) node = node.parent;
    const item = node ? state.item(node.userData.uid) : undefined;
    if (!item) return null;
    const normal = hit.face ? hit.face.normal.clone().transformDirection(hit.object.matrixWorld) : new THREE.Vector3(0, 0, 1);
    return { item, mesh: hit.object as THREE.Mesh, faceIndex: hit.faceIndex, point: hit.point.clone(), normal };
  }

  // Placement helpers -----------------------------------------------------------------
  worldBox(item: SceneItem, precise = true): THREE.Box3 {
    item.node.updateMatrixWorld(true);
    return new THREE.Box3().setFromObject(item.node, precise);
  }

  dropToBed(items: SceneItem[]) {
    for (const group of groupByAssembly(items)) {
      const box = new THREE.Box3();
      group.forEach((i) => box.union(this.worldBox(i)));
      if (box.isEmpty()) continue;
      group.forEach((i) => {
        i.node.position.z -= box.min.z;
        i.node.updateMatrix();
      });
    }
    this.refreshHelpers();
    state.emit("transform");
  }

  layFlat(item: SceneItem, worldNormal: THREE.Vector3) {
    const down = new THREE.Vector3(0, 0, -1);
    const q = new THREE.Quaternion().setFromUnitVectors(worldNormal.clone().normalize(), down);
    const group = item.assembly ? state.items.filter((i) => i.assembly === item.assembly) : [item];
    const pivot = this.worldBox(item).getCenter(new THREE.Vector3());
    for (const i of group) {
      i.node.position.sub(pivot).applyQuaternion(q).add(pivot);
      i.node.quaternion.premultiply(q);
      i.node.updateMatrix();
    }
    this.dropToBed(group);
  }

  /** Shelf-pack visible objects (assemblies move as one) onto the bed. */
  arrange(gap = 6): boolean {
    const groups = groupByAssembly(state.items.filter((i) => i.visible));
    const boxes = groups.map((g) => {
      const b = new THREE.Box3();
      g.forEach((i) => b.union(this.worldBox(i, false)));
      return { g, b, size: b.getSize(new THREE.Vector3()) };
    });
    boxes.sort((a, b) => b.size.y - a.size.y || b.size.x - a.size.x);
    let x = gap, y = gap, row = 0, fits = true;
    for (const entry of boxes) {
      if (x + entry.size.x > BED.x - gap && x > gap) {
        x = gap;
        y += row + gap;
        row = 0;
      }
      if (y + entry.size.y > BED.y - gap || x + entry.size.x > BED.x - gap) fits = false;
      const dx = x - entry.b.min.x;
      const dy = y - entry.b.min.y;
      entry.g.forEach((i) => {
        i.node.position.x += dx;
        i.node.position.y += dy;
        i.node.updateMatrix();
      });
      x += entry.size.x + gap;
      row = Math.max(row, entry.size.y);
    }
    // Centre the whole layout on the bed.
    const all = new THREE.Box3();
    boxes.forEach((e) => e.g.forEach((i) => all.union(this.worldBox(i, false))));
    if (!all.isEmpty() && fits) {
      const c = all.getCenter(new THREE.Vector3());
      boxes.forEach((e) => e.g.forEach((i) => {
        i.node.position.x += BED.x / 2 - c.x;
        i.node.position.y += BED.y / 2 - c.y;
        i.node.updateMatrix();
      }));
    }
    this.dropToBed(state.items.filter((i) => i.visible));
    return fits;
  }
}

function visibleDeep(object: THREE.Object3D | null): boolean {
  for (let o = object; o; o = o.parent) if (!o.visible) return false;
  return true;
}

export function groupByAssembly(items: SceneItem[]): SceneItem[][] {
  const groups = new Map<string, SceneItem[]>();
  for (const item of items) {
    const key = item.assembly ?? item.uid;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(item);
  }
  return [...groups.values()];
}

/** Row-major 4x4 for the engine (three.js stores column-major). */
export function rowMajor(matrix: THREE.Matrix4): number[][] {
  const e = matrix.elements;
  return [
    [e[0], e[4], e[8], e[12]],
    [e[1], e[5], e[9], e[13]],
    [e[2], e[6], e[10], e[14]],
    [e[3], e[7], e[11], e[15]],
  ];
}

export let viewport: Viewport;
export function createViewport(container: HTMLElement) {
  viewport = new Viewport(container);
  return viewport;
}
