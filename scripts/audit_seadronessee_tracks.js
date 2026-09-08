const fs = require("fs");
const path = require("path");
const repoRoot = path.resolve(__dirname, "..");

const files = [
  ["train", path.join(repoRoot, "data", "raw", "seadronessee", "annotations", "instances_train_objects_in_water.json")],
  ["val", path.join(repoRoot, "data", "raw", "seadronessee", "annotations", "instances_val_objects_in_water.json")],
];

const bands = [
  { name: "30plusminus5__80plusminus10", low: [25, 35], high: [70, 90] },
  { name: "30plusminus10__80plusminus10", low: [20, 40], high: [70, 90] },
  { name: "low10to30__high70plus", low: [10, 30], high: [70, Infinity] },
];

const result = {};

for (const [split, file] of files) {
  const d = JSON.parse(fs.readFileSync(file, "utf8"));
  const imageById = new Map(d.images.map((im) => [im.id, im]));
  const trackStats = new Map();
  const videoStats = new Map();

  for (const im of d.images) {
    const video = im.source?.video ?? String(im.video_id);
    const alt = im.meta?.altitude;
    if (!Number.isFinite(alt)) continue;
    const v = videoStats.get(video) ?? { min: Infinity, max: -Infinity, frames: 0 };
    v.min = Math.min(v.min, alt);
    v.max = Math.max(v.max, alt);
    v.frames += 1;
    videoStats.set(video, v);
  }

  for (const ann of d.annotations) {
    const im = imageById.get(ann.image_id);
    if (!im) continue;
    const alt = im.meta?.altitude;
    if (!Number.isFinite(alt)) continue;
    const video = im.source?.video ?? String(im.video_id);
    const key = `${video}::${ann.track_id}`;
    const t = trackStats.get(key) ?? {
      video,
      trackId: ann.track_id,
      categoryId: ann.category_id,
      min: Infinity,
      max: -Infinity,
      observations: 0,
      byBand: Object.fromEntries(bands.map((b) => [b.name, { low: 0, high: 0 }])),
    };
    t.min = Math.min(t.min, alt);
    t.max = Math.max(t.max, alt);
    t.observations += 1;
    for (const b of bands) {
      if (alt >= b.low[0] && alt <= b.low[1]) t.byBand[b.name].low += 1;
      if (alt >= b.high[0] && alt <= b.high[1]) t.byBand[b.name].high += 1;
    }
    trackStats.set(key, t);
  }

  const tracks = [...trackStats.values()];
  const imagesWithAlt = d.images.filter((im) => Number.isFinite(im.meta?.altitude));
  const altitudes = imagesWithAlt.map((im) => im.meta.altitude);
  const splitResult = {
    images: d.images.length,
    annotations: d.annotations.length,
    videos: videoStats.size,
    tracks: tracks.length,
    altitude: {
      min: Math.min(...altitudes),
      max: Math.max(...altitudes),
    },
    bands: {},
    tracksSpanningAtLeast40m: tracks.filter((t) => t.max - t.min >= 40).length,
    tracksWithMinAtMost35AndMaxAtLeast70: tracks.filter((t) => t.min <= 35 && t.max >= 70).length,
    videoRanges: [...videoStats.entries()]
      .map(([video, v]) => ({ video, ...v, span: v.max - v.min }))
      .sort((a, b) => b.span - a.span),
  };

  for (const b of bands) {
    const lowImages = imagesWithAlt.filter(
      (im) => im.meta.altitude >= b.low[0] && im.meta.altitude <= b.low[1],
    ).length;
    const highImages = imagesWithAlt.filter(
      (im) => im.meta.altitude >= b.high[0] && im.meta.altitude <= b.high[1],
    ).length;
    const matchedTracks = tracks.filter(
      (t) => t.byBand[b.name].low > 0 && t.byBand[b.name].high > 0,
    );
    splitResult.bands[b.name] = {
      lowImages,
      highImages,
      matchedTracks: matchedTracks.length,
      matchedTrackDetails: matchedTracks.map((t) => ({
        video: t.video,
        trackId: t.trackId,
        categoryId: t.categoryId,
        lowObservations: t.byBand[b.name].low,
        highObservations: t.byBand[b.name].high,
        minAltitude: t.min,
        maxAltitude: t.max,
      })),
    };
  }

  result[split] = splitResult;
}

fs.writeFileSync(path.join(repoRoot, "data", "audits", "seadronessee_track_audit.json"), JSON.stringify(result, null, 2));

for (const [split, r] of Object.entries(result)) {
  console.log(`\n${split.toUpperCase()}`);
  console.log({
    images: r.images,
    annotations: r.annotations,
    videos: r.videos,
    tracks: r.tracks,
    altitude: r.altitude,
    tracksSpanningAtLeast40m: r.tracksSpanningAtLeast40m,
    tracksWithMinAtMost35AndMaxAtLeast70: r.tracksWithMinAtMost35AndMaxAtLeast70,
  });
  for (const [name, b] of Object.entries(r.bands)) {
    console.log(name, {
      lowImages: b.lowImages,
      highImages: b.highImages,
      matchedTracks: b.matchedTracks,
    });
  }
  console.log("widest video altitude spans", r.videoRanges.slice(0, 8));
}

