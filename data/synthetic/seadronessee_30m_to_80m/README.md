# SeaDronesSee paired synthetic 80 m object patches

This local dataset contains two paired 640x640 synthetic views for each eligible labelled object from the 25--35 m SeaDronesSee split:

- `geometry_only/`: wider real source footprint reduced to 640x640 with Lanczos resampling.
- `calibrated/`: the same geometry followed by the locked low-level calibration effects.

Both variants use the same `pair_id`, source annotation and video-level split. No new background is generated. Objects whose required 80 m footprint crosses the source frame boundary are recorded in `manifests/excluded_manifest.csv`.

## Counts

- Built paired objects: 1,465
- Excluded source objects: 1,842

```text
category_name  boat  buoy  jetski  swimmer
split                                     
test             37     2       9      227
train           126    49       9      826
val              19     3      16      142
```

## Locked calibrated effects

```json
{
  "extra_psf_sigma_output_px": 0.0,
  "visibility_m": null,
  "airlight_srgb": [
    0.78,
    0.84,
    0.9
  ],
  "peak_electrons": null,
  "read_noise_electrons": 2.0,
  "jpeg_quality": 95
}
```

Calibration configuration SHA-256: `8fe6c319c145bfe6c41b4bd884be5417741d8218678ea8dfc0735cca6b82988f`

Source manifest: `data/splits/seadronessee/seadronessee_25_35m_object_split.csv`

Use only the training split to fit a Procrustes mapping. Keep validation for method selection and test for final evaluation. The geometry-only variant is the zero-shot control; the calibrated variant is the primary synthetic mapping source.

## Interpretation limits

The geometric transformation assumes unchanged camera intrinsics, an approximately planar sea surface and comparable viewing direction. It uses each source image's recorded altitude, so the linear scale is `source_altitude / 80`, rather than treating every source as exactly 30 m.

The eligible set is not a random sample: objects near frame boundaries are more likely to be excluded because their wider 80 m footprint is unavailable. Report the eligibility counts with results and do not present this subset as complete coverage of the source dataset.

The calibrated variant is calibration-assisted and SeaDronesSee-specific. The accepted calibration currently adds only a quality-95 JPEG round trip; held-out validation did not justify additional blur, haze or sensor noise. It is therefore a controlled approximation, not a claim of photorealistic 80 m image formation.
