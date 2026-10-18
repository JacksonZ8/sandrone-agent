import assert from "node:assert/strict";
import test from "node:test";
import {
  REST_POSE,
  deform,
  deformMesh,
  emotionPose,
  gridMesh,
  headWeight,
  layoutFromPatches,
  minTriangleArea,
  type RigPose,
} from "../src/character/rig.ts";

// 与 data/character/portrait/manifest.json 同尺寸的布局
const layout = layoutFromPatches(
  1254,
  { x: 424, y: 485, w: 413, h: 178 },
  { x: 560, y: 616, w: 135, h: 105 },
);
const pose = (change: Partial<RigPose>): RigPose => ({ ...REST_POSE, ...change });
const EXTREMES: Partial<RigPose>[] = [
  { yaw: 1 },
  { yaw: -1 },
  { pitch: 1 },
  { pitch: -1 },
  { roll: 0.12 },
  { roll: -0.12 },
  { breath: 1, hair: 8, bob: -12 },
  { yaw: 1, pitch: -1, roll: 0.1, breath: -1, hair: -8, bob: 4, lean: 0.006 },
];

test("rest pose leaves every point where it was", () => {
  for (const [x, y] of [
    [0, 0],
    [630, 574],
    [200, 900],
    [1254, 1254],
  ]) {
    assert.deepEqual(deform(x, y, REST_POSE, layout), [x, y]);
  }
});

test("the face is inside the head and the lower body is not", () => {
  assert.equal(headWeight(630, 574, layout), 1);
  assert.ok(headWeight(630, 700, layout) > 0.8, "chin moves with the head");
  assert.equal(headWeight(630, 1100, layout), 0);
  assert.equal(headWeight(60, 1200, layout), 0);
});

test("turning moves the face centre more than the head edge, and the body not at all", () => {
  const turned = pose({ yaw: 1 });
  const centre = deform(630, 574, turned, layout)[0] - 630;
  const edge = deform(630 + 330, 574, turned, layout)[0] - (630 + 330);
  const body = deform(630, 1150, turned, layout)[0] - 630;
  assert.ok(centre > 20 && centre < 40, `face shift ${centre}`);
  assert.ok(edge < centre * 0.7, `edge ${edge} vs centre ${centre}`);
  assert.equal(body, 0);
});

test("the bottom edge of the picture never moves", () => {
  for (const change of EXTREMES) {
    for (const x of [0, 300, 627, 1000, 1254]) {
      const [, y] = deform(x, 1254, pose(change), layout);
      assert.ok(Math.abs(y - 1254) < 1e-6, `bottom moved to ${y} for ${JSON.stringify(change)}`);
    }
  }
});

test("the mesh never folds over, even at the extremes", () => {
  const mesh = gridMesh({ x: 0, y: 0, w: 1254, h: 1254 }, 40, 40);
  const out = new Float32Array(mesh.source.length);
  assert.ok(minTriangleArea(mesh.source, mesh.indices) > 0);
  for (const change of EXTREMES) {
    deformMesh(mesh, pose(change), layout, out);
    const area = minTriangleArea(out, mesh.indices);
    assert.ok(area > 0, `folded (${area}) for ${JSON.stringify(change)}`);
  }
});

test("a mouth patch follows the head and opens around its own centre", () => {
  const mouth = gridMesh({ x: 560, y: 616, w: 135, h: 105 }, 4, 4);
  const out = new Float32Array(mouth.source.length);
  deformMesh(mouth, REST_POSE, layout, out, 0.5, 668);
  // 闭合一半：上沿下移、下沿上移，中心不动
  assert.ok(Math.abs(out[1] - (668 - 26)) < 1);
  assert.ok(Math.abs(out[out.length - 1] - (668 + 26)) < 1);
  deformMesh(mouth, pose({ yaw: 1 }), layout, out);
  const face = deform(627, 668, pose({ yaw: 1 }), layout)[0] - 627;
  assert.ok(Math.abs(out[0] - 560 - face) < 6, "mouth moves with the face");
});

test("emotion motions settle and unknown emotions add nothing", () => {
  assert.deepEqual(emotionPose("平静", 0, 0), {});
  assert.deepEqual(emotionPose("专注", 3, 3), {});
  const angry = emotionPose("生气", 0.03, 0).yaw ?? 0;
  const angryLater = emotionPose("生气", 3, 0).yaw ?? 0;
  assert.ok(Math.abs(angryLater) < Math.abs(angry) * 0.01 + 1e-3);
  const happy = emotionPose("开心", 3, 0).bob ?? 0;
  assert.ok(Math.abs(happy) < 0.01);
});
