# UAV Altitude Feature Alignment

Research code and study materials for investigating how changes in UAV flight altitude affect the internal representations of frozen vision foundation models.

The primary experiment tests whether a single orthogonal transformation can map high-altitude features into a low-altitude reference space well enough for a linear probe trained only at the lower altitude to transfer successfully.

> **Research status:** Protocol and implementation scaffold. No accuracy, successful-flight, or deployment result is claimed yet.

## Research question

Can an orthogonal transformation estimated from matched real low/high-altitude calibration objects improve an unchanged low-altitude linear probe on previously unseen real high-altitude objects?

The direct evaluation pipeline is:

```text
Low-altitude training:
Labelled images -> Frozen encoder -> Low-altitude features -> Train probe -> Fixed probe

Calibration:
Matched low/high images -> Frozen encoder -> Paired features -> Fit Procrustes matrix R

High-altitude testing:
High-altitude image -> Frozen encoder -> Raw feature -> Apply R -> Corrected feature -> Fixed probe -> Prediction
```

The encoder and linear probe remain unchanged during high-altitude testing. This isolates the effect of the feature-space correction.

## Study structure

The research is organised into three connected stages:

1. **Primary feature-correction study:** Fit an orthogonal mapping using matched real low/high-altitude calibration pairs and evaluate it on held-out physical objects.
2. **Synthetic-altitude extension:** Transform lower-altitude imagery to approximate the higher altitude and determine whether it reproduces a transferable part of the real feature shift.
3. **Practical adaptation experiment:** If a fixed feature correction is insufficient, train or fine-tune a classifier or detector using synthetic high-altitude imagery and test it on independent real imagery.

Synthetic-data training is a separate adaptation question. It may improve practical performance without proving that the original feature displacement is orthogonal.

## Seven experimental arms

All arms use the same held-out physical objects and the same fixed low-altitude probe.

| Arm | Method | Purpose |
|---|---|---|
| A | Real low-altitude features | Source-domain reference |
| B | Uncorrected real high-altitude features | Cross-altitude baseline |
| C | Strict orthogonal Procrustes | Direct test of the primary hypothesis |
| D | Centred Procrustes | Tests whether rotation plus a mean translation is required |
| E | Mean shift only | Tests whether mean alignment explains the improvement |
| F | Regularised unconstrained linear mapping | Tests whether orthogonality is too restrictive |
| G | Procrustes with shuffled calibration pairs | Negative control for exact object matching |

The primary comparison is **Arm C versus Arm B**. A low calibration error alone is not sufficient; the correction must improve the prespecified task metric on held-out real high-altitude objects.

## Current flight configuration

The analysis code uses configurable altitude values:

```python
LOW_ALTITUDE_M = 10.0
HIGH_ALTITUDE_M = 25.0
```

These values support the current DJI Tello pilot. The same analysis can later be configured for the original 30/80 m experiment when a suitable aircraft is available. The Tello's published specifications list a maximum flight height of 30 m, 5 MP still images at 2592 x 1936 pixels, and an 82.6-degree field of view.

The notebook calculates geometric scale and approximate ground sample distance from the selected altitudes. These calculations assume a nadir camera, approximately flat ground, fixed optics, and consistent image resolution; measurements from real matched images are still required.

## Repository contents

```text
uav-altitude-feature-alignment/
|-- 3a/
|   |-- 3a.ipynb                 # Main research and experiment notebook
|   `-- uav_3a_protocol.pdf      # Detailed Question 3a protocol
|-- data/
|   |-- README.md                # Data layout, provenance, and storage rules
|   |-- manifest.csv             # Master observation manifest used by the notebook
|   |-- raw/<dataset>/           # Original downloaded or captured data (Git-ignored)
|   |-- patches_fixed_canvas/    # Reproducible scale-preserving object patches
|   |-- features/                # Saved frozen-encoder features
|   |-- manifests/               # Dataset-specific observation tables
|   |-- splits/                  # Fixed experiment partitions
|   `-- audits/                  # Compact dataset audit results
|-- scripts/
|   `-- audit_seadronessee_tracks.js
|-- results/
|   `-- q3a/                     # Evaluation tables and figures
|-- uav_30m_to_80m_high_level_study_draft.pdf
`-- README.md
```

Raw imagery, downloaded annotations, generated patches, and feature arrays are excluded by `.gitignore`. See [`data/README.md`](data/README.md) for the dataset layout, provenance requirements, and SeaDronesSee audit details.

## Notebook capabilities

[`3a/3a.ipynb`](3a/3a.ipynb) contains code for:

- reproducible project configuration;
- manifest creation and validation;
- physical-object-level dataset splitting;
- fixed native-pixel canvas extraction;
- frozen DINOv3 feature extraction with a DINOv2 fallback;
- strict and centred Orthogonal Procrustes;
- all seven experimental arms;
- activation-contrast and PCA diagnostics for altitude-related feature shifts;
- fixed linear-probe training;
- balanced accuracy, macro-F1, and alignment metrics;
- object-level paired bootstrap confidence intervals; and
- parameterised field-of-view and GSD calculations.

## Installation

Create and activate a Python environment from the repository root:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install numpy pandas matplotlib pillow scikit-learn jupyter torch "transformers>=4.56" accelerate
```

