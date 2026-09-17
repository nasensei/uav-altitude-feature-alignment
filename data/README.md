# Data directory

This directory keeps original imagery, derived patches, extracted features, manifests, fixed experiment splits, and compact audit results separate. The same layout is used for every UAV dataset so the experiment code does not depend on one aircraft or camera.

## Layout

```text
data/
|-- raw/
|   |-- seadronessee/
|   |   |-- images/                  # Original SeaDronesSee frames
|   |   `-- annotations/             # Original annotation JSON files
|   |-- uavdt/                       # Original UAVDT files
|   |-- midair/                      # Original Mid-Air files
|   `-- tello/                       # Locally collected Tello files
|-- patches_fixed_canvas/
|   `-- seadronessee/                # Reproducible object crops
|-- features/
|   `-- seadronessee/
|       `-- dinov3_vits16/           # Frozen-encoder feature arrays
|-- manifests/                       # Dataset-specific observation tables
|-- splits/
|   `-- seadronessee/                # Saved calibration/development/test assignments
|-- audits/                          # Small, reproducible dataset checks
`-- manifest.csv                     # Master manifest expected by 3a/3a.ipynb
```

## Storage rules

- Preserve files under `raw/` exactly as downloaded or captured.
- Write processed object crops under `patches_fixed_canvas/<dataset>/`.
- Write feature arrays under `features/<dataset>/<encoder>/`.
- Record the dataset name, source file, image ID, video or flight ID, object or track ID, category, altitude, camera metadata, and split in a manifest.
- Split by flight and physical object before generating crops or features.
- Do not commit raw imagery, downloaded annotations, patches, or feature arrays to Git. They are excluded by the repository `.gitignore`.
- Commit preparation scripts, manifest schemas, fixed split definitions, source/version notes, and compact audit results.

If local storage becomes limited, `raw/`, `patches_fixed_canvas/`, and `features/` may be placed on another drive and exposed to the code through a configured data-root path. Do not duplicate large datasets solely to preserve this visual layout.

## SeaDronesSee

The current local copy contains the public training and validation `objects_in_water` MOT annotations under `raw/seadronessee/annotations/`. The full image archive has not been downloaded.

Sources:

- Official project: https://github.com/Ben93kie/SeaDronesSee
- Dataset paper: https://openaccess.thecvf.com/content/WACV2022/html/Varga_SeaDronesSee_A_Maritime_Benchmark_for_Detecting_Humans_in_Open_Water_WACV_2022_paper.html
- Public annotation mirror used for the local audit: https://huggingface.co/datasets/ObjEarth/ObjEarth-Data/tree/main/SeaDronesSee/MOT/annotations

The committed audit in `audits/seadronessee_track_audit.json` shows that the available train/validation data contain no object track observed in both the approximately 30 m and approximately 80 m bands. SeaDronesSee can support unpaired altitude analysis and a limited matched approximately 60 m versus 110 m pilot, but it cannot serve as the sole matched 30/80 m evaluation dataset.

Re-run the audit after placing the annotation files in the location shown above:

```powershell
node scripts/audit_seadronessee_tracks.js
```

### Object Detection v2 altitude subsets

The official compressed Object Detection v2 release is stored under `raw/seadronessee/odv2/`. The project downloader reads the release metadata and can fetch only the altitude bands needed by this study:

```powershell
python scripts/download_seadronessee_odv2.py --repo . --bands 25:35,70:100
```

This preserves the official train/validation folders, annotations, and filenames. The current bands support the 30 m baseline and a later real high-altitude evaluation while avoiding an unnecessary full download. Run the same command with `--all` to retrieve the complete labelled train/validation image release. The generated `download_manifest.json` records the selected files and their source metadata.

## Master manifest

`manifest.csv` remains at the existing path because the current notebook reads it directly. Dataset-specific manifests may also be stored under `manifests/` and combined into the master manifest when an experiment uses more than one dataset.