PyTorch installation can depend on the available CPU or CUDA hardware. Use the installation command recommended by [PyTorch](https://pytorch.org/get-started/locally/) if GPU support is required.

Start Jupyter:

```powershell
jupyter notebook
```

Then open `3a/3a.ipynb` and select the environment's Python kernel.

## Data manifest

Each image observation is represented by one row in `data/manifest.csv`.

| Column | Meaning |
|---|---|
| `sample_id` | Unique image or patch identifier |
| `object_id` | Identity of the physical ground object |
| `class_name` | Classification label |
| `altitude_m` | Recorded altitude above local ground |
| `image_path` | Image path relative to the repository |
| `center_x`, `center_y` | Object centre in source-image pixels |
| `session_id` | Flight or collection session |
| `site_id` | Geographic area identifier |
| `split` | `source_train`, `calibration`, `development`, or `final_test` |

All observations, altitudes, frames, and derived crops of one `object_id` must remain in one partition. Adjacent frames are repeated observations and must not be treated as independent objects.

## Running the experiment

1. Configure the altitudes, patch size, and encoder near the beginning of the notebook.
2. Populate `data/manifest.csv` with matched low/high-altitude observations.
3. Validate the manifest and assign object-level partitions.
4. Generate fixed-canvas patches and inspect matched pairs manually.
5. Load the frozen encoder and extract one feature vector per patch.
6. Run the feature-shift diagnostics and inspect calibration rank.
7. Execute `run_q3a(manifest, features)` to fit the probe, evaluate the seven arms, and estimate uncertainty.
8. Save the split manifest, model identifiers, fitted mappings, metrics, and plots needed to reproduce the result.

The pilot dataset is intended to validate this pipeline. A small pilot should not be used to make a final performance claim.

## Interpretation

- **Arm C improves over Arm B:** supports a transferable orthogonal component for the tested encoder, representation, site, objects, and altitude pair.
- **Arm D improves but Arm C does not:** suggests that a mean translation is required in addition to an orthogonal map.
- **Arm F improves while C and D do not:** suggests a linear relationship that is not approximately orthogonal.
- **Calibration alignment improves but held-out recognition does not:** suggests overfitting or alignment unrelated to the probe's decision boundary.
- **No correction improves performance:** may indicate nonlinear change, lost visual information, insufficient calibration data, or remaining capture and registration confounds.

## Methodological safeguards

- Freeze the encoder, preprocessing, feature layer, and probe before final testing.
- Split by physical object before generating frames, crops, or augmentations.
- Estimate Procrustes only from calibration objects.
- Keep development and final-test objects separate.
- Use a fixed native-pixel patch size at both altitudes for the primary scale-sensitive experiment.
- Fit dimensionality reduction without using final-test objects.
- Report uncertainty using physical objects rather than individual video frames as the resampling unit.

## References

- P. H. Schonemann, [A Generalized Solution of the Orthogonal Procrustes Problem](https://doi.org/10.1007/BF02289451), 1966.
- [SciPy `orthogonal_procrustes` documentation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.linalg.orthogonal_procrustes.html).
- O. Simeoni et al., [DINOv3](https://arxiv.org/abs/2508.10104), 2025.
- [Official DINOv3 implementation](https://github.com/facebookresearch/dinov3).
- [Ryze Tello specifications](https://www.ryzerobotics.com/tello/specs).
